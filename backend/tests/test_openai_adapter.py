import pytest

from autojev.adapters.openai_compatible import (
    ModelResponseError,
    OpenAIAuxiliaryScenarioGenerator,
    OpenAICompatibleClient,
    OpenAIJevAgent,
    ProviderConnectionError,
    extract_json,
)
from autojev.domain.models import (
    ActionResult,
    AgentDecision,
    AssertionResult,
    Observation,
    ScenarioStatus,
    utc_now,
)


@pytest.mark.asyncio
async def test_list_models_uses_openrouter_model_name(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {
                        "id": "openai/gpt-4o",
                        "name": "GPT-4o",
                        "architecture": {
                            "input_modalities": ["text", "image"],
                            "output_modalities": ["text"],
                        },
                    }
                ]
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: FakeClient())
    models = await OpenAICompatibleClient(
        "https://openrouter.ai/api/v1", "sk-or-test"
    ).list_models()

    assert models == [
        {
            "id": "openai/gpt-4o",
            "label": "GPT-4o",
            "input_modalities": ["text", "image"],
            "output_modalities": ["text"],
            "supports_vision": True,
            "capabilities_known": True,
        }
    ]


@pytest.mark.asyncio
async def test_list_models_never_guesses_vision_from_model_name(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": "vendor/super-vision-model"}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: FakeClient())

    models = await OpenAICompatibleClient(
        "http://provider.example/v1", None
    ).list_models()

    assert models == [
        {
            "id": "vendor/super-vision-model",
            "label": "vendor/super-vision-model",
            "input_modalities": [],
            "output_modalities": [],
            "supports_vision": False,
            "capabilities_known": False,
        }
    ]


@pytest.mark.asyncio
async def test_openrouter_catalog_does_not_mask_rejected_api_key(monkeypatch):
    class FakeResponse:
        status_code = 401

        def raise_for_status(self):
            raise AssertionError("401 must be translated before raise_for_status")

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, **_kwargs):
            assert url == "https://openrouter.ai/api/v1/key"
            return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: FakeClient())
    client = OpenAICompatibleClient(
        "https://openrouter.ai/api/v1", "sk-or-invalid"
    )

    with pytest.raises(
        ProviderConnectionError, match="chave do OpenRouter foi recusada"
    ):
        await client.list_models()


@pytest.mark.asyncio
async def test_chat_json_captures_and_drains_provider_usage(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {
                    "prompt_tokens": 31,
                    "completion_tokens": 9,
                    "total_tokens": 40,
                    "cost": 0.00042,
                },
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: FakeClient())
    client = OpenAICompatibleClient("https://openrouter.ai/api/v1", "sk-or-test")
    assert await client.chat_json("openai/gpt-4o", "system", "user") == {"ok": True}
    assert client.drain_usage() == {
        "prompt_tokens": 31,
        "completion_tokens": 9,
        "total_tokens": 40,
        "cost": 0.00042,
        "cost_known": True,
    }
    assert client.drain_usage()["total_tokens"] == 0


@pytest.mark.asyncio
async def test_openrouter_jev_uses_native_decisions_endpoint_and_usage(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "answers": {
                    "next_move": {
                        "type": "choice",
                        "choice": "move_001",
                        "confidence": 1.0,
                    }
                },
                "usage": {
                    "input_tokens": 120,
                    "output_tokens": 16,
                    "cost": 0.00001,
                },
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["json"] = kwargs["json"]
            return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: FakeClient())
    client = OpenAICompatibleClient("https://openrouter.ai/api/v1", "sk-or-test")

    answers = await client.decisions(
        "typesafe/jev-1.13",
        {"page": "state"},
        {
            "next_move": {
                "type": "choice",
                "instructions": "Choose",
                "criteria": {"move_001": "Move", "move_finish": "Finish"},
            }
        },
    )

    assert captured["url"] == "https://openrouter.ai/api/alpha/decisions"
    assert captured["json"]["model"] == "typesafe/jev-1.13"
    assert answers["next_move"]["choice"] == "move_001"
    assert client.drain_usage() == {
        "prompt_tokens": 120,
        "completion_tokens": 16,
        "total_tokens": 136,
        "cost": 0.00001,
        "cost_known": True,
    }


