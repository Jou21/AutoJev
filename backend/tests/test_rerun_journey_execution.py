"""Durable journey replay through the service, without models or external I/O."""

import asyncio
import copy
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from autojev.adapters.playwright_executor import BrowserUnavailableError, PlaywrightExecutor
from autojev.adapters.repository import SQLiteRepository
from autojev.adapters.secrets import SecretCipher
from autojev.domain.models import AgentDecision, ExplorationOptions, Observation
from autojev.services.business_checks import check_business_outcomes
from autojev.services.exploration import ExplorationManager
from autojev.services.rerun_recipe import build_rerun_recipe
from test_scenario_rerun import URL, action_result, make_service, observed


ENTRY = "https://example.test/setup.html"


def setup_page(*, filled=False, fresh=False):
    field_ref, button_ref = ("e71", "e72") if fresh else ("e1", "e2")
    value = "AutoJev QA city" if filled else ""
    return Observation(
        ENTRY, "Local setup", "City Continue",
        [{"ref": field_ref, "tag": "input", "type": "text", "name": "City",
          "enabled": True, "has_value": filled},
         {"ref": button_ref, "tag": "button", "type": "button", "name": "Continue",
          "enabled": True}],
        page_state={"fields": [{"ref": field_ref, "id": "city", "identity": "city",
                                "name": "City", "type": "text", "value": value,
                                "has_value": filled}]},
    )


def persist_journey(repository, source_id, states, decisions, app_url, results=None):
    for step, (before, after, decision) in enumerate(zip(states, states[1:], decisions), 1):
        result = results[step - 1] if results is not None else action_result(before, after, decision)
        result.assertions.extend(check_business_outcomes(before, decision, result, after))
        ExplorationManager._append_observable_state_evidence(
            result, decision, after, state_changed=True,
            capture_console_errors=True, capture_network_errors=True,
        )
        result.evidence.update(action_step=step, before_screenshot=before.screenshot,
                               before_observed_at=before.captured_at)
        recipe = build_rerun_recipe(before, decision, app_url, capture_journey=True)
        assert recipe["enabled"], (step, asdict(decision), recipe)
        result.evidence["rerun_recipe"] = recipe
        repository.add_action(source_id, step, asdict(decision), result.to_dict())
    scenario = ExplorationManager._scenario_from_evidence(states[-1], decisions[-1], result, 1)
    repository.add_scenario(source_id, 1, scenario)
    return scenario.id


def durable_service(tmp_path):
    service, repository, source_id, _, reports = make_service(tmp_path)
    with repository._connect() as connection:
        connection.execute("DELETE FROM actions WHERE exploration_id = ?", (source_id,))
    states = [setup_page(), setup_page(filled=True), observed(), observed(selected="za")]
    decisions = [AgentDecision("fill", "Fill local city", target="e1", value="AutoJev QA city"),
                 AgentDecision("click", "Continue to inventory", target="e2"),
                 AgentDecision("select", "Change sorting", target="e1", value="za")]
    scenario_id = persist_journey(repository, source_id, states, decisions, URL)
    # A fresh repository/service must use the durable plan, not the original objects.
    reopened = SQLiteRepository(service.settings.database_path, SecretCipher(tmp_path, "test"))
    reopened.initialize()
    service.repository = service.manager.repository = reopened
    return service, reopened, source_id, scenario_id, reports


