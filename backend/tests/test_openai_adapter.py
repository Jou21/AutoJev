import httpx
import pytest
from PIL import Image

from autojev.adapters.openai_compatible import (
    DETERMINISTIC_AGENT_MODEL,
    ModelResponseError,
    OpenAIAuxiliaryScenarioGenerator,
    OpenAICompatibleClient,
    OpenAIJevAgent,
    ProviderConnectionError,
    _visual_document_schema,
    extract_json,
)


def _png(path, color="green", size=(64, 48)):
    Image.new("RGB", size, color).save(path, format="PNG")
from autojev.domain.models import (
    ActionResult,
    AgentDecision,
    AssertionResult,
    Observation,
    ScenarioStatus,
    utc_now,
)


@pytest.mark.parametrize("catalog", ["supported", "unknown", "unavailable"])
async def test_visual_strict_output_is_enabled_only_from_declared_openrouter_capability(monkeypatch, catalog):
    posts, gets = [], []

    def respond(request):
        import json
        if request.method == "GET":
            gets.append(request.url.path)
            return httpx.Response(503 if catalog == "unavailable" else 200, json={"data": [
                {"id": "fixture/vision", "supported_parameters": ["structured_outputs"] if catalog == "supported" else []}
            ]})
        posts.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"visual_assessment":{"findings":[]}}'}}]})

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=transport, **kwargs))
    client = OpenAICompatibleClient("https://openrouter.ai/api/v1", "fixture-only")
    schema = {"type": "object", "properties": {"visual_assessment": {"type": "object"}}, "required": ["visual_assessment"], "additionalProperties": False}
    for _ in range(2):
        await client.chat_json("fixture/vision", "system", "user", response_schema=schema)
    assert gets == ["/api/v1/models"]  # metadata cached; no key request/inference
    assert len(posts) == 2
    for payload in posts:
        assert payload["response_format"]["type"] == ("json_schema" if catalog == "supported" else "json_object")
        if catalog == "supported":
            assert payload["provider"] == {"require_parameters": True}
            assert payload["response_format"]["json_schema"]["schema"] == schema
            assert payload["response_format"]["json_schema"]["strict"] is True
        else:
            assert "provider" not in payload


@pytest.mark.parametrize("status,message,expected_calls", [
    (400, "unsupported response_format parameter", 2),
    (400, "invalid request", 1),
    (404, "unknown model", 1),
    (429, "rate limit", 1),
    (401, "unauthorized", 1),
    (503, "unsupported response_format temporary server error", 1),
])
async def test_chat_format_retry_requires_explicit_capability_rejection(monkeypatch, status, message, expected_calls):
    calls = []

    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(status, json={"error": {"message": message}}, headers={"Retry-After": "120"})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    client = OpenAICompatibleClient("http://fixture.test/v1", None)
    if expected_calls == 2:
        assert await client.chat_json("fixture", "system", "user") == {"ok": True}
    else:
        with pytest.raises(ProviderConnectionError) as caught:
            await client.chat_json("fixture", "system", "user")
        assert caught.value.retry_after_seconds == (120 if status == 429 else None)
    assert len(calls) == expected_calls


async def test_strict_visual_schema_rejection_does_not_retry_or_downgrade(monkeypatch):
    import json

    posts = []

    def respond(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{
                "id": "fixture/vision", "supported_parameters": ["structured_outputs"],
            }]})
        posts.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {
            "message": "This vision model does not support response_format json_schema",
        }})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    client = OpenAICompatibleClient("https://openrouter.ai/api/v1", "fixture-only")
    with pytest.raises(ProviderConnectionError):
        await client.chat_json("fixture/vision", "system", "user", response_schema={"type": "object"})
    assert len(posts) == 1
    assert posts[0]["response_format"]["type"] == "json_schema"


