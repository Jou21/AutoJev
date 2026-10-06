from copy import deepcopy
from dataclasses import asdict
import json
from types import SimpleNamespace

import pytest
from PIL import Image
from autojev.adapters.openai_compatible import (
    OpenAIAuxiliaryScenarioGenerator,
    ProviderConnectionError,
    _visual_document_schema,
)
from autojev.adapters.report import StandaloneHtmlReport
from autojev.domain.models import (
    ActionResult,
    AgentDecision,
    AssertionResult,
    Observation,
    ScenarioStatus,
    utc_now,
)
from autojev.services.exploration import (
    ExplorationManager,
    _LiveScenarioPipeline,
    _ScenarioEnrichmentJob,
)
from autojev.services.scenario_timeline import prepare_scenario_timelines
from autojev.services.visual_checks import (
    assess_visual_findings,
    visual_finding_schema,
    visual_review_counts,
)


def _png(path, color="green"):
    Image.new("RGB", (64, 48), color).save(path, format="PNG")

IMAGE = {
    "ref": "after",
    "screenshot": "after.png",
    "url": "https://shop.test/products",
    "detail": "low",
}
FINDING = {
    "category": "image_content_mismatch",
    "confidence": 0.98,
    "expected": "The named Backpack product must depict a backpack, not an unrelated animal.",
    "observed": "The product labelled Sauce Labs Backpack shows a dog instead of a backpack.",
    "evidence": {
        "image_ref": "after",
        "page_url": IMAGE["url"],
        "element_label": "Sauce Labs Backpack",
        "region": {"x": 0.1, "y": 0.2, "width": 0.2, "height": 0.3},
        "expected_basis": "visible_text",
        "expected_excerpt": "Sauce Labs Backpack",
    },
}
TEXT = "Products: Sauce Labs Backpack $29.99 Add to cart"


def assess(findings, images=None, **kwargs):
    return assess_visual_findings(
        {"findings": findings},
        images=[IMAGE] if images is None else images,
        model="fixture-vision",
        reference_text=TEXT,
        **kwargs,
    )


def test_concrete_image_mismatch_adds_negative_evidence_not_functional_bug_confirmation():
    assessment, assertions = assess([FINDING, deepcopy(FINDING)])
    assert assessment["accepted_count"] == 1
    assert len(assertions) == 1 and assertions[0]["passed"] is False
    assert assertions[0]["actual"]["confirmed_functional_bug"] is False
    assert assertions[0]["actual"]["screenshot"] == "after.png"


def test_same_visual_deviation_on_different_attempt_captures_has_distinct_timeline_id():
    first, _ = assess([FINDING])
    retry, _ = assess([FINDING], images=[{**IMAGE, "screenshot": "after-retry.png"}])
    assert first["accepted_count"] == retry["accepted_count"] == 1
    assert first["findings"][0]["id"] != retry["findings"][0]["id"]


@pytest.mark.parametrize(
    "mutation",
    [
        "low_confidence",
        "wrong_image",
        "wrong_page",
        "invented_identity",
        "design_preference",
        "out_of_bounds",
        "string_confidence",
    ],
)
def test_unverified_model_claims_cannot_fail_a_ct(mutation):
    finding = deepcopy(FINDING)
    if mutation == "low_confidence":
        finding["confidence"] = 0.89
    elif mutation == "wrong_image":
        finding["evidence"]["image_ref"] = "before"
    elif mutation == "wrong_page":
        finding["evidence"]["page_url"] = "https://other.test/"
    elif mutation == "invented_identity":
        finding["evidence"]["expected_excerpt"] = "Expensive product never supplied"
    elif mutation == "design_preference":
        finding["category"] = "layout_disruption"
        finding["evidence"]["expected_basis"] = "visible_text"
    elif mutation == "out_of_bounds":
        finding["evidence"]["region"]["width"] = 1.0
    else:
        finding["confidence"] = "0.98"
    assessment, assertions = assess([finding])
    assert not assertions and assessment["accepted_count"] == 0


def test_empty_or_missing_visual_payload_never_claims_full_visual_pass():
    empty, assertions = assess([])
    assert empty["reason"] == "no_visual_deviation_reported" and not assertions
    assert any(
        "não significa aprovação visual" in note for note in empty["limitations"]
    )
    unavailable, assertions = assess_visual_findings(
        None, images=[IMAGE], model="fixture", reference_text=TEXT
    )
    assert unavailable["status"] == "unavailable" and not assertions