def install_journey_browser(monkeypatch, *, fault=None, setup_check=None):
    instances = []

    class Browser:
        def __init__(self, *args, **kwargs):
            self.actions, self.started, self.closed = [], [], False
            self.states = [setup_page(fresh=True), setup_page(filled=True, fresh=True),
                           observed("e81"), observed("e81", selected="za")]
            if fault == "initial_drift":
                self.states[0].title = "Unexpected setup page"
            instances.append(self)

        async def start(self, url):
            self.started.append(url)
            assert url == ENTRY
            return copy.deepcopy(self.states[0])

        async def execute(self, decision, credentials):
            assert credentials == {}
            index = len(self.actions)
            self.actions.append(copy.deepcopy(decision))
            if fault == "setup_exception" and index == 0:
                raise RuntimeError("local setup execution failed")
            after = copy.deepcopy(self.states[index + 1])
            if fault == "state_drift" and index == 0:
                after.page_state["fields"][0]["value"] = "AutoJev QA other city"
            if fault == "final_drift" and index == 1:
                after.title = "Unexpected inventory page"
            result = action_result(self.states[index], after, decision)
            if setup_check is not None and index == 1:
                from autojev.domain.models import AssertionResult
                result.assertions.append(AssertionResult(
                    "text_contains", "Known setup marker", "Local observed content",
                    setup_check, "Previously observed setup check"))
            if fault == "setup_failure" and index == 0:
                result.success = False
                result.message = "Local setup failed"
            return result, after

        async def show_intervention_panel(self, *args, **kwargs):
            raise AssertionError("A valid journey must not request manual preparation")

        async def wait_until_inactive(self):
            await asyncio.Event().wait()

        async def close(self):
            self.closed = True

    monkeypatch.setattr("autojev.services.scenario_rerun.PlaywrightExecutor", Browser)
    return instances


async def finish(service, new_id):
    await asyncio.wait_for(service.tasks[new_id], timeout=10)


def test_journey_survives_sqlite_reopen_and_preflight_is_read_only(tmp_path):
    service, repository, source_id, scenario_id, _ = durable_service(tmp_path)
    original = repository.get_exploration(source_id)
    journey = original["scenarios"][0]["evidence"]["rerun_journey"]
    assert journey["version"] == "ct-journey-v1"
    assert journey["complete"] is True and journey["gaps"] == []
    assert journey["entry_url"] == ENTRY
    assert [step["step_number"] for step in journey["steps"]] == [1, 2, 3]
    assert [step["recipe"]["decision"]["action"] for step in journey["steps"]] == ["fill", "click", "select"]
    assert journey["checks"]
    preflight = service.preflight(source_id, scenario_id)
    assert preflight["available"] is True and preflight["automatic_preparation"] is True
    assert preflight["recipe_steps"] == 3
    assert repository.get_exploration(source_id) == original
    assert len(repository.list_explorations()) == 1


@pytest.mark.asyncio
async def test_automatic_setup_fill_click_then_final_checks_are_durable(tmp_path, monkeypatch):
    service, repository, source_id, scenario_id, reports = durable_service(tmp_path)
    original = repository.get_exploration(source_id)
    browsers = install_journey_browser(monkeypatch)
    new_id = service.submit(source_id, scenario_id)
    await finish(service, new_id)
    browser = browsers[0]
    item = repository.get_exploration(new_id)
    assert browser.started == [ENTRY] and browser.closed
    assert [(action.action, action.target) for action in browser.actions] == [
        ("fill", "e71"), ("click", "e72"), ("select", "e81")]
    assert browser.actions[0].value == "AutoJev QA city"
    assert item["status"] == "completed" and item["steps_completed"] == 3
    assert [action["step_number"] for action in item["actions"]] == [1, 2, 3]
    assert all(action["result"]["evidence"]["rerun_recipe"]["enabled"] for action in item["actions"])
    assert len(item["scenarios"]) == 1 and item["scenarios"][0]["status"] == "PASS"
    evidence = item["scenarios"][0]["evidence"]
    assert evidence["missing_original_checks"] == []
    assert {check["kind"] for check in evidence["assertions"]} >= {"selection_matches", "no_new_browser_errors"}
    assert item["usage"]["total_tokens"] == 0 and len(reports) == 1
    assert not any(event["type"] == "waiting_input" for event in item["events"])
    assert repository.get_exploration(source_id) == original
    reopened = SQLiteRepository(service.settings.database_path, SecretCipher(tmp_path, "test"))
    reopened.initialize()
    assert reopened.get_exploration(new_id) == item


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,dispatched,persisted", [
    ("initial_drift", 0, 0),
    ("state_drift", 1, 1),
    ("setup_failure", 1, 1),
    ("setup_exception", 1, 0),
    ("final_drift", 2, 2),
])
async def test_setup_failure_or_state_drift_never_dispatches_final_action(
    tmp_path, monkeypatch, fault, dispatched, persisted,
):
    service, repository, source_id, scenario_id, _ = durable_service(tmp_path)
    original = repository.get_exploration(source_id)
    browsers = install_journey_browser(monkeypatch, fault=fault)
    new_id = service.submit(source_id, scenario_id)
    await finish(service, new_id)
    item = repository.get_exploration(new_id)
    assert len(browsers[0].actions) == dispatched and browsers[0].closed
    assert all(action.action != "select" for action in browsers[0].actions)
    assert len(item["actions"]) == persisted
    assert item["status"] in {"partial", "failed"}
    assert item["scenarios"][0]["status"] == "BLOCKED"
    assert item["scenarios"][0]["evidence"]["action_dispatched"] is False
    assert not any(event["type"] == "waiting_input" for event in item["events"])
    assert repository.get_exploration(source_id) == original


