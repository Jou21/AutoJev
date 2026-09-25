from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import asdict
from typing import Any

from autojev.adapters.openai_compatible import (
    ModelResponseError,
    OpenAIAuxiliaryScenarioGenerator,
    OpenAICompatibleClient,
    OpenAIJevAgent,
)
from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.adapters.report import StandaloneHtmlReport
from autojev.adapters.repository import SQLiteRepository
from autojev.adapters.secrets import EphemeralCredentialVault, redact
from autojev.config import Settings
from autojev.domain.models import (
    AgentDecision,
    ExplorationOptions,
    ExplorationStatus,
    Observation,
    utc_now,
)


class ExplorationManager:
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
        self.semaphore = asyncio.Semaphore(max_concurrent)

    def submit(self, exploration_id: str) -> None:
        task = asyncio.create_task(self._guarded_run(exploration_id))
        self.tasks[exploration_id] = task
        task.add_done_callback(lambda _: self.tasks.pop(exploration_id, None))

    async def shutdown(self) -> None:
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _guarded_run(self, exploration_id: str) -> None:
        async with self.semaphore:
            await self._run(exploration_id)

    def _client_for(self, provider_id: str) -> OpenAICompatibleClient:
        provider = self.repository.get_provider(provider_id)
        if not provider.enabled:
            raise RuntimeError(f"Provider {provider.name} is disabled")
        return OpenAICompatibleClient(
            provider.base_url,
            self.repository.get_provider_secret(provider_id),
            self.settings.http_timeout_seconds,
        )

    @staticmethod
    def _fingerprint(url: str, decision: AgentDecision) -> str:
        raw = json.dumps(
            {
                "url": url.split("#", 1)[0],
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

    def _record_observation_issues(
        self,
        exploration_id: str,
        observation: Observation,
        known: set[str],
    ) -> None:
        for issue in observation.console_errors + observation.network_errors:
            stable_issue = {
                "type": issue.get("type"),
                "message": issue.get("message"),
                "url": issue.get("url"),
                "status_code": issue.get("status_code"),
            }
            key = hashlib.sha256(
                json.dumps(stable_issue, sort_keys=True).encode()
            ).hexdigest()
            if key in known:
                continue
            known.add(key)
            record = {**issue, "screenshot": observation.screenshot}
            self.repository.add_issue(exploration_id, record)
            self.repository.add_event(
                exploration_id,
                "error",
                "issue_detected",
                str(issue.get("message", "Browser issue observed")),
                {"type": issue.get("type"), "status_code": issue.get("status_code")},
            )

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

    async def _run(self, exploration_id: str) -> None:
        credentials = self.credential_vault.get(exploration_id)
        secret_values = list(credentials.values())
        executor: PlaywrightExecutor | None = None
        options: ExplorationOptions | None = None
        terminal = ExplorationStatus.FAILED
        error_message: str | None = None
        try:
            item = self.repository.get_exploration(exploration_id, include_details=False)
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
            deadline = started + options.max_duration_seconds
            agent_client = self._client_for(item["agent"]["provider_id"])
            auxiliary_client = self._client_for(item["auxiliary_llm"]["provider_id"])
            agent = OpenAIJevAgent(agent_client, item["agent"]["model_id"])
            screenshot_dir = self.settings.artifact_dir / exploration_id / "screenshots"
            auxiliary = OpenAIAuxiliaryScenarioGenerator(
                auxiliary_client,
                item["auxiliary_llm"]["model_id"],
                screenshot_dir,
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
            observation = await asyncio.wait_for(
                executor.start(item["app_url"]),
                timeout=max(deadline - time.monotonic(), 0.1),
            )
            self.repository.update_exploration(exploration_id, current_url=observation.url)
            self._record_screenshot(exploration_id, observation, "Initial page")
            self.repository.add_event(
                exploration_id,
                "info",
                "browser_launched",
                "Playwright opened the application",
                {"url": observation.url},
            )
            history: list[dict[str, Any]] = []
            action_counts: dict[str, int] = {}
            url_visits: dict[str, int] = {}
            known_issues: set[str] = set()
            scenario_fingerprints: set[str] = set()
            scenario_number = 0
            consecutive_model_errors = 0
            last_canonical_url: str | None = None
            self._record_observation_issues(exploration_id, observation, known_issues)

            for step in range(1, options.max_steps + 1):
                if self.repository.is_cancel_requested(exploration_id):
                    terminal = ExplorationStatus.CANCELLED
                    self.repository.add_event(
                        exploration_id, "warning", "cancelled", "Cancellation requested"
                    )
                    break
                if time.monotonic() - started >= options.max_duration_seconds:
                    terminal = ExplorationStatus.COMPLETED
                    self.repository.add_event(
                        exploration_id,
                        "warning",
                        "limit_reached",
                        "Maximum exploration duration reached",
                    )
                    break
                canonical_url = observation.url.split("#", 1)[0]
                if canonical_url != last_canonical_url:
                    url_visits[canonical_url] = url_visits.get(canonical_url, 0) + 1
                    last_canonical_url = canonical_url
                if url_visits[canonical_url] > 8:
                    terminal = ExplorationStatus.COMPLETED
                    self.repository.add_event(
                        exploration_id,
                        "warning",
                        "loop_prevented",
                        "Page visit limit reached; exploration stopped safely",
                    )
                    break
                try:
                    decision = await asyncio.wait_for(
                        agent.decide(
                            observation, history, sorted(credentials.keys())
                        ),
                        timeout=max(deadline - time.monotonic(), 0.1),
                    )
                    consecutive_model_errors = 0
                except ModelResponseError as exc:
                    consecutive_model_errors += 1
                    self.repository.add_event(
                        exploration_id,
                        "warning",
                        "invalid_agent_decision",
                        str(exc),
                    )
                    if consecutive_model_errors >= 3:
                        raise RuntimeError("JEV returned three invalid decisions in sequence") from exc
                    continue
                if decision.action == "finish":
                    terminal = ExplorationStatus.COMPLETED
                    self.repository.add_event(
                        exploration_id,
                        "info",
                        "agent_finished",
                        "JEV concluded that exploration was complete",
                        {"rationale": decision.rationale},
                    )
                    break
                fingerprint = self._fingerprint(observation.url, decision)
                action_counts[fingerprint] = action_counts.get(fingerprint, 0) + 1
                if action_counts[fingerprint] > 2:
                    history.append(
                        {
                            "action": decision.action,
                            "target": decision.target,
                            "result": "rejected by loop protection",
                        }
                    )
                    self.repository.add_event(
                        exploration_id,
                        "warning",
                        "loop_prevented",
                        "Repeated action rejected",
                        {"action": decision.action, "target": decision.target},
                    )
                    if action_counts[fingerprint] > 4:
                        terminal = ExplorationStatus.COMPLETED
                        break
                    continue
                self.repository.add_event(
                    exploration_id,
                    "info",
                    "action_started",
                    f"Executing {decision.action}",
                    {
                        "step": step,
                        "action": decision.action,
                        "target": decision.target,
                    },
                )
                result, next_observation = await asyncio.wait_for(
                    executor.execute(decision, credentials),
                    timeout=max(deadline - time.monotonic(), 0.1),
                )
                safe_decision = redact(asdict(decision), secret_values)
                safe_result = redact(result.to_dict(), secret_values)
                self.repository.add_action(
                    exploration_id, step, safe_decision, safe_result
                )
                self.repository.update_exploration(
                    exploration_id,
                    current_url=next_observation.url,
                    steps_completed=step,
                )
                self._record_screenshot(exploration_id, next_observation, decision.action)
                self._record_observation_issues(
                    exploration_id, next_observation, known_issues
                )
                self.repository.add_event(
                    exploration_id,
                    "info" if result.success else "error",
                    "action_completed",
                    result.message,
                    {
                        "step": step,
                        "action": decision.action,
                        "success": result.success,
                        "url": result.after_url,
                    },
                )
                history.append(
                    {
                        "action": decision.action,
                        "target": decision.target,
                        "success": result.success,
                        "before_url": result.before_url,
                        "after_url": result.after_url,
                        "assertions": [asdict(value) for value in result.assertions],
                    }
                )
                if options.generate_test_cases and (
                    decision.scenario_hint or decision.assertions or not result.success
                ) and fingerprint not in scenario_fingerprints:
                    scenario_fingerprints.add(fingerprint)
                    scenario_number += 1
                    scenario = await asyncio.wait_for(
                        auxiliary.create_scenario(
                            next_observation, decision, result, scenario_number
                        ),
                        timeout=max(deadline - time.monotonic(), 0.1),
                    )
                    self.repository.add_scenario(
                        exploration_id, scenario_number, scenario
                    )
                    if result.screenshot:
                        # Associate the existing file with the scenario as a separate metadata row.
                        self.repository.add_screenshot(
                            exploration_id,
                            result.screenshot,
                            f"Evidence for {scenario.id}",
                            scenario.id,
                        )
                    self.repository.add_event(
                        exploration_id,
                        "info",
                        "scenario_created",
                        f"{scenario.id} — {scenario.title}",
                        {"scenario_id": scenario.id, "status": scenario.status},
                    )
                observation = next_observation
            else:
                terminal = ExplorationStatus.COMPLETED
                self.repository.add_event(
                    exploration_id,
                    "warning",
                    "limit_reached",
                    "Maximum number of steps reached",
                )
        except asyncio.CancelledError:
            terminal = ExplorationStatus.CANCELLED
            error_message = "Backend shutdown interrupted this exploration"
        except asyncio.TimeoutError:
            terminal = ExplorationStatus.COMPLETED
            self.repository.add_event(
                exploration_id,
                "warning",
                "limit_reached",
                "Maximum exploration duration reached",
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
                await executor.close()
            self.credential_vault.discard(exploration_id)
            finished = utc_now()
            self.repository.update_exploration(
                exploration_id,
                status=terminal,
                finished_at=finished,
                error=error_message,
            )
            if options and options.generate_html_report:
                try:
                    exploration = self.repository.get_exploration(exploration_id)
                    report_path = (
                        self.settings.artifact_dir / exploration_id / "report.html"
                    )
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
                        "Standalone HTML report generated",
                    )
                except Exception as report_error:
                    self.repository.add_event(
                        exploration_id,
                        "error",
                        "report_failed",
                        str(redact(str(report_error), secret_values))[:1000],
                    )