async def test_gemini_visual_transport_retains_strict_shape_without_expensive_decoding_constraints(monkeypatch):
    import json

    posts = []

    def respond(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{
                "id": "google/gemini-2.5-flash-lite",
                "supported_parameters": ["structured_outputs", "reasoning"],
                "reasoning": {"mandatory": False},
            }]})
        posts.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {
            "content": '{"visual_assessment":{"findings":[]}}',
        }}]})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    client = OpenAICompatibleClient("https://openrouter.ai/api/v1", "fixture-only")
    schema = _visual_document_schema()
    assert await client.chat_json(
        "google/gemini-2.5-flash-lite", "Review supplied images only", "fixture evidence",
        response_schema=schema, max_output_tokens=3072, disable_reasoning=True,
    ) == {"visual_assessment": {"findings": []}}
    assert len(posts) == 1
    payload = posts[0]
    assert payload["provider"] == {"require_parameters": True}
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"] == {
        "name": "autojev_visual_case", "strict": True, "schema": schema,
    }
    assert payload["max_tokens"] == 3072
    assert payload["reasoning"] == {"enabled": False, "exclude": True}


async def test_auxiliary_rate_limit_honors_retry_after_without_retry_burst(monkeypatch):
    now, calls = [10.0], []
    monkeypatch.setattr("autojev.adapters.openai_compatible.time.monotonic", lambda: now[0])

    class Client:
        async def chat_json(self, *_args, **_kwargs):
            calls.append(now[0])
            if len(calls) == 1:
                raise ProviderConnectionError("HTTP 429 rate limit", retry_after_seconds=120)
            return {"title": "Documentation"}

    generator = OpenAIAuxiliaryScenarioGenerator(Client(), "fixture")
    first = await generator.create_scenario(*_scenario_inputs(), 1)
    assert first.evidence["documentation_provenance"]["reason"] == "auxiliary_rate_limited"
    now[0] = 100.0
    await generator.create_scenario(*_scenario_inputs(), 2)
    assert calls == [10.0]
    now[0] = 131.0
    resumed = await generator.create_scenario(*_scenario_inputs(), 3)
    assert calls == [10.0, 131.0]
    assert resumed.evidence["documentation_provenance"]["source"] == "auxiliary_llm"


async def test_daily_quota_is_non_retryable_and_reset_header_is_respected(monkeypatch):
    from autojev.adapters.openai_compatible import _retry_after_seconds
    monkeypatch.setattr("autojev.adapters.openai_compatible.time.time", lambda: 100.0)
    response = httpx.Response(429, headers={"X-RateLimit-Reset": "140"})
    assert _retry_after_seconds(response) == 40
    response = httpx.Response(429, headers={"Retry-After": "50", "X-RateLimit-Reset": "140"})
    assert _retry_after_seconds(response) == 50

    class Client:
        calls = 0
        async def chat_json(self, *_args, **_kwargs):
            self.calls += 1
            raise ProviderConnectionError("HTTP 429 Rate limit exceeded: free-models-per-day")

    client = Client()
    generator = OpenAIAuxiliaryScenarioGenerator(client, "fixture:free")
    first = await generator.create_scenario(*_scenario_inputs(), 1)
    second = await generator.create_scenario(*_scenario_inputs(), 2)
    assert first.evidence["documentation_provenance"]["reason"] == "auxiliary_rate_limited_daily_quota"
    assert second.evidence["documentation_provenance"]["model_input"] == "none"
    assert generator.non_retryable_reason == "auxiliary_rate_limited_daily_quota"
    assert client.calls == 1