def test_all_malformed_findings_are_unavailable_not_a_completed_empty_review():
    visual, assertions = assess([{"category": "overlap"}])
    assert visual["status"] == "unavailable"
    assert visual["reason"] == "visual_assessment_schema_invalid"
    assert visual["rejected_count"] == 1 and not assertions


def test_provider_schema_is_compact_but_keeps_the_strict_structural_contract():
    schema = _visual_document_schema()
    original = visual_finding_schema()
    expensive = {
        "minLength", "maxLength", "minimum", "maximum",
        "exclusiveMinimum", "exclusiveMaximum", "minItems", "maxItems",
        "default", "title",
    }

    def check(node):
        if isinstance(node, dict):
            assert not expensive.intersection(node)
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert node["required"] == list(node["properties"])
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(schema)
    findings = schema["properties"]["visual_assessment"]["properties"]["findings"]
    assert findings["type"] == "array"
    assert "maxItems" not in findings
    assert findings["items"]["properties"]["category"]["enum"] == original["properties"]["category"]["enum"]
    assert findings["items"]["properties"]["evidence"] == {"$ref": "#/$defs/_Evidence"}
    assert schema["$defs"]["_Evidence"]["properties"]["region"] == {"$ref": "#/$defs/_Region"}
    # Building a provider schema must never mutate the local validation schema.
    assert original["properties"]["expected"]["minLength"] == 12
    assert original["properties"]["confidence"]["maximum"] == 1
    assert original["$defs"]["_Region"]["properties"]["width"]["exclusiveMinimum"] == 0


def test_compact_provider_schema_does_not_relax_the_local_twelve_finding_cap():
    _visual_document_schema()
    visual, assertions = assess([deepcopy(FINDING) for _ in range(13)])
    assert visual["status"] == "unavailable"
    assert visual["reason"] == "visual_assessment_schema_invalid"
    assert visual["rejected_count"] == 13
    assert visual["accepted_count"] == 0
    assert not assertions
    # Exactly 12 remains admissible, with duplicate claims deduplicated locally.
    visual, assertions = assess([deepcopy(FINDING) for _ in range(12)])
    assert visual["status"] == "assessed"
    assert visual["accepted_count"] == len(assertions) == 1


def _missing_checkout_finding():
    finding = deepcopy(FINDING)
    finding.update(
        category="missing_expected_content",
        expected="The Checkout button should remain visible on the cart page.",
        observed="The Checkout button is missing from the cart page.",
    )
    finding["evidence"].update(
        element_label="Checkout", expected_excerpt="Checkout", expected_basis="visible_text",
    )
    return finding


def test_visible_hit_tested_control_contradicts_missing_claim_without_claiming_visual_pass():
    visual, assertions = assess_visual_findings(
        {"findings": [_missing_checkout_finding()]}, images=[IMAGE],
        model="fixture", reference_text="Your Cart Checkout",
        interactive_elements=[{
            "ref": "e4", "tag": "button", "name": "  Checkout  ",
            "in_viewport": True, "interactable": True,
        }],
    )
    assert visual["status"] == "assessed"
    assert visual["reason"] == "visual_findings_uncertain"
    assert visual["accepted_count"] == 0
    assert visual["uncertain_count"] == 1
    assert visual["findings"][0]["decision_reason"] == "missing_control_contradicted_by_browser_evidence"
    assert not assertions


@pytest.mark.parametrize("flags", [
    {"in_viewport": False, "interactable": True},
    {"in_viewport": True, "interactable": False},
    {"in_viewport": True, "interactable": True, "occluded": True},
    {"in_viewport": True, "interactable": True, "obstructed_by_overlay": True},
    {"in_viewport": True, "interactable": True, "aria_hidden": "true"},
    {},
])
def test_offscreen_occluded_or_unmeasured_dom_control_is_not_proof_of_visible_presence(flags):
    visual, _ = assess_visual_findings(
        {"findings": [_missing_checkout_finding()]}, images=[IMAGE],
        model="fixture", reference_text="Your Cart Checkout",
        interactive_elements=[{"name": "Checkout", **flags}],
    )
    assert visual["findings"][0]["decision_reason"] != "missing_control_contradicted_by_browser_evidence"


