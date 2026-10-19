"""Login detection should describe the observed page, not just URL mismatch."""
import asyncio
from copy import deepcopy

import autojev.services.scenario_rerun as rerun_module
import pytest
from test_scenario_rerun import (
    URL,
    install_browser,
    install_filled_login_browser,
    make_service,
    observed,
    wait_preparation,
)

LOGIN_LABEL = "Você está na tela de login. Preencha usuário e senha para continuar."


async def finish_cancelled(service, run_id, task):
    if not task.done():
        service.cancel(run_id)
    await asyncio.gather(task, return_exceptions=True)


def install_login_at(monkeypatch, url, **kwargs):
    browsers = install_filled_login_browser(monkeypatch, **kwargs)
    executor_type = rerun_module.PlaywrightExecutor
    original_start = executor_type.start

    async def start(self, requested_url):
        self.page.url = url
        return await original_start(self, requested_url)

    monkeypatch.setattr(executor_type, "start", start)
    return browsers


@pytest.mark.asyncio
@pytest.mark.parametrize("login_url", ["https://example.test/", URL])
async def test_observed_login_is_prioritized_even_when_url_also_changed_or_did_not_change(
    tmp_path, monkeypatch, login_url,
):
    service, repository, source_id, _, _ = make_service(tmp_path)
    source = deepcopy(repository.get_exploration(source_id))
    browsers = install_login_at(monkeypatch, login_url)
    run_id = service.submit(source_id, "CT001")
    task = service.tasks[run_id]
    try:
        await wait_preparation(service, run_id, 1)
        state = service.state_for(run_id)
        preparation = state["preparation"]
        assert preparation["reason_code"] == "authentication_required"
        assert preparation["reason_label"] == LOGIN_LABEL
        assert preparation["current_url"] == login_url
        assert preparation["expected_url"] == URL and preparation["attempts"] == 0
        panel_preparation = service._browser_panel_payload(run_id, [])["preparation"]
        assert panel_preparation["reason_code"] == "authentication_required"
        assert panel_preparation["reason_label"] == LOGIN_LABEL
        assert LOGIN_LABEL in preparation["instruction"]
        assert "página aberta não é a mesma" not in preparation["instruction"]
        await asyncio.sleep(0.02)
        # Detection, polling and displaying the form cannot submit credentials.
        assert not browsers[0].login_attempts and not browsers[0].actions
        assert not task.done()
        replay = repository.get_exploration(run_id)
        assert replay["status"] == "waiting_input" and replay["actions"] == replay["scenarios"] == []
        waiting = next(event for event in reversed(replay["events"]) if event["type"] == "waiting_input")
        assert waiting["payload"]["preparation"]["reason_code"] == "authentication_required"
        assert waiting["payload"]["preparation"]["reason_label"] == LOGIN_LABEL
        assert repository.get_exploration(source_id) == source
    finally:
        await finish_cancelled(service, run_id, task)


@pytest.mark.asyncio
async def test_an_ordinary_wrong_page_keeps_url_reason_without_a_login_attempt(tmp_path, monkeypatch):
    service, repository, source_id, _, _ = make_service(tmp_path)
    page = observed("e42")
    page.url = "https://example.test/another-screen.html"
    page.title, page.text = "Other screen", "Products and settings"
    browsers = install_browser(monkeypatch, before=page)
    executor_type = rerun_module.PlaywrightExecutor

    async def forbidden_login(self, observation=None):
        pytest.fail("A non-login page must not submit credentials")

    monkeypatch.setattr(executor_type, "attempt_filled_login", forbidden_login)
    run_id = service.submit(source_id, "CT001")
    task = service.tasks[run_id]
    try:
        await wait_preparation(service, run_id, 1)
        preparation = service.state_for(run_id)["preparation"]
        assert preparation["reason_code"] == "before_url_changed"
        assert "página aberta não é a mesma" in preparation["reason_label"]
        assert preparation["reason_label"] != LOGIN_LABEL
        service.resume(run_id)
        await wait_preparation(service, run_id, 2)
        assert service.state_for(run_id)["preparation"]["reason_code"] == "before_url_changed"
        assert not browsers[0].actions and not repository.get_exploration(run_id)["actions"]
    finally:
        await finish_cancelled(service, run_id, task)


