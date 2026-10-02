from copy import deepcopy

from autojev.services.scenario_timeline import prepare_scenario_timelines


def action(
    step,
    shot,
    started="2026-10-02T12:00:02+00:00",
    finished="2026-10-02T12:00:04+00:00",
):
    return {
        "step_number": step,
        "decision": {"action": "click", "target": "e1"},
        "result": {
            "action": "click",
            "success": True,
            "started_at": started,
            "finished_at": finished,
            "before_url": "https://example.test/items",
            "after_url": "https://example.test/cart",
            "screenshot": shot,
            "assertions": [{"kind": "observable_state_change", "passed": True}],
        },
    }


def run():
    return {
        "id": "run",
        "app_url": "https://example.test",
        "status": "completed",
        "summary": {"total_scenarios": 1, "pass": 1, "fail": 0},
        "actions": [action(1, "after.png")],
        "screenshots": [
            {"path": "before.png", "created_at": "2026-10-02T12:00:01+00:00"},
            {"path": "after.png", "created_at": "2026-10-02T12:00:04+00:00"},
        ],
        "scenarios": [
            {
                "id": "CT001",
                "title": "Carrinho",
                "status": "PASS",
                "screenshots": ["after.png"],
                "evidence": {
                    "action_succeeded": True,
                    "assertions": [{"kind": "observable_state_change", "passed": True}],
                },
            }
        ],
        "issues": [],
    }


def test_legacy_projection_has_chronology_before_after_and_preserves_history():
    original = run()
    original["issues"] = [
        {
            "type": "http",
            "status_code": 401,
            "message": "POST returned HTTP 401",
            "url": "https://events.backtrace.io/api/submit",
            "occurred_at": "2026-10-02T12:00:03+00:00",
            "screenshot": "after.png",
        }
    ]
    backup = deepcopy(original)
    projected = prepare_scenario_timelines(original)
    assert original == backup
    assert projected["summary"] == backup["summary"]
    scenario = projected["scenarios"][0]
    assert scenario["status"] == "PASS"
    assert set(scenario["screenshots"]) == {"before.png", "after.png"}
    events = scenario["evidence"]["timeline"]
    assert [event["kind"] for event in events] == [
        "observation",
        "action",
        "signal",
        "assertion",
        "observation",
    ]
    signal = scenario["evidence"]["technical_signals"][0]
    assert signal["classification"] == "external_telemetry"
    assert signal["affects_verdict"] is False
    assert prepare_scenario_timelines(projected) == projected


def test_unlinked_startup_and_restoration_get_context_not_nearest_ct():
    original = run()
    original["actions"].append(
        action(
            2, "restore.png", "2026-10-02T12:00:05+00:00", "2026-10-02T12:00:06+00:00"
        )
    )
    original["issues"] = [
        {
            "type": "http",
            "status_code": 401,
            "message": "POST returned HTTP 401",
            "url": "https://events.backtrace.io/api/submit",
            "screenshot": "login.png",
            "occurred_at": "2026-10-02T12:00:00+00:00",
        },
        {
            "type": "http",
            "status_code": 404,
            "message": "GET returned HTTP 404",
            "url": "https://example.test/cart",
            "screenshot": "restore.png",
            "occurred_at": "2026-10-02T12:00:05.500+00:00",
        },
    ]
    projected = prepare_scenario_timelines(original)
    assert projected["scenarios"][0]["evidence"]["technical_signals"] == []
    assert len(projected["scenario_contexts"]) == 2
    assert all(
        context["status"] == "INFO" for context in projected["scenario_contexts"]
    )
    assert "etapa 2" in projected["scenario_contexts"][1]["title"]
    assert projected["summary"]["total_scenarios"] == 1
    assert projected["technical_signal_summary"]["context_occurrences"] == 2


def test_ambiguous_screenshot_does_not_guess_ct_owner():
    original = run()
    original["scenarios"].append(deepcopy(original["scenarios"][0]))
    original["scenarios"][1]["id"] = "CT002"
    original["issues"] = [
        {
            "type": "http",
            "status_code": 500,
            "message": "GET returned HTTP 500",
            "url": "https://example.test/items",
            "screenshot": "after.png",
        }
    ]
    projected = prepare_scenario_timelines(original)
    assert all(not s["evidence"]["technical_signals"] for s in projected["scenarios"])
    assert len(projected["scenario_contexts"]) == 1


