"""Explicit Continue submits fixture-only login fields once, never in a loop."""

import asyncio
import json

import pytest

from autojev.adapters.repository import SQLiteRepository
from autojev.adapters.report import StandaloneHtmlReport
from autojev.adapters.secrets import EphemeralCredentialVault, SecretCipher
from autojev.config import Settings
from autojev.domain.models import (
    ExplorationOptions,
    ExplorationRequest,
    ModelSelection,
    ProviderModel,
)
from autojev.services.exploration import ExplorationManager
from test_automatic_login import LOGIN, PASSWORD, USER, fixture_executor_type


@pytest.mark.parametrize("accepted,cancel_after_submit", [
    pytest.param(True, False, id="successful-login"),
    pytest.param(False, False, id="rejected-login"),
    pytest.param(True, True, id="cancel-during-login"),
])
async def test_manual_continue_submits_filled_login_once_then_explores_or_returns_to_gate(
    tmp_path, monkeypatch, accepted, cancel_after_submit,
):
    repo = SQLiteRepository(tmp_path / "fixture.sqlite3", SecretCipher(tmp_path, "fixture"))
    repo.initialize()
    provider = repo.create_provider(
        "Fixture only", "http://fixture.invalid", None, True,
        [ProviderModel("fixture-agent", "Agent"), ProviderModel("fixture-aux", "Aux")],
    )
    run_id = repo.create_exploration(ExplorationRequest(
        "http://login.fixture.test/", ModelSelection(provider.id, "fixture-agent"),
        ModelSelection(provider.id, "fixture-aux"), ExplorationOptions(max_steps=1),
    ))
    vault = EphemeralCredentialVault()  # No automatic-login credentials.
    executor_type = fixture_executor_type(LOGIN)
    instances, prompts, interventions = [], [], []

    class Executor(executor_type):
        confirmed_attempts = 0
        automatic_attempts = 0
        submissions_after_attempt = None
        submissions_before_close = None
        authenticated_root = None
        ct_actions = 0

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            instances.append(self)

        async def attempt_login(self, credentials, observation):
            self.automatic_attempts += 1
            return await super().attempt_login(credentials, observation)

        async def attempt_filled_login(self, observation):
            self.confirmed_attempts += 1
            result = await super().attempt_filled_login(observation)
            self.submissions_after_attempt = await self._page.evaluate("window.loginSubmissions")
            if cancel_after_submit:
                # A user cancellation arriving during the awaited login must
                # stop before any planner, agent or original CT action runs.
                repo.request_cancel(run_id)
            return result

        async def execute(self, *args, **kwargs):
            self.ct_actions += 1
            return await super().execute(*args, **kwargs)

        def protect_authenticated_history(self):
            super().protect_authenticated_history()
            self.authenticated_root = self._authenticated_history_root_url

        async def close(self):
            if self._page and not self._page.is_closed():
                self.submissions_before_close = await self._page.evaluate("window.loginSubmissions")
            await super().close()

    class Client:
        async def chat_json(self, _model, _system, content, **_kwargs):
            prompts.append(content)
            return {"title": "Fixture documentation"}

        def drain_usage(self):
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                    "cost": 0, "cost_known": False}

    settings = Settings(tmp_path, tmp_path / "fixture.sqlite3", tmp_path / "artifacts",
                        None, (), True, "chromium", 1)
    manager = ExplorationManager(repo, vault, StandaloneHtmlReport(settings.artifact_dir), settings)

    async def manual_gate(_id, observation, executor, panel_message=None, deadline=None):
        interventions.append(panel_message)
        assert await executor.is_login_page()
        assert not prompts, "Login fields must never be sent to an agent before authentication"
        if len(interventions) == 1:
            await executor._page.locator('[name="username"]').fill(USER)
            await executor._page.locator('[name="password"]').fill(
                PASSWORD if accepted else "fixture-wrong-password",
            )
            assert await executor._page.evaluate("window.loginSubmissions") == 0
            return "manual"  # Yellow Continue only; never click the site's Login button.
        assert executor.confirmed_attempts == 1
        assert await executor._page.evaluate("window.loginSubmissions") == 1
        return "cancelled"  # Reject safely without an automatic login retry.

    monkeypatch.setattr(manager, "_client_for", lambda _id: Client())
    monkeypatch.setattr(manager, "_wait_for_user_input", manual_gate)
    monkeypatch.setattr("autojev.services.exploration.PlaywrightExecutor", Executor)
    await asyncio.wait_for(manager._run(run_id), timeout=30)

    executor = instances[0]
    run = repo.get_exploration(run_id)
    event_types = [event["type"] for event in run["events"]]
    results = [event for event in run["events"] if event["type"] == "confirmed_login_result"]
    assert executor.confirmed_attempts == 1
    assert executor.automatic_attempts == 0
    assert executor.submissions_after_attempt == 1
    assert event_types.count("confirmed_login_started") == 1
    assert len(results) == 1
    if cancel_after_submit:
        assert len(interventions) == 1
        assert run["status"] == "cancelled"
        assert "authenticated_scope_started" not in event_types
        assert executor.ct_actions == 0
        assert not run["actions"]
        assert not prompts
    elif accepted:
        assert len(interventions) == 1
        assert "authenticated_scope_started" in event_types
        assert run["status"] in {"completed", "partial"}
        assert executor.authenticated_root.endswith("/inventory.html")
    else:
        assert len(interventions) == 2
        assert run["status"] == "cancelled"
        assert "login_not_completed" in event_types
        assert "authenticated_scope_started" not in event_types
        assert executor.submissions_before_close == 1
        assert not prompts
    assert all(secret not in json.dumps(run) for secret in (USER, PASSWORD.strip(), "fixture-wrong-password"))
    assert all(secret not in json.dumps(prompts) for secret in (USER, PASSWORD.strip(), "fixture-wrong-password"))
    assert run["usage"]["total_tokens"] == 0
    assert not vault.get(run_id)