@pytest.mark.asyncio
async def test_a_foreign_login_keeps_the_origin_warning_instead_of_inviting_authorized_login(tmp_path, monkeypatch):
    service, _, source_id, _, _ = make_service(tmp_path)
    browsers = install_login_at(monkeypatch, "https://unrelated.test/login")
    run_id = service.submit(source_id, "CT001")
    task = service.tasks[run_id]
    try:
        await wait_preparation(service, run_id, 1)
        preparation = service.state_for(run_id)["preparation"]
        assert preparation["reason_code"] == "cross_origin_source"
        assert "fora do site autorizado" in preparation["reason_label"]
        assert preparation["reason_label"] != LOGIN_LABEL
        assert not browsers[0].login_attempts and not browsers[0].actions
        service.resume(run_id)
        await wait_preparation(service, run_id, 2)
        assert service.state_for(run_id)["preparation"]["reason_code"] == "cross_origin_source"
        assert not browsers[0].login_attempts and not browsers[0].actions
    finally:
        await finish_cancelled(service, run_id, task)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,reason", [
    ("rejected", "login_rejected"),
    ("manual_required", "incomplete_credentials"),
    ("manual_required", "authentication_challenge"),
])
async def test_unsuccessful_login_keeps_login_reason_and_specific_feedback_until_new_confirmation(
    tmp_path, monkeypatch, status, reason,
):
    service, repository, source_id, _, _ = make_service(tmp_path)
    browsers = install_login_at(monkeypatch, "https://example.test/", outcomes=[(status, reason)])
    run_id = service.submit(source_id, "CT001")
    task = service.tasks[run_id]
    try:
        await wait_preparation(service, run_id, 1)
        assert not browsers[0].login_attempts
        service.resume(run_id)
        await wait_preparation(service, run_id, 2)
        retry = service.state_for(run_id)["preparation"]
        assert retry["reason_code"] == "authentication_required"
        assert retry["reason_label"] == LOGIN_LABEL
        assert retry["login_attempt"]["status"] == status and retry["login_attempt"]["reason"] == reason
        assert retry["login_attempt"]["reason_label"] in retry["instruction"]
        assert "página aberta não é a mesma" not in retry["instruction"]
        await asyncio.sleep(0.02)
        assert len(browsers[0].login_attempts) == 1 and not browsers[0].actions and not task.done()
        assert repository.get_exploration(run_id)["status"] == "waiting_input"
    finally:
        await finish_cancelled(service, run_id, task)


@pytest.mark.asyncio
@pytest.mark.parametrize("after_login_guard", ["state", "url"])
async def test_successful_login_does_not_leave_stale_authentication_reason_or_bypass_original_guards(
    tmp_path, monkeypatch, after_login_guard,
):
    service, repository, source_id, _, _ = make_service(tmp_path)
    wrong = observed("e42")
    if after_login_guard == "state":
        wrong.page_state["cart"] = {"count": 9}
        expected_reason = "before_state_changed"
    else:
        wrong.url = "https://example.test/other-products.html"
        expected_reason = "before_url_changed"
    browsers = install_login_at(monkeypatch, "https://example.test/", after_login=wrong)
    run_id = service.submit(source_id, "CT001")
    task = service.tasks[run_id]
    try:
        await wait_preparation(service, run_id, 1)
        service.resume(run_id)
        await wait_preparation(service, run_id, 2)
        browser = browsers[0]
        retry = service.state_for(run_id)["preparation"]
        assert retry["login_attempt"]["status"] == "success"
        assert retry["reason_code"] == expected_reason
        assert retry["reason_label"] != LOGIN_LABEL
        assert LOGIN_LABEL not in retry["instruction"]
        assert len(browser.login_attempts) == 1 and not browser.actions and not task.done()
        assert not repository.get_exploration(run_id)["actions"]
        browser.page = observed("e42")
        await asyncio.sleep(0.01)
        assert not browser.actions  # Merely fixing prerequisites is not consent.
        service.resume(run_id)
        await asyncio.wait_for(task, 3)
        assert len(browser.login_attempts) == len(browser.actions) == 1
        assert repository.get_exploration(run_id)["status"] == "completed"
        assert service.state_for(run_id)["preparation"]["reason_code"] is None
    finally:
        await finish_cancelled(service, run_id, task)
