from copy import deepcopy

from autojev.services.execution_summary import (
    execution_duration_summary,
    formatted_execution_duration,
    report_snapshot_key,
)


def test_total_duration_includes_auxiliary_tail_before_report_final_timestamp():
    run = {
        "status": "completed", "started_at": "2026-10-04T03:22:54+00:00",
        "finished_at": "2026-10-04T03:28:48+00:00",
        "events": [{"created_at": "2026-10-04T03:38:49+00:00", "type": "report_generation_started"}],
    }
    before = deepcopy(run)
    summary = execution_duration_summary(run)
    assert summary["total_seconds"] == 955
    assert summary["ended_at"] == "2026-10-04T03:38:49+00:00"
    assert formatted_execution_duration(run) == "00:15:55"
    assert run == before


def test_final_timestamp_wins_over_earlier_updated_at_and_events():
    run = {
        "status": "completed", "started_at": "2026-10-04T03:22:54+00:00",
        "finished_at": "2026-10-04T03:38:50+00:00", "updated_at": "2026-10-04T03:38:49+00:00",
        "events": [{"created_at": "2026-10-04T03:38:48+00:00"}],
    }
    assert execution_duration_summary(run)["total_seconds"] == 956
    first_key = report_snapshot_key(run)
    assert first_key == report_snapshot_key(deepcopy(run))
    run["finished_at"] = "2026-10-04T03:38:51+00:00"
    assert first_key != report_snapshot_key(run)


def test_live_postprocessing_duration_stays_open_even_after_browser_finished():
    summary = execution_duration_summary({
        "status": "postprocessing", "started_at": "2026-10-04T03:22:54Z",
        "finished_at": "2026-10-04T03:28:48Z",
    })
    assert summary["ongoing"] is True
    assert summary["ended_at"] is None and summary["total_seconds"] is None


def test_duration_tolerates_missing_bad_and_mixed_zone_historical_timestamps():
    assert formatted_execution_duration({}) == "—"
    assert formatted_execution_duration({"started_at": "invalid", "finished_at": "invalid"}) == "—"
    assert formatted_execution_duration({
        "status": "completed", "created_at": "2026-10-04T03:22:54",
        "finished_at": "2026-10-04T00:22:57-03:00", "updated_at": "invalid",
        "events": [{"created_at": "invalid"}],
    }) == "00:00:03"


def test_negative_duration_is_clamped_without_inventing_end_time():
    assert formatted_execution_duration({
        "status": "failed", "started_at": "2026-10-04T03:22:54Z",
        "finished_at": "2026-10-04T03:22:53Z",
    }) == "00:00:00"
    assert formatted_execution_duration({"status": "completed", "started_at": "2026-10-04T03:22:54Z"}) == "—"