@pytest.fixture
def local_journey_site():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if self.path == "/setup.html":
                body = ('<title>Local setup</title><label>City<input id="city"></label>'
                        '<button type="button" onclick="location.href=\'/inventory.html\'">Continue</button>')
            elif self.path == "/inventory.html":
                body = ('<title>Products</title><label>Sort products<select id="sort">'
                        '<option value="az">Name (A to Z)</option>'
                        '<option value="za">Name (Z to A)</option></select></label>')
            else:
                self.send_error(404)
                return
            payload = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_real_local_browser_replays_two_setup_actions_without_manual_input(
    tmp_path, monkeypatch, local_journey_site,
):
    origin, requests = local_journey_site
    service, repository, source_id, _, _ = make_service(tmp_path)
    with repository._connect() as connection:
        connection.execute("DELETE FROM actions WHERE exploration_id = ?", (source_id,))
        connection.execute("UPDATE explorations SET app_url = ? WHERE id = ?", (origin, source_id))
    recorder = PlaywrightExecutor(tmp_path / "recorded", ExplorationOptions(),
                                  headless=True, browser_name="chromium")
    states, decisions, results = [], [], []
    try:
        try:
            states.append(await recorder.start(origin + "/setup.html"))
        except BrowserUnavailableError as exc:
            pytest.skip(f"Local Chromium unavailable: {exc}")
        for action, name, value in [("fill", "City", "AutoJev QA city"),
                                    ("click", "Continue", None),
                                    ("select", "Sort products", "za")]:
            controls = [element for element in states[-1].interactive_elements
                        if name in str(element.get("name", "")) or name in str(element.get("label", ""))]
            assert controls, (name, states[-1].interactive_elements)
            control = controls[0]
            decision = AgentDecision(action, "Local journey regression", target=control["ref"], value=value)
            result, after = await recorder.execute(decision, {})
            assert result.success, result.message
            decisions.append(decision)
            results.append(result)
            states.append(after)
    finally:
        await recorder.close()
    scenario_id = persist_journey(repository, source_id, states, decisions, origin, results)
    original = repository.get_exploration(source_id)
    assert service.preflight(source_id, scenario_id)["automatic_preparation"] is True
    executed = []

    class HeadlessLocalExecutor(PlaywrightExecutor):
        def __init__(self, *args, **kwargs):
            kwargs["headless"] = True
            super().__init__(*args, **kwargs)

        async def execute(self, decision, credentials):
            executed.append(decision.action)
            return await super().execute(decision, credentials)

        async def show_intervention_panel(self, *args, **kwargs):
            raise AssertionError("Real local journey must not request manual preparation")

    monkeypatch.setattr("autojev.services.scenario_rerun.PlaywrightExecutor", HeadlessLocalExecutor)
    new_id = service.submit(source_id, scenario_id)
    await asyncio.wait_for(service.tasks[new_id], timeout=60)
    item = repository.get_exploration(new_id)
    assert executed == ["fill", "click", "select"], item.get("error")
    assert item["status"] == "completed", {
        "missing": item["scenarios"][0]["evidence"].get("missing_original_checks"),
        "original": original["scenarios"][0]["evidence"]["assertions"],
        "replayed": item["scenarios"][0]["evidence"]["assertions"],
    }
    assert item["scenarios"][0]["status"] == "PASS"
    assert item["scenarios"][0]["evidence"]["missing_original_checks"] == []
    assert len(item["actions"]) == 3 and item["steps_completed"] == 3
    assert requests.count("/setup.html") == requests.count("/inventory.html") == 2
    assert repository.get_exploration(source_id) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("positions", [(0,), (2,), (0, 2)])