def test_old_guard_only_fail_is_explained_not_rewritten_as_pass():
    original = run()
    scenario = original["scenarios"][0]
    scenario["status"] = "FAIL"
    scenario["evidence"]["assertions"].append(
        {
            "kind": "no_new_browser_errors",
            "passed": False,
            "actual": {"network_errors": 1, "console_errors": 0},
        }
    )
    projected = prepare_scenario_timelines(original)
    verdict = projected["scenarios"][0]["evidence"]["verdict"]
    assert verdict["kind"] == "technical_failure"
    assert verdict["historical"] is True
    assert projected["scenarios"][0]["status"] == "FAIL"
    assert "não confirma" in verdict["explanation"]


def test_native_signal_and_global_copy_are_shown_once():
    original = run()
    signal = {
        "type": "http",
        "status_code": 500,
        "message": "GET returned HTTP 500",
        "url": "https://example.test/items",
        "screenshot": "after.png",
        "occurred_at": "2026-10-02T12:00:03+00:00",
        "step_number": 1,
    }
    original["scenarios"][0]["evidence"].update(
        {
            "action_step": 1,
            "technical_signals": [signal],
            "timeline": [
                {
                    "kind": "signal",
                    "signal": signal,
                    "occurred_at": signal["occurred_at"],
                }
            ],
        }
    )
    original["issues"] = [signal]
    projected = prepare_scenario_timelines(original)
    assert len(projected["scenarios"][0]["evidence"]["technical_signals"]) == 1
    assert projected["scenario_contexts"] == []
    assert projected["technical_signal_summary"]["occurrences"] == 1


def test_correlated_reset_channels_do_not_become_two_incidents():
    original = run()
    original["issues"] = [
        {
            "type": "console",
            "message": "Failed to load resource: net::ERR_CONNECTION_RESET",
            "url": "https://example.test/cart",
            "occurred_at": "2026-10-02T12:00:03.001+00:00",
            "screenshot": "after.png",
        },
        {
            "type": "network",
            "message": "net::ERR_CONNECTION_RESET",
            "url": "https://events.backtrace.io/api/submit",
            "occurred_at": "2026-10-02T12:00:03+00:00",
            "screenshot": "after.png",
        },
    ]
    projected = prepare_scenario_timelines(original)
    assert projected["technical_signal_summary"]["occurrences"] == 1
    signal = projected["scenarios"][0]["evidence"]["technical_signals"][0]
    assert signal["evidence_channels"] == ["network", "console"]
    assert signal["affects_verdict"] is False


def test_late_console_partner_enriches_existing_native_occurrence():
    original = run()
    signal = {
        "occurrence_id": "request-1",
        "type": "http",
        "status_code": 500,
        "message": "GET returned HTTP 500",
        "url": "https://example.test/items",
        "evidence_channels": ["network"],
        "step_number": 1,
    }
    original["scenarios"][0]["evidence"].update(
        {
            "action_step": 1,
            "technical_signals": [signal],
            "timeline": [{"kind": "signal", "signal": signal}],
        }
    )
    original["issues"] = [
        {
            **signal,
            "evidence_channels": ["network", "console"],
            "related_console_errors": [
                {"type": "console", "message": "Failed to load resource"}
            ],
        }
    ]
    projected = prepare_scenario_timelines(original)
    evidence = projected["scenarios"][0]["evidence"]
    assert len(evidence["technical_signals"]) == 1
    assert evidence["technical_signals"][0]["evidence_channels"] == [
        "network",
        "console",
    ]
    entry = next(item for item in evidence["timeline"] if item["kind"] == "signal")
    assert entry["signal"]["related_console_errors"][0]["type"] == "console"


def test_retry_action_step_entries_link_signals_to_the_existing_ct():
    original = run()
    original["actions"].append(action(2, "retry.png"))
    original["scenarios"][0]["evidence"].update(
        {
            "action_step": 2,
            "timeline": [
                {"kind": "action", "action_step": 1},
                {"kind": "action", "action_step": 2},
            ],
        }
    )
    original["issues"] = [
        {
            "type": "http",
            "status_code": 500,
            "step_number": 1,
            "message": "GET returned HTTP 500",
            "url": "https://example.test/items",
        }
    ]
    projected = prepare_scenario_timelines(original)
    assert len(projected["scenarios"][0]["evidence"]["technical_signals"]) == 1
    assert projected["scenario_contexts"] == []