@pytest.mark.parametrize("category,basis", [
    ("image_content_mismatch", "visible_text"),
    ("clipped_content", "content_clipped"),
    ("unreadable_text", "visible_text"),
])
def test_control_presence_guard_does_not_hide_other_grounded_visual_deviations(category, basis):
    finding = deepcopy(FINDING)
    finding["category"] = category
    finding["evidence"]["expected_basis"] = basis
    visual, assertions = assess(
        [finding], interactive_elements=[{
            "name": "Sauce Labs Backpack", "in_viewport": True, "interactable": True,
        }],
    )
    assert visual["accepted_count"] == 1
    assert len(assertions) == 1


@pytest.mark.parametrize("field,value", [
    ("confidence", 1.01),
    ("confidence", -0.01),
    ("expected", "short"),
    ("expected", "x" * 1501),
    ("observed", "too short"),
    ("observed", "x" * 2001),
    ("evidence.page_url", "x" * 2001),
    ("evidence.element_label", "x" * 201),
    ("evidence.expected_excerpt", "x" * 1001),
    ("evidence.region.x", -0.01),
    ("evidence.region.y", 1.01),
    ("evidence.region.width", 0.0),
    ("evidence.region.height", 1.01),
])
def test_compact_provider_schema_does_not_relax_local_lengths_or_numeric_bounds(field, value):
    _visual_document_schema()
    finding = deepcopy(FINDING)
    keys = field.split(".")
    target = finding
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value
    visual, assertions = assess([finding])
    assert visual["status"] == "unavailable"
    assert visual["reason"] == "visual_assessment_schema_invalid"
    assert visual["rejected_count"] == 1
    assert not assertions
    assert visual["images"] == [IMAGE]


@pytest.mark.parametrize("images", [[], [{**IMAGE, "ref": "before"}]])
def test_before_only_or_missing_image_is_not_a_valid_review_of_current_result(images):
    visual, assertions = assess([], images=images)
    assert visual["status"] == "unavailable"
    assert visual["reason"] == "after_screenshot_not_available"
    assert visual["images"] == images and not assertions


def test_visual_summary_separates_local_pass_from_visual_unavailability():
    scenarios = [
        {"status": "PASS", "evidence": {"visual_assessment": {"status": "unavailable", "reason": "auxiliary_rate_limited"}}},
        {"status": "PASS", "evidence": {"visual_assessment": {"status": "unavailable", "reason": "visual_assessment_not_returned"}}},
        {"status": "PASS", "evidence": {"visual_assessment": {"status": "assessed", "findings": []}}},
        {"status": "PASS", "evidence": {"visual_assessment": {"status": "pending"}}},
        {"status": "PASS", "evidence": {}},
    ]
    assert visual_review_counts(scenarios) == {
        "total": 5, "assessed": 1, "pending": 1, "unavailable": 3,
        "rate_limited": 1, "incomplete_response": 1,
        "unavailable_reasons": [
            {"reason": "auxiliary_rate_limited", "count": 1, "label": "limitados pelo provedor"},
            {"reason": "reason_not_recorded", "count": 1, "label": "sem revisão visual válida; motivo não classificado"},
            {"reason": "visual_assessment_not_returned", "count": 1, "label": "com resposta visual ausente ou inválida"},
        ],
    }


def test_every_unavailable_visual_review_has_one_reason_including_tail_and_timeout():
    reasons = ["documentation_time_limit"] * 122 + ["TimeoutError"] * 7
    scenarios = [
        {"status": "PASS", "evidence": {"visual_assessment": {"status": "unavailable", "reason": reason}}}
        for reason in reasons
    ] + [{"status": "PASS", "evidence": {"visual_assessment": {"status": "assessed"}}}] * 17
    counts = visual_review_counts(scenarios)
    assert counts["total"] == 146 and counts["assessed"] == 17
    assert counts["unavailable"] == 129
    assert counts["rate_limited"] == counts["incomplete_response"] == 0
    assert sum(item["count"] for item in counts["unavailable_reasons"]) == 129
    assert counts["unavailable_reasons"] == [
        {"reason": "TimeoutError", "count": 7, "label": "com tempo de resposta da LLM auxiliar excedido"},
        {"reason": "documentation_time_limit", "count": 122, "label": "sem revisão antes do limite de espera das revisões auxiliares"},
    ]