@pytest.mark.parametrize("failure", [None, "rejected", "drift"])
async def test_authentication_setups_execute_at_recorded_boundaries(
    tmp_path, monkeypatch, positions, failure,
):
    import json
    from autojev.adapters.playwright_executor import LoginAttempt
    from autojev.services.rerun_setup import build_authentication_setup
    from test_rerun_setup import VAULT, observation as login_observation

    service, repository, source_id, scenario_id, _ = durable_service(tmp_path)
    login = login_observation()
    login.url = "https://example.test/login.html"
    setup = build_authentication_setup(login, VAULT, URL)
    assert setup["enabled"]
    for position in positions:
        repository.add_event(source_id, "info", "rerun_authentication_setup", "Recorded login",
                             {"after_step": position, "setup": setup})
    # Recreate the scenario so the repository captures authentication events.
    states = [setup_page(), setup_page(filled=True), observed(), observed(selected="za")]
    decisions = [AgentDecision("fill", "Fill city", target="e1", value="AutoJev QA city"),
                 AgentDecision("click", "Continue", target="e2"),
                 AgentDecision("select", "Sort", target="e1", value="za")]
    # Only the scenario needs rebuilding; keep the existing ordered action prefix.
    result = action_result(states[-2], states[-1], decisions[-1])
    result.assertions.extend(check_business_outcomes(states[-2], decisions[-1], result, states[-1]))
    ExplorationManager._append_observable_state_evidence(
        result, decisions[-1], states[-1], state_changed=True,
        capture_console_errors=True, capture_network_errors=True)
    result.evidence["action_step"] = 3
    scenario = ExplorationManager._scenario_from_evidence(states[-1], decisions[-1], result, 1)
    repository.add_scenario(source_id, 1, scenario)
    original = repository.get_exploration(source_id)
    journey = original["scenarios"][0]["evidence"]["rerun_journey"]
    assert [entry["after_step"] for entry in journey["authentications"]] == list(positions)
    assert service.preflight(source_id, scenario_id)["required_inputs"] == ["password", "username"]
    calls = []

    class AuthBrowser:
        def __init__(self, *args, **kwargs):
            self.index = 0
            self.closed = False
            self.states = [setup_page(fresh=True), setup_page(filled=True, fresh=True),
                           observed("e81"), observed("e81", selected="za")]

        def challenge(self):
            current = copy.deepcopy(login)
            for index, control in enumerate(current.interactive_elements):
                control["ref"] = f"e{index + 90}"
            if failure == "drift":
                current.interactive_elements[0]["name"] = "Changed identity control"
            return current

        async def start(self, url):
            assert url == (login.url if 0 in positions else ENTRY)
            return self.challenge() if 0 in positions else self.states[0]

        async def attempt_login(self, credentials, before):
            assert credentials == VAULT
            calls.append(("authenticate", self.index))
            return LoginAttempt("manual_required" if failure == "rejected" else "success",
                                "rejected" if failure == "rejected" else "authenticated",
                                copy.deepcopy(self.states[self.index]))

        async def execute(self, decision, credentials):
            assert credentials == VAULT
            calls.append((decision.action, self.index))
            before = self.states[self.index]
            self.index += 1
            after = self.challenge() if self.index in positions else copy.deepcopy(self.states[self.index])
            return action_result(before, after, decision), after

        async def show_intervention_panel(self, *args, **kwargs):
            raise AssertionError("Authentication must not fall back to manual input")

        async def close(self):
            self.closed = True

    monkeypatch.setattr("autojev.services.scenario_rerun.PlaywrightExecutor", AuthBrowser)
    before_count = len(repository.list_explorations())
    for credentials in [None, {}, {"username": VAULT["username"]}, {**VAULT, "password": ""}]:
        with pytest.raises(ValueError, match="insumos privados"):
            service.submit(source_id, scenario_id, credentials=credentials)
        assert len(repository.list_explorations()) == before_count
    new_id = service.submit(source_id, scenario_id, credentials=VAULT)
    await finish(service, new_id)
    item = repository.get_exploration(new_id)
    if failure is None:
        expected = []
        for index, name in enumerate(["fill", "click", "select"]):
            if index in positions:
                expected.append(("authenticate", index))
            expected.append((name, index))
        assert calls == expected
        assert item["status"] == "completed" and item["scenarios"][0]["status"] == "PASS"
        events = [event for event in item["events"] if event["type"] == "rerun_authentication_setup"]
        assert [event["payload"] for event in events] == journey["authentications"]
        assert len(item["actions"]) == 3
    else:
        assert ("select", 2) not in calls
        assert item["status"] in {"partial", "failed"}
        assert item["scenarios"][0]["status"] == "BLOCKED"
        assert len(item["actions"]) == positions[0]
    assert not any(event["type"] == "waiting_input" for event in item["events"])
    assert all(value not in json.dumps(item) for value in VAULT.values())
    assert repository.get_exploration(source_id) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("original_literal", [False, True], ids=["credential-alias", "literal-to-alias"])