@pytest.mark.parametrize("budget,expected_calls,expected_source", [(1, 1, "local_fallback"), (2, 2, "auxiliary_llm")])
async def test_auxiliary_transport_budget_also_bounds_explicit_format_retry(monkeypatch, budget, expected_calls, expected_source):
    calls = []
    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(400, json={"error": {"message": "unsupported response_format parameter"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"title":"Documented"}'}}]})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    client = OpenAICompatibleClient("http://fixture.test/v1", None)
    client.chat_request_budget = budget
    generator = OpenAIAuxiliaryScenarioGenerator(client, "fixture")
    scenario = await generator.create_scenario(*_scenario_inputs(), 1)
    assert len(calls) == expected_calls
    assert generator.request_count == expected_calls == client.chat_request_count
    assert scenario.evidence["documentation_provenance"]["source"] == expected_source
    if budget == 1:
        assert scenario.evidence["documentation_provenance"]["reason"] == "auxiliary_request_budget_exhausted"


async def test_visual_cache_requires_identical_pixels_and_semantics_and_rebinds_capture_ids(tmp_path):
    for name in ("first.png", "same.png"):
        _png(tmp_path / name)
    _png(tmp_path / "different.png", color="blue")

    class Client:
        calls = 0
        async def chat_json(self, *_args, **_kwargs):
            self.calls += 1
            return {"title": "Visual review", "visual_assessment": {"findings": []}}

    client = Client()
    generator = OpenAIAuxiliaryScenarioGenerator(client, "fixture", tmp_path, "visual")
    first = await generator.create_scenario(*_scenario_inputs(screenshot="first.png"), 1)
    cached = await generator.create_scenario(*_scenario_inputs(screenshot="same.png"), 2)
    assert client.calls == 1
    assert first.evidence["visual_assessment"]["status"] == "assessed"
    assert cached.evidence["documentation_provenance"]["source"] == "local_evidence"
    assert cached.evidence["visual_review_provenance"]["source"] == "auxiliary_cache"
    assert cached.evidence["visual_assessment"]["reused_identical_evidence"] is True
    assert cached.evidence["visual_assessment"]["images"][0]["screenshot"] == "same.png"
    await generator.create_scenario(*_scenario_inputs(screenshot="different.png"), 3)
    observation, decision, result = _scenario_inputs(screenshot="same.png")
    observation.text = "Different product identity"
    await generator.create_scenario(observation, decision, result, 4)
    assert client.calls == 3


async def test_invalid_visual_response_is_never_cached(tmp_path):
    _png(tmp_path / "image.png")

    class Client:
        calls = 0
        async def chat_json(self, *_args, **_kwargs):
            self.calls += 1
            return {"title": "Review", "visual_assessment": {"findings": [{}]}}

    client = Client()
    generator = OpenAIAuxiliaryScenarioGenerator(client, "fixture", tmp_path, "visual")
    for number in range(2):
        scenario = await generator.create_scenario(*_scenario_inputs(screenshot="image.png"), number)
        assert scenario.evidence["visual_assessment"]["status"] == "unavailable"
    assert client.calls == 2


async def test_cache_only_miss_never_claims_requests_or_changes_cooldown(tmp_path):
    _png(tmp_path / "image.png")

    class Client:
        chat_request_count = 4
        chat_request_budget = 4

        async def chat_json(self, *_args, **_kwargs):
            pytest.fail("A cache-only lookup must never call the provider")

    client = Client()
    generator = OpenAIAuxiliaryScenarioGenerator(client, "fixture", tmp_path, "visual")
    generator.request_count = 4
    generator.rate_limit_count = 2
    generator._cooldown_until = float("inf")
    generator._cooldown_reason = "HTTP 401 unauthorized"
    generator.non_retryable_reason = "auxiliary_authentication_rejected"
    before = (generator.request_count, generator.rate_limit_count, generator._cooldown_until, generator._cooldown_reason, generator.non_retryable_reason)
    assert await generator.cached_scenario(*_scenario_inputs(screenshot="image.png"), 1) is None
    assert before == (generator.request_count, generator.rate_limit_count, generator._cooldown_until, generator._cooldown_reason, generator.non_retryable_reason)
    assert client.chat_request_count == 4


async def test_cache_only_hit_reuses_exact_review_after_transport_budget_exhaustion(monkeypatch, tmp_path):
    for name in ("first.png", "same.png"):
        _png(tmp_path / name)
    _png(tmp_path / "different.png", color="blue")
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {
            "content": '{"visual_assessment":{"findings":[]}}',
        }}]})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    client = OpenAICompatibleClient("http://fixture.test/v1", None)
    client.chat_request_budget = 1
    generator = OpenAIAuxiliaryScenarioGenerator(client, "fixture", tmp_path, "visual")
    first = await generator.create_scenario(*_scenario_inputs(screenshot="first.png"), 1)
    assert first.evidence["visual_assessment"]["status"] == "assessed"
    assert client.chat_request_count == generator.request_count == 1
    generator._vision_rejected = True  # A later rejection must not invalidate saved exact evidence.
    generator._cooldown_until = float("inf")
    generator.non_retryable_reason = "auxiliary_authentication_rejected"
    cached = await generator.cached_scenario(*_scenario_inputs(screenshot="same.png"), 9)
    assert cached is not None and cached.id == "CT009"
    assert cached.evidence["visual_review_provenance"]["source"] == "auxiliary_cache"
    assert cached.evidence["visual_assessment"]["status"] == "assessed"
    assert cached.evidence["visual_assessment"]["images"][0]["screenshot"] == "same.png"
    assert await generator.cached_scenario(*_scenario_inputs(screenshot="different.png"), 10) is None
    observation, decision, result = _scenario_inputs(screenshot="same.png")
    observation.text = "Different product identity"
    assert await generator.cached_scenario(observation, decision, result, 11) is None
    assert len(requests) == client.chat_request_count == generator.request_count == 1
    assert generator._cooldown_until == float("inf")
    assert generator.non_retryable_reason == "auxiliary_authentication_rejected"