@pytest.mark.asyncio
async def test_decisions_without_answers_is_a_recoverable_model_error(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "decision-without-answers"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: FakeClient())
    client = OpenAICompatibleClient("https://openrouter.ai/api/v1", "sk-or-test")

    with pytest.raises(ModelResponseError, match="no typed decisions"):
        await client.decisions(
            "typesafe/jev-1.13",
            {"page": "state"},
            {
                "next_move": {
                    "type": "choice",
                    "instructions": "Choose",
                    "criteria": {"move_finish": "Finish"},
                }
            },
        )


def test_extract_json_accepts_fenced_object_but_rejects_prose():
    assert extract_json('```json\n{"action":"finish"}\n```')["action"] == "finish"
    with pytest.raises(ModelResponseError):
        extract_json("there is no structured result")


def test_status_is_derived_from_executor_evidence():
    result = ActionResult(
        action="click",
        success=True,
        started_at=utc_now(),
        finished_at=utc_now(),
        before_url="https://example.test",
        after_url="https://example.test/dashboard",
        assertions=[
            AssertionResult("url_contains", "dashboard", "/dashboard", True, "Observed")
        ],
    )
    assert (
        OpenAIAuxiliaryScenarioGenerator.evidence_status(result) == ScenarioStatus.PASS
    )

    result.assertions[0].passed = False
    assert (
        OpenAIAuxiliaryScenarioGenerator.evidence_status(result) == ScenarioStatus.FAIL
    )

    result.assertions = []
    assert (
        OpenAIAuxiliaryScenarioGenerator.evidence_status(result)
        == ScenarioStatus.SKIPPED
    )

    result.success = False
    result.message = "PermissionError: PolicyBlocked: destructive click"
    assert (
        OpenAIAuxiliaryScenarioGenerator.evidence_status(result)
        == ScenarioStatus.BLOCKED
    )


@pytest.mark.asyncio
async def test_malformed_agent_json_becomes_recoverable_model_error():
    class FakeClient:
        async def chat_json(self, *args, **kwargs):
            return {"action": "click", "target": {}, "assertions": 42}

    agent = OpenAIJevAgent(FakeClient(), "model")
    observation = Observation(
        url="https://example.test",
        title="Page",
        text="Page",
        interactive_elements=[{"ref": "e0", "name": "Button"}],
    )
    with pytest.raises(ModelResponseError):
        await agent.decide(observation, [], [])


@pytest.mark.asyncio
async def test_typesafe_agent_only_sees_current_frontier_targets():
    class FakeClient:
        def __init__(self):
            self.state = None
            self.questions = None

        async def decisions(self, _model, state, questions):
            self.state = state
            self.questions = questions
            return {
                "next_move": {
                    "choice": "move_001",
                    "confidence": 0.91,
                    "probabilities": {"move_001": 0.95, "move_finish": 0.05},
                },
                "qa_focus": {"choice": "functional_behavior"},
                "issue_visible": {"noul": 0.08},
            }

    client = FakeClient()
    agent = OpenAIJevAgent(client, "typesafe/jev-1.13")
    observation = Observation(
        url="https://example.test",
        title="Page",
        text="Page",
        interactive_elements=[
            {"ref": "e0", "name": "Tried"},
            {"ref": "e1", "name": "Out of scope"},
            {
                "ref": "e2",
                "tag": "a",
                "role": "link",
                "name": "Next safe control",
                "href": "https://example.test/settings#users",
                "in_navigation": False,
                "in_viewport": True,
            },
        ],
    )

    decision = await agent.decide(
        observation,
        [],
        [],
        {"current_untried": [{"ref": "e2"}], "scope": "entire application"},
    )

    assert decision.target == "e2"
    assert decision.action == "click"
    assert decision.assertions == [
        {"kind": "url_contains", "expected": "/settings#users"}
    ]
    assert "choice confidence: 0.91" in decision.rationale
    assert set(client.questions) == {"next_move", "qa_focus", "issue_visible"}
    assert set(client.questions["next_move"]["criteria"]) == {"move_001"}
    assert client.state["candidate_moves"]["move_001"] == {
        "action": "click",
        "target": "e2",
        "value": None,
        "name": "Next safe control",
        "href": "https://example.test/settings#users",
    }
    assert "e0" not in str(client.state["candidate_moves"])
    assert "e1" not in str(client.state["candidate_moves"])


