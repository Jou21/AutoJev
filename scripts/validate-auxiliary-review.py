"""One real auxiliary review of already persisted evidence, without changing a CT.

The browser result/assertions/screenshots are original. Page text is rebuilt
only from persisted assertion values, not claimed to be a complete DOM replay.
"""
import argparse
import asyncio
import json
import sys
import time
from dataclasses import fields
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from autojev.adapters.openai_compatible import OpenAIAuxiliaryScenarioGenerator, OpenAICompatibleClient
from autojev.domain.models import ActionResult, AgentDecision, AssertionResult, Observation


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("--auxiliary-model", default="google/gemini-2.5-flash-lite")
    args = parser.parse_args()
    async with httpx.AsyncClient(timeout=30) as api:
        response = await api.get(f"http://localhost:8000/api/explorations/{args.run_id}")
        response.raise_for_status()
        run = response.json()
    action = next(a for a in run["actions"] if any(
        c.get("kind") == "order_confirmation" and c.get("passed")
        for c in a["result"].get("assertions", [])
    ) and any(c.get("kind") == "checkout_items_match_cart" and c.get("expected")
              for c in a["result"].get("assertions", [])))
    result_data = dict(action["result"])
    result_data["assertions"] = [AssertionResult(**a) for a in result_data["assertions"]]
    result = ActionResult(**{f.name: result_data[f.name] for f in fields(ActionResult) if f.name in result_data})
    decision = AgentDecision(**{f.name: action["decision"][f.name] for f in fields(AgentDecision) if f.name in action["decision"]})
    observation = Observation(url=result.after_url, title="Swag Labs", text=json.dumps(
        [a.actual for a in result.assertions], ensure_ascii=False), interactive_elements=[], screenshot=result.screenshot)
    key = dotenv_values(ROOT / "backend/.env").get("OPEN_ROUTER")
    if not key:
        raise RuntimeError("Authorized key missing")
    client = OpenAICompatibleClient("https://openrouter.ai/api/v1", key, 45)
    client.chat_request_budget = 2  # Includes at most one explicit format compatibility retry.
    generator = OpenAIAuxiliaryScenarioGenerator(client, args.auxiliary_model,
        ROOT / "data/artifacts" / run["id"] / "screenshots", "visual")
    started = time.monotonic()
    scenario = await asyncio.wait_for(generator.create_scenario(observation, decision, result, 1), timeout=50)
    visual = scenario.evidence.get("visual_assessment", {})
    measurements = {"source": "actual OpenAIAuxiliaryScenarioGenerator", "scope": "One persisted populated-purchase action; no report or verdict mutation", "run_id": run["id"], "step": action["step_number"],
        "elapsed_seconds": round(time.monotonic() - started, 2), "requests": generator.request_count,
        "visual_status": visual.get("status"), "visual_reason": visual.get("reason"),
        "images_sent": len(visual.get("images", [])), "accepted_findings": visual.get("accepted_count"),
        "documentation_source": scenario.evidence.get("documentation_provenance", {}).get("source"), "usage": client.drain_usage()}
    output = ROOT / "data/mcp-validation/optimized-auxiliary-review.json"
    output.write_text(json.dumps(measurements, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(measurements, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