@pytest.mark.parametrize("malformed", [{}, {"visual_assessment": {"findings": [{}]}}])
async def test_cache_only_does_not_accept_malformed_saved_review(tmp_path, malformed):
    _png(tmp_path / "image.png")

    class Client:
        calls = 0

        async def chat_json(self, *_args, **_kwargs):
            self.calls += 1
            return {"visual_assessment": {"findings": []}}

    client = Client()
    generator = OpenAIAuxiliaryScenarioGenerator(client, "fixture", tmp_path, "visual")
    await generator.create_scenario(*_scenario_inputs(screenshot="image.png"), 1)
    key = next(iter(generator._response_cache))
    generator._response_cache[key] = malformed
    assert await generator.cached_scenario(*_scenario_inputs(screenshot="image.png"), 2) is None
    assert client.calls == generator.request_count == 1


@pytest.mark.parametrize("provider_error,reason", [
    ("Provider returned HTTP 503: temporarily unavailable", "auxiliary_provider_temporary_error"),
    ("Provider returned HTTP 504: gateway timeout", "auxiliary_timeout"),
    ("Provider returned HTTP 408: request expired", "auxiliary_provider_temporary_error"),
    ("Provider returned HTTP 400: invalid input", "auxiliary_provider_error"),
])
async def test_temporary_provider_failures_have_explicit_retry_reason(tmp_path, provider_error, reason):
    _png(tmp_path / "image.png")

    class Client:
        async def chat_json(self, *_args, **_kwargs):
            raise ProviderConnectionError(provider_error)

    generator = OpenAIAuxiliaryScenarioGenerator(Client(), "fixture", tmp_path, "visual")
    scenario = await generator.create_scenario(*_scenario_inputs(screenshot="image.png"), 1)
    assert scenario.evidence["visual_assessment"]["reason"] == reason


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
        "cost_complete": True,
        "usage_records": 1,
        "cost_reported_records": 1,
    }
    assert client.drain_usage()["total_tokens"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "body_cost", "expected_cost", "known"),
    [
        ({"x-litellm-response-cost": "0.00123"}, None, 0.00123, True),
        ({"x-litellm-response-cost": "0.00123"}, 0.5, 0.5, True),
        ({"x-litellm-response-cost": ""}, None, 0.0, False),
        ({"x-litellm-response-cost": "abc"}, None, 0.0, False),
        ({}, None, 0.0, False),
    ],
)
async def test_chat_json_reads_litellm_cost_header(monkeypatch, headers, body_cost, expected_cost, known):
    usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
    if body_cost is not None:
        usage["cost"] = body_cost

    def handler(request):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": true}'}}], "usage": usage},
            headers=headers,
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    client = OpenAICompatibleClient("https://litellm.example.test", "sk-test")
    assert await client.chat_json("gpt-x", "system", "user") == {"ok": True}
    drained = client.drain_usage()
    assert drained["cost"] == pytest.approx(expected_cost)
    assert drained["cost_known"] is known
    assert drained["cost_complete"] is known


