from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
from collections.abc import Awaitable
from contextlib import suppress
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from autojev.adapters.openai_compatible import (
    LOCAL_AGENT_PROVIDER_ID,
    DeterministicDecisionClient,
    ModelResponseError,
    OpenAIAuxiliaryScenarioGenerator,
    OpenAICompatibleClient,
    OpenAIJevAgent,
)
from autojev.adapters.playwright_executor import (
    BrowserUnavailableError,
    LoginAttempt,
    PlaywrightExecutor,
)
from autojev.adapters.report import StandaloneHtmlReport
from autojev.adapters.repository import SQLiteRepository
from autojev.adapters.secrets import EphemeralCredentialVault, redact
from autojev.config import DEFAULT_AUXILIARY_MAX_REQUESTS, Settings
from autojev.domain.models import (
    ActionResult,
    AgentDecision,
    AssertionResult,
    ExplorationOptions,
    ExplorationStatus,
    Observation,
    ScenarioStatus,
    TestScenario,
    utc_now,
)
from autojev.services.business_checks import check_business_outcomes
from autojev.services.auxiliary_review_policy import (
    clamp_review_max_attempts,
    is_retryable_review_reason,
    review_retry_reason,
)
from autojev.services.visual_review_input import build_visual_review_input
from autojev.services.action_dispatch import is_stale_undispatched_action
from autojev.services.coverage import CoveragePlanner, canonical_url
from autojev.services.rerun_recipe import build_rerun_recipe
from autojev.services.scenario_documentation import local_scenario_documentation
from autojev.services.technical_signals import (
    build_scenario_evidence,
    classify_signal,
    correlate_issues,
    scenario_screenshots,
    signal_occurrence_id,
)
from autojev.services.visual_checks import (
    assess_visual_findings,
    unavailable_visual_assessment,
)
from autojev.services.verification_summary import NON_BUSINESS_CHECKS, evidence_verification_summary


@dataclass(slots=True)
class _ScenarioEnrichmentJob:
    fingerprint: str
    revision: int
    observation: Observation
    decision: AgentDecision
    result: ActionResult
    sequence: int
    retries: int = 0
    queue_order: int = 0
    ready_at: float = 0.0
    attempts: int = 0
    provider_requests: int = 0
    defer_paid_until_final: bool = False


