from __future__ import annotations

from typing import Any, Protocol

from .models import ActionResult, AgentDecision, Observation, TestScenario


class AgentPort(Protocol):
    async def decide(
        self,
        observation: Observation,
        history: list[dict[str, Any]],
        credential_keys: list[str],
        coverage_context: dict[str, Any] | None = None,
    ) -> AgentDecision: ...


class AuxiliaryLLMPort(Protocol):
    async def create_scenario(
        self,
        observation: Observation,
        decision: AgentDecision,
        result: ActionResult,
        scenario_number: int,
    ) -> TestScenario: ...


class BrowserExecutorPort(Protocol):
    async def start(self, url: str) -> Observation: ...

    async def execute(
        self, decision: AgentDecision, credentials: dict[str, str]
    ) -> tuple[ActionResult, Observation]: ...

    async def close(self) -> None: ...


class ReportPort(Protocol):
    def generate(self, exploration: dict[str, Any], output_path: str) -> str: ...