async def test_private_recipe_input_requires_explicit_vault_before_creating_run(
    tmp_path, monkeypatch, original_literal,
):
    import json
    from autojev.adapters.playwright_executor import _RerunPageState

    secret = "local-private-city-value"
    credentials = {"city_input": secret}
    service, repository, source_id, _, _ = make_service(tmp_path)
    with repository._connect() as connection:
        connection.execute("DELETE FROM actions WHERE exploration_id = ?", (source_id,))
    before, after = setup_page(), setup_page(filled=True)
    after.page_state["fields"][0]["value"] = secret
    after.page_state = _RerunPageState(after.page_state, {"e1": "city_input"})
    decision = AgentDecision(
        "fill", "Fill private city", target="e1",
        value=secret if original_literal else None,
        credential_key=None if original_literal else "city_input",
    )
    recipe = build_rerun_recipe(before, decision, URL, credentials=credentials, capture_journey=True)
    assert recipe["enabled"] and recipe["decision"]["input_ref"] == "city_input"
    result = action_result(before, after, decision)
    result.assertions.extend(check_business_outcomes(before, decision, result, after))
    ExplorationManager._append_observable_state_evidence(
        result, decision, after, state_changed=True,
        capture_console_errors=True, capture_network_errors=True)
    result.evidence.update(action_step=1, rerun_recipe=recipe)
    # Persist only a vault reference, as exploration does for private actions.
    private_decision = AgentDecision("fill", "Fill private city", target="e1", credential_key="city_input")
    repository.add_action(source_id, 1, asdict(private_decision), result.to_dict())
    scenario = ExplorationManager._scenario_from_evidence(after, private_decision, result, 1)
    repository.add_scenario(source_id, 1, scenario)
    original = repository.get_exploration(source_id)
    assert service.preflight(source_id, scenario.id)["required_inputs"] == ["city_input"]
    count = len(repository.list_explorations())
    for inputs in [None, {}, {"city_input": ""}, {"wrong_alias": secret}]:
        with pytest.raises(ValueError, match="insumos privados"):
            service.submit(source_id, scenario.id, credentials=inputs)
        assert len(repository.list_explorations()) == count
    executed = []

    class PrivateBrowser:
        def __init__(self, *args, **kwargs):
            pass

        async def start(self, url):
            assert url == ENTRY
            return setup_page(fresh=True)

        async def execute(self, current, vault):
            assert vault == credentials
            assert current.credential_key == "city_input" and current.value is None
            assert current.target == "e71"
            executed.append(current.action)
            current_after = copy.deepcopy(after)
            current_after.interactive_elements[0]["ref"] = "e71"
            current_after.page_state["fields"][0]["ref"] = "e71"
            current_after.page_state["fields"][0]["value"] = "[FIELD VALUE]"
            current_after.page_state = _RerunPageState(current_after.page_state, {"e71": "city_input"})
            output = action_result(setup_page(fresh=True), current_after, current)
            return output, current_after

        async def close(self):
            pass

    monkeypatch.setattr("autojev.services.scenario_rerun.PlaywrightExecutor", PrivateBrowser)
    new_id = service.submit(source_id, scenario.id, credentials=credentials)
    await finish(service, new_id)
    item = repository.get_exploration(new_id)
    assert executed == ["fill"]
    assert item["status"] == "completed" and item["scenarios"][0]["status"] == "PASS"
    evidence = item["scenarios"][0]["evidence"]
    assert evidence["missing_original_checks"] == []
    original_checks = original["scenarios"][0]["evidence"]["assertions"]
    assert any(check["kind"] == "field_value_matches" and check["passed"] for check in original_checks)
    assert {check["kind"] for check in original_checks} <= {check["kind"] for check in evidence["assertions"]}
    assert all(check["passed"] for check in evidence["assertions"])
    assert "rerun_field_bindings" not in json.dumps(item)
    assert secret not in json.dumps(item)
    assert repository.get_exploration(source_id) == original