class _LiveScenarioPipeline:
    """Persist CT drafts immediately and enrich them beside browser work.

    One auxiliary worker is intentionally enough: it runs concurrently with
    JEV + Playwright without producing a burst of provider requests. Revisions
    prevent a slow response for an obsolete locator attempt from overwriting a
    newer retry of the same semantic action.
    """

    _QUEUE_LIMIT = 256

    def __init__(
        self,
        manager: ExplorationManager,
        exploration_id: str,
        auxiliary: OpenAIAuxiliaryScenarioGenerator,
        auxiliary_client: OpenAICompatibleClient,
        *,
        use_local_evidence: bool,
        executor: PlaywrightExecutor | None = None,
        options: ExplorationOptions | None = None,
        secret_values: list[str] | None = None,
    ) -> None:
        self.manager = manager
        self.repository = manager.repository
        self.exploration_id = exploration_id
        self.auxiliary = auxiliary
        self.auxiliary_client = auxiliary_client
        self.use_local_evidence = use_local_evidence
        self.executor = executor
        self.secret_values = secret_values or []
        configured_budget = getattr(options, "auxiliary_max_requests", None)
        self.request_budget = int(configured_budget if configured_budget is not None else self._setting("auxiliary_max_requests", DEFAULT_AUXILIARY_MAX_REQUESTS))
        self.max_attempts = clamp_review_max_attempts(
            getattr(options, "auxiliary_review_max_attempts", None)
            or self._setting("auxiliary_review_max_attempts", 3)
        )
        self.queue: asyncio.Queue[_ScenarioEnrichmentJob | None] = asyncio.Queue(
            maxsize=self._QUEUE_LIMIT
        )
        self.revisions: dict[str, int] = {}
        self.sequences: dict[str, int] = {}
        self.enriched: set[str] = set()
        self.skipped: set[str] = set()
        self.drafts: dict[str, TestScenario] = {}
        self.worker: asyncio.Task[None] | None = None
        self.started = False
        self.closed = False
        self._usage_is_drained = True
        self._next_request_at = 0.0
        self._provider_pause_count = 0
        self._stop_reason: str | None = None
        self._queue_sequence = 0
        self._visually_reviewed_pages: set[str] = set()
        self._visually_reviewed_families: set[str] = set()
        self._dispatched_visual_fingerprints: set[str] = set()
        self._deferred_visual_jobs: dict[str, _ScenarioEnrichmentJob] = {}
        self._flushing_final_reviews = False
        if isinstance(auxiliary_client, OpenAICompatibleClient):
            # The transport enforces the same bound even for its one explicit
            # format-compatibility retry; a configured cap is never exceeded.
            auxiliary_client.chat_request_budget = self.request_budget

    def _setting(self, name: str, default: Any) -> Any:
        return getattr(getattr(self.manager, "settings", None), name, default)

    def _uses_compact_visual_contract(self, scenario: TestScenario) -> bool:
        return (
            (scenario.evidence.get("visual_review_provenance") or {}).get("response_contract") == "visual_findings_v1"
            or getattr(self.auxiliary, "response_contract", None) == "visual_findings_v1"
        )

    def _progress_payload(self) -> dict[str, Any]:
        return {
            "current": len(self.enriched) + len(self.skipped), "total": self.total,
            "enriched": len(self.enriched), "local_evidence": self.total - len(self.enriched),
            "pending": self.total - len(self.enriched) - len(self.skipped),
            "deferred": sum((s.evidence.get("visual_review_provenance") or s.evidence.get("documentation_provenance") or {}).get("reason") == "awaiting_provider_retry" for s in self.drafts.values()),
            "provider_requests": int(getattr(self.auxiliary, "request_count", 0)),
            "request_budget": self.request_budget,
            "max_attempts": self.max_attempts,
            "coalesced_visual_revisits": len(self._deferred_visual_jobs),
            "stage": "parallel_enrichment",
        }

    def _hold_visual_revisit(
        self, job: _ScenarioEnrichmentJob, cached: TestScenario | None,
    ) -> bool:
        """Review new CTs in parallel, coalesce paid revisits until coverage ends.

        Restoration can revisit the same CT many times minutes apart, beyond
        a short debounce. Its local evidence stays durable immediately, but
        only the latest revisit needs another paid image review. Exact cache
        hits remain free to apply live. Never reuse an old visual verdict for
        changed evidence.
        """
        if self.closed or not job.defer_paid_until_final or cached is not None:
            return False
        if self.revisions.get(job.fingerprint) == job.revision:
            self._deferred_visual_jobs[job.fingerprint] = job
            self.repository.add_event(
                self.exploration_id, "info", "scenario_visual_revisit_coalesced",
                f"CT{job.sequence:03d}: atualização visual agrupada para revisar a evidência final",
                {**self._progress_payload(), "scenario_id": f"CT{job.sequence:03d}",
                 "revision": job.revision, "reason": "awaiting_final_visual_evidence"},
            )
        return True

    def _flush_final_visual_jobs(self) -> None:
        # Fill available queue slots, then let each worker completion pump the
        # rest. Put BEFORE task_done so Queue.join cannot finish between jobs.
        # This handles more revisits than the bounded queue can hold at once.
        if not self._flushing_final_reviews:
            return
        while self._deferred_visual_jobs and not self.queue.full():
            fingerprint = next(iter(self._deferred_visual_jobs))
            job = self._deferred_visual_jobs.pop(fingerprint)
            if (self.revisions.get(fingerprint) == job.revision
                and fingerprint in self.sequences and fingerprint not in self.enriched):
                self.queue.put_nowait(job)

    def _compact_queue(self) -> int:
        """Drop obsolete revisions without dropping any current CT review.

        Browser retries can update one CT dozens of times during a provider
        pause. Those obsolete jobs must not consume the bounded backlog and
        crowd out the latest evidence. Rotation preserves FIFO ordering and
        unfinished-task accounting, including an already waiting join().
        """
        discarded = 0
        for _ in range(self.queue.qsize()):
            job = self.queue.get_nowait()
            try:
                if job is None or self.revisions.get(job.fingerprint) == job.revision:
                    self.queue.put_nowait(job)
                else:
                    discarded += 1
            finally:
                self.queue.task_done()
        return discarded

    @staticmethod
    def _review_family(job: _ScenarioEnrichmentJob) -> str:
        # A scheduling hint only: this is NOT an evidence-cache key and never
        # marks another CT assessed. Group repeated checks on the same route
        # solely to distribute scarce provider requests more usefully.
        return json.dumps([
            canonical_url(job.observation.url),
            sorted(assertion.kind for assertion in job.result.assertions),
            str((job.observation.page_state.get("checkout") or {}).get("stage") or ""),
        ], sort_keys=True)

    def _review_priority(self, job: _ScenarioEnrichmentJob) -> int:
        if getattr(self.auxiliary, "evidence_mode", "text") != "visual":
            return 0
        business = [assertion for assertion in job.result.assertions if assertion.kind not in NON_BUSINESS_CHECKS]
        state = job.observation.page_state
        checkout = state.get("checkout") or {}
        if any(not assertion.passed for assertion in business):
            priority = 600
        elif not job.result.success:
            priority = 550
        elif any(not assertion.passed for assertion in job.result.assertions):
            priority = 520
        elif checkout.get("stage") in {"review", "complete"}:
            priority = 420
        elif state.get("product_detail"):
            priority = 400
        elif business:
            priority = 260
        else:
            priority = 100
        if canonical_url(job.observation.url) not in self._visually_reviewed_pages:
            priority += 120
        if self._review_family(job) in self._visually_reviewed_families:
            priority -= 180
        if (job.defer_paid_until_final and job.result.success
            and all(assertion.passed for assertion in job.result.assertions)):
            # At the final tail a healthy revisit must not spend a scarce
            # slot before another CT's first review. Failures retain their
            # diagnostic priority. This is ordering, never evidence reuse.
            priority -= 1000
        # An unsuccessful retry should not monopolize the next scarce slot.
        return priority - job.retries * 80

    def _reprioritize_job(self, selected: _ScenarioEnrichmentJob) -> _ScenarioEnrichmentJob:
        """Choose useful current evidence, preserving FIFO ties and all jobs.

        Called once on dequeue and again AFTER a provider pause, when cart,
        detail, checkout or a business failure may have arrived meanwhile.
        Rotation keeps Queue.join accounting correct; no CT is discarded for
        having a low priority. Once the finite backlog stops growing, every
        retained job is eligible until an explicit provider budget ends work.
        """
        if getattr(self.auxiliary, "evidence_mode", "text") != "visual":
            return selected
        for _ in range(self.queue.qsize()):
            candidate = self.queue.get_nowait()
            if candidate is None:
                self.queue.put_nowait(candidate)
                self.queue.task_done()
                continue
            if self.revisions.get(candidate.fingerprint) != candidate.revision:
                self.queue.task_done()
                continue
            selected_key = (self._review_priority(selected), -selected.queue_order)
            candidate_key = (self._review_priority(candidate), -candidate.queue_order)
            if candidate_key > selected_key:
                self.queue.put_nowait(selected)
                self.queue.task_done()
                selected = candidate
            else:
                self.queue.put_nowait(candidate)
                self.queue.task_done()
        return selected

    async def _provider_gate(self, job: _ScenarioEnrichmentJob) -> str | None:
        """Await quota recovery while keeping browser work independent.

        Waiting creates no HTTP request and consumes no attempt. The global
        budgets stop persistent quota errors; Retry-After is never shortened.
        """
        if int(getattr(self.auxiliary, "request_count", 0)) >= self.request_budget:
            return "auxiliary_request_budget_exhausted"
        if int(getattr(self.auxiliary, "rate_limit_count", 0)) >= self._setting("auxiliary_max_rate_limits", 6):
            return "auxiliary_rate_limit_budget_exhausted"
        if getattr(self.auxiliary, "non_retryable_reason", None):
            return str(self.auxiliary.non_retryable_reason)
        cooldown = float(getattr(self.auxiliary, "cooldown_remaining_seconds", 0))
        if not math.isfinite(cooldown):
            return "auxiliary_authentication_rejected"
        delay = max(cooldown, self._next_request_at - time.monotonic(), 0.0)
        if delay <= 0:
            return None
        # A daily Retry-After cannot finish inside this bounded run. Preserve
        # the CT as unavailable, rather than occupying a task until tomorrow.
        if delay > self._setting("auxiliary_tail_timeout_seconds", 600.0):
            return "auxiliary_provider_pause_exceeds_budget"
        if cooldown > 0:
            self._provider_pause_count += 1
            self.repository.add_event(
                self.exploration_id, "warning", "scenario_enrichment_waiting_provider",
                "LLM auxiliar aguardando o limite do provedor; CTs locais continuam disponíveis",
                {**self._progress_payload(), "scenario_id": f"CT{job.sequence:03d}", "retry_after_seconds": round(delay, 2)},
            )
        pause = asyncio.create_task(asyncio.sleep(delay))
        cancellation = asyncio.create_task(self.manager._wait_for_cancellation(self.exploration_id))
        try:
            done, _ = await asyncio.wait({pause, cancellation}, return_when=asyncio.FIRST_COMPLETED)
            if cancellation in done:
                cancellation.result()
                return "exploration_cancelled"
            return None
        finally:
            for task in (pause, cancellation):
                if not task.done():
                    task.cancel()
            await asyncio.gather(pause, cancellation, return_exceptions=True)

    def _defer_rate_limited(self, job: _ScenarioEnrichmentJob) -> bool:
        return self._defer_review(job, "auxiliary_rate_limited")

    def _defer_review(self, job: _ScenarioEnrichmentJob, reason: str) -> bool:
        if job.retries >= self.max_attempts - 1 or job.provider_requests >= self.max_attempts:
            self._mark_skipped(job, "auxiliary_rate_limit_retry_exhausted" if reason == "auxiliary_rate_limited" else reason)
            return False
        job.retries += 1
        retry_delay = self._setting(
            "auxiliary_review_retry_delay_seconds",
            0.5 if isinstance(self.auxiliary_client, OpenAICompatibleClient) else 0.0,
        )
        job.ready_at = time.monotonic() + max(0.0, float(retry_delay)) * job.retries
        scenario = self.drafts.get(job.fingerprint)
        if scenario is not None:
            if scenario.evidence.get("visual_assessment", {}).get("requested"):
                scenario.evidence["visual_assessment"] = {
                    **(scenario.evidence.get("visual_assessment") or {}),
                    "requested": True, "status": "pending", "reason": "awaiting_provider_retry",
                    "retry_count": job.retries,
                    "max_attempts": self.max_attempts,
                    "limitations": ["Revisão aguardando uma nova tentativa limitada; não é uma aprovação visual."],
                }
            if self._uses_compact_visual_contract(scenario):
                scenario.evidence["visual_review_provenance"] = {
                    "provider_requests": 0,
                    **(scenario.evidence.get("visual_review_provenance") or {}),
                    "source": "local_draft", "status": "pending",
                    "reason": "awaiting_provider_retry", "model_input": "none",
                    "response_contract": "visual_findings_v1",
                }
                # The provider deferred a review, not the local document.
                # Do not mislabel deterministic CT text as an LLM fallback.
                scenario.evidence["documentation_provenance"] = {
                    "source": "local_evidence", "reason": "deterministic_browser_evidence",
                    "model_input": "none", "evidence_mode": "visual",
                    "provider_requests": 0, "cache_key": None,
                }
            else:
                scenario.evidence["documentation_provenance"] = {
                    "source": "local_draft", "reason": "awaiting_provider_retry", "model_input": "none",
                    "evidence_mode": "visual" if scenario.evidence.get("visual_assessment", {}).get("requested") else "text",
                }
            scenario.evidence["verification_summary"] = evidence_verification_summary(scenario.evidence)
            self.repository.add_scenario(self.exploration_id, job.sequence, scenario)
        try:
            self.queue.put_nowait(job)
        except asyncio.QueueFull:
            self._mark_skipped(job, "auxiliary_backlog_full")
            return False
        self.repository.add_event(
            self.exploration_id, "warning", "scenario_enrichment_deferred",
            f"CT{job.sequence:03d}: nova tentativa de revisão ({job.retries + 1} de {self.max_attempts})",
            {**self._progress_payload(), "scenario_id": f"CT{job.sequence:03d}", "retry_count": job.retries, "reason": reason},
        )
        return True

    @property
    def total(self) -> int:
        return len(self.sequences)

    @property
    def has_work(self) -> bool:
        return bool(
            self.sequences
            or not self.queue.empty()
            or (self.worker is not None and not self.worker.done())
        )

    async def start(self) -> None:
        if self.started:
            return
        self.started = True
        self.repository.add_event(
            self.exploration_id,
            "info",
            "scenario_parallel_started",
            (
                "A criação dos casos de teste começou em paralelo com a "
                "exploração do navegador"
            ),
            {
                **self._progress_payload(),
            },
        )
        if not self.use_local_evidence:
            self.worker = asyncio.create_task(self._worker())

    async def capture(
        self,
        fingerprint: str,
        observation: Observation,
        decision: AgentDecision,
        result: ActionResult,
        sequence: int,
    ) -> None:
        """Make the local CT durable before returning to browser planning."""

        if self.closed or is_stale_undispatched_action(result):
            return
        revision = self.revisions.get(fingerprint, 0) + 1
        existed = fingerprint in self.sequences
        self.revisions[fingerprint] = revision
        self.sequences[fingerprint] = sequence
        self.enriched.discard(fingerprint)
        self.skipped.discard(fingerprint)
        self._deferred_visual_jobs.pop(fingerprint, None)

        scenario = self.manager._scenario_from_evidence(
            observation, decision, result, sequence
        )
        if getattr(self.auxiliary, "evidence_mode", "text") == "visual":
            scenario.evidence["visual_review_input"] = build_visual_review_input(
                observation, decision, result, sequence, secret_values=self.secret_values
            )
        if scenario.evidence.get("visual_assessment", {}).get("requested") and (
            self.use_local_evidence or self.worker is None or self.worker.done()
        ):
            scenario.evidence["visual_assessment"] = unavailable_visual_assessment(
                requested=True, model=self.auxiliary.model,
                reason="local_evidence_mode" if self.use_local_evidence else "auxiliary_worker_unavailable",
            )
        self._preserve_visual_history(
            scenario, self.drafts.get(fingerprint), archive_current=True
        )
        self.drafts[fingerprint] = scenario
        if self.use_local_evidence or self.worker is None or self.worker.done():
            self.skipped.add(fingerprint)
        self.repository.add_scenario(self.exploration_id, sequence, scenario)
        self.repository.add_event(
            self.exploration_id,
            "info",
            "scenario_draft_updated" if existed else "scenario_draft_created",
            (
                f"{scenario.id} atualizado com a tentativa mais recente"
                if existed
                else f"{scenario.id} preservado enquanto a exploração continua"
            ),
            {
                **self._progress_payload(),
                "scenario_id": scenario.id,
                "revision": revision,
            },
        )

        if self.use_local_evidence or self.worker is None:
            return
        if self.worker.done():
            # Never let a documentation failure hold browser coverage hostage.
            # The deterministic draft above is already durable, so preserve it
            # and make the degraded state explicit instead of filling a queue
            # that no worker can consume.
            self.skipped.add(fingerprint)
            self.repository.add_event(
                self.exploration_id,
                "warning",
                "scenario_enrichment_worker_unavailable",
                (
                    f"{scenario.id} foi preservado localmente porque o worker "
                    "da LLM auxiliar não estava disponível"
                ),
                {
                    **self._progress_payload(),
                    "scenario_id": scenario.id,
                },
            )
            return
        job = _ScenarioEnrichmentJob(
            fingerprint=fingerprint,
            revision=revision,
            observation=observation,
            decision=decision,
            result=result,
            sequence=sequence,
            defer_paid_until_final=(
                getattr(self.auxiliary, "evidence_mode", "text") == "visual"
                and fingerprint in self._dispatched_visual_fingerprints
            ),
        )
        self._queue_sequence += 1
        job.queue_order = self._queue_sequence
        # Wait only until this evidence settles, not once per queued CT. Old
        # queued jobs are ready immediately; rapid retries replace their job
        # before any paid request is made. Browser execution never awaits it.
        debounce = self._setting(
            "auxiliary_review_debounce_seconds",
            0.35 if isinstance(self.auxiliary_client, OpenAICompatibleClient) else 0.0,
        )
        job.ready_at = time.monotonic() + max(0.0, float(debounce))
        self._compact_queue()
        try:
            self.queue.put_nowait(job)
        except asyncio.QueueFull:
            # Browser coverage must never wait for documentation. The local CT
            # is already safe and remains available even if the provider lags.
            self.skipped.add(fingerprint)
            self.repository.add_event(
                self.exploration_id,
                "warning",
                "scenario_enrichment_backlog_full",
                (
                    f"{scenario.id} foi preservado localmente; a fila da LLM "
                    "auxiliar atingiu o limite de segurança"
                ),
                {
                    **self._progress_payload(),
                    "scenario_id": scenario.id,
                },
            )
            self._mark_skipped(job, "auxiliary_backlog_full")

    @staticmethod
    def _preserve_visual_history(
        scenario: TestScenario, previous: TestScenario | None, *, archive_current: bool,
    ) -> None:
        """Keep prior image reviews in the CT, not in its current verdict.

        Auxiliary evidence deliberately never mutates browser ActionResults.
        Therefore action-retry merging alone cannot preserve an already
        completed visual review. Carry it through draft/worker replacement
        while leaving only the latest attempt's assertions authoritative.
        """
        if previous is None:
            return
        history = [
            *deepcopy(scenario.evidence.get("visual_assessment_history") or []),
            *deepcopy(previous.evidence.get("visual_assessment_history") or []),
        ]
        assessment = previous.evidence.get("visual_assessment") or {}
        if archive_current and assessment.get("status") == "assessed":
            history.append({
                "action_step": previous.evidence.get("action_step"),
                "assessment": deepcopy(assessment),
            })
        if history:
            scenario.evidence["visual_assessment_history"] = list({
                json.dumps(item, sort_keys=True): item for item in history
            }.values())
        entries = list(scenario.evidence.get("timeline") or [])
        shots = list(scenario.screenshots)
        for entry in previous.evidence.get("timeline") or []:
            if (entry.get("assertion") or {}).get("kind") != "visual_model":
                continue
            if not archive_current and not entry.get("previous_attempt"):
                continue
            archived = deepcopy(entry)
            archived["previous_attempt"] = True
            archived["title"] = "Tentativa anterior · indício visual a revisar"
            entries.append(archived)
            if archived.get("screenshot"):
                shots.append(str(archived["screenshot"]))
        scenario.evidence["timeline"] = sorted(
            {entry["id"]: entry for entry in entries}.values(),
            key=lambda entry: str(entry.get("occurred_at") or ""),
        )
        scenario.screenshots = list(dict.fromkeys(shots))

    async def remove(self, fingerprint: str, sequence: int) -> None:
        """Invalidate an unverified draft recovered by a later no-op retry."""

        if fingerprint not in self.sequences:
            return
        self.revisions[fingerprint] = self.revisions.get(fingerprint, 0) + 1
        stored_sequence = self.sequences.pop(fingerprint, sequence)
        self.enriched.discard(fingerprint)
        self.skipped.discard(fingerprint)
        self.drafts.pop(fingerprint, None)
        self._deferred_visual_jobs.pop(fingerprint, None)
        self._dispatched_visual_fingerprints.discard(fingerprint)
        scenario_id = f"CT{stored_sequence:03d}"
        self.repository.delete_scenario(self.exploration_id, scenario_id)
        self.repository.add_event(
            self.exploration_id,
            "info",
            "scenario_draft_removed",
            (
                f"{scenario_id} provisório foi removido após uma tentativa "
                "bem-sucedida sem falha verificável"
            ),
            {
                **self._progress_payload(),
                "scenario_id": scenario_id,
            },
        )

    async def _cached_review(self, job: _ScenarioEnrichmentJob) -> TestScenario | None:
        lookup = getattr(self.auxiliary, "cached_scenario", None)
        if lookup is None:
            return None
        return await lookup(job.observation, job.decision, job.result, job.sequence)

    async def _settle_review(self, job: _ScenarioEnrichmentJob) -> None:
        delay = job.ready_at - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)

    async def _review_scenario(
        self, job: _ScenarioEnrichmentJob, cached: TestScenario | None,
    ) -> TestScenario:
        if cached is not None:
            return cached
        job.attempts += 1
        return await self.auxiliary.create_scenario(
            job.observation, job.decision, job.result, job.sequence
        )

    async def _worker(self) -> None:
        while True:
            job = await self.queue.get()
            try:
                try:
                    if job is None:
                        return
                    if self.revisions.get(job.fingerprint) != job.revision:
                        continue
                    if self.repository.is_cancel_requested(self.exploration_id):
                        self._mark_skipped(job, "exploration_cancelled")
                        return

                    job = self._reprioritize_job(job)
                    await self._settle_review(job)
                    if self.revisions.get(job.fingerprint) != job.revision:
                        continue
                    # Cache hits cost nothing and remain usable after the
                    # paid-request cap, a provider pause or an auth rejection.
                    cached = await self._cached_review(job)
                    if self._hold_visual_revisit(job, cached):
                        continue
                    if cached is None:
                        gate_reason = await self._provider_gate(job)
                        if gate_reason:
                            self._mark_skipped(job, gate_reason)
                            continue
                        if self.revisions.get(job.fingerprint) != job.revision:
                            continue
                        job = self._reprioritize_job(job)
                        await self._settle_review(job)
                        if self.revisions.get(job.fingerprint) != job.revision:
                            continue
                        cached = await self._cached_review(job)
                        if self._hold_visual_revisit(job, cached):
                            continue

                    if self.repository.is_cancel_requested(self.exploration_id):
                        self._mark_skipped(job, "exploration_cancelled")
                        return

                    review_started_at = time.monotonic()
                    requests_before = int(getattr(self.auxiliary, "request_count", 0))
                    transport_budget = getattr(self.auxiliary_client, "chat_request_budget", None)
                    if cached is None and getattr(self.auxiliary, "evidence_mode", "text") == "visual":
                        # Mark BEFORE awaiting the response: a browser revisit
                        # during an in-flight call must also be coalesced.
                        self._dispatched_visual_fingerprints.add(job.fingerprint)
                    if isinstance(self.auxiliary_client, OpenAICompatibleClient) and cached is None:
                        # The format compatibility fallback also consumes an
                        # HTTP request. It must not silently multiply the
                        # per-review three-attempt allowance.
                        self.auxiliary_client.chat_request_budget = min(
                            self.request_budget,
                            self.auxiliary_client.chat_request_count + max(0, self.max_attempts - job.provider_requests),
                        )
                    scenario_task = asyncio.create_task(
                        self._review_scenario(job, cached)
                    )
                    self._usage_is_drained = False
                    cancellation_task = asyncio.create_task(
                        self.manager._wait_for_cancellation(self.exploration_id)
                    )
                    try:
                        done, _ = await asyncio.wait(
                            {scenario_task, cancellation_task},
                            timeout=45.0,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if cancellation_task in done:
                            cancellation_task.result()
                            self._mark_skipped(job, "exploration_cancelled")
                            return
                        if scenario_task not in done:
                            raise TimeoutError
                        scenario = scenario_task.result()
                        # Ground the response even if the browser advanced.
                        # It belongs to that earlier attempt, not the latest
                        # verdict, but its paid evidence must not disappear.
                        self.manager._merge_scenario_enrichment(
                            scenario, job.observation, job.decision, job.result
                        )
                        if self.revisions.get(job.fingerprint) != job.revision:
                            current = self.drafts.get(job.fingerprint)
                            if current and (scenario.evidence.get("visual_assessment") or {}).get("status") == "assessed":
                                self._preserve_visual_history(current, scenario, archive_current=True)
                                self.repository.add_scenario(self.exploration_id, self.sequences[job.fingerprint], current)
                            self.repository.add_event(
                                self.exploration_id, "info", "scenario_review_obsolete",
                                f"CT{job.sequence:03d}: revisão de uma tentativa anterior; resultado atual preservado",
                                {**self._progress_payload(), "scenario_id": f"CT{job.sequence:03d}",
                                 "revision": job.revision, "archived": bool(current),
                                 "review_provider_requests": max(0, int(getattr(self.auxiliary, "request_count", 0)) - requests_before)},
                            )
                            continue
                        if job.fingerprint not in self.sequences:
                            continue
                        # Local checks stay authoritative. A grounded visual
                        # assessment may add negative evidence, never erase a
                        # business failure or replace browser observations.
                        original = self.drafts.get(job.fingerprint)
                        if original and original.evidence.get("visual_review_input"):
                            scenario.evidence["visual_review_input"] = deepcopy(original.evidence["visual_review_input"])
                        scenario.evidence["auxiliary_review_metrics"] = {
                            "elapsed_seconds": round(time.monotonic() - review_started_at, 3),
                            "provider_requests": max(0, int(getattr(self.auxiliary, "request_count", 0)) - requests_before),
                            "attempts": job.attempts,
                            "max_attempts": self.max_attempts,
                            "total_provider_requests": job.provider_requests + max(0, int(getattr(self.auxiliary, "request_count", 0)) - requests_before),
                        }
                        self._preserve_visual_history(
                            scenario, self.drafts.get(job.fingerprint), archive_current=False
                        )
                        self.repository.add_scenario(
                            self.exploration_id, job.sequence, scenario
                        )
                        self.drafts[job.fingerprint] = scenario
                        provenance = (scenario.evidence.get("visual_review_provenance")
                                      or scenario.evidence.get("documentation_provenance", {}))
                        retry_reason = review_retry_reason(scenario)
                        if (not self._uses_compact_visual_contract(scenario)
                            and provenance.get("source") in {"auxiliary_llm", "auxiliary_cache"}):
                            # Legacy full-document responses can successfully
                            # document the CT without supplying a visual review.
                            # Do not discard that received documentation.
                            retry_reason = None
                        spent = max(0, int(getattr(self.auxiliary, "request_count", 0)) - requests_before)
                        if (retry_reason == "auxiliary_request_budget_exhausted"
                            and int(getattr(self.auxiliary, "request_count", 0)) < self.request_budget
                            and job.provider_requests + spent >= self.max_attempts):
                            retry_reason = "auxiliary_review_attempts_exhausted"
                            self._mark_skipped(job, retry_reason)
                            continue
                        if retry_reason and is_retryable_review_reason(retry_reason):
                            # Account this attempt before deciding whether to
                            # queue another one; finally accounts all paths.
                            job.provider_requests += spent
                            if job.provider_requests >= self.max_attempts:
                                self._mark_skipped(job, retry_reason)
                            else:
                                self._defer_review(job, retry_reason)
                            job.provider_requests -= spent
                            continue
                        if provenance.get("source", "auxiliary_llm") not in {"auxiliary_llm", "auxiliary_cache"}:
                            self._mark_skipped(
                                job,
                                str(
                                    provenance.get("reason")
                                    or "auxiliary_local_fallback"
                                ),
                            )
                            continue
                        visual = scenario.evidence.get("visual_assessment") or {}
                        if self._uses_compact_visual_contract(scenario) and visual.get("status") != "assessed":
                            # A compact response supplied no documentation to
                            # enrich. Missing/invalid visual JSON is an
                            # unavailable review, never completed enrichment.
                            self._mark_skipped(job, str(visual.get("reason") or "auxiliary_visual_evidence_unavailable"))
                            continue
                        self.enriched.add(job.fingerprint)
                        self.skipped.discard(job.fingerprint)
                        if visual.get("status") == "assessed":
                            self._visually_reviewed_pages.add(canonical_url(job.observation.url))
                            self._visually_reviewed_families.add(self._review_family(job))
                        if visual.get("requested") and visual.get("status") == "unavailable":
                            self.repository.add_event(
                                self.exploration_id, "warning", "scenario_visual_unavailable",
                                f"{scenario.id}: documentação recebida, mas análise visual indisponível; verificações locais preservadas",
                                {**self._progress_payload(), "scenario_id": scenario.id, "reason": visual.get("reason")},
                            )
                        self.repository.add_event(
                            self.exploration_id,
                            "info",
                            "scenario_enriched",
                            (
                                f"{scenario.id}: revisão reutilizada para evidência idêntica, sem nova chamada"
                                if provenance.get("source") == "auxiliary_cache"
                                else f"{scenario.id} aprimorado pela LLM auxiliar em paralelo"
                            ),
                            {
                                **self._progress_payload(),
                                "scenario_id": scenario.id,
                                "reused_identical_evidence": provenance.get("source") == "auxiliary_cache",
                                "review_elapsed_seconds": scenario.evidence["auxiliary_review_metrics"]["elapsed_seconds"],
                                "review_provider_requests": scenario.evidence["auxiliary_review_metrics"]["provider_requests"],
                            },
                        )
                    except Exception as exc:
                        reason = exc.__class__.__name__
                        if is_retryable_review_reason(reason):
                            spent = max(0, int(getattr(self.auxiliary, "request_count", 0)) - requests_before)
                            job.provider_requests += spent
                            self._defer_review(job, reason)
                            job.provider_requests -= spent
                        else:
                            self._mark_skipped(job, reason)
                    finally:
                        # Free-model pacing is per-run and includes rejected
                        # calls. Paid models are not delayed unnecessarily.
                        if str(getattr(self.auxiliary, "model", "")).endswith(":free") and (
                            not hasattr(self.auxiliary, "request_count")
                            or int(self.auxiliary.request_count) > requests_before
                        ):
                            self._next_request_at = time.monotonic() + self._setting("auxiliary_request_interval_seconds", 3.1)
                        for task in (scenario_task, cancellation_task):
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(
                            scenario_task, cancellation_task, return_exceptions=True
                        )
                        job.provider_requests += max(0, int(getattr(self.auxiliary, "request_count", 0)) - requests_before)
                        if isinstance(self.auxiliary_client, OpenAICompatibleClient):
                            self.auxiliary_client.chat_request_budget = transport_budget
                        # Accounting failures must not kill the sole worker.
                        # finish() performs one final idempotent drain as well.
                        with suppress(Exception):
                            await self.manager._record_model_usage(
                                self.exploration_id,
                                [self.auxiliary_client],
                                self.executor,
                                "auxiliary",
                            )
                            self._usage_is_drained = True
                except asyncio.CancelledError:
                    if job is not None:
                        self._mark_skipped(job, self._stop_reason or ("exploration_cancelled" if self.repository.is_cancel_requested(self.exploration_id) else "documentation_interrupted"))
                    raise
                except Exception as exc:
                    # Repository/cancellation checks can fail outside the
                    # provider block. Preserve the local CT and keep consuming
                    # later jobs rather than silently killing the queue.
                    if job is not None:
                        self._mark_skipped(job, exc.__class__.__name__)
            finally:
                self._flush_final_visual_jobs()
                self.queue.task_done()

    def _mark_skipped(self, job: _ScenarioEnrichmentJob, reason: str) -> None:
        if self.revisions.get(job.fingerprint) != job.revision or job.fingerprint in self.enriched:
            return
        self.skipped.add(job.fingerprint)
        with suppress(Exception):
            scenario = self.drafts.get(job.fingerprint)
            if scenario and scenario.evidence.get("visual_assessment", {}).get("requested") and scenario.evidence.get("visual_assessment", {}).get("status") != "assessed":
                review = scenario.evidence.get("visual_review_provenance") or scenario.evidence.get("documentation_provenance") or {}
                allowed = {
                    "before": (job.result.evidence.get("before_screenshot"), job.result.before_url),
                    "after": (job.observation.screenshot, job.observation.url),
                }
                images = [
                    image for image in (scenario.evidence.get("visual_assessment") or {}).get("images", [])
                    if isinstance(image, dict) and image.get("ref") in allowed
                    and allowed[image["ref"]][0]
                    and image.get("screenshot") == Path(str(allowed[image["ref"]][0])).name
                    and image.get("url") == allowed[image["ref"]][1]
                ] if review.get("model_input") == "text_and_image" else []
                scenario.evidence["visual_assessment"] = unavailable_visual_assessment(
                    requested=True, model=str(getattr(self.auxiliary, "model", "")), reason=reason,
                    images=images,
                )
            if scenario:
                if self._uses_compact_visual_contract(scenario):
                    scenario.evidence["visual_review_provenance"] = {
                        "model_input": "none", "provider_requests": 0,
                        **(scenario.evidence.get("visual_review_provenance") or {}),
                        "source": "local_fallback", "status": "unavailable", "reason": reason,
                        "response_contract": "visual_findings_v1",
                    }
                    scenario.evidence["documentation_provenance"] = {
                        "source": "local_evidence", "reason": "deterministic_browser_evidence",
                        "model_input": "none", "evidence_mode": "visual",
                        "provider_requests": 0, "cache_key": None,
                    }
                else:
                    scenario.evidence["documentation_provenance"] = {
                        **(scenario.evidence.get("documentation_provenance") or {}),
                        "source": "local_fallback", "reason": reason,
                    }
                scenario.evidence["verification_summary"] = evidence_verification_summary(scenario.evidence)
                self.repository.add_scenario(self.exploration_id, job.sequence, scenario)
            self.repository.add_event(
                self.exploration_id,
                "warning",
                "scenario_enrichment_skipped",
                (
                    f"CT{job.sequence:03d} manteve a evidência local; "
                    "a LLM auxiliar não concluiu o aprimoramento"
                ),
                {
                    **self._progress_payload(),
                    "scenario_id": f"CT{job.sequence:03d}",
                    "reason": reason,
                },
            )

    def _discard_queued_jobs(self, reason: str) -> None:
        self._flushing_final_reviews = False
        deferred = list(self._deferred_visual_jobs.values())
        self._deferred_visual_jobs.clear()
        for job in deferred:
            self._mark_skipped(job, reason)
        while True:
            try:
                job = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                if job is not None:
                    self._mark_skipped(job, reason)
            finally:
                self.queue.task_done()

    async def finish(self, *, cancelled: bool = False) -> None:
        if self.closed:
            return
        self.closed = True
        completion_reason = "local_evidence_mode"
        interrupted: asyncio.CancelledError | None = None

        try:
            if self.worker is not None:
                if cancelled:
                    completion_reason = "exploration_cancelled"
                    self._stop_reason = completion_reason
                    self.worker.cancel()
                elif self.worker.done():
                    completion_reason = "auxiliary_worker_stopped"
                else:
                    completion_reason = "documentation_complete"
                    self._flushing_final_reviews = True
                    self._flush_final_visual_jobs()
                    timeout = self._setting("auxiliary_tail_timeout_seconds", 600.0)
                    queue_drained = asyncio.create_task(self.queue.join())
                    try:
                        done, _ = await asyncio.wait(
                            {queue_drained, self.worker},
                            timeout=timeout,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if queue_drained in done:
                            queue_drained.result()
                        elif self.worker in done:
                            completion_reason = "auxiliary_worker_stopped"
                        else:
                            completion_reason = "documentation_time_limit"
                    finally:
                        if not queue_drained.done():
                            queue_drained.cancel()
                        await asyncio.gather(queue_drained, return_exceptions=True)
        except asyncio.CancelledError as exc:
            # shutdown() may cancel _run while this tail is draining. Always
            # stop and join the detached worker before propagating cancellation
            # so no provider call can survive and continue spending credits.
            completion_reason = "backend_shutdown"
            interrupted = exc
        finally:
            self._stop_reason = completion_reason
            self._flushing_final_reviews = False
            if self.worker is not None:
                if not self.worker.done():
                    self.worker.cancel()
                await asyncio.gather(self.worker, return_exceptions=True)
            self._discard_queued_jobs(completion_reason)
            # A response can register usage immediately before cancellation.
            # Drain once more after the worker is joined; drain_usage is
            # idempotent, so already persisted usage is not double-counted.
            if not self._usage_is_drained:
                with suppress(Exception):
                    await self.manager._record_model_usage(
                        self.exploration_id,
                        [self.auxiliary_client],
                        None,
                        "auxiliary",
                    )
                    self._usage_is_drained = True

        if interrupted is not None:
            raise interrupted
        if self.repository.is_cancel_requested(self.exploration_id):
            completion_reason = "exploration_cancelled"

        self.repository.add_event(
            self.exploration_id,
            "info",
            "scenario_postprocessing_completed",
            (
                f"Casos finalizados: {len(self.enriched)} aprimorados pela LLM e "
                f"{self.total - len(self.enriched)} preservados com evidência "
                "do navegador"
            ),
            {
                **self._progress_payload(),
                "current": self.total,
                "total": self.total,
                "enriched": len(self.enriched),
                "local_evidence": self.total - len(self.enriched),
                "reason": completion_reason,
                "stage": "scenarios_complete",
                "provider_requests": int(getattr(self.auxiliary, "request_count", 0)),
                "provider_rate_limits": int(getattr(self.auxiliary, "rate_limit_count", 0)),
                "provider_pauses": self._provider_pause_count,
                "request_budget": self.request_budget,
            },
        )


class ExplorationManager:
    _GENERIC_RESOURCE_ERROR = re.compile(
        r"failed to load resource:.*?status(?:\s+(?:of|code))?\s+(\d{3})",
        re.IGNORECASE,
    )

    def __init__(
        self,
        repository: SQLiteRepository,
        credential_vault: EphemeralCredentialVault,
        report_renderer: StandaloneHtmlReport,
        settings: Settings,
        max_concurrent: int = 2,
    ) -> None:
        self.repository = repository
        self.credential_vault = credential_vault
        self.report_renderer = report_renderer
        self.settings = settings
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.input_events: dict[str, asyncio.Event] = {}
        self.usage_locks: dict[str, asyncio.Lock] = {}
        self.pending_model_usage: dict[str, dict[str, dict[str, Any]]] = {}
        self.semaphore = asyncio.Semaphore(max_concurrent)

    def submit(self, exploration_id: str) -> None:
        self.input_events[exploration_id] = asyncio.Event()
        task = asyncio.create_task(self._guarded_run(exploration_id))
        self.tasks[exploration_id] = task
        task.add_done_callback(lambda _: self.tasks.pop(exploration_id, None))

    def resume_after_user_input(self, exploration_id: str) -> None:
        event = self.input_events.get(exploration_id)
        if event is None:
            return
        event.set()

    def cancel(self, exploration_id: str) -> None:
        event = self.input_events.get(exploration_id)
        if event is not None:
            event.set()

    @staticmethod
    def _needs_login(observation: Observation) -> bool:
        if observation.page_state.get("authentication_challenge") is True:
            return True
        element_text = " ".join(
            str(element.get("name") or "")
            for element in observation.interactive_elements
        )
        text = f"{observation.title} {observation.text} {observation.url} {element_text}".lower()
        login_words = (
            "log in",
            "login",
            "sign in",
            "sign-in",
            "entrar",
            "acessar",
            "username",
            "user-name",
        )
        has_login_copy = any(word in text for word in login_words)
        has_password = any(
            str(element.get("type") or "").lower() == "password"
            or "password" in str(element.get("name") or "").lower()
            or "senha" in str(element.get("name") or "").lower()
            for element in observation.interactive_elements
        )
        return has_password and has_login_copy

    MAX_SESSION_RELOGINS = 5
    LOGIN_PANEL_MESSAGE = (
        "Preencha usuário e senha na página e clique em Continuar exploração abaixo. "
        "O AutoJev tentará entrar usando os campos preenchidos, sem enviar credenciais à IA. "
        "Você também pode fazer login pelo próprio site. CAPTCHA e etapas adicionais devem ser concluídos manualmente."
    )

    @staticmethod
    def _filled_login_feedback(attempt: LoginAttempt) -> dict[str, str]:
        """Only fixed local messages may leave the credential submission path."""
        messages = {
            "authenticated": "Login realizado. Conferindo a página e os pré-requisitos do teste.",
            "incomplete_credentials": "Preencha usuário e senha no site e clique em Continuar exploração para tentar entrar.",
            "login_rejected": "O site não aceitou o login. Corrija os campos na página e clique em Continuar exploração para uma nova tentativa.",
            "login_not_confirmed": "Não foi possível confirmar a entrada. Confira a resposta do site; nenhuma nova tentativa ocorrerá sem outro clique seu.",
            "authentication_challenge": "Conclua o CAPTCHA ou a etapa adicional no próprio site e depois continue.",
            "unsafe_origin": "O login está fora do site autorizado. Volte ao site do teste antes de continuar.",
            "unsafe_form_action": "O formulário de login não possui um envio seguro para este site. Faça o login manualmente no site.",
            "unsafe_authentication_request": "Um envio inseguro de credenciais foi bloqueado. Confira o site e faça o login manualmente.",
            "controls_changed": "Os campos de login mudaram. Confira os campos e confirme novamente quando estiverem prontos.",
            "ambiguous_form": "Não foi possível identificar um único formulário seguro de login. Faça o login pelo próprio site e depois continue.",
            "ambiguous_username": "Não foi possível identificar o campo de usuário com segurança. Faça o login pelo próprio site.",
            "additional_required_fields": "O login exige outros campos. Complete a entrada pelo próprio site e depois continue.",
            "unobserved_controls": "Os controles de login não puderam ser confirmados. Faça o login pelo próprio site.",
        }
        status = attempt.status if attempt.status in {"success", "rejected", "manual_required"} else "manual_required"
        reason = attempt.reason if attempt.reason in messages else "ambiguous_form"
        return {"status": status, "reason": reason, "reason_label": messages[reason]}

    @classmethod
    def _automatic_login_message(cls, reason: str) -> str:
        if reason == "authentication_challenge":
            return (
                "O site exige CAPTCHA ou uma etapa adicional de autenticação. "
                "Conclua essa etapa no site. A exploração retomará quando a entrada for confirmada. "
                "Não reenviamos suas credenciais automaticamente."
            )
        explanation = {
            "incomplete_credentials": "As credenciais informadas estão incompletas.",
            "login_rejected": "O site rejeitou a tentativa de login automático. Não vamos reenviar suas credenciais.",
            "authentication_challenge": "O site exige CAPTCHA ou uma etapa adicional de autenticação.",
            "unsafe_origin": "A tela de login não está na origem autorizada.",
            "unsafe_form_action": "O formulário envia o login para outra origem ou usa um envio não seguro.",
            "unsafe_authentication_request": "Uma requisição de autenticação não segura foi bloqueada.",
            "controls_changed": "Os campos de login mudaram durante o preenchimento automático.",
            "already_attempted": "A tentativa automática já foi realizada nesta exploração.",
            "login_not_confirmed": "Não foi possível confirmar o login automático. A tentativa não será repetida.",
        }.get(reason, "Não foi possível identificar um formulário de login único e seguro.")
        return f"{explanation} Continue manualmente. {cls.LOGIN_PANEL_MESSAGE}"

    async def _poll_login_gone(
        self, executor: PlaywrightExecutor, deadline: float | None
    ) -> bool:
        """Retorna True quando a tela de login some (login feito no site).

        Exige 2 leituras seguidas sem formulário para não disparar durante
        o carregamento da navegação. Retorna False se o prazo estourar.
        """
        clear_reads = 0
        while True:
            await asyncio.sleep(1.5)
            if deadline is not None and time.monotonic() >= deadline:
                return False
            try:
                if await executor.is_login_page():
                    clear_reads = 0
                else:
                    clear_reads += 1
                    if clear_reads >= 2:
                        return True
            except Exception:
                pass

    async def _wait_for_user_input(
        self,
        exploration_id: str,
        observation: Observation,
        executor: PlaywrightExecutor,
        panel_message: str | None = None,
        deadline: float | None = None,
    ) -> str:
        """Aguarda login manual sem ignorar o fechamento do navegador."""
        event = self.input_events.setdefault(exploration_id, asyncio.Event())
        event.clear()
        self.repository.update_exploration(
            exploration_id, status=ExplorationStatus.WAITING_INPUT
        )
        self.repository.add_event(
            exploration_id,
            "warning",
            "waiting_input",
            "Login detected. Complete the login in the browser window, then continue the exploration.",
            {"kind": "login", "url": observation.url},
        )
        await executor.show_intervention_panel(
            panel_message or self.LOGIN_PANEL_MESSAGE
        )
        app_resume = asyncio.create_task(event.wait())
        browser_resume = asyncio.create_task(executor.wait_for_user_input())
        auto_resume = asyncio.create_task(self._poll_login_gone(executor, deadline))
        browser_closed = asyncio.create_task(executor.wait_until_inactive())
        done, pending = await asyncio.wait(
            {app_resume, browser_resume, auto_resume, browser_closed},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if browser_closed in done:
            # Closing the browser is an explicit user stop signal.  Do not
            # leave the exploration waiting for the login confirmation event.
            return "browser_closed"
        auto = auto_resume in done and auto_resume.result() is True
        timed_out = auto_resume in done and auto_resume.result() is False
        for task in done:
            if task not in {auto_resume, browser_closed}:
                task.result()
        if self.repository.is_cancel_requested(exploration_id):
            return "cancelled"
        if timed_out:
            return "timeout"
        if auto:
            # Login feito direto no site: segue sozinho, sem exigir clique.
            # Garante que o loop do executor seja liberado e a seção de
            # ação seja escondida (o modo cancelado acima não limpa o painel).
            executor.notify_user_input()
            await executor.hide_intervention_panel()
            self.repository.update_exploration(
                exploration_id, status=ExplorationStatus.RUNNING
            )
            self.repository.add_event(
                exploration_id,
                "info",
                "login_auto_detected",
                "Login completed in the browser. Exploration resumed automatically.",
            )
            await executor.update_live_panel(
                "Exploração retomada", "Login detectado — seguindo automaticamente."
            )
            return "auto"
        self.repository.update_exploration(
            exploration_id, status=ExplorationStatus.RUNNING
        )
        self.repository.add_event(
            exploration_id,
            "info",
            "input_received",
            "User confirmed that the login step is complete.",
        )
        return "manual"

    @staticmethod
    def _operation_timeout(deadline: float | None) -> float:
        """Bound one external call without imposing a run-wide deadline."""

        if deadline is None:
            return 120.0
        return max(deadline - time.monotonic(), 0.1)

    @staticmethod
    async def _await_decision_while_browser_active(
        executor: PlaywrightExecutor,
        decision_call: Awaitable[AgentDecision],
        timeout: float,
    ) -> AgentDecision:
        """Cancel an in-flight planning request when the browser is closed."""

        decision_task = asyncio.ensure_future(decision_call)
        browser_closed = asyncio.create_task(executor.wait_until_inactive())
        try:
            done, _ = await asyncio.wait(
                {decision_task, browser_closed},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise asyncio.TimeoutError
            if browser_closed in done:
                raise BrowserUnavailableError(
                    "Browser window was closed by the user"
                )
            return decision_task.result()
        finally:
            for task in (decision_task, browser_closed):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                decision_task, browser_closed, return_exceptions=True
            )

    @staticmethod
    def _step_label(step: int, max_steps: int | None) -> str:
        return f"Etapa {step}" + (f" de {max_steps}" if max_steps is not None else "")

    async def shutdown(self) -> None:
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _guarded_run(self, exploration_id: str) -> None:
        try:
            async with self.semaphore:
                await self._run(exploration_id)
        except asyncio.CancelledError:
            # Cancellation can also be delivered while _run is already in its
            # asynchronous finally/post-processing section. In that phase the
            # inner try/except is no longer active, so keep the persisted run
            # from being stranded as queued/running/postprocessing on shutdown.
            message = "Backend shutdown interrupted this exploration"
            self.repository.add_event(
                exploration_id,
                "warning",
                "cancelled",
                message,
                {"reason": "backend_shutdown"},
            )
            self.repository.update_exploration(
                exploration_id,
                status=ExplorationStatus.CANCELLED,
                finished_at=utc_now(),
                error=message,
            )
            self.input_events.pop(exploration_id, None)
            self.credential_vault.discard(exploration_id)
            getattr(self, "usage_locks", {}).pop(exploration_id, None)

    def _client_for(self, provider_id: str) -> OpenAICompatibleClient:
        provider = self.repository.get_provider(provider_id)
        if not provider.enabled:
            raise RuntimeError(f"Provider {provider.name} is disabled")
        return OpenAICompatibleClient(
            provider.base_url,
            self.repository.get_provider_secret(provider_id),
            self.settings.http_timeout_seconds,
        )

    async def _record_model_usage(
        self,
        exploration_id: str,
        clients: list[OpenAICompatibleClient],
        executor: PlaywrightExecutor | None = None,
        role: str | None = None,
    ) -> None:
        if role not in {"jev", "auxiliary"}:
            raise ValueError("Model usage must have an explicit JEV or auxiliary role")
        # JEV and the auxiliary documentation worker intentionally run in
        # parallel. Serialize only their tiny accounting section so concurrent
        # read-modify-write updates cannot lose tokens or cost.
        lock = self.usage_locks.setdefault(exploration_id, asyncio.Lock())
        panel_snapshot: tuple[int, float | None, bool] | None = None
        async with lock:
            if not hasattr(self, "pending_model_usage"):
                self.pending_model_usage = {}
            pending = self.pending_model_usage.setdefault(exploration_id, {})
            # drain_usage() transfers ownership to this buffer. Do not drop
            # it until the repository write succeeds: a transient DB read or
            # write error must be retryable even when the next drain is empty.
            usage = pending.setdefault(role, {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost": 0.0,
                "cost_known": False,
                "cost_complete": True,
                "usage_records": 0,
                "cost_reported_records": 0,
            })
            for client in clients:
                current = client.drain_usage()
                for key in (
                    "prompt_tokens",
                    "completion_tokens",
                    "total_tokens",
                    "cost",
                ):
                    usage[key] += current.get(key) or 0
                usage["cost_known"] = (
                    usage["cost_known"] or current["cost_known"]
                )
                records = int(current.get("usage_records", int(bool(
                    current.get("total_tokens")
                    or current.get("prompt_tokens")
                    or current.get("completion_tokens")
                    or current.get("cost_known")
                ))))
                usage["usage_records"] += records
                usage["cost_reported_records"] += int(current.get(
                    "cost_reported_records", records if current["cost_known"] else 0
                ))
                if records:
                    usage["cost_complete"] = usage["cost_complete"] and bool(
                        current.get("cost_complete", current["cost_known"])
                    )
            if (
                not any(
                    usage[key]
                    for key in (
                        "prompt_tokens",
                        "completion_tokens",
                        "total_tokens",
                    )
                )
                and not usage["cost_known"]
                and not usage["usage_records"]
            ):
                pending.pop(role, None)
                if not pending:
                    self.pending_model_usage.pop(exploration_id, None)
                return
            item = self.repository.get_exploration(
                exploration_id, include_details=False
            )
            previous = item.get("usage", {})
            by_role = dict(previous.get("by_role") or {})
            aggregate_previous = dict(previous)
            if by_role:
                aggregate_previous["usage_records"] = sum(
                    int(bucket.get("usage_records") or 0)
                    for bucket in by_role.values()
                )
                aggregate_previous["cost_reported_records"] = sum(
                    int(bucket.get("cost_reported_records") or 0)
                    for bucket in by_role.values()
                )
                # A legacy aggregate may contain priced calls that predate
                # the per-role ledger. Retain that reported subtotal even
                # after later increments create a partial role breakdown.
                if previous.get("cost_known") and not (
                    previous.get("cost_complete") is True
                    and aggregate_previous["usage_records"] == 0
                    and not previous.get("total_tokens")
                    and not previous.get("cost")
                ):
                    aggregate_previous["cost_reported_records"] = max(
                        1, aggregate_previous["cost_reported_records"]
                    )
            aggregate = self._merge_usage_bucket(aggregate_previous, usage)
            by_role[role] = self._merge_usage_bucket(by_role.get(role) or {}, usage)
            total_tokens = aggregate["total_tokens"]
            cost_known = aggregate["cost_known"]
            cost = aggregate["cost"]
            self.repository.update_exploration(
                exploration_id,
                prompt_tokens=aggregate["prompt_tokens"],
                completion_tokens=aggregate["completion_tokens"],
                total_tokens=total_tokens,
                cost=cost if cost_known else None,
                cost_known=cost_known,
                cost_complete=aggregate["cost_complete"],
                usage_by_role=by_role,
            )
            pending.pop(role, None)
            if not pending:
                self.pending_model_usage.pop(exploration_id, None)
            panel_snapshot = (total_tokens, cost, cost_known)

        # Browser DOM I/O may be slow while a navigation is in flight. Keep it
        # outside the accounting lock so the auxiliary worker can never block
        # JEV usage persistence (or vice versa).
        if executor and panel_snapshot is not None:
            total_tokens, cost, cost_known = panel_snapshot
            with suppress(Exception):
                await executor.update_usage_panel(
                    total_tokens, cost if cost_known else None, cost_known
                )

    @staticmethod
    def _merge_usage_bucket(
        previous: dict[str, Any], current: dict[str, Any]
    ) -> dict[str, Any]:
        """Keep reported subtotals separate from missing provider billing.

        A newly initialized zero-call bucket is a proven zero, but that zero
        must not make the first response with missing cost look priced. Legacy
        aggregate prices remain valid; their missing role split is never
        reconstructed from model IDs or estimated from token counts.
        """
        previous_records = int(previous.get("usage_records", int(bool(
            previous.get("total_tokens") or previous.get("cost_known")
        ))))
        current_records = int(current.get("usage_records") or 0)
        records = previous_records + current_records
        reported = int(previous.get(
            "cost_reported_records", previous_records if previous.get("cost_known") else 0
        )) + int(current.get("cost_reported_records") or 0)
        cost_known = reported > 0 or (
            records == 0 and bool(previous.get("cost_known"))
        )
        return {
            **{
                key: int(previous.get(key) or 0) + int(current.get(key) or 0)
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            },
            "cost": (
                float(previous.get("cost") or 0.0) + float(current.get("cost") or 0.0)
            ) if cost_known else None,
            "cost_known": cost_known,
            "cost_complete": bool(previous.get("cost_complete", False))
            and bool(current.get("cost_complete", current.get("cost_known"))),
            "usage_records": records,
            "cost_reported_records": reported,
        }

    @staticmethod
    def _fingerprint(
        url: str, decision: AgentDecision, state_key: str | None = None
    ) -> str:
        raw = json.dumps(
            {
                "url": canonical_url(url),
                # Refs are only meaningful inside one semantic browser state.
                # The same e0 may legitimately identify a different control in
                # another modal/hash/React surface at the same URL.
                "state": state_key,
                "action": decision.action,
                "target": decision.target,
                "value": decision.value,
                "credential_key": decision.credential_key,
                "key": decision.key,
                "destination": decision.url,
            },
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def _next_failed_restoration_streak(
        current: int,
        *,
        is_restoration: bool,
        success: bool,
        state_changed: bool,
    ) -> int:
        """Count only consecutive restoration attempts that made no progress."""

        if is_restoration and (not success or not state_changed):
            return current + 1
        return 0

    @classmethod
    def _deterministic_decision(
        cls,
        observation: Observation,
        coverage_context: dict[str, Any],
        *,
        consulted_policy_keys: set[str],
        consecutive_no_progress: int,
    ) -> tuple[AgentDecision | None, str, str | None]:
        """Route browser mechanics locally and reserve JEV for QA judgment.

        The coverage planner has already constrained ``current_untried`` to
        safe, same-origin atomic moves.  Requiring a paid model to repeat an
        obvious click/fill/select adds latency and cost without adding QA
        judgment.  Ambiguous controls and stalled/complex states still go to
        JEV, which remains the decision-maker when interpretation is useful.
        """

        state_key = str(coverage_context.get("state_key") or "unknown")
        state_policy_key = f"state:{state_key}"
        if consecutive_no_progress >= 2:
            return None, "two_attempts_without_progress", state_policy_key

        candidates = [
            item
            for item in coverage_context.get("current_untried", [])
            if isinstance(item, dict)
            and item.get("ref")
            and item.get("suggested_action")
        ]
        if not candidates:
            return None, "no_safe_deterministic_candidate", state_policy_key

        def normalised_name(item: dict[str, Any]) -> str:
            return " ".join(
                re.findall(r"[\w]+", str(item.get("name") or "").casefold())
            )

        names: dict[str, set[str]] = {}
        for item in candidates:
            name = normalised_name(item)
            if name:
                names.setdefault(name, set()).add(str(item.get("ref")))
        duplicate_names = sorted(
            name for name, refs in names.items() if len(refs) > 1
        )
        if duplicate_names:
            duplicate_key = (
                f"duplicate:{canonical_url(observation.url)}:"
                f"{duplicate_names[0]}"
            )
            if duplicate_key not in consulted_policy_keys:
                return (
                    None,
                    "ambiguous_duplicate_controls",
                    duplicate_key,
                )

        selected = candidates[0]
        action = str(selected.get("suggested_action") or "")
        if action not in {"click", "fill", "select", "press", "hover"}:
            return None, "action_requires_jev_judgment", state_policy_key

        observed_controls = [
            element
            for element in observation.interactive_elements
            if isinstance(element, dict)
        ]
        has_conventional_form = any(
            str(element.get("tag") or "").casefold()
            in {"input", "select", "textarea"}
            and str(element.get("type") or "text").casefold()
            not in {"hidden", "password", "file"}
            for element in observed_controls
        )
        all_navigation = all(
            bool(item.get("in_navigation") or item.get("href"))
            for item in candidates
        )
        action_families = {
            str(item.get("suggested_action") or "") for item in candidates
        }
        generic_labels = {
            "action",
            "button",
            "confirm",
            "continue",
            "link",
            "more",
            "next",
            "ok",
            "submit",
        }
        selected_name = normalised_name(selected)
        if (
            selected_name in generic_labels
            and not has_conventional_form
            and not (selected.get("in_navigation") or selected.get("href"))
            and state_policy_key not in consulted_policy_keys
        ):
            return None, "ambiguous_business_control", state_policy_key
        if (
            len(action_families) > 1
            and not has_conventional_form
            and not all_navigation
            and state_policy_key not in consulted_policy_keys
        ):
            return None, "new_complex_state", state_policy_key

        label = str(
            selected.get("option_label")
            or selected.get("name")
            or selected.get("ref")
        )[:240]
        if has_conventional_form:
            local_reason = "conventional_form"
        elif selected.get("in_navigation") or selected.get("href"):
            local_reason = "known_navigation"
        elif len(candidates) == 1:
            local_reason = "single_clear_control"
        else:
            local_reason = "clear_safe_frontier"
        rationale = (
            "AutoJev's deterministic invocation policy selected the next "
            f"safe, untried {action} without a JEV call ({local_reason})."
        )
        return (
            AgentDecision(
                action=action,
                target=str(selected["ref"]),
                value=(
                    str(selected["suggested_value"])
                    if selected.get("suggested_value") is not None
                    else None
                ),
                key=(
                    str(selected["suggested_key"])
                    if selected.get("suggested_key") is not None
                    else None
                ),
                rationale=rationale,
                scenario_hint={
                    "title": f"{action.replace('_', ' ').title()}: {label}",
                    "description": rationale,
                    "preconditions": [f"The browser is on {observation.url}"],
                    "expected_result": (
                        "The control responds consistently and introduces no "
                        "attributable application failure. External browser "
                        "signals are recorded separately from functional checks."
                    ),
                },
            ),
            local_reason,
            None,
        )

    @staticmethod
    def _is_jev_model(model_id: str) -> bool:
        return model_id.removeprefix("~").startswith("typesafe/jev-")

    @staticmethod
    def _issue_timestamp(issue: dict[str, Any]) -> float | None:
        value = issue.get("occurred_at")
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return None

    @classmethod
    def _deduplicate_observation_issues(
        cls, observation: Observation
    ) -> list[dict[str, Any]]:
        """Collapse two browser channels that describe the same failed response.

        Chromium emits both a generic console line ("Failed to load resource")
        and a response event for many HTTP failures.  They are useful evidence,
        but they are not two product defects.  Pair them by status and timestamp,
        retaining the console record inside the more precise HTTP record.
        """

        return correlate_issues(
            observation.console_errors + observation.network_errors
        )

    @staticmethod
    def _issue_key(issue: dict[str, Any]) -> str:
        stable_issue = {
            "type": issue.get("type"),
            "method": issue.get("method"),
            "message": issue.get("message"),
            "url": issue.get("url"),
            "status_code": issue.get("status_code"),
        }
        return hashlib.sha256(
            json.dumps(stable_issue, sort_keys=True).encode()
        ).hexdigest()

    @staticmethod
    def _issue_page_context(
        issue: dict[str, Any], observation: Observation
    ) -> dict[str, str]:
        """Keep event-time attribution distinct from a later buffer capture."""

        recorded_page_url = issue.get("page_url")
        page_url = str(recorded_page_url or observation.url)
        page_url_source = (
            str(issue.get("page_url_source") or "observation")
            if recorded_page_url else "observation"
        )
        return {
            "page_url": page_url,
            "page_url_source": page_url_source,
            "observation_url": observation.url,
        }

    def _record_observation_issues(
        self,
        exploration_id: str,
        observation: Observation,
        known: set[str],
        *,
        action_step: int | None = None,
        action_started_at: str | None = None,
        action_finished_at: str | None = None,
        scenario_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Persist distinct occurrences; return only new signatures for prompts.

        A periodic request can fail in several CTs. Those occurrences remain
        durable, while a known signature need not repeatedly enlarge AI prompts.
        """

        new_issues: list[dict[str, Any]] = []
        for issue in self._deduplicate_observation_issues(observation):
            key = self._issue_key(issue)
            occurrence_id = signal_occurrence_id(issue, observation.captured_at)
            occurrence_key = f"occurrence:{occurrence_id}"
            if occurrence_key in known:
                continue
            first_signature = key not in known
            known.add(key)
            known.add(occurrence_key)
            record = classify_signal(
                {**issue, "occurrence_id": occurrence_id}, observation.url
            )
            association = {"kind": "observation_context"}
            if action_step is not None:
                association = self._signal_action_association(
                    issue, action_started_at, action_finished_at, action_step, scenario_id
                )
            record.update(
                screenshot=observation.screenshot,
                **self._issue_page_context(issue, observation),
                step_number=action_step,
                scenario_id=scenario_id,
                association=association,
                action_started_at=action_started_at,
                action_finished_at=action_finished_at,
                new_signature=first_signature,
            )
            # Association is temporal context, not a causal attribution. In
            # particular a signal emitted while planning must not claim to
            # reject the later CT merely because both share a screenshot.
            record = classify_signal(record, observation.url)
            if first_signature:
                new_issues.append(record)
            self.repository.add_issue(exploration_id, record)
            self.repository.add_event(
                exploration_id,
                "error" if record["affects_verdict"] else "warning",
                "issue_detected",
                str(issue.get("message", "Browser issue observed")),
                {
                    "type": issue.get("type"),
                    "status_code": issue.get("status_code"),
                    "occurrence_id": occurrence_id,
                    "classification": record["classification"],
                    "affects_verdict": record["affects_verdict"],
                    "step_number": action_step,
                    "scenario_id": scenario_id,
                },
            )
        return new_issues

    @classmethod
    def _signal_action_association(
        cls,
        issue: dict[str, Any],
        action_started_at: str | None,
        action_finished_at: str | None,
        action_step: int | None,
        scenario_id: str | None = None,
    ) -> dict[str, Any]:
        occurred = cls._issue_timestamp(issue)
        started = cls._issue_timestamp({"occurred_at": action_started_at})
        finished = cls._issue_timestamp({"occurred_at": action_finished_at})
        kind = "observation_context"
        if occurred is not None and started is not None:
            kind = "before_action" if occurred < started else "during_action"
            if finished is not None and occurred > finished:
                kind = "after_action"
        return {
            "kind": kind,
            "step_number": action_step,
            "scenario_id": scenario_id,
            "causal_link_confirmed": False,
        }

    @classmethod
    def _issues_relevant_to_action(
        cls,
        observation: Observation,
        action_started_at: str | None = None,
        action_finished_at: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return only browser issues emitted after this action began.

        Storage deduplication and action attribution are deliberately separate:
        a request that fails again during another action is still negative
        evidence, while polling/telemetry emitted while JEV was planning must
        not be assigned to the action that happened afterwards.
        """

        action_start = cls._issue_timestamp({"occurred_at": action_started_at})
        action_finish = cls._issue_timestamp({"occurred_at": action_finished_at})
        relevant: list[dict[str, Any]] = []
        for issue in cls._deduplicate_observation_issues(observation):
            occurred_at = cls._issue_timestamp(issue)
            if (
                action_start is not None
                and occurred_at is not None
                and occurred_at < action_start
            ):
                # The browser can emit polling/telemetry while JEV is planning.
                # Those records share the next observation buffer, but they
                # were not caused by the action that followed.
                continue
            if (
                action_finish is not None
                and occurred_at is not None
                and occurred_at > action_finish
            ):
                continue
            relevant.append(issue)
        return relevant

    @classmethod
    def _keep_only_new_observation_issues(
        cls,
        observation: Observation,
        new_issues: list[dict[str, Any]],
        action_started_at: str | None = None,
        action_finished_at: str | None = None,
    ) -> None:
        """Keep only new, attributable signatures in model observation context.

        The caller already persisted all occurrences and copied them into local
        CT evidence. Clearing recurring/background prompt noise never removes
        a timeline signal or changes an action's independently checked verdict.
        """

        started = cls._issue_timestamp({"occurred_at": action_started_at})
        finished = cls._issue_timestamp({"occurred_at": action_finished_at})
        prompt_issues = []
        for raw_issue in new_issues:
            issue = classify_signal(raw_issue, observation.url)
            occurred = cls._issue_timestamp(issue)
            if not issue["affects_verdict"]:
                continue
            if occurred is not None:
                if started is not None and occurred < started:
                    continue
                if finished is not None and occurred > finished:
                    continue
            prompt_issues.append(issue)

        observation.console_errors = [
            issue
            for issue in prompt_issues
            if issue.get("type") in {"console", "pageerror"}
        ]
        observation.network_errors = [
            issue for issue in prompt_issues if issue.get("type") in {"http", "network"}
        ]

    @staticmethod
    def _scenario_capture_policy(
        *,
        decision_source: str,
        is_restoration_action: bool,
        result: ActionResult,
    ) -> tuple[bool, str | None]:
        """Decide whether an executed action is meaningful test-case evidence."""

        if is_stale_undispatched_action(result):
            return False, "stale_control_not_dispatched"
        if decision_source == "coverage" or is_restoration_action:
            # Graph traversal is usually supporting navigation. But a real
            # business check or attributable negative evidence observed while
            # replaying a path is still a test result, not disposable plumbing.
            if not any(
                not assertion.passed or assertion.kind not in {"observable_state_change", "no_new_browser_errors"}
                for assertion in result.assertions
            ):
                return False, "mechanical_coverage_action"
        if (result.evidence.get("business_settle") or {}).get("status") == "api_wait_timed_out":
            return True, None
        if result.success and not result.assertions:
            return False, "successful_action_without_verifiable_evidence"
        return True, None

    @classmethod
    def _reconcile_pending_scenario(
        cls,
        pending: dict[
            str, tuple[Observation, AgentDecision, ActionResult, int]
        ],
        omitted: set[str],
        scenario_fingerprint: str,
        observation: Observation,
        decision: AgentDecision,
        result: ActionResult,
        scenario_number: int,
        *,
        decision_source: str,
        is_restoration_action: bool,
    ) -> tuple[int, str | None, bool]:
        """Merge retries into one semantic CT draft.

        Returns the updated sequence counter, the omission reason (when any),
        and whether the caller should emit the one-time no-evidence event.
        """

        capture_scenario, omission_reason = cls._scenario_capture_policy(
            decision_source=decision_source,
            is_restoration_action=is_restoration_action,
            result=result,
        )
        if capture_scenario:
            existing = pending.get(scenario_fingerprint)
            if existing is None:
                scenario_number += 1
                scenario_sequence = scenario_number
            else:
                # Refs can change when Playwright observes the page again. A
                # later verified attempt replaces the provisional verdict but
                # keeps its original position among the captured scenarios.
                scenario_sequence = existing[3]
                old_evidence = build_scenario_evidence(*existing[:3])
                new_evidence = build_scenario_evidence(observation, decision, result)
                # The latest attempt determines the verdict, but it does not
                # erase the earlier failure or the path used to recover it.
                timeline_by_id = {
                    entry["id"]: entry
                    for entry in old_evidence["timeline"] + new_evidence["timeline"]
                }
                signals_by_id = {
                    signal["occurrence_id"]: signal
                    for signal in (
                        old_evidence["technical_signals"]
                        + new_evidence["technical_signals"]
                    )
                }
                new_evidence["timeline"] = sorted(
                    timeline_by_id.values(),
                    key=lambda entry: cls._issue_timestamp(entry) or 0,
                )
                new_evidence["technical_signals"] = list(signals_by_id.values())
                result.evidence = new_evidence
            pending[scenario_fingerprint] = (
                observation,
                decision,
                result,
                scenario_sequence,
            )
            omitted.discard(scenario_fingerprint)
            return scenario_number, None, False

        should_emit_omission = False
        if omission_reason == "successful_action_without_verifiable_evidence":
            existing = pending.get(scenario_fingerprint)
            previous_result = existing[2] if existing is not None else None
            previous_message = (
                previous_result.message.lower() if previous_result is not None else ""
            )
            provisional_failure = bool(
                previous_result is not None
                and not previous_result.success
                and "policyblocked" not in previous_message
                and "unavailable" not in previous_message
            )
            if existing is None or provisional_failure:
                # A successful retry with no observable delta clears only an
                # unverified execution/locator failure. Never discard an
                # earlier PASS, assertion-backed FAIL, or policy restriction.
                pending.pop(scenario_fingerprint, None)
                should_emit_omission = scenario_fingerprint not in omitted
                omitted.add(scenario_fingerprint)
        return scenario_number, omission_reason, should_emit_omission

    def _record_screenshot(
        self,
        exploration_id: str,
        observation: Observation,
        label: str,
        scenario_id: str | None = None,
    ) -> None:
        if observation.screenshot:
            self.repository.add_screenshot(
                exploration_id, observation.screenshot, label, scenario_id
            )

    @staticmethod
    def _scenario_from_evidence(
        observation: Observation,
        decision: AgentDecision,
        result: ActionResult,
        scenario_number: int,
    ) -> TestScenario:
        status = ScenarioStatus.SKIPPED
        if is_stale_undispatched_action(result):
            # Defensive classification for direct callers. The live manager
            # keeps these attempts as automation context, not new CTs.
            status = ScenarioStatus.BLOCKED
        elif not result.success:
            status = (
                ScenarioStatus.BLOCKED
                if "PolicyBlocked" in result.message
                or "unavailable" in result.message.lower()
                else ScenarioStatus.FAIL
            )
        elif (result.evidence.get("business_settle") or {}).get("status") == "api_wait_timed_out" and not any(
            not assertion.passed for assertion in result.assertions
        ):
            status = ScenarioStatus.BLOCKED
        elif result.assertions:
            failed = [assertion for assertion in result.assertions if not assertion.passed]
            status = (
                ScenarioStatus.BLOCKED
                if failed and all(assertion.kind == "form_validation_probe_precondition" for assertion in failed)
                else ScenarioStatus.FAIL if failed else ScenarioStatus.PASS
            )
        evidence = build_scenario_evidence(observation, decision, result)
        evidence["verification_summary"] = ExplorationManager._verification_summary(evidence)
        return TestScenario(
            id=f"CT{scenario_number:03d}",
            **local_scenario_documentation(observation, decision, result),
            status=status,
            url=result.after_url,
            created_at=utc_now(),
            screenshots=scenario_screenshots(evidence, result),
            evidence=evidence,
        )

    @staticmethod
    def _merge_scenario_enrichment(
        scenario: TestScenario, observation: Observation,
        decision: AgentDecision, result: ActionResult,
    ) -> None:
        """Keep browser checks authoritative; admit only grounded visual negatives."""
        generated = scenario.evidence or {}
        native = build_scenario_evidence(observation, decision, result)
        provenance = generated.get("documentation_provenance")
        if isinstance(provenance, dict):
            native["documentation_provenance"] = dict(provenance)
        review_provenance = generated.get("visual_review_provenance")
        if isinstance(review_provenance, dict):
            native["visual_review_provenance"] = dict(review_provenance)
        else:
            review_provenance = provenance  # Immutable legacy response contract.
        probe = (decision.scenario_hint or {}).get("validation_probe") or {}
        # The attempted rule is fixed before execution. A provider may see
        # another field's earlier error, but must not relabel a blocked Postal
        # Code probe as a successful Last Name test.
        if probe.get("kind") == "required_field" or (
            isinstance(provenance, dict) and provenance.get("source") == "local_evidence"
        ):
            for key, value in local_scenario_documentation(observation, decision, result).items():
                setattr(scenario, key, value)
        status = ExplorationManager._scenario_from_evidence(observation, decision, result, 1).status
        if native.get("visual_assessment", {}).get("requested"):
            assessment = generated.get("visual_assessment") or {}
            allowed = {
                "before": (result.evidence.get("before_screenshot"), result.before_url),
                "after": (observation.screenshot, observation.url),
            }
            images = [
                image for image in assessment.get("images", [])
                if isinstance(image, dict) and image.get("ref") in allowed
                and allowed[image["ref"]][0]
                and image.get("screenshot") == Path(str(allowed[image["ref"]][0])).name
                and image.get("url") == allowed[image["ref"]][1]
            ]
            if (
                isinstance(review_provenance, dict)
                and review_provenance.get("source") in {"auxiliary_llm", "auxiliary_cache"}
                and review_provenance.get("model_input") == "text_and_image"
                and assessment.get("status") == "assessed" and images
            ):
                assessment, extra = assess_visual_findings(
                    {"findings": assessment.get("findings", [])}, images=images,
                    model=str(assessment.get("model") or ""), reference_text=observation.text,
                    before_text=str(result.evidence.get("before_text") or ""), action_succeeded=result.success,
                    interactive_elements=observation.interactive_elements,
                )
                if generated.get("visual_assessment", {}).get("reused_identical_evidence"):
                    assessment["reused_identical_evidence"] = True
                    assessment["cache_key"] = review_provenance.get("cache_key")
                native["assertions"].extend(extra)
                for assertion in extra:
                    native["timeline"].append({
                        "id": assertion["actual"]["finding_id"], "kind": "assertion", "occurred_at": utc_now(),
                        "title": "Revisão visual · indício a revisar", "message": assertion["message"],
                        "url": observation.url, "screenshot": assertion["actual"]["screenshot"],
                        "step_number": native.get("action_step"), "assertion": assertion,
                    })
                if extra and status != ScenarioStatus.BLOCKED:
                    status = ScenarioStatus.FAIL
            else:
                assessment = unavailable_visual_assessment(
                    requested=True, model=str(assessment.get("model") or ""),
                    reason=str(assessment.get("reason") or "auxiliary_visual_evidence_unavailable"),
                    # Keep only locally verified references, never arbitrary
                    # provider paths. Sent images are context, not validation.
                    images=images if isinstance(review_provenance, dict) and review_provenance.get("model_input") == "text_and_image" else [],
                )
            native["visual_assessment"] = assessment
        native["verification_summary"] = ExplorationManager._verification_summary(native)
        scenario.evidence = native
        scenario.screenshots = scenario_screenshots(native, result)
        scenario.status = status
        scenario.url = result.after_url

    @staticmethod
    def _verification_summary(evidence: dict[str, Any]) -> dict[str, Any]:
        return evidence_verification_summary(evidence)

    def _record_coverage_restrictions(self, exploration_id: str, coverage: CoveragePlanner, observation: Observation | None) -> None:
        drain = getattr(coverage, "drain_restrictions", None)
        for restriction in drain() if callable(drain) else []:
            self.repository.add_event(
                exploration_id, "warning", "coverage_restriction_recorded",
                "Restrição de cobertura registrada individualmente; não é um teste aprovado",
                {**restriction, "screenshot": observation.screenshot if observation else None,
                 "observed_at": observation.captured_at if observation else utc_now()},
            )
        drain_resolutions = getattr(coverage, "drain_restriction_resolutions", None)
        for restriction in drain_resolutions() if callable(drain_resolutions) else []:
            self.repository.add_event(
                exploration_id, "info", "coverage_restriction_resolved",
                "Controle testado posteriormente; restrição de retomada resolvida com evidência, sem alterar o resultado do CT",
                restriction,
            )
        drain_updates = getattr(coverage, "drain_restriction_updates", None)
        for restriction in drain_updates() if callable(drain_updates) else []:
            self.repository.add_event(
                exploration_id, "warning", "coverage_restriction_updated",
                "Restrição vinculada a uma pré-condição que falhou; evidência preservada, sem aprovação presumida",
                restriction,
            )

    @staticmethod
    def _append_observable_state_evidence(
        result: ActionResult,
        decision: AgentDecision,
        observation: Observation,
        *,
        state_changed: bool,
        capture_console_errors: bool,
        capture_network_errors: bool,
        new_issues: list[dict[str, Any]] | None = None,
    ) -> None:
        """Turn a verified post-action delta into deterministic assertions.

        JEV predicts useful behavior before execution, but Playwright owns the
        verdict. A visible route/DOM/control-state delta is concrete evidence
        that an interaction produced an observable result. Only attributable
        application technical failures affect the health guard; external noise
        is preserved in the CT timeline without fabricating a functional FAIL.
        """

        if decision.action in {"wait", "screenshot"}:
            return
        has_result_check = any(
            assertion.kind not in {"observable_state_change", "no_new_browser_errors"}
            for assertion in result.assertions
        )
        if result.success and state_changed and not has_result_check and not any(
            item.kind == "observable_state_change" for item in result.assertions
        ):
            result.assertions.append(
                AssertionResult(
                    kind="observable_state_change",
                    expected="The route or interactive page state changes",
                    actual="An observable state delta was captured after the action",
                    passed=True,
                    message="Observed",
                )
            )
        if not (capture_console_errors or capture_network_errors):
            return
        source_issues = (
            ExplorationManager._deduplicate_observation_issues(observation)
            if new_issues is None
            else new_issues
        )
        classified_issues = [
            classify_signal(issue, result.before_url or observation.url)
            for issue in source_issues
        ]
        verdict_issues = [
            issue for issue in classified_issues if issue["affects_verdict"]
        ]
        console_count = (
            sum(
                issue.get("type") in {"console", "pageerror"}
                for issue in verdict_issues
            )
            if capture_console_errors
            else 0
        )
        network_count = (
            sum(issue.get("type") in {"http", "network"} for issue in verdict_issues)
            if capture_network_errors
            else 0
        )
        error_count = console_count + network_count
        if not result.success and error_count == 0:
            return
        # A request that newly fails is concrete negative evidence even when
        # the visible page does not change. Conversely, a successful no-op
        # with no new error stays assertion-free and is omitted as a CT.
        if not state_changed and error_count == 0 and not has_result_check:
            return
        result.assertions.append(
            AssertionResult(
                kind="no_new_browser_errors",
                expected="0 attributable application runtime, HTTP or network failures",
                actual={
                    "console_errors": console_count,
                    "network_errors": network_count,
                },
                passed=error_count == 0,
                message=(
                    "Observed"
                    if error_count == 0
                    else (
                        "An application technical check failed during this action; "
                        "a business defect is not automatically confirmed"
                    )
                ),
            )
        )

    async def _materialize_scenarios(
        self,
        exploration_id: str,
        pending: list[tuple[Observation, AgentDecision, ActionResult, int]],
        auxiliary: OpenAIAuxiliaryScenarioGenerator,
        auxiliary_client: OpenAICompatibleClient,
        use_local_evidence: bool,
    ) -> None:
        """Persist every captured scenario, then enrich it when time allows.

        The deterministic evidence pass is intentionally first: the user sees
        all captured cases immediately and a slow auxiliary model can never
        make cases disappear. The second pass upgrades those drafts with the
        auxiliary LLM inside a bounded documentation window.
        """

        if not pending:
            return
        total = len(pending)
        self.repository.add_event(
            exploration_id,
            "info",
            "scenario_preparation_started",
            f"Preparando {total} casos de teste a partir das evidências capturadas",
            {"current": 0, "total": total, "stage": "drafts"},
        )

        # First make the complete, evidence-backed set durable. This pass is
        # local and fast, so a report remains complete even if the auxiliary
        # provider is unavailable or its documentation budget expires.
        for observation, decision, result, scenario_number in pending:
            scenario = self._scenario_from_evidence(
                observation, decision, result, scenario_number
            )
            self.repository.add_scenario(exploration_id, scenario_number, scenario)
            if result.screenshot:
                self.repository.add_screenshot(
                    exploration_id,
                    result.screenshot,
                    f"Evidence for {scenario.id}",
                    scenario.id,
                )
        self.repository.add_event(
            exploration_id,
            "info",
            "scenario_drafts_ready",
            f"{total} casos de teste já estão seguros com evidência do navegador",
            {"current": total, "total": total, "stage": "drafts"},
        )

        if use_local_evidence or self.repository.is_cancel_requested(exploration_id):
            reason = (
                "exploration_cancelled"
                if self.repository.is_cancel_requested(exploration_id)
                else "local_evidence_mode"
            )
            self.repository.add_event(
                exploration_id,
                "info",
                "scenario_postprocessing_completed",
                f"Casos finalizados: {total} com evidência validada pelo navegador",
                {
                    "current": total,
                    "total": total,
                    "enriched": 0,
                    "local_evidence": total,
                    "reason": reason,
                    "stage": "scenarios_complete",
                },
            )
            return

        documentation_deadline = time.monotonic() + min(max(len(pending) * 8, 45), 180)
        enriched = 0
        processed = 0
        completion_reason = "documentation_complete"
        self.repository.add_event(
            exploration_id,
            "info",
            "scenario_enrichment_started",
            "A LLM auxiliar começou a aprimorar os casos de teste",
            {"current": 0, "total": total, "stage": "enrichment"},
        )
        for observation, decision, result, scenario_number in pending:
            if self.repository.is_cancel_requested(exploration_id):
                completion_reason = "exploration_cancelled"
                break
            if time.monotonic() >= documentation_deadline:
                completion_reason = "documentation_time_limit"
                break

            remaining = max(documentation_deadline - time.monotonic(), 0.1)
            scenario_task = asyncio.create_task(
                auxiliary.create_scenario(
                    observation, decision, result, scenario_number
                )
            )
            cancellation_task = asyncio.create_task(
                self._wait_for_cancellation(exploration_id)
            )
            try:
                done, _ = await asyncio.wait(
                    {scenario_task, cancellation_task},
                    timeout=min(45.0, remaining),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if cancellation_task in done:
                    cancellation_task.result()
                    completion_reason = "exploration_cancelled"
                    break
                if scenario_task not in done:
                    raise TimeoutError
                scenario = scenario_task.result()
                self._merge_scenario_enrichment(scenario, observation, decision, result)
                self.repository.add_scenario(exploration_id, scenario_number, scenario)
                enriched += 1
                event_type = "scenario_enriched"
                event_level = "info"
                event_message = f"{scenario.id} aprimorado pela LLM auxiliar"
            except Exception as exc:
                completion_reason = exc.__class__.__name__
                event_type = "scenario_enrichment_skipped"
                event_level = "warning"
                event_message = (
                    f"CT{scenario_number:03d} manteve a evidência local; "
                    "a LLM auxiliar não respondeu a tempo"
                )
            finally:
                for task in (scenario_task, cancellation_task):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(
                    scenario_task, cancellation_task, return_exceptions=True
                )
            processed += 1
            await self._record_model_usage(exploration_id, [auxiliary_client], None, "auxiliary")
            self.repository.add_event(
                exploration_id,
                event_level,
                event_type,
                event_message,
                {
                    "scenario_id": f"CT{scenario_number:03d}",
                    "current": processed,
                    "total": total,
                    "enriched": enriched,
                    "local_evidence": total - enriched,
                    "stage": "enrichment",
                },
            )

        local_evidence = total - enriched
        self.repository.add_event(
            exploration_id,
            "info",
            "scenario_postprocessing_completed",
            (
                f"Casos finalizados: {enriched} aprimorados pela LLM e "
                f"{local_evidence} preservados com evidência do navegador"
            ),
            {
                "current": total,
                "total": total,
                "enriched": enriched,
                "local_evidence": local_evidence,
                "reason": completion_reason,
                "stage": "scenarios_complete",
            },
        )

    async def _wait_for_cancellation(self, exploration_id: str) -> None:
        """Wake an in-flight provider call soon after the user cancels."""

        while not self.repository.is_cancel_requested(exploration_id):
            await asyncio.sleep(0.1)

    async def _run(self, exploration_id: str) -> None:
        credentials = self.credential_vault.get(exploration_id)
        secret_values = list(credentials.values())
        executor: PlaywrightExecutor | None = None
        options: ExplorationOptions | None = None
        coverage: CoveragePlanner | None = None
        auxiliary: OpenAIAuxiliaryScenarioGenerator | None = None
        auxiliary_client: OpenAICompatibleClient | None = None
        agent_client: OpenAICompatibleClient | None = None
        scenario_pipeline: _LiveScenarioPipeline | None = None
        agent_model_id = ""
        auxiliary_model_id = ""
        pending_scenarios: dict[
            str, tuple[Observation, AgentDecision, ActionResult, int]
        ] = {}
        terminal = ExplorationStatus.FAILED
        error_message: str | None = None
        try:
            item = self.repository.get_exploration(
                exploration_id, include_details=False
            )
            options = ExplorationOptions(**item["options"])
            if item["cancel_requested"]:
                terminal = ExplorationStatus.CANCELLED
                self.repository.add_event(
                    exploration_id,
                    "warning",
                    "cancelled",
                    "Exploration was cancelled before a browser was started",
                )
                return
            started = time.monotonic()
            deadline = (
                started + options.max_duration_seconds
                if options.max_duration_seconds is not None
                else None
            )
            agent_client = (
                DeterministicDecisionClient()
                if item["agent"]["provider_id"] == LOCAL_AGENT_PROVIDER_ID
                else self._client_for(item["agent"]["provider_id"])
            )
            auxiliary_client = self._client_for(item["auxiliary_llm"]["provider_id"])
            agent_model_id = item["agent"]["model_id"]
            auxiliary_model_id = item["auxiliary_llm"]["model_id"]
            agent = OpenAIJevAgent(agent_client, agent_model_id)
            screenshot_dir = self.settings.artifact_dir / exploration_id / "screenshots"
            auxiliary = OpenAIAuxiliaryScenarioGenerator(
                auxiliary_client,
                auxiliary_model_id,
                screenshot_dir,
                options.auxiliary_evidence_mode,
            )
            executor = PlaywrightExecutor(
                screenshot_dir,
                options,
                headless=self.settings.headless,
                browser_name=self.settings.browser,
                secret_values=secret_values,
            )
            self.repository.update_exploration(
                exploration_id, status=ExplorationStatus.RUNNING, started_at=utc_now()
            )
            self.repository.add_event(
                exploration_id, "info", "started", "Exploration started"
            )
            self.repository.add_event(
                exploration_id,
                "info",
                "auxiliary_evidence_mode",
                (
                    "Auxiliary LLM will receive structured text and screenshots"
                    if options.auxiliary_evidence_mode == "visual"
                    else "Auxiliary LLM will receive structured text only"
                ),
                {
                    "mode": options.auxiliary_evidence_mode,
                    "screenshots_saved_locally": options.capture_screenshots,
                    "screenshots_sent_to_model": (
                        options.auxiliary_evidence_mode == "visual"
                    ),
                },
            )
            observation = await asyncio.wait_for(
                executor.start(item["app_url"]),
                timeout=self._operation_timeout(deadline),
            )
            self.repository.update_exploration(
                exploration_id, current_url=observation.url
            )
            # saucedemo.com -> www.saucedemo.com: plan coverage on the origin
            # the browser actually serves, or every link reads as off-origin.
            entry_url = getattr(executor, "entry_url", None) or item["app_url"]
            self._record_screenshot(exploration_id, observation, "Initial page")
            self.repository.add_event(
                exploration_id,
                "info",
                "browser_launched",
                "Playwright opened the application",
                {"url": observation.url},
            )
            await executor.show_live_panel()
            await executor.update_live_panel(
                "Exploração iniciada", f"Navegador aberto em {observation.url[:80]}"
            )
            if options.generate_test_cases:
                scenario_pipeline = _LiveScenarioPipeline(
                    self,
                    exploration_id,
                    auxiliary,
                    auxiliary_client,
                    use_local_evidence=self._is_jev_model(auxiliary_model_id),
                    executor=executor,
                    options=options,
                    secret_values=secret_values,
                )
                await scenario_pipeline.start()
            history: list[dict[str, Any]] = []
            action_counts: dict[str, int] = {}
            known_issues: set[str] = set()
            omitted_scenario_fingerprints: set[str] = set()
            scenario_number = 0
            consecutive_model_errors = 0
            login_retried = False
            login_feedback_message = ""
            automatic_login_attempted = False
            # A site can end the session mid-run (saucedemo: 10 minutes). Only
            # after a confirmed automatic login may it be repeated, bounded,
            # and never after a rejection.
            last_automatic_login_succeeded = False
            session_relogins = 0
            authentication_boundary_crossed = False
            actions_executed = 0
            consecutive_loop_rejections = 0
            consecutive_failed_restorations = 0
            consecutive_no_progress = 0
            consulted_policy_keys: set[str] = set()
            planning_attempts = 0
            max_planning_attempts = (
                max(options.max_steps * 6, 30)
                if options.max_steps is not None
                else None
            )
            # Teto de teimosia: após N rejeições seguidas sem nenhuma ação
            # nova, encerra (o resto é fumaça do modelo, não exploração).
            MAX_CONSECUTIVE_LOOP_REJECTIONS = 3
            coverage = CoveragePlanner(entry_url, options.focus_area)
            coverage.observe(observation)
            self.repository.add_event(
                exploration_id,
                "info",
                "coverage_scope",
                (
                    f"Focused exploratory scope: {options.focus_area}"
                    if options.focus_area
                    else "Exploratory scope: entire same-origin application"
                ),
                {"focus_area": options.focus_area},
            )
            self.repository.add_event(
                exploration_id,
                "info",
                "jev_invocation_policy",
                "JEV is reserved for ambiguity, complex states and stalled progress",
                {
                    "local_actions": [
                        "clear_control",
                        "conventional_form",
                        "known_navigation",
                        "known_restoration",
                    ],
                    "jev_escalations": [
                        "ambiguous_controls",
                        "new_complex_state",
                        "unexpected_no_progress",
                        "no_safe_local_candidate",
                    ],
                },
            )
            if not self._needs_login(observation):
                self._record_observation_issues(
                    exploration_id, observation, known_issues
                )

            # With no explicit safety budget, coverage decides when the run is
            # complete. The loop still has deterministic repeat protection and
            # per-operation timeouts, but no arbitrary whole-run ceiling.
            while options.max_steps is None or actions_executed < options.max_steps:
                if self.repository.is_cancel_requested(exploration_id):
                    terminal = ExplorationStatus.CANCELLED
                    self.repository.add_event(
                        exploration_id, "warning", "cancelled", "Cancellation requested"
                    )
                    break
                if not executor.is_active():
                    raise BrowserUnavailableError(
                        "Browser window was closed by the user"
                    )
                if deadline is not None and time.monotonic() >= deadline:
                    terminal = ExplorationStatus.PARTIAL
                    self.repository.add_event(
                        exploration_id,
                        "warning",
                        "coverage_partial",
                        "Maximum exploration duration reached; coverage is partial",
                        {"reason": "duration_limit", **coverage.context(observation)},
                    )
                    break
                if self._needs_login(observation):
                    # After a Logout control the sign-in page enters the
                    # coverage graph; mark it so no later backtrack "restores"
                    # it and asks the user to log in yet again.
                    coverage.mark_entry_gate(observation)
                    panel_message = None
                    session_relogin = bool(
                        automatic_login_attempted
                        and last_automatic_login_succeeded
                        and authentication_boundary_crossed
                        and session_relogins < self.MAX_SESSION_RELOGINS
                    )
                    if credentials and (not automatic_login_attempted or session_relogin):
                        # Claim the attempt before awaiting: rejection, timeout
                        # and later returns to login must never cause resubmits.
                        automatic_login_attempted = True
                        last_automatic_login_succeeded = False
                        if session_relogin:
                            session_relogins += 1
                            self.repository.add_event(
                                exploration_id, "info", "automatic_relogin_started",
                                "O site encerrou a sessão; entrando de novo automaticamente pelo Playwright, sem enviar credenciais à IA",
                                {"attempt": session_relogins, "max_attempts": self.MAX_SESSION_RELOGINS},
                            )
                        else:
                            self.repository.add_event(
                                exploration_id, "info", "automatic_login_started",
                                "Tentando login automaticamente pelo Playwright, sem enviar credenciais à IA",
                            )
                        await executor.update_live_panel("Autenticando", "Tentativa local de login · sem chamadas à IA")
                        try:
                            login = await asyncio.wait_for(
                                (executor.attempt_session_relogin if session_relogin
                                 else executor.attempt_login)(credentials, observation),
                                timeout=min(25, self._operation_timeout(deadline)),
                            )
                        except asyncio.TimeoutError:
                            login = None
                        if login and login.status == "success" and login.observation:
                            last_automatic_login_succeeded = True
                            observation = login.observation
                            self._record_screenshot(exploration_id, observation, "After automatic login")
                            self.repository.update_exploration(exploration_id, current_url=observation.url)
                            self.repository.add_event(
                                exploration_id, "info", "automatic_login_succeeded",
                                "Login automático confirmado; iniciando a exploração autenticada",
                            )
                        else:
                            reason = login.reason if login else "login_not_confirmed"
                            panel_message = self._automatic_login_message(reason)
                            self.repository.add_event(
                                exploration_id, "warning", "automatic_login_manual_required",
                                panel_message, {"reason": reason},
                            )
                    elif credentials:
                        panel_message = self._automatic_login_message("already_attempted")
                    if self.repository.is_cancel_requested(exploration_id):
                        terminal = ExplorationStatus.CANCELLED
                        break
                    if login_retried:
                        panel_message = (
                            (login_feedback_message or "A entrada ainda não foi confirmada. Confira a resposta do site e ajuste os campos.") + " "
                            + self.LOGIN_PANEL_MESSAGE
                        )
                    if self._needs_login(observation):
                        outcome = await self._wait_for_user_input(
                            exploration_id,
                            observation,
                            executor,
                            panel_message,
                            deadline,
                        )
                        if outcome == "cancelled" or self.repository.is_cancel_requested(
                            exploration_id
                        ):
                            terminal = ExplorationStatus.CANCELLED
                            break
                        if outcome == "browser_closed":
                            raise BrowserUnavailableError(
                                "Browser window was closed by the user"
                            )
                        if outcome == "timeout":
                            terminal = ExplorationStatus.PARTIAL
                            self.repository.add_event(
                                exploration_id,
                                "warning",
                                "coverage_partial",
                                "Maximum exploration duration reached while waiting for login; coverage is partial",
                                {
                                    "reason": "duration_limit",
                                    **coverage.context(observation),
                                },
                            )
                            break
                        observation = await asyncio.wait_for(
                            executor.observe("after-user-input"),
                            timeout=self._operation_timeout(deadline),
                        )
                        if outcome == "manual" and self._needs_login(observation):
                            # One explicit Continue click authorizes one local
                            # submission of the user's already-filled form. It
                            # never authorizes a background credential retry.
                            await executor.update_live_panel("Entrando no site", "Tentativa solicitada por você · sem chamadas à IA")
                            self.repository.add_event(exploration_id, "info", "confirmed_login_started",
                                                      "Tentando entrar com os campos preenchidos após confirmação do usuário")
                            try:
                                login = await asyncio.wait_for(executor.attempt_filled_login(observation),
                                    timeout=min(25, self._operation_timeout(deadline)))
                            except asyncio.TimeoutError:
                                login = LoginAttempt("manual_required", "login_not_confirmed")
                            feedback = self._filled_login_feedback(login)
                            login_feedback_message = feedback["reason_label"]
                            self.repository.add_event(exploration_id, "info" if feedback["status"] == "success" else "warning",
                                                      "confirmed_login_result", feedback["reason_label"], feedback)
                            observation = await asyncio.wait_for(executor.observe("after-confirmed-login"),
                                timeout=self._operation_timeout(deadline))
                        if self.repository.is_cancel_requested(exploration_id):
                            terminal = ExplorationStatus.CANCELLED
                            break
                        if deadline is not None and time.monotonic() >= deadline:
                            terminal = ExplorationStatus.PARTIAL
                            self.repository.add_event(exploration_id, "warning", "coverage_partial",
                                "Maximum exploration duration reached while confirming login; coverage is partial",
                                {"reason": "duration_limit", **coverage.context(observation)})
                            break
                        self._record_screenshot(exploration_id, observation, "After login")
                        self.repository.update_exploration(
                            exploration_id, current_url=observation.url
                        )
                        if self._needs_login(observation):
                            # A failed explicit submission returns to the gate;
                            # no automatic retry or original action follows it.
                            login_retried = True
                            self.repository.add_event(
                                exploration_id,
                                "warning",
                                "login_not_completed",
                                "Login page still detected after user confirmation. Waiting for login again.",
                                {"url": observation.url},
                            )
                            await executor.show_intervention_panel(
                                (feedback["reason_label"] + " " if outcome == "manual" else "")
                                + self.LOGIN_PANEL_MESSAGE
                            )
                            continue
                    login_retried = False
                    login_feedback_message = ""
                    executor.protect_authenticated_history()
                    if not authentication_boundary_crossed:
                        # The login document is an entry gate, not the parent
                        # of every authenticated screen. Start coverage at the
                        # first authenticated observation so logical
                        # backtracking can never target the sign-in page.
                        # Retain the supplied document entry URL. Login may
                        # reach a SPA route via pushState; moving the logical
                        # root there must not silently certify a GET deep link.
                        coverage = CoveragePlanner(entry_url, options.focus_area)
                        coverage.observe(observation)
                        authentication_boundary_crossed = True
                        self.repository.add_event(
                            exploration_id,
                            "info",
                            "authenticated_scope_started",
                            "Coverage root moved to the authenticated application",
                            {"url": observation.url},
                        )
                    self._record_observation_issues(
                        exploration_id, observation, known_issues
                    )
                # Coverage owns the mechanical definition of "nothing safe is
                # left to visit". Asking the model for one more decision here
                # used to let it request go_back from the authenticated root
                # forever, even though the frontier was already exhausted.
                if coverage.can_finish():
                    self._record_coverage_restrictions(exploration_id, coverage, observation)
                    terminal = ExplorationStatus.COMPLETED
                    if coverage.coverage_complete():
                        self.repository.add_event(
                            exploration_id,
                            "info",
                            "coverage_complete",
                            "Coverage frontier exhausted; exploration complete",
                            coverage.context(observation),
                        )
                    else:
                        self.repository.add_event(
                            exploration_id,
                            "warning",
                            "coverage_complete_with_restrictions",
                            "Reachable coverage exhausted; blocked controls were documented as restrictions",
                            {
                                "reason": "blocked_candidates",
                                **coverage.context(observation),
                            },
                        )
                    break
                # Replaying a known transition recipe is graph traversal, not
                # a new QA decision. Execute it directly so a long recovery
                # cannot spend one full JEV prompt (or grow its history) for
                # every mechanical browser step.
                mechanical_restore = coverage.next_restoration_action(observation)
                self._record_coverage_restrictions(exploration_id, coverage, observation)
                decision_source = "coverage" if mechanical_restore else "jev"
                if mechanical_restore is not None:
                    decision, coverage_override = mechanical_restore
                else:
                    coverage_state = coverage.context(observation)
                    (
                        local_decision,
                        routing_reason,
                        consultation_key,
                    ) = self._deterministic_decision(
                        observation,
                        coverage_state,
                        consulted_policy_keys=consulted_policy_keys,
                        consecutive_no_progress=consecutive_no_progress,
                    )
                    if local_decision is not None:
                        decision_source = "local_policy"
                        decision = local_decision
                        decision, coverage_override = coverage.reconcile(
                            observation, decision
                        )
                        self.repository.add_event(
                            exploration_id,
                            "info",
                            "decision_routed_locally",
                            "Playwright received an unambiguous action without calling JEV",
                            {
                                "reason": routing_reason,
                                "action": decision.action,
                                "target": decision.target,
                                "state_key": coverage_state.get("state_key"),
                            },
                        )
                    else:
                        decision_source = "jev"
                        if consultation_key:
                            consulted_policy_keys.add(consultation_key)
                        # A JEV consultation is the escalation itself. Give
                        # the diagnosed state a fresh bounded chance instead
                        # of escalating every following control forever.
                        consecutive_no_progress = 0
                        self.repository.add_event(
                            exploration_id,
                            "info",
                            "jev_consulted",
                            "JEV was called because the local policy required QA judgment",
                            {
                                "reason": routing_reason,
                                "state_key": coverage_state.get("state_key"),
                                "candidate_count": len(
                                    coverage_state.get("current_untried", [])
                                ),
                            },
                        )
                    # The browser can be closed between two observations.  Do
                    # this health check immediately before the paid model call
                    # so a closed window never burns another JEV request.
                    if local_decision is None and not executor.is_active():
                        raise BrowserUnavailableError(
                            "Browser window was closed by the user"
                        )
                    if local_decision is None:
                        planning_attempts += 1
                        if (
                            max_planning_attempts is not None
                            and planning_attempts > max_planning_attempts
                        ):
                            terminal = ExplorationStatus.PARTIAL
                            self.repository.add_event(
                                exploration_id,
                                "warning",
                                "coverage_partial",
                                "Exploration planning limit reached before the coverage frontier was exhausted",
                                coverage.context(observation),
                            )
                            break
                        try:
                            decision = await self._await_decision_while_browser_active(
                                executor,
                                agent.decide(
                                    observation,
                                    # Coverage context contains the durable graph;
                                    # recent functional decisions are enough for
                                    # the model and keep token use bounded.
                                    history[-20:],
                                    sorted(credentials.keys()),
                                    coverage_state,
                                ),
                                self._operation_timeout(deadline),
                            )
                            consecutive_model_errors = 0
                        except ModelResponseError as exc:
                            await self._record_model_usage(
                                exploration_id, [agent_client], executor, "jev"
                            )
                            consecutive_model_errors += 1
                            self.repository.add_event(
                                exploration_id,
                                "warning",
                                "invalid_agent_decision",
                                str(exc),
                            )
                            if consecutive_model_errors >= 3:
                                raise RuntimeError(
                                    "JEV returned three invalid decisions in sequence"
                                ) from exc
                            continue
                        await self._record_model_usage(
                            exploration_id, [agent_client], executor, "jev"
                        )
                        decision, coverage_override = coverage.reconcile(
                            observation, decision
                        )
                if coverage_override:
                    self.repository.add_event(
                        exploration_id,
                        "info",
                        (
                            "coverage_action_planned"
                            if decision_source == "coverage"
                            else "coverage_decision_overridden"
                        ),
                        (
                            "Coverage planner continued a deterministic recovery path"
                            if decision_source == "coverage"
                            else "Coverage planner replaced the model decision to continue systematic exploration"
                        ),
                        {
                            "reason": coverage_override,
                            "source": decision_source,
                            "action": decision.action,
                            "target": decision.target,
                            "url": decision.url,
                        },
                    )
                is_restoration_action = decision.action == "go_back" or bool(
                    coverage_override
                    and (
                        "restore" in coverage_override
                        or "backtrack" in coverage_override
                        or coverage_override == "focus_boundary_return"
                    )
                )
                # A decision emitted directly by the coverage graph is
                # traversal/recovery machinery even when its concrete action
                # is click or navigate. It must not enter JEV history, loop
                # accounting, or the user-facing test-case inventory.
                is_mechanical_action = (
                    decision_source == "coverage" or is_restoration_action
                )
                if decision.action == "finish":
                    self._record_coverage_restrictions(exploration_id, coverage, observation)
                    if not coverage.can_finish():
                        state = coverage.context(observation)
                        history.append(
                            {
                                "action": "finish",
                                "target": None,
                                "result": "rejected: coverage frontier or backtracking path is not exhausted",
                                "coverage_state": state,
                            }
                        )
                        self.repository.add_event(
                            exploration_id,
                            "warning",
                            "finish_rejected",
                            "Premature finish rejected; coverage frontier is not exhausted",
                            state,
                        )
                        await executor.update_live_panel(
                            self._step_label(actions_executed + 1, options.max_steps),
                            "JEV tentou encerrar cedo — retomando a cobertura.",
                        )
                        continue
                    if options.focus_area and not coverage.focus_reached:
                        terminal = ExplorationStatus.PARTIAL
                        self.repository.add_event(
                            exploration_id,
                            "warning",
                            "coverage_scope_not_found",
                            "The requested screen/menu scope was not found among reachable safe routes",
                            coverage.context(observation),
                        )
                        break
                    if not coverage.coverage_complete():
                        # The reachable frontier is exhausted. Controls that
                        # were unsafe or consistently non-executable remain as
                        # documented restrictions, not as unvisited frontier.
                        terminal = ExplorationStatus.COMPLETED
                        self.repository.add_event(
                            exploration_id,
                            "warning",
                            "coverage_complete_with_restrictions",
                            "Reachable coverage exhausted; blocked controls were documented as restrictions",
                            {
                                "reason": "blocked_candidates",
                                "rationale": decision.rationale,
                                **coverage.context(observation),
                            },
                        )
                        break
                    terminal = ExplorationStatus.COMPLETED
                    self.repository.add_event(
                        exploration_id,
                        "info",
                        "coverage_complete",
                        "Coverage frontier exhausted; exploration complete",
                        {
                            "rationale": decision.rationale,
                            **coverage.context(observation),
                        },
                    )
                    break
                coverage_state_key = coverage.state_key(observation)
                fingerprint = self._fingerprint(
                    observation.url,
                    decision,
                    coverage_state_key,
                )
                semantic_decision_key = coverage.decision_key(observation, decision)
                validation_probe = (decision.scenario_hint or {}).get("validation_probe") or {}
                if validation_probe:
                    # All three submit probes can target the same button and
                    # route. Preserve distinct required-field expectations.
                    semantic_decision_key = json.dumps({
                        "move": semantic_decision_key,
                        "probe_kind": validation_probe.get("kind"),
                        "probe_field": validation_probe.get("field_identity"),
                        "probe_expected": validation_probe.get("expected"),
                    }, sort_keys=True)
                scenario_fingerprint = (
                    hashlib.sha256(
                        f"{coverage_state_key}:{semantic_decision_key}".encode()
                    ).hexdigest()
                    if semantic_decision_key
                    else fingerprint
                )
                # Bounded planner probes/replays have their own retry budget.
                # Three different required-field probes may submit the same
                # button; they must not consume a later JEV happy-path retry.
                if not is_mechanical_action:
                    action_counts[fingerprint] = action_counts.get(fingerprint, 0) + 1
                if not is_mechanical_action and action_counts[fingerprint] > 2:
                    consecutive_loop_rejections += 1
                    # Direciona o modelo para elementos ainda não tentados,
                    # em vez de só dizer "não" e desistir em seguida.
                    hint = (
                        f"rejected by loop protection: '{decision.action} on "
                        f"{decision.target}' was already tried "
                        f"{action_counts[fingerprint]}x on this page. You MUST "
                        "choose a DIFFERENT element."
                    )
                    untried = coverage.context(observation)["current_untried"][:6]
                    if untried:
                        hint += " Untried semantic options include: " + ", ".join(
                            str(item.get("name") or item.get("ref")) for item in untried
                        )
                    history.append(
                        {
                            "action": decision.action,
                            "target": decision.target,
                            "result": hint,
                        }
                    )
                    self.repository.add_event(
                        exploration_id,
                        "warning",
                        "loop_prevented",
                        "Repeated action rejected",
                        {
                            "action": decision.action,
                            "target": decision.target,
                            "attempt": action_counts[fingerprint],
                        },
                    )
                    if consecutive_loop_rejections >= MAX_CONSECUTIVE_LOOP_REJECTIONS:
                        if options.max_steps is None:
                            terminal = ExplorationStatus.FAILED
                            error_message = (
                                "O JEV não conseguiu avançar após várias decisões "
                                "repetidas. A execução foi interrompida para evitar um loop."
                            )
                            self.repository.add_event(
                                exploration_id,
                                "error",
                                "coverage_stalled",
                                error_message,
                                coverage.context(observation),
                            )
                        else:
                            terminal = ExplorationStatus.PARTIAL
                            self.repository.add_event(
                                exploration_id,
                                "warning",
                                "coverage_partial",
                                "JEV kept repeating tried actions; coverage is partial",
                                coverage.context(observation),
                            )
                        break
                    continue
                self.repository.add_event(
                    exploration_id,
                    "info",
                    "action_started",
                    f"Executing {decision.action}",
                    {
                        "step": actions_executed + 1,
                        "action": decision.action,
                        "target": decision.target,
                        "rationale": decision.rationale,
                    },
                )
                next_action = actions_executed + 1
                await executor.update_live_panel(
                    self._step_label(next_action, options.max_steps),
                    f"Executando · {decision.action}"
                    f"{' ' + str(decision.target) if decision.target else ''}",
                )
                # Capture preconditions before dispatch can mutate the page.
                # Private replay metadata is persisted only on the action,
                # never passed to auxiliary models or copied into CT evidence.
                rerun_recipe = build_rerun_recipe(
                    observation, decision, entry_url, secret_values=secret_values,
                )
                result, next_observation = await asyncio.wait_for(
                    executor.execute(decision, credentials),
                    timeout=self._operation_timeout(deadline),
                )
                before_state_key = coverage.effect_key(observation)
                after_state_key = coverage.effect_key(next_observation)
                state_changed = before_state_key != after_state_key
                result.evidence["action_effect"] = {"state_changed": state_changed}
                result.assertions.extend(
                    check_business_outcomes(observation, decision, result, next_observation)
                )
                if options.auxiliary_evidence_mode == "visual":
                    result.evidence["visual_assessment"] = {
                        "requested": True,
                        "status": "pending",
                        "reason": "awaiting_auxiliary_assessment",
                        "limitations": [
                            "A análise visual ainda não foi concluída; não é uma aprovação visual."
                        ],
                    }
                if (
                    decision.action == "go_back"
                    and result.success
                    and not state_changed
                ):
                    result.success = False
                    result.message = (
                        "Playwright attempted go_back, but no route or interactive-state "
                        "change was observed"
                    )
                elif (
                    decision.action in {"click", "hover"}
                    and result.success
                    and not state_changed
                ):
                    result.message = f"{result.message}; no new route or interactive-state change was observed"
                action_issues = self._issues_relevant_to_action(
                    next_observation, result.started_at, result.finished_at
                )
                self._append_observable_state_evidence(
                    result,
                    decision,
                    next_observation,
                    state_changed=state_changed,
                    capture_console_errors=options.capture_console_errors,
                    capture_network_errors=options.capture_network_errors,
                    new_issues=action_issues,
                )
                capture_scenario, _ = self._scenario_capture_policy(
                    decision_source=decision_source,
                    is_restoration_action=is_restoration_action,
                    result=result,
                )
                existing_scenario = pending_scenarios.get(scenario_fingerprint)
                candidate_sequence = (
                    existing_scenario[3] if existing_scenario else scenario_number + 1
                )
                candidate_scenario_id = (
                    f"CT{candidate_sequence:03d}"
                    if options.generate_test_cases and capture_scenario
                    else None
                )
                new_observation_issues = self._record_observation_issues(
                    exploration_id,
                    next_observation,
                    known_issues,
                    action_step=next_action,
                    action_started_at=result.started_at,
                    action_finished_at=result.finished_at,
                    scenario_id=candidate_scenario_id,
                )
                new_occurrence_ids = {
                    issue["occurrence_id"] for issue in new_observation_issues
                }
                local_signals = []
                for issue in self._deduplicate_observation_issues(next_observation):
                    signal = classify_signal(
                        {
                            **issue,
                            "occurrence_id": signal_occurrence_id(
                                issue, next_observation.captured_at
                            ),
                        },
                        result.before_url or next_observation.url,
                    )
                    signal.update(
                        screenshot=next_observation.screenshot,
                        **self._issue_page_context(issue, next_observation),
                        step_number=next_action,
                        scenario_id=candidate_scenario_id,
                        association=self._signal_action_association(
                            issue,
                            result.started_at,
                            result.finished_at,
                            next_action,
                            candidate_scenario_id,
                        ),
                        new_signature=signal["occurrence_id"] in new_occurrence_ids,
                    )
                    signal = classify_signal(
                        signal, result.before_url or next_observation.url
                    )
                    local_signals.append(signal)
                result.evidence.update(
                    action_step=next_action,
                    before_screenshot=observation.screenshot,
                    before_observed_at=observation.captured_at,
                    before_text=observation.text[:3500] if options.auxiliary_evidence_mode == "visual" else "",
                    technical_signals=local_signals,
                )
                result.evidence = build_scenario_evidence(
                    next_observation, decision, result
                )
                # The precise records are already durable in the issue table.
                # Keep only newly observed signatures on the observation used
                # by the CT generator and by the next JEV prompt, so periodic
                # third-party telemetry cannot be attributed repeatedly.
                self._keep_only_new_observation_issues(
                    next_observation,
                    new_observation_issues,
                    result.started_at,
                    result.finished_at,
                )
                safe_decision = redact(asdict(decision), secret_values)
                safe_result = redact(result.to_dict(), secret_values)
                # The builder has already rejected secret/credential values.
                # Redacting hashes with short credentials would corrupt them.
                safe_result.setdefault("evidence", {})["rerun_recipe"] = rerun_recipe
                self.repository.add_action(
                    exploration_id, next_action, safe_decision, safe_result
                )
                actions_executed += 1
                coverage.record_action(observation, decision, result, next_observation)
                self._record_coverage_restrictions(exploration_id, coverage, next_observation)
                verified_outcome = any(
                    assertion.kind not in {"observable_state_change", "no_new_browser_errors"}
                    for assertion in result.assertions
                )
                if result.success and (state_changed or verified_outcome):
                    consecutive_no_progress = 0
                elif not is_mechanical_action:
                    consecutive_no_progress += 1
                coverage_after = coverage.context(next_observation)
                consecutive_failed_restorations = self._next_failed_restoration_streak(
                    consecutive_failed_restorations,
                    is_restoration=is_mechanical_action,
                    success=result.success,
                    state_changed=state_changed,
                )
                consecutive_loop_rejections = 0
                self.repository.update_exploration(
                    exploration_id,
                    current_url=next_observation.url,
                    steps_completed=actions_executed,
                )
                self._record_screenshot(
                    exploration_id, next_observation, decision.action
                )
                self.repository.add_event(
                    exploration_id,
                    "warning" if is_stale_undispatched_action(result)
                    else "info" if result.success and all(a.passed for a in result.assertions) else "error",
                    "action_completed",
                    (
                        "Referência alterada antes da ação: nenhum comando foi executado. "
                        "A página foi observada novamente; a retomada tem tentativa limitada."
                        if is_stale_undispatched_action(result) else
                        "Verificação de resultado falhou; o CT será reprovado e a exploração continuará. " + result.message
                        if result.success and any(not a.passed for a in result.assertions)
                        else result.message if result.success
                        else (
                            "Falha registrada nesta opção; a exploração seguirá "
                            f"pelas demais. Detalhe: {result.message}"
                        )
                    ),
                    {
                        "step": actions_executed,
                        "action": decision.action,
                        "success": result.success,
                        "url": result.after_url,
                        "state_changed": state_changed,
                        "assertions_passed": all(a.passed for a in result.assertions),
                        "exploration_continues": not result.success,
                        "failure_policy": (
                            "retry_then_continue_frontier"
                            if not result.success
                            else None
                        ),
                        "action_dispatch": result.evidence.get("action_dispatch"),
                        "coverage": {
                            "known_states": coverage_after["known_states"],
                            "frontier_states": coverage_after["frontier_states"],
                            "frontier_candidates": coverage_after[
                                "frontier_candidates"
                            ],
                            "candidates_attempted": coverage_after[
                                "candidates_attempted"
                            ],
                        },
                    },
                )
                await executor.update_live_panel(
                    self._step_label(actions_executed, options.max_steps),
                    f"{'REFERÊNCIA ATUALIZADA — RETOMANDO' if is_stale_undispatched_action(result) else 'OK' if result.success and all(a.passed for a in result.assertions) else 'FALHA REGISTRADA — CONTINUANDO'} · {decision.action}"
                    f"{' ' + str(decision.target) if decision.target else ''} · "
                    f"{(result.after_url or '')[:80]}",
                )
                if not is_mechanical_action:
                    history.append(
                        {
                            "action": decision.action,
                            "target": decision.target,
                            "rationale": decision.rationale,
                            "success": result.success,
                            "result": result.message,
                            "before_url": result.before_url,
                            "after_url": result.after_url,
                            "state_changed": state_changed,
                            "next_page_title": next_observation.title,
                            "new_console_errors": len(next_observation.console_errors),
                            "new_network_errors": len(next_observation.network_errors),
                            "coverage_after": {
                                "known_states": coverage_after["known_states"],
                                "frontier_states": coverage_after["frontier_states"],
                                "frontier_candidates": coverage_after[
                                    "frontier_candidates"
                                ],
                                "path_depth": coverage_after["path_depth"],
                            },
                            "assertions": [
                                asdict(value) for value in result.assertions
                            ],
                        }
                    )
                # Preserve evidence now and document it after navigation. The
                # local CT is durable immediately. Its auxiliary documentation
                # runs in a bounded worker and never blocks browser coverage.
                if options.generate_test_cases:
                    previous_scenario = pending_scenarios.get(
                        scenario_fingerprint
                    )
                    (
                        scenario_number,
                        omission_reason,
                        should_emit_omission,
                    ) = self._reconcile_pending_scenario(
                        pending_scenarios,
                        omitted_scenario_fingerprints,
                        scenario_fingerprint,
                        next_observation,
                        decision,
                        result,
                        scenario_number,
                        decision_source=decision_source,
                        is_restoration_action=is_restoration_action,
                    )
                    current_scenario = pending_scenarios.get(
                        scenario_fingerprint
                    )
                    if (
                        scenario_pipeline is not None
                        and current_scenario is not None
                        and current_scenario is not previous_scenario
                    ):
                        await scenario_pipeline.capture(
                            scenario_fingerprint,
                            current_scenario[0],
                            current_scenario[1],
                            current_scenario[2],
                            current_scenario[3],
                        )
                    elif (
                        scenario_pipeline is not None
                        and previous_scenario is not None
                        and current_scenario is None
                    ):
                        await scenario_pipeline.remove(
                            scenario_fingerprint,
                            previous_scenario[3],
                        )
                    if should_emit_omission:
                        # Emit one explanation per semantic no-op, rather than
                        # flooding the timeline if the model repeats it.
                        self.repository.add_event(
                            exploration_id,
                            "info",
                            "scenario_omitted_no_evidence",
                            (
                                "A ação foi executada, mas não produziu uma "
                                "mudança ou asserção verificável; nenhum CT foi criado."
                            ),
                            {
                                "step": actions_executed,
                                "action": decision.action,
                                "target": decision.target,
                                "url": result.after_url,
                                "reason": omission_reason,
                            },
                        )
                observation = next_observation
                if consecutive_failed_restorations >= 2:
                    settlement = coverage.abandon_stalled_restoration(
                        reason="consecutive_restore_without_progress"
                    )
                    self._record_coverage_restrictions(exploration_id, coverage, observation)
                    if settlement is not None:
                        self.repository.add_event(
                            exploration_id,
                            "warning",
                            "coverage_route_unreachable",
                            (
                                "Uma combinação antiga de tela não pôde ser "
                                "recriada. O AutoJev registrou a restrição e "
                                "continuou pela próxima opção de teste."
                            ),
                            {
                                **settlement,
                                "action": decision.action,
                                "target": decision.target,
                            },
                        )
                        consecutive_failed_restorations = 0
                        continue

                    # Backtracking has its own bounded fallback in the coverage
                    # planner. Let the next iteration replace the failed path;
                    # if no path can be settled, the ordinary non-restoration
                    # loop protection remains the final safety net.
                    self.repository.add_event(
                        exploration_id,
                        "warning",
                        "coverage_backtrack_retry",
                        (
                            "O retorno não alterou a tela; o planejador vai "
                            "descartar esse caminho e tentar a próxima fronteira."
                        ),
                        {
                            "action": decision.action,
                            "target": decision.target,
                            **coverage.context(observation),
                        },
                    )
                    consecutive_failed_restorations = 0
            else:
                terminal = ExplorationStatus.PARTIAL
                self.repository.add_event(
                    exploration_id,
                    "warning",
                    "limit_reached",
                    "Maximum number of actions reached; coverage is partial",
                    {"reason": "action_limit", **coverage.context(observation)},
                )
        except asyncio.CancelledError:
            terminal = ExplorationStatus.CANCELLED
            error_message = "Backend shutdown interrupted this exploration"
            self.repository.add_event(
                exploration_id,
                "warning",
                "cancelled",
                error_message,
                {"reason": "backend_shutdown"},
            )
        except BrowserUnavailableError as exc:
            terminal = ExplorationStatus.CANCELLED
            error_message = None
            # Launch and navigation failures raise the same error as a closed
            # window; keep the cause so they can be told apart.
            self.repository.add_event(
                exploration_id,
                "warning",
                "browser_closed",
                (
                    "A janela do navegador foi fechada. A exploração foi "
                    "encerrada sem novas chamadas ao JEV e os resultados "
                    "capturados foram preservados."
                ),
                {"detail": str(exc)[:500]},
            )
            self.repository.add_event(
                exploration_id,
                "warning",
                "cancelled",
                "Exploração cancelada porque a janela do navegador foi fechada",
                {"reason": "browser_closed"},
            )
        except asyncio.TimeoutError:
            if options and options.max_duration_seconds is None:
                terminal = ExplorationStatus.FAILED
                error_message = (
                    "Uma operação do navegador ou do modelo excedeu 120 segundos. "
                    "A execução foi interrompida por bloqueio técnico, não por limite de cobertura."
                )
                self.repository.add_event(
                    exploration_id,
                    "error",
                    "coverage_stalled",
                    error_message,
                    coverage.context(observation) if coverage else {},
                )
            else:
                terminal = ExplorationStatus.PARTIAL
                self.repository.add_event(
                    exploration_id,
                    "warning",
                    "coverage_partial",
                    "Maximum exploration duration reached; coverage is partial",
                    {
                        "reason": "duration_limit",
                        **(coverage.context(observation) if coverage else {}),
                    },
                )
        except Exception as exc:
            terminal = ExplorationStatus.FAILED
            error_message = str(redact(str(exc), secret_values))[:2000]
            self.repository.add_event(
                exploration_id,
                "error",
                "failed",
                error_message,
            )
        finally:
            # Persist responses that arrived just before cancellation, or
            # uncertain billing left by a failed JEV transport. Draining is
            # idempotent and shares the same lock as the auxiliary worker.
            if agent_client is not None:
                with suppress(Exception):
                    await self._record_model_usage(
                        exploration_id, [agent_client], None, "jev"
                    )
            if coverage is not None:
                with suppress(Exception):
                    self._record_coverage_restrictions(exploration_id, coverage, locals().get("observation"))
            if executor:
                with suppress(Exception):
                    await executor.update_live_panel(
                        "Exploração concluída",
                        f"Status: {terminal.value} — veja o relatório no AutoJev.",
                    )
                await executor.close()
                executor = None
            self.input_events.pop(exploration_id, None)
            self.credential_vault.discard(exploration_id)
            finished = utc_now()
            needs_postprocessing = bool(
                (scenario_pipeline and scenario_pipeline.has_work)
                or (options and options.generate_html_report)
            )
            persisted_status = (
                ExplorationStatus.POSTPROCESSING if needs_postprocessing else terminal
            )
            # Browser work has ended. Keep a distinct live phase so polling,
            # cancellation and token/cost updates continue until the report is
            # actually available, without pretending the browser is running.
            self.repository.update_exploration(
                exploration_id,
                status=persisted_status,
                finished_at=finished,
                error=error_message,
            )
            if scenario_pipeline is not None:
                try:
                    await scenario_pipeline.finish(
                        cancelled=(
                            terminal
                            in {
                                ExplorationStatus.CANCELLED,
                                ExplorationStatus.FAILED,
                            }
                            or self.repository.is_cancel_requested(exploration_id)
                        )
                    )
                except Exception as scenario_error:
                    self.repository.add_event(
                        exploration_id,
                        "error",
                        "scenario_postprocessing_failed",
                        str(redact(str(scenario_error), secret_values))[:1000],
                    )
            def apply_postprocessing_cancellation() -> bool:
                nonlocal terminal
                if (
                    persisted_status != ExplorationStatus.POSTPROCESSING
                    or not self.repository.is_cancel_requested(exploration_id)
                    or terminal == ExplorationStatus.CANCELLED
                ):
                    return False
                terminal = ExplorationStatus.CANCELLED
                self.repository.add_event(
                    exploration_id,
                    "warning",
                    "cancelled",
                    "Pós-processamento cancelado pelo usuário; os resultados já capturados foram preservados",
                    {"reason": "postprocessing_cancelled"},
                )
                return True

            apply_postprocessing_cancellation()
            if options and options.generate_html_report:
                try:
                    self.repository.add_event(
                        exploration_id,
                        "info",
                        "report_generation_started",
                        "Casos de teste prontos; montando o relatório final",
                        {"current": 0, "total": 1, "stage": "report"},
                    )
                    exploration = self.repository.get_exploration(exploration_id)
                    # Render the durable terminal outcome rather than freezing
                    # the transient "postprocessing" phase into the report.
                    exploration["status"] = terminal.value
                    report_path = (
                        self.settings.artifact_dir / exploration_id / "report.html"
                    )
                    generated = await asyncio.to_thread(
                        self.report_renderer.generate,
                        exploration,
                        str(report_path),
                    )
                    if apply_postprocessing_cancellation():
                        # Cancellation can arrive while the report is being
                        # rendered in a worker thread. Regenerate the small
                        # standalone artifact once so its terminal label agrees
                        # with the run stored by the API.
                        exploration = self.repository.get_exploration(
                            exploration_id
                        )
                        exploration["status"] = terminal.value
                        generated = await asyncio.to_thread(
                            self.report_renderer.generate,
                            exploration,
                            str(report_path),
                        )
                    self.repository.update_exploration(
                        exploration_id, report_path=generated
                    )
                    self.repository.add_event(
                        exploration_id,
                        "info",
                        "report_ready",
                        "Relatório final pronto para abrir",
                        {"current": 1, "total": 1, "stage": "report"},
                    )
                except Exception as report_error:
                    self.repository.add_event(
                        exploration_id,
                        "error",
                        "report_failed",
                        str(redact(str(report_error), secret_values))[:1000],
                    )
            apply_postprocessing_cancellation()
            if persisted_status == ExplorationStatus.POSTPROCESSING:
                self.repository.update_exploration(
                    exploration_id,
                    status=terminal,
                    finished_at=utc_now(),
                    error=error_message,
                )
                # A cancellation request can be committed in the narrow gap
                # between the check above and this terminal update. Reconcile
                # once after the write so an accepted post-processing cancel
                # can never leave the run marked as completed.
                if apply_postprocessing_cancellation():
                    if options and options.generate_html_report:
                        try:
                            exploration = self.repository.get_exploration(
                                exploration_id
                            )
                            exploration["status"] = terminal.value
                            report_path = (
                                self.settings.artifact_dir
                                / exploration_id
                                / "report.html"
                            )
                            generated = await asyncio.to_thread(
                                self.report_renderer.generate,
                                exploration,
                                str(report_path),
                            )
                            self.repository.update_exploration(
                                exploration_id, report_path=generated
                            )
                        except Exception as report_error:
                            self.repository.add_event(
                                exploration_id,
                                "error",
                                "report_failed",
                                str(
                                    redact(str(report_error), secret_values)
                                )[:1000],
                            )
                    self.repository.update_exploration(
                        exploration_id,
                        status=terminal,
                        finished_at=utc_now(),
                        error=error_message,
                    )
            self.usage_locks.pop(exploration_id, None)
