from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ExplorationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    POSTPROCESSING = "postprocessing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScenarioStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"


@dataclass(slots=True)
class ProviderModel:
    id: str
    label: str
    enabled: bool = True


@dataclass(slots=True)
class Provider:
    id: str
    name: str
    base_url: str
    enabled: bool
    has_api_key: bool
    models: list[ProviderModel] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class ModelSelection:
    provider_id: str
    model_id: str


@dataclass(slots=True)
class ExplorationOptions:
    mode: str = "full"
    # Optional human-readable scope (screen, menu or submenu).  When omitted the
    # coverage planner treats the whole same-origin application as its scope.
    focus_area: str | None = None
    # None means coverage-driven execution: the run stops when every reachable
    # safe candidate in the selected scope has been exhausted, not at an
    # arbitrary action/time ceiling.
    max_steps: int | None = None
    max_duration_seconds: int | None = None
    capture_screenshots: bool = True
    capture_console_errors: bool = True
    capture_network_errors: bool = True
    generate_test_cases: bool = True
    generate_html_report: bool = True
    viewport_width: int = 1440
    viewport_height: int = 900


@dataclass(slots=True)
class Observation:
    url: str
    title: str
    text: str
    interactive_elements: list[dict[str, Any]]
    console_errors: list[dict[str, Any]] = field(default_factory=list)
    network_errors: list[dict[str, Any]] = field(default_factory=list)
    screenshot: str | None = None
    captured_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class AgentDecision:
    action: str
    rationale: str
    target: str | None = None
    value: str | None = None
    credential_key: str | None = None
    key: str | None = None
    url: str | None = None
    assertions: list[dict[str, Any]] = field(default_factory=list)
    scenario_hint: dict[str, Any] | None = None


@dataclass(slots=True)
class AssertionResult:
    kind: str
    expected: Any
    actual: Any
    passed: bool
    message: str


@dataclass(slots=True)
class ActionResult:
    action: str
    success: bool
    started_at: str
    finished_at: str
    before_url: str
    after_url: str
    target: str | None = None
    value: str | None = None
    message: str = ""
    assertions: list[AssertionResult] = field(default_factory=list)
    screenshot: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TestScenario:
    id: str
    title: str
    description: str
    preconditions: list[str]
    steps: list[str]
    expected_result: str
    actual_result: str
    status: ScenarioStatus
    url: str
    created_at: str
    screenshots: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Issue:
    kind: str
    message: str
    url: str
    occurred_at: str
    status_code: int | None = None
    screenshot: str | None = None


@dataclass(slots=True)
class ExplorationRequest:
    app_url: str
    agent: ModelSelection
    auxiliary_llm: ModelSelection
    options: ExplorationOptions