def repersist_scenario(repository, source_id, stored, *, observation_only=False):
    from autojev.domain.models import TestScenario

    data = {key: copy.deepcopy(stored[key]) for key in TestScenario.__dataclass_fields__ if key in stored}
    data["evidence"].pop("rerun_journey", None)
    if observation_only:
        data["evidence"].pop("action_step", None)
        data["evidence"]["observation_only"] = True
    repository.add_scenario(source_id, 1, TestScenario(**data))


@pytest.mark.parametrize("mismatch", [None, "entry_url", "state_digest", "effect_digest", "gap"])
def test_initial_contract_mismatch_is_concrete_durable_gap(tmp_path, mismatch):
    service, repository, source_id, scenario_id, _ = durable_service(tmp_path)
    item = repository.get_exploration(source_id)
    stored = item["scenarios"][0]
    first = stored["evidence"]["rerun_journey"]["steps"][0]["recipe"]
    contract = {"entry_url": first["before_url"], "state_digest": first["before_state_digest"],
                "effect_digest": first["before_effect_digest"]}
    if mismatch == "entry_url":
        contract["entry_url"] = URL
    elif mismatch in {"state_digest", "effect_digest"}:
        contract[mismatch] = "0" * 64
    elif mismatch == "gap":
        contract["gap"] = "unverifiable_initial_state"
    repository.add_event(source_id, "info", "rerun_initial_contract", "Initial state", contract)
    repersist_scenario(repository, source_id, stored)
    reopened = SQLiteRepository(service.settings.database_path, SecretCipher(tmp_path, "test"))
    reopened.initialize()
    original = reopened.get_exploration(source_id)
    journey = original["scenarios"][0]["evidence"]["rerun_journey"]
    assert journey["complete"] is (mismatch is None)
    assert journey["gaps"] == ([] if mismatch is None else [{"reason": "unrecorded_initial_preparation"}])
    service.repository = service.manager.repository = reopened
    assert service.preflight(source_id, scenario_id)["available"] is (mismatch is None)
    if mismatch is not None:
        count = len(reopened.list_explorations())
        with pytest.raises(ValueError):
            service.submit(source_id, scenario_id)
        assert len(reopened.list_explorations()) == count
    assert reopened.get_exploration(source_id) == original


