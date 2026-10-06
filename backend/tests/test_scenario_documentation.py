from autojev.domain.models import ActionResult, AgentDecision, AssertionResult, Observation, utc_now
from autojev.services.scenario_documentation import local_scenario_documentation


def inputs():
    observation = Observation("https://app.test/cart", "Cart", "Cart", [])
    decision = AgentDecision("fill", "Test field retention", target="e1", value="private-input")
    result = ActionResult("fill", True, utc_now(), utc_now(), observation.url, observation.url)
    return observation, decision, result


def test_local_document_uses_actual_checks_not_an_unexecuted_hint():
    observation, decision, result = inputs()
    decision.scenario_hint = {"title": "Fill name", "expected_result": "An entire order should succeed"}
    result.assertions = [AssertionResult("field_retains_value", True, False, False, "The field must retain its supplied value.")]
    document = local_scenario_documentation(observation, decision, result)
    assert document["title"] == "Fill name"
    assert document["expected_result"] == "The field must retain its supplied value."
    assert "Não confirmada" in document["actual_result"]
    assert "private-input" not in str(document)
    assert "entire order" not in str(document)


def test_local_document_does_not_invent_a_business_check():
    document = local_scenario_documentation(*inputs())
    assert document["title"] == "Explore fill on Cart"
    assert "Nenhuma condição específica" in document["expected_result"]
    assert "Confirmada" not in document["actual_result"]


def test_required_probe_title_and_execution_failure_are_preserved():
    observation, decision, result = inputs()
    decision.scenario_hint = {"validation_probe": {"kind": "required_field", "name": "Last Name"}}
    result.success = False
    result.message = "Locator unavailable"
    document = local_scenario_documentation(observation, decision, result)
    assert document["title"] == "Validação obrigatória: Last Name"
    assert "A ação não foi concluída" in document["actual_result"]
    assert "Locator unavailable" in document["actual_result"]


def test_generic_observed_message_does_not_become_an_expected_business_result():
    observation, decision, result = inputs()
    result.assertions = [AssertionResult("no_new_browser_errors", 0, 0, True, "Observed")]
    document = local_scenario_documentation(observation, decision, result)
    assert "falhas técnicas atribuíveis" in document["expected_result"]
    assert "Observed" not in document["expected_result"]
    assert "Confirmada:" in document["actual_result"]