def test_no_image_or_failed_action_cannot_support_visual_assertions():
    assert assess([FINDING], images=[])[1] == []
    assert assess([FINDING], action_succeeded=False)[1] == []


@pytest.mark.parametrize(
    ("label", "observed"),
    [
        ("AutoJev", "The floating panel obscures the application cart button."),
        ("Cart", "The AutoJev floating panel obscures the application cart button."),
        ("Cart", "The JEV status panel obscures the application cart button."),
        ("Carrinho", "O painel flutuante do JEV encobre o botão de carrinho."),
    ],
)
def test_our_status_overlay_cannot_become_an_application_visual_failure(
    label, observed
):
    finding = deepcopy(FINDING)
    finding.update(
        category="overlap",
        confidence=0.99,
        expected="The application cart button must remain visible.",
        observed=observed,
    )
    finding["evidence"].update(
        element_label=label,
        expected_basis="geometric_occlusion",
        expected_excerpt="",
    )

    assessment, assertions = assess([finding])

    assert assertions == []
    assert assessment["accepted_count"] == 0
    assert assessment["findings"][0]["decision_reason"] == (
        "autojev_overlay_is_not_application_evidence"
    )


def test_application_overlay_remains_eligible_for_visual_review():
    finding = deepcopy(FINDING)
    finding.update(
        category="overlap",
        expected="The application cart button must remain visible.",
        observed="The application promotion banner obscures the cart button.",
    )
    finding["evidence"].update(
        element_label="Cart",
        expected_basis="geometric_occlusion",
        expected_excerpt="",
    )
    assessment, assertions = assess([finding])
    assert assessment["accepted_count"] == 1 and len(assertions) == 1


def inputs():
    obs = Observation(
        url=IMAGE["url"],
        title="Products",
        text=TEXT,
        interactive_elements=[],
        screenshot="after.png",
    )
    decision = AgentDecision("click", "open product", target="e0")
    result = ActionResult(
        "click",
        True,
        utc_now(),
        utc_now(),
        obs.url,
        obs.url,
        target="e0",
        screenshot="after.png",
        assertions=[
            AssertionResult(
                "product_detail_identity",
                "Backpack",
                "Backpack",
                True,
                "Correct product opened",
            )
        ],
        evidence={
            "before_screenshot": "before.png",
            "before_text": TEXT,
            "visual_assessment": {"requested": True, "status": "pending"},
        },
    )
    return obs, decision, result


class FixtureClient:
    def __init__(self, payload=None):
        self.calls = []
        self.payload = (
            payload
            if payload is not None
            else {
                "title": "Check selected product",
                "visual_assessment": {"findings": [FINDING]},
            }
        )

    async def chat_json(self, model, system, content, **kwargs):
        self.calls.append((system, content))
        return deepcopy(self.payload)


async def test_generator_and_merge_use_browser_control_evidence_without_erasing_native_checks(tmp_path):
    _png(tmp_path / "after.png")
    client = FixtureClient({"visual_assessment": {"findings": [_missing_checkout_finding()]}})
    observation, decision, result = inputs()
    observation.text += " Checkout"
    observation.interactive_elements = [{
        "ref": "e4", "tag": "button", "name": "Checkout",
        "in_viewport": True, "interactable": True,
    }]
    ct = await OpenAIAuxiliaryScenarioGenerator(client, "fixture", tmp_path, "visual").create_scenario(
        observation, decision, result, 1,
    )
    submitted = json.loads(client.calls[0][1][0]["text"])
    assert submitted["page"]["interactive_elements"][0]["in_viewport"] is True
    assert submitted["page"]["interactive_elements"][0]["interactable"] is True
    assert ct.status == ScenarioStatus.PASS
    assert ct.evidence["visual_assessment"]["uncertain_count"] == 1
    assert ct.evidence["visual_assessment"]["findings"][0]["decision_reason"] == "missing_control_contradicted_by_browser_evidence"
    ExplorationManager._merge_scenario_enrichment(ct, observation, decision, result)
    assert ct.status == ScenarioStatus.PASS  # the existing native product check
    assert ct.evidence["visual_assessment"]["accepted_count"] == 0
    assert ct.evidence["visual_assessment"]["uncertain_count"] == 1
    assert not any(check.get("kind") == "visual_model" for check in ct.evidence["assertions"])
    assert ct.evidence["assertions"][0]["kind"] == "product_detail_identity"
    assert ct.evidence["assertions"][0]["passed"] is True