def test_observation_only_scenario_has_explicit_gap_without_inventing_action(tmp_path):
    service, repository, source_id, scenario_id, _ = durable_service(tmp_path)
    stored = repository.get_exploration(source_id)["scenarios"][0]
    with repository._connect() as connection:
        connection.execute("DELETE FROM actions WHERE exploration_id = ?", (source_id,))
    repository.add_event(source_id, "info", "rerun_initial_contract", "Initial state",
                         {"entry_url": ENTRY, "state_digest": "a" * 64, "effect_digest": "b" * 64})
    repersist_scenario(repository, source_id, stored, observation_only=True)
    original = repository.get_exploration(source_id)
    journey = original["scenarios"][0]["evidence"]["rerun_journey"]
    assert journey["complete"] is False
    assert journey["gaps"] == [{"reason": "observation_only_without_action_recipe"}]
    assert journey["steps"] == [] and journey["action_step"] is None
    assert original["actions"] == []
    assert service.preflight(source_id, scenario_id)["available"] is False
    with pytest.raises(ValueError):
        service.submit(source_id, scenario_id)
    assert repository.get_exploration(source_id) == original
    assert len(repository.list_explorations()) == 1


def test_reading_legacy_scenario_never_backfills_or_rewrites_journey(tmp_path):
    service, repository, source_id, _, _ = make_service(tmp_path)
    original = repository.get_exploration(source_id)
    assert "rerun_journey" not in original["scenarios"][0]["evidence"]
    with repository._connect() as connection:
        raw_before = connection.execute("SELECT data_json FROM scenarios WHERE exploration_id = ?",
                                        (source_id,)).fetchall()
        raw_before = [tuple(row) for row in raw_before]
    # Even a later initial event does not authorize rewriting legacy evidence on read.
    repository.add_event(source_id, "info", "rerun_initial_contract", "Late initial state",
                         {"entry_url": ENTRY, "gap": "not_an_original_contract"})
    snapshot = repository.get_exploration(source_id)
    reopened = SQLiteRepository(service.settings.database_path, SecretCipher(tmp_path, "test"))
    reopened.initialize()
    service.repository = service.manager.repository = reopened
    for _ in range(2):
        assert reopened.get_exploration(source_id) == snapshot
        preflight = service.preflight(source_id, "CT001")
        assert preflight["available"] is True and not preflight.get("automatic_preparation", False)
    with reopened._connect() as connection:
        raw_after = connection.execute("SELECT data_json FROM scenarios WHERE exploration_id = ?",
                                       (source_id,)).fetchall()
    assert [tuple(row) for row in raw_after] == raw_before
    assert len(reopened.list_explorations()) == 1


@pytest.mark.asyncio
async def test_rerun_of_completed_rerun_retains_all_setup_steps(tmp_path, monkeypatch):
    service, repository, source_id, scenario_id, _ = durable_service(tmp_path)
    browsers = install_journey_browser(monkeypatch)
    first_id = service.submit(source_id, scenario_id)
    await finish(service, first_id)
    first = repository.get_exploration(first_id)
    assert first["status"] == "completed"
    first_scenario = first["scenarios"][0]
    journey = first_scenario["evidence"]["rerun_journey"]
    assert journey["complete"] is True and journey["gaps"] == []
    assert first_scenario["evidence"]["action_step"] == 3
    assert [step["step_number"] for step in journey["steps"]] == [1, 2, 3]
    assert all(action["result"]["evidence"]["rerun_recipe"]["journey_capture"]
               for action in first["actions"])
    reopened = SQLiteRepository(service.settings.database_path, SecretCipher(tmp_path, "test"))
    reopened.initialize()
    service.repository = service.manager.repository = reopened
    preflight = service.preflight(first_id, first_scenario["id"])
    assert preflight["available"] is True and preflight["automatic_preparation"] is True
    assert preflight["recipe_steps"] == 3
    second_id = service.submit(first_id, first_scenario["id"])
    await finish(service, second_id)
    second = reopened.get_exploration(second_id)
    assert second["status"] == "completed" and second["steps_completed"] == 3
    assert second["scenarios"][0]["status"] == "PASS"
    assert second["scenarios"][0]["evidence"]["missing_original_checks"] == []
    assert [action["decision"]["action"] for action in second["actions"]] == ["fill", "click", "select"]
    assert second["scenarios"][0]["evidence"]["rerun_journey"]["complete"] is True
    assert len(browsers) == 2 and all(browser.closed for browser in browsers)
    assert reopened.get_exploration(first_id) == first