@pytest.mark.asyncio
@pytest.mark.asyncio
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
async def test_planner_agent_only_sees_current_frontier_targets():
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
    agent = OpenAIJevAgent(client, DETERMINISTIC_AGENT_MODEL)
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
    assert "confiança na escolha: 0.91" in decision.rationale
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
@pytest.mark.parametrize("info_status", [200, 404])
async def test_litellm_model_info_declares_vision_capability(monkeypatch, info_status):
    calls = []

    class FakeResponse:
        def __init__(self, url):
            self.url = url
            self.status_code = info_status if url.endswith("/model/info") else 200

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("nope", request=None, response=self)

        def json(self):
            if self.url.endswith("/model/info"):
                return {
                    "data": [
                        {"model_name": "claude-sonnet", "model_info": {"supports_vision": True, "mode": "chat"}},
                        {"model_name": "claude-sonnet", "model_info": {"supports_vision": False}},
                        {"model_name": "titan-embed", "model_info": {"supports_vision": False, "mode": "embedding"}},
                        {"model_name": "unknown", "model_info": {"supports_vision": None}},
                    ]
                }
            return {"data": [{"id": "claude-sonnet"}, {"id": "titan-embed"}, {"id": "unknown"}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, **kwargs):
            calls.append(url)
            return FakeResponse(url)

    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: FakeClient())
    client = OpenAICompatibleClient("https://litellm.example.test", "sk-test")

    models = {model["id"]: model for model in await client.list_models()}

    assert "https://litellm.example.test/model/info" in calls
    if info_status == 200:
        assert models["claude-sonnet"]["supports_vision"] is True
        assert models["claude-sonnet"]["capabilities_known"] is True
        assert models["titan-embed"]["supports_vision"] is False
        assert models["titan-embed"]["capabilities_known"] is True
        assert models["unknown"]["capabilities_known"] is False  # null is not "no vision"
    else:  # a gateway without /model/info keeps the plain catalog
        assert all(not model["capabilities_known"] for model in models.values())


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_planner_agent_only_offers_finish_at_an_exhausted_root():
    class FakeClient:
        async def decisions(self, _model, state, questions):
            assert set(questions["next_move"]["criteria"]) == {"move_finish"}
            assert set(state["candidate_moves"]) == {"move_finish"}
            return {
                "next_move": {"choice": "move_finish", "confidence": 1.0},
                "qa_focus": {"choice": "functional_behavior"},
                "issue_visible": {"noul": 0.0},
            }

    agent = OpenAIJevAgent(FakeClient(), DETERMINISTIC_AGENT_MODEL)
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
async def test_planner_agent_uses_atomic_backtrack_move_when_frontier_is_empty():
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

    agent = OpenAIJevAgent(FakeClient(), DETERMINISTIC_AGENT_MODEL)
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
async def test_planner_agent_returns_prevalidated_fill_move_with_safe_value():
    class FakeClient:
        async def decisions(self, _model, state, _questions):
            assert state["candidate_moves"]["move_001"]["action"] == "fill"
            assert state["candidate_moves"]["move_001"]["value"] == "AutoJev QA"
            return {
                "next_move": {"choice": "move_001", "confidence": 0.84},
                "qa_focus": {"choice": "validation_error_handling"},
                "issue_visible": {"noul": 0.0},
            }

    agent = OpenAIJevAgent(FakeClient(), DETERMINISTIC_AGENT_MODEL)
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
    assert decision.scenario_hint["title"] == "Validação e tratamento de erros: Search orders"


@pytest.mark.asyncio
async def test_planner_agent_receives_distinct_menu_and_select_move_variants():
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

    agent = OpenAIJevAgent(FakeClient(), DETERMINISTIC_AGENT_MODEL)
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
async def test_planner_agent_rejects_malformed_or_unknown_atomic_move():
    class FakeClient:
        async def decisions(self, *_args):
            return {"next_move": {"choice": {"not": "a move id"}}}

    agent = OpenAIJevAgent(FakeClient(), DETERMINISTIC_AGENT_MODEL)
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
    _png(screenshot)
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
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


@pytest.mark.parametrize("size,expected", [
    ((1440, 900), (768, 480)),
    ((900, 1440), (480, 768)),
    ((320, 200), (320, 200)),
])
def test_auxiliary_preview_really_downscales_without_overwriting_original(tmp_path, size, expected):
    import base64
    from io import BytesIO

    screenshot = tmp_path / "masked.png"
    Image.new("RGBA", size, (255, 0, 0, 0)).save(screenshot, format="PNG")
    original = screenshot.read_bytes()
    generator = OpenAIAuxiliaryScenarioGenerator(object(), "model", tmp_path)
    observation, _, _ = _scenario_inputs(screenshot=screenshot.name)
    content = generator._multimodal_content(observation, "evidence")
    assert isinstance(content, list)
    url = content[1]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    with Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1]))) as preview:
        assert preview.size == expected
        assert preview.mode == "RGB"
        assert preview.getpixel((0, 0)) == (255, 255, 255)
    assert screenshot.read_bytes() == original
    with Image.open(screenshot) as saved:
        assert saved.size == size and saved.mode == "RGBA"