@pytest.mark.asyncio
async def test_typesafe_agent_only_offers_finish_at_an_exhausted_root():
    class FakeClient:
        async def decisions(self, _model, state, questions):
            assert set(questions["next_move"]["criteria"]) == {"move_finish"}
            assert set(state["candidate_moves"]) == {"move_finish"}
            return {
                "next_move": {"choice": "move_finish", "confidence": 1.0},
                "qa_focus": {"choice": "functional_behavior"},
                "issue_visible": {"noul": 0.0},
            }

    agent = OpenAIJevAgent(FakeClient(), "typesafe/jev-1.13")
    observation = Observation(
        url="https://example.test",
        title="Root",
        text="Root",
        interactive_elements=[],
    )

    decision = await agent.decide(
        observation,
        [],
        [],
        {
            "current_untried": [],
            "path_depth": 1,
            "frontier_candidates": 0,
            "scope": "entire application",
        },
    )

    assert decision.action == "finish"
    assert decision.target is None


@pytest.mark.asyncio
async def test_typesafe_agent_uses_atomic_backtrack_move_when_frontier_is_empty():
    class FakeClient:
        async def decisions(self, _model, _state, questions):
            assert set(questions["next_move"]["criteria"]) == {
                "move_back",
                "move_finish",
            }
            return {
                "next_move": {"choice": "move_back", "confidence": 1.0},
                "qa_focus": {"choice": "navigation_information_architecture"},
                "issue_visible": {"noul": 0.0},
            }

    agent = OpenAIJevAgent(FakeClient(), "typesafe/jev-1.13")
    observation = Observation(
        url="https://example.test/child",
        title="Child",
        text="Child",
        interactive_elements=[],
    )

    decision = await agent.decide(
        observation,
        [],
        [],
        {
            "current_untried": [],
            "path_depth": 2,
            "frontier_candidates": 1,
            "scope": "entire application",
        },
    )

    assert decision.action == "go_back"
    assert decision.target is None


@pytest.mark.asyncio
async def test_typesafe_agent_returns_prevalidated_fill_move_with_safe_value():
    class FakeClient:
        async def decisions(self, _model, state, _questions):
            assert state["candidate_moves"]["move_001"]["action"] == "fill"
            assert state["candidate_moves"]["move_001"]["value"] == "AutoJev QA"
            return {
                "next_move": {"choice": "move_001", "confidence": 0.84},
                "qa_focus": {"choice": "validation_error_handling"},
                "issue_visible": {"noul": 0.0},
            }

    agent = OpenAIJevAgent(FakeClient(), "typesafe/jev-1.13")
    observation = Observation(
        url="https://example.test/search",
        title="Search",
        text="Search",
        interactive_elements=[
            {
                "ref": "e4",
                "tag": "input",
                "type": "search",
                "name": "Search orders",
                "enabled": True,
            }
        ],
    )

    decision = await agent.decide(
        observation,
        [],
        [],
        {
            "scope": "entire application",
            "current_untried": [
                {
                    "ref": "e4",
                    "name": "Search orders",
                    "suggested_action": "fill",
                    "suggested_value": "AutoJev QA",
                }
            ],
        },
    )

    assert (decision.action, decision.target, decision.value) == (
        "fill",
        "e4",
        "AutoJev QA",
    )
    assert decision.scenario_hint["title"].startswith("Validation Error Handling")