async def test_visual_response_contract_is_complete_and_omission_is_not_retried(tmp_path):
    _png(tmp_path / "after.png")

    class Client(FixtureClient):
        async def chat_json(self, model, system, content, **kwargs):
            schema = kwargs["response_schema"]
            assert schema["required"] == ["visual_assessment"]
            assert list(schema["properties"]) == ["visual_assessment"]
            assert schema["additionalProperties"] is False
            assert "visual_assessment" in system.split("Complete response contract:\n")[1]
            return await super().chat_json(model, system, content, **kwargs)

    client = Client({"title": "Documentation without a visual review"})
    obs, decision, result = inputs()
    scenario = await OpenAIAuxiliaryScenarioGenerator(client, "fixture", tmp_path, "visual").create_scenario(obs, decision, result, 1)
    assert len(client.calls) == 1
    assert scenario.status == ScenarioStatus.PASS  # local product assertion only
    assert scenario.evidence["visual_assessment"]["reason"] == "visual_assessment_not_returned"
    ExplorationManager._merge_scenario_enrichment(scenario, obs, decision, result)
    assert scenario.evidence["visual_assessment"]["status"] == "unavailable"
    assert scenario.evidence["visual_assessment"]["images"] == [IMAGE]
    assert not any(assertion["kind"] == "visual_model" for assertion in scenario.evidence["assertions"])


async def test_visual_findings_survive_enrichment_merge_and_appear_in_ct_timeline_and_report(
    tmp_path,
):
    _png(tmp_path / "before.png", "blue")
    _png(tmp_path / "after.png")
    client = FixtureClient()
    generator = OpenAIAuxiliaryScenarioGenerator(
        client, "fixture-vision", tmp_path, "visual"
    )
    obs, decision, result = inputs()
    ct = await generator.create_scenario(obs, decision, result, 1)
    assert len(client.calls[0][1]) == 3  # text + before + after, one provider call
    assert all(part["image_url"]["detail"] == "low" for part in client.calls[0][1][1:])
    ExplorationManager._merge_scenario_enrichment(ct, obs, decision, result)
    assert ct.status == ScenarioStatus.FAIL
    assert ct.evidence["visual_assessment"]["accepted_count"] == 1
    assert (
        result.assertions[0].passed is True and len(result.assertions) == 1
    )  # do not race/mutate browser work
    assert ct.evidence["assertions"][0]["kind"] == "product_detail_identity"
    assert any(
        entry.get("assertion", {}).get("kind") == "visual_model"
        and entry["screenshot"] == "after.png"
        for entry in ct.evidence["timeline"]
    )
    data = {
        "id": "run",
        "app_url": obs.url,
        "scenarios": [{**asdict(ct), "status": ct.status.value}],
    }
    presented = prepare_scenario_timelines(data)
    assert presented["scenarios"][0]["evidence"]["verdict"]["kind"] == "visual_failure"
    output = tmp_path / "report.html"
    StandaloneHtmlReport(tmp_path).generate(data, str(output))
    html = output.read_text()
    assert "FAIL · INDÍCIO VISUAL" in html
    assert (
        "Indício visual da LLM" in html and "não em um bug funcional confirmado" in html
    )


async def test_text_mode_sends_no_images_and_ignores_visual_claims(tmp_path):
    _png(tmp_path / "after.png")
    client = FixtureClient()
    obs, decision, result = inputs()
    result.evidence.pop("visual_assessment")
    ct = await OpenAIAuxiliaryScenarioGenerator(
        client, "fixture", tmp_path, "text"
    ).create_scenario(obs, decision, result, 1)
    assert isinstance(client.calls[0][1], str)
    assert ct.status == ScenarioStatus.PASS
    assert "visual_assessment" not in ct.evidence
    assert not any(a["kind"] == "visual_model" for a in ct.evidence["assertions"])