@pytest.mark.asyncio
@pytest.mark.parametrize("replayed_passed", [False, True], ids=["known-failure-reproduced", "outcome-changed"])
async def test_prior_known_failing_check_does_not_contaminate_selected_ct(
    tmp_path, monkeypatch, replayed_passed,
):
    import json

    service, repository, source_id, scenario_id, _ = durable_service(tmp_path)
    stored = repository.get_exploration(source_id)["scenarios"][0]
    with repository._connect() as connection:
        row = connection.execute("SELECT id, result_json FROM actions WHERE exploration_id = ? AND step_number = 2",
                                 (source_id,)).fetchone()
        result = json.loads(row["result_json"])
        # A concrete decision check suppresses the generic observable-state fallback.
        result["assertions"] = [check for check in result["assertions"]
                                if check["kind"] != "observable_state_change"]
        result["assertions"].append({"kind": "text_contains", "expected": "Known setup marker",
                                     "actual": "Local observed content", "passed": False,
                                     "message": "Previously observed setup check"})
        connection.execute("UPDATE actions SET result_json = ? WHERE id = ?", (json.dumps(result), row["id"]))
    repersist_scenario(repository, source_id, stored)
    original = repository.get_exploration(source_id)
    outcome = original["scenarios"][0]["evidence"]["rerun_journey"]["steps"][1]["outcome"]
    assert any(check["kind"] == "text_contains" and check["passed"] is False for check in outcome["checks"])
    browsers = install_journey_browser(monkeypatch, setup_check=replayed_passed)
    new_id = service.submit(source_id, scenario_id)
    await finish(service, new_id)
    item = repository.get_exploration(new_id)
    if not replayed_passed:
        assert [action.action for action in browsers[0].actions] == ["fill", "click", "select"]
        assert item["status"] == "completed" and item["scenarios"][0]["status"] == "PASS"
        assert item["scenarios"][0]["evidence"]["missing_original_checks"] == []
        assert all(check["passed"] for check in item["scenarios"][0]["evidence"]["assertions"])
        assert any(check["kind"] == "text_contains" and not check["passed"]
                   for check in item["actions"][1]["result"]["assertions"])
    else:
        assert [action.action for action in browsers[0].actions] == ["fill", "click"]
        assert item["status"] == "partial"
        assert item["error"] == "setup_checks_changed"
        assert item["scenarios"][0]["status"] == "BLOCKED"
    assert browsers[0].closed
    assert repository.get_exploration(source_id) == original


def test_auxiliary_visual_opinion_does_not_destroy_local_replay_contract(tmp_path):
    from autojev.domain.models import TestScenario
    service, repository, source_id, scenario_id, _ = durable_service(tmp_path)
    stored = repository.get_exploration(source_id)["scenarios"][0]
    original = copy.deepcopy(stored["evidence"]["rerun_journey"])
    assert original["complete"] is True
    stored["evidence"]["assertions"].append({"kind": "visual_model", "expected": "Visual review", "actual": "Issue", "passed": False, "message": "Model opinion"})
    scenario = TestScenario(**stored)
    for _ in range(2):
        repository.add_scenario(source_id, 1, scenario)
        updated = repository.get_exploration(source_id)["scenarios"][0]
        assert updated["evidence"]["rerun_journey"] == original
        assert updated["evidence"]["assertions"][-1]["kind"] == "visual_model"
    assert service.preflight(source_id, scenario_id)["available"] is True