@pytest.mark.asyncio
async def test_typesafe_agent_receives_distinct_menu_and_select_move_variants():
    class FakeClient:
        async def decisions(self, _model, state, questions):
            moves = state["candidate_moves"]
            assert [(move["action"], move["target"]) for move in moves.values()] == [
                ("hover", "e1"),
                ("click", "e1"),
                ("select", "e2"),
                ("select", "e2"),
            ]
            assert moves["move_003"]["option_label"] == "Plano gratuito"
            assert moves["move_004"]["option_label"] == "Plano profissional"
            assert (
                questions["next_move"]["criteria"]["move_004"]["safe_test_option"]
                == "Plano profissional"
            )
            return {
                "next_move": {"choice": "move_004", "confidence": 0.92},
                "qa_focus": {"choice": "state_consistency"},
                "issue_visible": {"noul": 0.0},
            }

    agent = OpenAIJevAgent(FakeClient(), "typesafe/jev-1.13")
    observed = Observation(
        url="https://example.test/settings",
        title="Settings",
        text="Settings",
        interactive_elements=[
            {
                "ref": "e1",
                "tag": "button",
                "role": "menuitem",
                "name": "Products",
                "aria_haspopup": "menu",
                "enabled": True,
            },
            {
                "ref": "e2",
                "tag": "select",
                "name": "Plan",
                "enabled": True,
            },
        ],
    )
    coverage = {
        "scope": "entire application",
        "current_untried": [
            {"ref": "e1", "suggested_action": "hover"},
            {"ref": "e1", "suggested_action": "click"},
            {
                "ref": "e2",
                "suggested_action": "select",
                "suggested_value": "plan_free_7f2",
                "option_label": "Plano gratuito",
            },
            {
                "ref": "e2",
                "suggested_action": "select",
                "suggested_value": "plan_pro_9ab",
                "option_label": "Plano profissional",
            },
        ],
    }

    decision = await agent.decide(observed, [], [], coverage)

    assert (decision.action, decision.target, decision.value) == (
        "select",
        "e2",
        "plan_pro_9ab",
    )
    assert "Plano profissional" in decision.rationale


@pytest.mark.asyncio
async def test_typesafe_agent_rejects_malformed_or_unknown_atomic_move():
    class FakeClient:
        async def decisions(self, *_args):
            return {"next_move": {"choice": {"not": "a move id"}}}

    agent = OpenAIJevAgent(FakeClient(), "typesafe/jev-1.13")
    observation = Observation(
        url="https://example.test",
        title="Page",
        text="Page",
        interactive_elements=[{"ref": "e0", "tag": "button", "name": "Open"}],
    )

    with pytest.raises(ModelResponseError):
        await agent.decide(
            observation,
            [],
            [],
            {
                "scope": "entire application",
                "current_untried": [{"ref": "e0", "suggested_action": "click"}],
            },
        )


def test_auxiliary_receives_masked_screenshot_as_multimodal_evidence(tmp_path):
    screenshot = tmp_path / "001.png"
    screenshot.write_bytes(b"masked-png")
    generator = OpenAIAuxiliaryScenarioGenerator(object(), "model", tmp_path)
    observation = Observation(
        url="https://example.test",
        title="Page",
        text="Page",
        interactive_elements=[],
        screenshot="001.png",
    )

    content = generator._multimodal_content(observation, "evidence")
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "evidence"}
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def _scenario_inputs(*, screenshot: str | None = None):
    observation = Observation(
        url="https://example.test/products",
        title="Products",
        text="Products page",
        interactive_elements=[],
        screenshot=screenshot,
    )
    decision = AgentDecision(
        action="click",
        target="e1",
        rationale="Exercise the product control",
        scenario_hint={"title": "Product control"},
    )
    result = ActionResult(
        action="click",
        success=True,
        started_at=utc_now(),
        finished_at=utc_now(),
        before_url=observation.url,
        after_url="https://example.test/products/1",
        message="Product details opened",
    )
    return observation, decision, result