async def test_visual_mode_without_available_capture_is_explicitly_unavailable(
    tmp_path,
):
    obs, decision, result = inputs()
    ct = await OpenAIAuxiliaryScenarioGenerator(
        FixtureClient(), "fixture", tmp_path, "visual"
    ).create_scenario(obs, decision, result, 1)
    ExplorationManager._merge_scenario_enrichment(ct, obs, decision, result)
    assert ct.status == ScenarioStatus.PASS  # local product identity, NOT visual PASS
    assert ct.evidence["visual_assessment"]["status"] == "unavailable"


async def test_model_cannot_overwrite_business_failure_with_pass_status(tmp_path):
    _png(tmp_path / "after.png")
    obs, decision, result = inputs()
    result.assertions[0].passed = False
    client = FixtureClient(
        {
            "status": "PASS",
            "title": "Model says success",
            "visual_assessment": {"findings": []},
        }
    )
    ct = await OpenAIAuxiliaryScenarioGenerator(
        client, "fixture", tmp_path, "visual"
    ).create_scenario(obs, decision, result, 1)
    ct.status = (
        ScenarioStatus.PASS
    )  # even a buggy alternative adapter cannot erase the local check
    ExplorationManager._merge_scenario_enrichment(ct, obs, decision, result)
    assert (
        ct.status == ScenarioStatus.FAIL
        and ct.evidence["assertions"][0]["passed"] is False
    )


async def test_rate_limit_cooldown_uses_local_checks_without_more_provider_calls(
    tmp_path,
):
    _png(tmp_path / "after.png")
    class RateLimited:
        calls = 0

        async def chat_json(self, *_args, **_kwargs):
            self.calls += 1
            raise ProviderConnectionError(
                "Provider returned HTTP 429: too many requests"
            )

    client = RateLimited()
    generator = OpenAIAuxiliaryScenarioGenerator(client, "fixture", tmp_path, "visual")
    await generator.create_scenario(*inputs(), 1)
    ct = await generator.create_scenario(*inputs(), 2)
    assert client.calls == 1
    assert ct.status == ScenarioStatus.PASS
    assert ct.evidence["visual_review_provenance"]["model_input"] == "none"
    assert ct.evidence["visual_assessment"]["status"] == "unavailable"
    deadline = generator._cooldown_until
    await generator.create_scenario(*inputs(), 3)
    assert generator._cooldown_until == deadline  # not extended by skipped jobs


async def test_compact_visual_response_keeps_local_ct_documentation_and_checks(tmp_path):
    _png(tmp_path / "after.png")

    class Client(FixtureClient):
        async def chat_json(self, model, system, content, **kwargs):
            contract = json.loads(system.split("Complete response contract:\n")[1])
            assert contract == {"visual_assessment": {"findings": []}}
            assert "do NOT rewrite" in system
            assert kwargs["max_output_tokens"] == 3072
            assert kwargs["disable_reasoning"] is True
            return await super().chat_json(model, system, content, **kwargs)

    obs, decision, result = inputs()
    decision.scenario_hint = {"title": "Verify selected product"}
    client = Client({"visual_assessment": {"findings": []}})
    scenario = await OpenAIAuxiliaryScenarioGenerator(client, "fixture", tmp_path, "visual").create_scenario(obs, decision, result, 1)
    assert len(client.calls) == 1
    assert scenario.title == "Verify selected product"
    assert "Correct product opened" in scenario.expected_result
    assert "Confirmada: Correct product opened" in scenario.actual_result
    assert scenario.status == ScenarioStatus.PASS
    assert scenario.evidence["documentation_provenance"]["source"] == "local_evidence"
    assert scenario.evidence["documentation_provenance"]["provider_requests"] == 0
    assert scenario.evidence["visual_review_provenance"]["source"] == "auxiliary_llm"
    assert scenario.evidence["visual_review_provenance"]["response_contract"] == "visual_findings_v1"
    assert scenario.evidence["visual_assessment"]["status"] == "assessed"
    assert scenario.evidence["visual_assessment"]["accepted_count"] == 0