def test_invalid_image_is_not_sent_as_visual_evidence(tmp_path):
    screenshot = tmp_path / "invalid.png"
    screenshot.write_bytes(b"not an actual PNG")
    generator = OpenAIAuxiliaryScenarioGenerator(object(), "model", tmp_path)
    observation, _, _ = _scenario_inputs(screenshot=screenshot.name)
    assert generator._multimodal_content(observation, "evidence") == "evidence"


async def test_visual_cache_does_not_merge_originals_with_identical_lossy_previews(tmp_path):
    from PIL.PngImagePlugin import PngInfo

    for name in ("first.png", "second.png"):
        metadata = PngInfo()
        metadata.add_text("capture", name)
        Image.new("RGB", (64, 48), "green").save(tmp_path / name, pnginfo=metadata)

    class Client:
        calls = 0
        async def chat_json(self, *_args, **_kwargs):
            self.calls += 1
            return {"title": "Review", "visual_assessment": {"findings": []}}

    client = Client()
    generator = OpenAIAuxiliaryScenarioGenerator(client, "fixture", tmp_path, "visual")
    first_content = generator._multimodal_content(_scenario_inputs(screenshot="first.png")[0], "evidence")
    second_content = generator._multimodal_content(_scenario_inputs(screenshot="second.png")[0], "evidence")
    assert first_content == second_content  # metadata stripped from the JPEG
    await generator.create_scenario(*_scenario_inputs(screenshot="first.png"), 1)
    await generator.create_scenario(*_scenario_inputs(screenshot="second.png"), 2)
    assert client.calls == 2  # original bytes differ: no approximate cache reuse


@pytest.mark.parametrize("base_url,parameters,reasoning,expected", [
    ("https://openrouter.ai/api/v1", ["reasoning"], None, {"enabled": False, "exclude": True}),
    ("https://openrouter.ai/api/v1", [], {"mandatory": False}, {"enabled": False, "exclude": True}),
    ("https://openrouter.ai/api/v1", ["reasoning"], {"mandatory": True, "supported_efforts": ["high", "minimal"]}, {"effort": "minimal", "exclude": True}),
    ("https://openrouter.ai/api/v1", ["reasoning"], {"mandatory": True, "supported_efforts": ["low", "high"]}, {"effort": "low", "exclude": True}),
    ("https://openrouter.ai/api/v1", ["reasoning"], {"mandatory": True}, None),
    ("https://openrouter.ai/api/v1", [], None, None),
    ("http://fixture.test/v1", ["reasoning"], None, None),
])
async def test_auxiliary_bounds_output_and_declared_reasoning_without_changing_jev(
    monkeypatch, base_url, parameters, reasoning, expected,
):
    import json

    gets, posts = [], []
    model = {"id": "fixture/model", "supported_parameters": parameters}
    if reasoning is not None:
        model["reasoning"] = reasoning

    def respond(request):
        if request.method == "GET":
            gets.append(request.url.path)
            assert "authorization" not in request.headers
            return httpx.Response(200, json={"data": [model]})
        posts.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"title":"Documented"}'}}]})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    client = OpenAICompatibleClient(base_url, "fixture-only")
    await client.chat_json("fixture/model", "JEV", "observation")
    assert "max_tokens" not in posts[0] and "reasoning" not in posts[0]
    generator = OpenAIAuxiliaryScenarioGenerator(client, "fixture/model")
    await generator._document_request("Documentation", "{}")
    await generator._document_request("Documentation", "{}")
    for payload in posts[1:]:
        assert payload["max_tokens"] == 3072
        assert payload.get("reasoning") == expected
    assert gets == (["/api/v1/models"] if "openrouter.ai" in base_url else [])
    assert len(posts) == 3 and generator.request_count == 2


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
        "provider_requests": 1,
        "cache_key": None,
    }