@pytest.mark.asyncio
async def test_auxiliary_scenario_records_llm_documentation_provenance():
    class FakeClient:
        async def chat_json(self, *_args, **_kwargs):
            return {
                "title": "Open product details",
                "steps": ["Open the product"],
                "expected_result": "Details are shown",
                "actual_result": "Details were shown",
            }

    generator = OpenAIAuxiliaryScenarioGenerator(FakeClient(), "model")
    scenario = await generator.create_scenario(*_scenario_inputs(), 1)

    assert scenario.evidence["documentation_provenance"] == {
        "source": "auxiliary_llm",
        "reason": "auxiliary_response_accepted",
        "evidence_mode": "text",
        "model_input": "text",
    }


@pytest.mark.asyncio
async def test_auxiliary_text_mode_never_sends_a_captured_screenshot(tmp_path):
    screenshot = tmp_path / "001.png"
    screenshot.write_bytes(b"masked-png")

    class FakeClient:
        def __init__(self):
            self.contents = []

        async def chat_json(self, _model, _system, content, **_kwargs):
            self.contents.append(content)
            return {"title": "Text documentation", "steps": ["Open product"]}

    client = FakeClient()
    generator = OpenAIAuxiliaryScenarioGenerator(client, "model", tmp_path)
    observation, decision, result = _scenario_inputs(screenshot=screenshot.name)
    result.screenshot = screenshot.name
    scenario = await generator.create_scenario(observation, decision, result, 2)

    assert len(client.contents) == 1
    assert isinstance(client.contents[0], str)
    assert "data:image" not in client.contents[0]
    assert scenario.screenshots == [screenshot.name]
    assert scenario.evidence["documentation_provenance"]["model_input"] == "text"


@pytest.mark.asyncio
async def test_auxiliary_retries_text_only_after_explicit_vision_rejection(tmp_path):
    screenshot = tmp_path / "001.png"
    screenshot.write_bytes(b"masked-png")

    class FakeClient:
        def __init__(self):
            self.contents = []

        async def chat_json(self, _model, _system, content, **_kwargs):
            self.contents.append(content)
            if len(self.contents) == 1:
                raise ProviderConnectionError(
                    "Provider returned HTTP 400: this model does not support image input"
                )
            return {"title": "Text-only documentation", "steps": ["Open product"]}

    client = FakeClient()
    generator = OpenAIAuxiliaryScenarioGenerator(
        client, "model", tmp_path, evidence_mode="visual"
    )
    scenario = await generator.create_scenario(
        *_scenario_inputs(screenshot=screenshot.name), 2
    )

    assert len(client.contents) == 2
    assert isinstance(client.contents[0], list)
    assert isinstance(client.contents[1], str)
    assert scenario.evidence["documentation_provenance"] == {
        "source": "auxiliary_llm",
        "reason": "text_only_retry_after_explicit_vision_incompatibility",
        "evidence_mode": "visual",
        "model_input": "visual_then_text_fallback",
    }