async def test_legacy_visual_reply_documentation_cannot_rewrite_browser_facts(tmp_path):
    _png(tmp_path / "after.png")
    obs, decision, result = inputs()
    decision.scenario_hint = {"title": "Observed product check"}
    result.assertions[0].passed = False
    payload = {
        "title": "Everything succeeded",
        "description": "Invented business approval",
        "steps": ["A step never performed"],
        "expected_result": "An invented requirement",
        "actual_result": "All checks passed",
        "visual_assessment": {"findings": [FINDING]},
    }
    client = FixtureClient(payload)
    scenario = await OpenAIAuxiliaryScenarioGenerator(client, "fixture", tmp_path, "visual").create_scenario(obs, decision, result, 1)
    ExplorationManager._merge_scenario_enrichment(scenario, obs, decision, result)
    assert scenario.title == "Observed product check"
    assert "Everything succeeded" not in scenario.description
    assert "A step never performed" not in scenario.steps
    assert "Não confirmada: Correct product opened" in scenario.actual_result
    assert scenario.status == ScenarioStatus.FAIL
    assert scenario.evidence["visual_assessment"]["accepted_count"] == 1
    assert scenario.evidence["assertions"][0]["passed"] is False


async def test_explicit_vision_rejection_is_remembered_without_repeated_billable_retries(
    tmp_path,
):
    class TextOnly(FixtureClient):
        async def chat_json(self, model, system, content, **kwargs):
            self.calls.append((system, content))
            if isinstance(content, list):
                raise ProviderConnectionError(
                    "HTTP 400: model does not support image input"
                )
            return {
                "title": "Local documented",
                "visual_assessment": {"findings": [FINDING]},
            }

    _png(tmp_path / "after.png")
    client = TextOnly()
    generator = OpenAIAuxiliaryScenarioGenerator(client, "fixture", tmp_path, "visual")
    await generator.create_scenario(*inputs(), 1)
    ct = await generator.create_scenario(*inputs(), 2)
    assert len(client.calls) == 1
    assert ct.evidence["visual_assessment"]["status"] == "unavailable"
    assert ct.evidence["visual_assessment"]["reason"] == "vision_input_not_supported"
    assert ct.status == ScenarioStatus.PASS


def test_cancelled_or_timed_out_visual_jobs_do_not_leave_pending_forever():
    class Repository:
        def __init__(self):
            self.scenarios, self.events = [], []

        def add_scenario(self, _run, _sequence, scenario):
            self.scenarios.append(scenario)

        def add_event(self, *args):
            self.events.append(args)

    repo = Repository()
    pipeline = _LiveScenarioPipeline(
        SimpleNamespace(repository=repo),
        "run",
        SimpleNamespace(model="fixture"),
        None,
        use_local_evidence=False,
    )
    obs, decision, result = inputs()
    ct = ExplorationManager._scenario_from_evidence(obs, decision, result, 1)
    pipeline.revisions["action"] = 2
    pipeline.sequences["action"] = 1
    pipeline.drafts["action"] = ct
    stale = _ScenarioEnrichmentJob("action", 1, obs, decision, result, 1)
    pipeline._mark_skipped(stale, "timeout")
    assert (
        not repo.scenarios and ct.evidence["visual_assessment"]["status"] == "pending"
    )
    current = _ScenarioEnrichmentJob("action", 2, obs, decision, result, 1)
    pipeline._mark_skipped(current, "exploration_cancelled")
    assert repo.scenarios[0].evidence["visual_assessment"]["status"] == "unavailable"
    assert (
        repo.scenarios[0].evidence["visual_assessment"]["reason"]
        == "exploration_cancelled"
    )
    assert repo.scenarios[0].status == ScenarioStatus.PASS


async def test_visual_merge_rejects_forged_screenshot_attribution(tmp_path):
    _png(tmp_path / "after.png")
    obs, decision, result = inputs()
    ct = await OpenAIAuxiliaryScenarioGenerator(
        FixtureClient(), "fixture", tmp_path, "visual"
    ).create_scenario(obs, decision, result, 1)
    assert ct.status == ScenarioStatus.FAIL
    ct.evidence["visual_assessment"]["images"][0]["screenshot"] = "other-test.png"
    ExplorationManager._merge_scenario_enrichment(ct, obs, decision, result)
    assert ct.status == ScenarioStatus.PASS  # only the local product check remains
    assert ct.evidence["visual_assessment"]["status"] == "unavailable"