@pytest.mark.asyncio
async def test_auxiliary_text_mode_never_sends_a_captured_screenshot(tmp_path):
    screenshot = tmp_path / "001.png"
    _png(screenshot)

    class FakeClient:
        def __init__(self):
            self.contents = []

        async def chat_json(self, _model, _system, content, **_kwargs):
            self.contents.append(content)
            assert "Brazilian Portuguese (pt-BR)" in _system
            assert "technical error messages in their original form" in _system
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
async def test_visual_review_does_not_spend_text_retry_after_explicit_vision_rejection(tmp_path):
    screenshot = tmp_path / "001.png"
    _png(screenshot)

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

    assert len(client.contents) == 1
    assert isinstance(client.contents[0], list)
    assert scenario.title == "Product control"
    assert scenario.evidence["documentation_provenance"]["source"] == "local_evidence"
    assert scenario.evidence["visual_review_provenance"] == {
        "source": "local_fallback",
        "reason": "vision_input_not_supported",
        "evidence_mode": "visual",
        "model_input": "text_and_image",
        "provider_requests": 1,
        "cache_key": None,
        "response_contract": "visual_findings_v1",
    }
    assert scenario.evidence["visual_assessment"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_visual_mode_recognises_openrouter_text_only_endpoint_message(tmp_path):
    screenshot = tmp_path / "001.png"
    _png(screenshot)

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

    assert client.calls == 1
    assert scenario.evidence["visual_review_provenance"]["reason"] == "vision_input_not_supported"


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
        (
            "Provider returned HTTP 400: This vision model does not support response_format json_schema",
            "auxiliary_provider_error",
        ),
        (
            "Provider returned HTTP 422: This multimodal model does not support structured_outputs",
            "auxiliary_provider_error",
        ),
    ],
)
async def test_auxiliary_does_not_retry_non_capability_failures_as_text(
    tmp_path, provider_error, fallback_reason
):
    screenshot = tmp_path / "001.png"
    _png(screenshot)

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
    assert scenario.evidence["documentation_provenance"]["source"] == "local_evidence"
    assert scenario.evidence["visual_review_provenance"] == {
        "source": "local_fallback",
        "reason": fallback_reason,
        "evidence_mode": "visual",
        "model_input": "text_and_image",
        "provider_requests": 1,
        "cache_key": None,
        "response_contract": "visual_findings_v1",
    }


async def test_visual_mode_does_not_send_before_only_image_payload(tmp_path):
    _png(tmp_path / "before.png")

    class Client:
        def __init__(self):
            self.contents = []

        async def chat_json(self, _model, _system, content, **kwargs):
            self.contents.append(content)
            assert "response_schema" not in kwargs
            return {"title": "Local cart verification", "steps": ["Open cart"]}

    client = Client()
    generator = OpenAIAuxiliaryScenarioGenerator(client, "fixture", tmp_path, evidence_mode="visual")
    observation, decision, result = _scenario_inputs(screenshot="missing-after.png")
    result.evidence = {"before_screenshot": "before.png", "before_url": result.before_url}
    scenario = await generator.create_scenario(observation, decision, result, 1)
    assert client.contents == []
    assert scenario.evidence["visual_review_provenance"]["provider_requests"] == 0
    assert scenario.evidence["visual_review_provenance"]["reason"] == "screenshot_not_available"
    assert scenario.evidence["visual_assessment"]["status"] == "unavailable"
    assert scenario.evidence["visual_assessment"]["images"] == []
    assert "before.png" in scenario.screenshots


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


@pytest.mark.asyncio
async def test_deterministic_agent_walks_the_planner_frontier_in_order_without_a_model():
    from autojev.adapters.openai_compatible import (
        DETERMINISTIC_AGENT_MODEL,
        DeterministicDecisionClient,
    )
    from autojev.services.report_localization import report_text

    client = DeterministicDecisionClient()
    agent = OpenAIJevAgent(client, DETERMINISTIC_AGENT_MODEL)
    observation = Observation(
        url="https://shop.test/inventory.html", title="Products", text="Products",
        interactive_elements=[
            {"ref": "e1", "tag": "button", "name": "Add to cart", "in_viewport": True},
            {"ref": "e2", "tag": "a", "name": "Cart", "href": "https://shop.test/cart.html", "in_viewport": True},
        ],
    )

    first = await agent.decide(observation, [], [], {"current_untried": [{"ref": "e2"}, {"ref": "e1"}]})
    assert (first.action, first.target) == ("click", "e2")  # the planner's first priority, not the DOM order
    assert "planejador determinístico do AutoJev" in report_text(first.rationale)
    assert {"kind": "url_contains", "expected": "/cart.html"} in first.assertions

    done = await agent.decide(observation, [], [], {"current_untried": [], "path_depth": 1})
    assert done.action == "finish"
    assert client.drain_usage()["cost_known"] is True and client.drain_usage()["cost"] == 0.0