@pytest.mark.asyncio
async def test_visual_mode_recognises_openrouter_text_only_endpoint_message(tmp_path):
    screenshot = tmp_path / "001.png"
    screenshot.write_bytes(b"masked-png")

    class FakeClient:
        def __init__(self):
            self.calls = 0

        async def chat_json(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ProviderConnectionError(
                    "Provider returned HTTP 404: No endpoints found that support image input"
                )
            return {"title": "Text fallback", "steps": ["Open product"]}

    client = FakeClient()
    generator = OpenAIAuxiliaryScenarioGenerator(
        client, "model", tmp_path, evidence_mode="visual"
    )
    scenario = await generator.create_scenario(
        *_scenario_inputs(screenshot=screenshot.name), 3
    )

    assert client.calls == 2
    assert scenario.evidence["documentation_provenance"]["reason"] == (
        "text_only_retry_after_explicit_vision_incompatibility"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_error", "fallback_reason"),
    [
        (
            "Provider returned HTTP 401: unauthorized; model does not support image input",
            "auxiliary_authentication_rejected",
        ),
        (
            "Provider returned HTTP 429: rate limit; image input is unsupported",
            "auxiliary_rate_limited",
        ),
        (
            "Provider request timed out while checking image input support",
            "auxiliary_timeout",
        ),
        (
            "Provider returned HTTP 400: invalid request",
            "auxiliary_provider_error",
        ),
    ],
)
async def test_auxiliary_does_not_retry_non_capability_failures_as_text(
    tmp_path, provider_error, fallback_reason
):
    screenshot = tmp_path / "001.png"
    screenshot.write_bytes(b"masked-png")

    class FakeClient:
        def __init__(self):
            self.calls = 0

        async def chat_json(self, *_args, **_kwargs):
            self.calls += 1
            raise ProviderConnectionError(provider_error)

    client = FakeClient()
    generator = OpenAIAuxiliaryScenarioGenerator(
        client, "model", tmp_path, evidence_mode="visual"
    )
    scenario = await generator.create_scenario(
        *_scenario_inputs(screenshot=screenshot.name), 3
    )

    assert client.calls == 1
    assert scenario.evidence["documentation_provenance"] == {
        "source": "local_fallback",
        "reason": fallback_reason,
        "evidence_mode": "visual",
        "model_input": "text_and_image",
    }


@pytest.mark.asyncio
async def test_auxiliary_enrichment_preserves_local_timeline_and_multiple_captures():
    import json

    from autojev.services.technical_signals import build_scenario_evidence

    class Client:
        async def chat_json(self, _model, _system, content, **_kwargs):
            prompt = json.loads(content)
            assert "timeline" not in prompt["verified_evidence"]
            assert prompt["technical_signals"] == []
            return {"title": "Documented action", "steps": ["Open product"], "status": "FAIL", "evidence": {"timeline": []}}

    observation, decision, result = _scenario_inputs(screenshot="after.png")
    result.screenshot = "after.png"
    result.evidence = {
        "action_step": 4,
        "before_screenshot": "before.png",
        "technical_signals": [{
            "occurrence_id": "signal-1", "classification": "external_telemetry",
            "label": "Telemetria externa", "message": "POST returned HTTP 401",
            "url": "https://events.backtrace.io/api/events", "affects_verdict": False,
        }],
    }
    expected = build_scenario_evidence(observation, decision, result)
    result.evidence = expected
    scenario = await OpenAIAuxiliaryScenarioGenerator(Client(), "model").create_scenario(
        observation, decision, result, 1,
    )
    assert scenario.screenshots == ["before.png", "after.png"]
    assert scenario.evidence["timeline"] == expected["timeline"]
    assert scenario.evidence["technical_signals"] == expected["technical_signals"]
    assert scenario.evidence["action_step"] == 4
    assert scenario.evidence["action_started_at"] == result.started_at
    assert scenario.evidence["documentation_provenance"]["source"] == "auxiliary_llm"
    assert scenario.status == OpenAIAuxiliaryScenarioGenerator.evidence_status(result)


@pytest.mark.asyncio
async def test_auxiliary_prompt_omits_repeated_signals_but_preserves_them_locally():
    import json

    class Client:
        async def chat_json(self, _model, _system, content, **_kwargs):
            assert json.loads(content)["technical_signals"] == []
            return {"title": "Documented action", "steps": ["Open product"]}

    observation, decision, result = _scenario_inputs()
    result.evidence = {"technical_signals": [{
        "occurrence_id": "signal-recurring", "classification": "application_http",
        "label": "Falha HTTP da aplicação", "message": "GET returned HTTP 500",
        "url": "https://example.test/api/products", "affects_verdict": True,
        "new_signature": False,
    }]}
    scenario = await OpenAIAuxiliaryScenarioGenerator(Client(), "model").create_scenario(
        observation, decision, result, 1,
    )
    assert scenario.evidence["technical_signals"][0]["occurrence_id"] == "signal-recurring"
    assert any(entry["kind"] == "signal" for entry in scenario.evidence["timeline"])
