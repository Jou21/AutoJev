from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Awaitable
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from autojev.adapters.openai_compatible import (
    ModelResponseError,
    OpenAIAuxiliaryScenarioGenerator,
    OpenAICompatibleClient,
    OpenAIJevAgent,
)
from autojev.adapters.playwright_executor import (
    BrowserUnavailableError,
    PlaywrightExecutor,
)
from autojev.adapters.report import StandaloneHtmlReport
from autojev.adapters.repository import SQLiteRepository
from autojev.adapters.secrets import EphemeralCredentialVault, redact
from autojev.config import Settings
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
from autojev.services.coverage import CoveragePlanner, canonical_url
from autojev.services.technical_signals import (
    build_scenario_evidence,
    classify_signal,
    correlate_issues,
    scenario_screenshots,
    signal_occurrence_id,
)


@dataclass(slots=True)
class _ScenarioEnrichmentJob:
    fingerprint: str
    revision: int
    observation: Observation
    decision: AgentDecision
    result: ActionResult
    sequence: int


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
    ) -> None:
        self.manager = manager
        self.repository = manager.repository
        self.exploration_id = exploration_id
        self.auxiliary = auxiliary
        self.auxiliary_client = auxiliary_client
        self.use_local_evidence = use_local_evidence
        self.executor = executor
        self.queue: asyncio.Queue[_ScenarioEnrichmentJob | None] = asyncio.Queue(
            maxsize=self._QUEUE_LIMIT
        )
        self.revisions: dict[str, int] = {}
        self.sequences: dict[str, int] = {}
        self.enriched: set[str] = set()
        self.skipped: set[str] = set()
        self.worker: asyncio.Task[None] | None = None
        self.started = False
        self.closed = False
        self._usage_is_drained = True

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
                "current": 0,
                "total": 0,
                "enriched": 0,
                "local_evidence": 0,
                "stage": "parallel_enrichment",
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

        if self.closed:
            return
        revision = self.revisions.get(fingerprint, 0) + 1
        existed = fingerprint in self.sequences
        self.revisions[fingerprint] = revision
        self.sequences[fingerprint] = sequence
        self.enriched.discard(fingerprint)
        self.skipped.discard(fingerprint)

        scenario = self.manager._scenario_from_evidence(
            observation, decision, result, sequence
        )
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
                "scenario_id": scenario.id,
                "current": self.total,
                "total": self.total,
                "enriched": len(self.enriched),
                "local_evidence": self.total - len(self.enriched),
                "stage": "parallel_enrichment",
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
                    "scenario_id": scenario.id,
                    "current": len(self.enriched) + len(self.skipped),
                    "total": self.total,
                    "enriched": len(self.enriched),
                    "local_evidence": self.total - len(self.enriched),
                    "stage": "parallel_enrichment",
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
        )
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
                    "scenario_id": scenario.id,
                    "current": len(self.enriched) + len(self.skipped),
                    "total": self.total,
                    "enriched": len(self.enriched),
                    "local_evidence": self.total - len(self.enriched),
                    "stage": "parallel_enrichment",
                },
            )

    async def remove(self, fingerprint: str, sequence: int) -> None:
        """Invalidate an unverified draft recovered by a later no-op retry."""

        if fingerprint not in self.sequences:
            return
        self.revisions[fingerprint] = self.revisions.get(fingerprint, 0) + 1
        stored_sequence = self.sequences.pop(fingerprint, sequence)
        self.enriched.discard(fingerprint)
        self.skipped.discard(fingerprint)
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
                "scenario_id": scenario_id,
                "current": self.total,
                "total": self.total,
                "enriched": len(self.enriched),
                "local_evidence": self.total - len(self.enriched),
                "stage": "parallel_enrichment",
            },
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
                        return

                    scenario_task = asyncio.create_task(
                        self.auxiliary.create_scenario(
                            job.observation,
                            job.decision,
                            job.result,
                            job.sequence,
                        )
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
                            return
                        if scenario_task not in done:
                            raise TimeoutError
                        scenario = scenario_task.result()
                        if self.revisions.get(job.fingerprint) != job.revision:
                            continue
                        if job.fingerprint not in self.sequences:
                            continue
                        # The model owns wording only. Preserve all local
                        # attempts, screenshots, assertions and signal IDs even
                        # if an adapter returns a sparse documentation payload.
                        local_evidence = build_scenario_evidence(
                            job.observation, job.decision, job.result
                        )
                        provenance = scenario.evidence.get("documentation_provenance")
                        scenario.evidence.update(local_evidence)
                        if provenance is not None:
                            scenario.evidence["documentation_provenance"] = provenance
                        scenario.screenshots = scenario_screenshots(
                            local_evidence, job.result
                        )
                        self.repository.add_scenario(
                            self.exploration_id, job.sequence, scenario
                        )
                        provenance = scenario.evidence.get(
                            "documentation_provenance", {}
                        )
                        if provenance.get("source", "auxiliary_llm") != "auxiliary_llm":
                            self._mark_skipped(
                                job,
                                str(
                                    provenance.get("reason")
                                    or "auxiliary_local_fallback"
                                ),
                            )
                            continue
                        self.enriched.add(job.fingerprint)
                        self.skipped.discard(job.fingerprint)
                        self.repository.add_event(
                            self.exploration_id,
                            "info",
                            "scenario_enriched",
                            f"{scenario.id} aprimorado pela LLM auxiliar em paralelo",
                            {
                                "scenario_id": scenario.id,
                                "current": len(self.enriched) + len(self.skipped),
                                "total": self.total,
                                "enriched": len(self.enriched),
                                "local_evidence": self.total - len(self.enriched),
                                "stage": "parallel_enrichment",
                            },
                        )
                    except Exception as exc:
                        self._mark_skipped(job, exc.__class__.__name__)
                    finally:
                        for task in (scenario_task, cancellation_task):
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(
                            scenario_task, cancellation_task, return_exceptions=True
                        )
                        # Accounting failures must not kill the sole worker.
                        # finish() performs one final idempotent drain as well.
                        with suppress(Exception):
                            await self.manager._record_model_usage(
                                self.exploration_id,
                                [self.auxiliary_client],
                                self.executor,
                            )
                            self._usage_is_drained = True
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Repository/cancellation checks can fail outside the
                    # provider block. Preserve the local CT and keep consuming
                    # later jobs rather than silently killing the queue.
                    if job is not None:
                        self._mark_skipped(job, exc.__class__.__name__)
            finally:
                self.queue.task_done()

    def _mark_skipped(self, job: _ScenarioEnrichmentJob, reason: str) -> None:
        if self.revisions.get(job.fingerprint) != job.revision:
            return
        self.skipped.add(job.fingerprint)
        with suppress(Exception):
            self.repository.add_event(
                self.exploration_id,
                "warning",
                "scenario_enrichment_skipped",
                (
                    f"CT{job.sequence:03d} manteve a evidência local; "
                    "a LLM auxiliar não concluiu o aprimoramento"
                ),
                {
                    "scenario_id": f"CT{job.sequence:03d}",
                    "current": len(self.enriched) + len(self.skipped),
                    "total": self.total,
                    "enriched": len(self.enriched),
                    "local_evidence": self.total - len(self.enriched),
                    "stage": "parallel_enrichment",
                    "reason": reason,
                },
            )

    def _discard_queued_jobs(self, reason: str) -> None:
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
                    self.worker.cancel()
                elif self.worker.done():
                    completion_reason = "auxiliary_worker_stopped"
                else:
                    completion_reason = "documentation_complete"
                    remaining = self.queue.qsize() + 1
                    timeout = min(max(remaining * 8, 15), 180)
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
                "current": self.total,
                "total": self.total,
                "enriched": len(self.enriched),
                "local_evidence": self.total - len(self.enriched),
                "reason": completion_reason,
                "stage": "scenarios_complete",
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

    LOGIN_PANEL_MESSAGE = (
        "1) Preencha usuário e senha na página. "
        "2) Clique no botão Login do próprio site e aguarde entrar. "
        "3) Só então clique em Continuar exploração abaixo."
    )

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
    ) -> None:
        # JEV and the auxiliary documentation worker intentionally run in
        # parallel. Serialize only their tiny accounting section so concurrent
        # read-modify-write updates cannot lose tokens or cost.
        lock = self.usage_locks.setdefault(exploration_id, asyncio.Lock())
        panel_snapshot: tuple[int, float, bool] | None = None
        async with lock:
            usage = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost": 0.0,
                "cost_known": False,
            }
            for client in clients:
                current = client.drain_usage()
                for key in (
                    "prompt_tokens",
                    "completion_tokens",
                    "total_tokens",
                    "cost",
                ):
                    usage[key] += current[key]
                usage["cost_known"] = (
                    usage["cost_known"] or current["cost_known"]
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
            ):
                return
            item = self.repository.get_exploration(
                exploration_id, include_details=False
            )
            previous = item.get("usage", {})
            total_tokens = int(previous.get("total_tokens") or 0) + int(
                usage["total_tokens"]
            )
            cost_known = bool(previous.get("cost_known")) or bool(
                usage["cost_known"]
            )
            cost = float(previous.get("cost") or 0.0) + float(usage["cost"])
            self.repository.update_exploration(
                exploration_id,
                prompt_tokens=int(previous.get("prompt_tokens") or 0)
                + int(usage["prompt_tokens"]),
                completion_tokens=int(previous.get("completion_tokens") or 0)
                + int(usage["completion_tokens"]),
                total_tokens=total_tokens,
                cost=cost if cost_known else None,
                cost_known=cost_known,
            )
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
                page_url=observation.url,
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

        if decision_source == "coverage" or is_restoration_action:
            return False, "mechanical_coverage_action"
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
        if not result.success:
            status = (
                ScenarioStatus.BLOCKED
                if "PolicyBlocked" in result.message
                or "unavailable" in result.message.lower()
                else ScenarioStatus.FAIL
            )
        elif result.assertions:
            status = (
                ScenarioStatus.PASS
                if all(assertion.passed for assertion in result.assertions)
                else ScenarioStatus.FAIL
            )
        hint = decision.scenario_hint or {}
        evidence = build_scenario_evidence(observation, decision, result)
        return TestScenario(
            id=f"CT{scenario_number:03d}",
            title=str(
                hint.get("title")
                or f"Explore {decision.action} on {observation.title or observation.url}"
            )[:300],
            description=str(hint.get("description") or decision.rationale)[:3000],
            preconditions=[
                str(item)[:500] for item in hint.get("preconditions", []) if item
            ][:20],
            steps=[f"Execute {decision.action} on the observed page"],
            expected_result=str(
                hint.get("expected_result")
                or "The declared assertion should be observed."
            )[:3000],
            actual_result=result.message or f"Browser ended at {result.after_url}",
            status=status,
            url=result.after_url,
            created_at=utc_now(),
            screenshots=scenario_screenshots(evidence, result),
            evidence=evidence,
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

        if not result.success or decision.action in {"wait", "screenshot"}:
            return
        if state_changed and not any(
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
        # A request that newly fails is concrete negative evidence even when
        # the visible page does not change. Conversely, a successful no-op
        # with no new error stays assertion-free and is omitted as a CT.
        if not state_changed and error_count == 0:
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
                provenance = scenario.evidence.get("documentation_provenance")
                local_evidence = build_scenario_evidence(observation, decision, result)
                scenario.evidence.update(local_evidence)
                if provenance is not None:
                    scenario.evidence["documentation_provenance"] = provenance
                scenario.screenshots = scenario_screenshots(local_evidence, result)
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
            await self._record_model_usage(exploration_id, [auxiliary_client], None)
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
            agent_client = self._client_for(item["agent"]["provider_id"])
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
                )
                await scenario_pipeline.start()
            history: list[dict[str, Any]] = []
            action_counts: dict[str, int] = {}
            known_issues: set[str] = set()
            omitted_scenario_fingerprints: set[str] = set()
            scenario_number = 0
            consecutive_model_errors = 0
            login_retried = False
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
            coverage = CoveragePlanner(item["app_url"], options.focus_area)
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
                    panel_message = None
                    if login_retried:
                        panel_message = (
                            "Login ainda não detectado! Você preencheu mas "
                            "não clicou no Login do site? Clique no botão Login "
                            "da página e aguarde entrar — ao entrar, a exploração "
                            "retoma sozinha."
                        )
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
                    self._record_screenshot(exploration_id, observation, "After login")
                    self.repository.update_exploration(
                        exploration_id, current_url=observation.url
                    )
                    if self._needs_login(observation):
                        # Usuário clicou "continuar" sem concluir o login:
                        # volta a aguardar em vez de deixar o JEV dar
                        # "finish" com 0 ações e relatório vazio.
                        # O painel mostra aviso explícito para não parecer
                        # que "nada acontece".
                        login_retried = True
                        self.repository.add_event(
                            exploration_id,
                            "warning",
                            "login_not_completed",
                            "Login page still detected after user confirmation. Waiting for login again.",
                            {"url": observation.url},
                        )
                        await executor.show_intervention_panel(
                            "Login ainda não detectado! Clique no botão Login "
                            "do próprio site, aguarde a próxima página carregar "
                            "e só então clique em Continuar exploração."
                        )
                        continue
                    login_retried = False
                    executor.protect_authenticated_history()
                    if not authentication_boundary_crossed:
                        # The login document is an entry gate, not the parent
                        # of every authenticated screen. Start coverage at the
                        # first authenticated observation so logical
                        # backtracking can never target the sign-in page.
                        coverage = CoveragePlanner(observation.url, options.focus_area)
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
                                exploration_id, [agent_client], executor
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
                            exploration_id, [agent_client], executor
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
                scenario_fingerprint = (
                    hashlib.sha256(
                        f"{coverage_state_key}:{semantic_decision_key}".encode()
                    ).hexdigest()
                    if semantic_decision_key
                    else fingerprint
                )
                action_counts[fingerprint] = action_counts.get(fingerprint, 0) + 1
                if action_counts[fingerprint] > 2 and not is_mechanical_action:
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
                result, next_observation = await asyncio.wait_for(
                    executor.execute(decision, credentials),
                    timeout=self._operation_timeout(deadline),
                )
                before_state_key = coverage.effect_key(observation)
                after_state_key = coverage.effect_key(next_observation)
                state_changed = before_state_key != after_state_key
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
                        page_url=next_observation.url,
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
                self.repository.add_action(
                    exploration_id, next_action, safe_decision, safe_result
                )
                actions_executed += 1
                coverage.record_action(observation, decision, result, next_observation)
                if result.success and state_changed:
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
                    "info" if result.success else "error",
                    "action_completed",
                    (
                        result.message
                        if result.success
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
                        "exploration_continues": not result.success,
                        "failure_policy": (
                            "retry_then_continue_frontier"
                            if not result.success
                            else None
                        ),
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
                    f"{'OK' if result.success else 'FALHA REGISTRADA — CONTINUANDO'} · {decision.action}"
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
        except BrowserUnavailableError:
            terminal = ExplorationStatus.CANCELLED
            error_message = None
            self.repository.add_event(
                exploration_id,
                "warning",
                "browser_closed",
                (
                    "A janela do navegador foi fechada. A exploração foi "
                    "encerrada sem novas chamadas ao JEV e os resultados "
                    "capturados foram preservados."
                ),
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
