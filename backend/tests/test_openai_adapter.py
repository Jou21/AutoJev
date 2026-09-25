import pytest

from autojev.adapters.openai_compatible import (
    ModelResponseError,
    OpenAIAuxiliaryScenarioGenerator,
    OpenAICompatibleClient,
    OpenAIJevAgent,
    extract_json,
)


@pytest.mark.asyncio
async def test_list_models_uses_openrouter_model_name(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": "openai/gpt-4o", "name": "GPT-4o"}]}

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

    assert models == [{"id": "openai/gpt-4o", "label": "GPT-4o"}]


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
from autojev.domain.models import (
    ActionResult,
    AssertionResult,
    Observation,
    ScenarioStatus,
    utc_now,
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
    assert OpenAIAuxiliaryScenarioGenerator.evidence_status(result) == ScenarioStatus.PASS

    result.assertions[0].passed = False
    assert OpenAIAuxiliaryScenarioGenerator.evidence_status(result) == ScenarioStatus.FAIL

    result.assertions = []
    assert OpenAIAuxiliaryScenarioGenerator.evidence_status(result) == ScenarioStatus.SKIPPED

    result.success = False
    result.message = "PermissionError: PolicyBlocked: destructive click"
    assert OpenAIAuxiliaryScenarioGenerator.evidence_status(result) == ScenarioStatus.BLOCKED


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
