"""Bounded localhost validation. Secrets stay in memory, never in CLI output.

Use backend/.venv/bin/python scripts/validate-live.py --preflight first.
Then --mode visual to create a real run with the user's configured provider.
The watchdog is for this diagnostic runner, not a limit on normal explorations.
"""
import argparse
import asyncio
import json
import time
from collections import Counter
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--mode", choices=("text", "visual"), default="visual")
    parser.add_argument("--auxiliary-model", default="google/gemini-2.5-flash-lite")
    parser.add_argument("--demo-profile", choices=("standard_user", "problem_user", "error_user", "visual_user"))
    parser.add_argument("--max-cost", type=float, default=0.10)
    parser.add_argument("--watchdog-seconds", type=int, default=1200)
    parser.add_argument("--api", default="http://localhost:8000/api")
    args = parser.parse_args()
    config = dotenv_values(ROOT / "backend/.env")
    async with httpx.AsyncClient(timeout=45) as client:
        providers_response = await client.get(f"{args.api}/providers")
        providers_response.raise_for_status()
        provider = next(p for p in providers_response.json() if "openrouter.ai" in p["base_url"] and p.get("enabled"))
        agent_model, auxiliary_model = "typesafe/jev-1.13", args.auxiliary_model
        known = {m["id"]: m for m in provider.get("models", [])}
        if any(not known.get(model, {}).get("enabled", True) or model not in known for model in (agent_model, auxiliary_model)):
            raise RuntimeError("Selected model is not enabled in the provider catalog; no run created")
        if args.mode == "visual" and not known.get(auxiliary_model, {}).get("supports_vision"):
            raise RuntimeError("Selected auxiliary model does not declare vision support; no run created")
        key = config.get("OPEN_ROUTER")
        if not key:
            raise RuntimeError("OPEN_ROUTER not configured in backend/.env")
        quota = await client.get("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"})
        # Never log provider response bodies, labels, key hashes or credentials.
        quota_data = quota.json().get("data", {}) if quota.is_success else {}
        print(json.dumps({"preflight_http": quota.status_code, "usage_daily": quota_data.get("usage_daily"), "limit_remaining": quota_data.get("limit_remaining"), "agent": agent_model, "auxiliary": auxiliary_model, "vision_declared": known.get(auxiliary_model, {}).get("supports_vision")}), flush=True)
        if args.preflight:
            return
        if not quota.is_success:
            raise RuntimeError("OpenRouter key preflight rejected; no run created")
        credentials = {"username": config.get("LOGIN_SAUCEDEMO"), "password": config.get("PASSWORD_SAUCEDEMO")}
        if args.demo_profile:
            # These are public SauceDemo test profiles. Never change .env or
            # use arbitrary real identities for a diagnostic comparison.
            credentials["username"] = args.demo_profile
        if not all(credentials.values()):
            raise RuntimeError("Application credentials missing; no run created")
        active = await client.get(f"{args.api}/explorations")
        if any(r["status"] in {"running", "waiting_input", "postprocessing", "queued"} for r in active.json()):
            raise RuntimeError("Another run is active; refusing to interfere")
        # The user explicitly supplied backend/.env for this validation. Use
        # that key, not an unrelated older key encrypted in the provider DB.
        configured = await client.patch(f"{args.api}/providers/{provider['id']}", json={"api_key": key})
        if not configured.is_success:
            raise RuntimeError(f"Provider key setup rejected HTTP {configured.status_code}; response withheld")
        response = await client.post(f"{args.api}/explorations", json={
            "app_url": "https://www.saucedemo.com",
            "agent": {"provider_id": provider["id"], "model_id": agent_model},
            "auxiliary_llm": {"provider_id": provider["id"], "model_id": auxiliary_model},
            "options": {"auxiliary_evidence_mode": args.mode}, "credentials": credentials,
        })
        if not response.is_success:
            raise RuntimeError(f"Creation rejected HTTP {response.status_code}; response withheld")
        run_id = response.json()["id"]
        print(f"RUN_ID={run_id}", flush=True)
        start, last = time.monotonic(), None
        terminal = {"completed", "failed", "partial", "cancelled"}
        while True:
            response = await client.get(f"{args.api}/explorations/{run_id}")
            response.raise_for_status()
            run = response.json()
            current = (run["status"], run.get("steps_completed"), len(run.get("scenarios", [])), (run.get("postprocessing") or {}).get("enriched"))
            if current != last:
                print(json.dumps({"status": current[0], "actions": current[1], "cts": current[2], "enriched": current[3], "cost": (run.get("usage") or {}).get("cost")}), flush=True)
                last = current
            if run["status"] in terminal:
                break
            if run["status"] == "waiting_input" or time.monotonic() - start > args.watchdog_seconds or float((run.get("usage") or {}).get("cost") or 0) > args.max_cost:
                await client.post(f"{args.api}/explorations/{run_id}/cancel")
                print("WATCHDOG_CANCEL_REQUESTED", flush=True)
            await asyncio.sleep(5)
        scenarios = run.get("scenarios", [])
        event_counts = Counter(e.get("type") for e in run.get("events", []))
        metrics = {"run_id": run_id, "status": run["status"], "summary": run.get("summary"), "usage": run.get("usage"), "verification": run.get("verification_summary"), "completion": run.get("completion"), "visual": dict(Counter((s.get("evidence", {}).get("visual_assessment") or {}).get("status", "not_requested") for s in scenarios)), "document_http_failures": sum(1 for i in run.get("issues", []) if i.get("status_code") == 404), "jev_consultations": event_counts["jev_consulted"], "local_decisions": event_counts["decision_routed_locally"], "coverage_moves": event_counts["coverage_action_planned"], "auxiliary": run.get("postprocessing"), "visual_unavailable_reasons": dict(Counter((s.get("evidence", {}).get("visual_assessment") or {}).get("reason", "unspecified") for s in scenarios if (s.get("evidence", {}).get("visual_assessment") or {}).get("status") == "unavailable")), "failures": [{"id": s["id"], "title": s["title"], "verdict": s.get("evidence", {}).get("verdict"), "assertions": [a for a in s.get("evidence", {}).get("assertions", []) if a.get("passed") is False]} for s in scenarios if s["status"] == "FAIL"]}
        metrics.update({"mode": args.mode, "agent_model": agent_model, "auxiliary_model": auxiliary_model, "demo_profile": args.demo_profile,
                        "review_metrics": [{"id": s["id"], **s.get("evidence", {}).get("auxiliary_review_metrics", {})} for s in scenarios]})
        metrics.update({"visual_review_summary": run.get("visual_review_summary"),
                        "duration_summary": run.get("duration_summary"),
                        "provider_pipeline": next((e.get("payload", {}) for e in reversed(run.get("events", []))
                                                   if e.get("type") == "scenario_postprocessing_completed"), {})})
        output = ROOT / "data/mcp-validation" / f"metrics-{run_id}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"METRICS={output}", flush=True)
        print(f"DETAIL=http://localhost:3000/#/exploracoes/{run_id}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
