"""Transient form feedback is exercised before the departing submission."""

from copy import deepcopy

import pytest

from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.domain.models import ActionResult, AgentDecision, ExplorationOptions, Observation, utc_now
from autojev.services.business_checks import check_business_outcomes
from autojev.services.coverage import CoveragePlanner


def feedback_page():
    return Observation(
        "https://app.test/checkout", "Checkout", "First Name is required",
        [
            {"ref": "field", "tag": "input", "type": "text", "name": "First Name",
             "form": "checkout", "required": True, "has_value": False},
            {"ref": "continue", "tag": "input", "type": "submit", "name": "Continue",
             "form": "checkout"},
            {"ref": "dismiss", "tag": "button", "type": "button", "name": "Dismiss error",
             "form": "checkout", "validation_dismiss": True, "validation_form": "checkout"},
        ],
        page_state={
            "forms": [{"id": "checkout", "submit_refs": ["continue"],
                       "validation_errors": ["First Name is required"]}],
            "fields": [{"identity": "first", "ref": "field", "form": "checkout",
                        "label": "First Name", "required": True, "has_value": False,
                        "value_token": "opaque-empty"}],
            "cart": {"count": 1},
        },
    )


@pytest.mark.parametrize("entry", ["next_restoration_action", "reconcile"])
def test_visible_inline_feedback_preempts_probe_and_positive_submit(entry):
    before = feedback_page()
    planner = CoveragePlanner(before.url)
    selected = (planner.next_restoration_action(before) if entry == "next_restoration_action"
                else planner.reconcile(before, AgentDecision("click", "submit", target="continue")))
    decision, source = selected
    assert decision.target == "dismiss" and source == "inline_validation_feedback_dismissed"
    after = deepcopy(before)
    after.interactive_elements = [item for item in after.interactive_elements if item["ref"] != "dismiss"]
    after.page_state["forms"][0]["validation_errors"] = []
    result = ActionResult("click", True, utc_now(), utc_now(), before.url, after.url)
    planner.record_action(before, decision, result, after)
    assert planner._inline_validation_dismissal(planner.states[planner.observe(before)]) is None
    assert not planner.restrictions


def test_a_label_alone_does_not_grant_inline_feedback_priority():
    before = feedback_page()
    del before.interactive_elements[-1]["validation_dismiss"]
    planner = CoveragePlanner(before.url)
    assert planner._inline_validation_dismissal(planner.states[planner.observe(before)]) is None


@pytest.mark.parametrize("failure", [None, "error_retained", "value_lost", "route_changed"])
def test_dismiss_business_checks_verify_feedback_and_opaque_field_retention(failure):
    before = feedback_page()
    after = deepcopy(before)
    after.page_state["forms"][0]["validation_errors"] = []
    if failure == "error_retained":
        after.page_state["forms"][0]["validation_errors"] = ["First Name is required"]
    if failure == "value_lost":
        after.page_state["fields"][0]["value_token"] = "opaque-changed"
    if failure == "route_changed":
        after.url += "/other"
    decision = AgentDecision("click", "dismiss", target="dismiss")
    result = ActionResult("click", True, utc_now(), utc_now(), before.url, after.url)
    checks = check_business_outcomes(before, decision, result, after)
    assert {check.kind for check in checks} == {
        "form_validation_feedback_dismissed", "form_fields_preserved_on_dismiss",
    }
    assert all(check.passed for check in checks) is (failure is None)


@pytest.mark.parametrize("button_type,label,owned,expected", [
    ("button", "Dismiss error", True, True),
    ("submit", "Dismiss error", True, False),
    ("reset", "Close", True, False),
    ("button", "Cancel order", True, False),
    ("button", "Dismiss error", False, False),
])
async def test_real_dom_grants_dismiss_only_to_form_owned_harmless_error_button(
    tmp_path, button_type, label, owned, expected,
):
    playwright_api = pytest.importorskip("playwright.async_api")
    playwright = await playwright_api.async_playwright().start()
    browser = await playwright.chromium.launch(headless=True)
    executor = PlaywrightExecutor(tmp_path, ExplorationOptions(capture_screenshots=False))
    executor._page = await browser.new_page()
    error = f'<p role="alert">First Name is required <button type="{button_type}" aria-label="{label}"></button></p>'
    try:
        await executor._page.set_content(
            '<form id="checkout"><label>First Name<input name="first" value="Sample"></label>'
            + (error if owned else '') + '<input type="submit" value="Continue"></form>'
            + ('' if owned else error)
        )
        before = await executor.observe("feedback")
        control = next(item for item in before.interactive_elements if item["name"] == label)
        assert (control.get("validation_dismiss") is True) is expected
        if expected:
            assert control["validation_form"] == before.page_state["forms"][0]["id"]
    finally:
        await browser.close()
        await playwright.stop()
