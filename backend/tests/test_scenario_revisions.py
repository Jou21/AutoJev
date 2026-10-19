"""Independent executions can share a current CT slot without rewriting data."""
import json
import sqlite3
from copy import deepcopy

import pytest
from autojev.adapters.repository import SQLiteRepository
from autojev.adapters.secrets import SecretCipher
from autojev.domain.models import (
    ExplorationOptions,
    ExplorationRequest,
    ModelSelection,
    ScenarioStatus,
)
from autojev.domain.models import (
    TestScenario as RecordedScenario,
)
from autojev.services.scenario_revisions import (
    project_scenario_revisions,
    scenario_report_origin,
    scenario_revisions_key,
)
from autojev.services.scenario_timeline import prepare_scenario_timelines


@pytest.fixture
def repo(tmp_path):
    repository = SQLiteRepository(tmp_path / "versions.sqlite3", SecretCipher(tmp_path, "versions-test-key"))
    repository.initialize()
    return repository


def case(identity="CT001", status="PASS", screenshot="same-name.png", **extra):
    return RecordedScenario(
        id=identity, title="Sort products", description="Recorded interaction", preconditions=[],
        steps=["Choose descending"], expected_result="Descending products", actual_result=status,
        status=ScenarioStatus(status), url="https://example.test/inventory.html",
        created_at="2026-10-18T10:00:00+00:00", screenshots=[screenshot],
        evidence={"action_step": 1, "timeline": [
            {"id": "before", "kind": "observation", "title": "Before", "screenshot": screenshot},
            {"id": "action", "kind": "action", "title": "Select", "step_number": 1},
        ], **extra},
    )


def run(repo, day=1, *, cases=None, parent=None, status="completed"):
    identity = repo.create_exploration(ExplorationRequest(
        "https://example.test", ModelSelection("local", "test"), ModelSelection("local", "test"),
        ExplorationOptions(),
    ))
    with sqlite3.connect(repo.database_path) as connection:
        connection.execute("UPDATE explorations SET created_at = ? WHERE id = ?",
                           (f"2026-10-{day:02d}T10:00:00+00:00", identity))
    repo.update_exploration(identity, status=status, finished_at=f"2026-10-{day:02d}T10:01:00+00:00")
    for index, scenario in enumerate([case()] if cases is None else cases, 1):
        repo.add_scenario(identity, index, scenario)
    if parent:
        link(repo, identity, *parent)
    return identity


def link(repo, child, parent, parent_case="CT001"):
    repo.add_event(child, "info", "scenario_rerun_source", "Stored source", {
        "source_exploration_id": parent, "source_scenario_id": parent_case,
    })


def test_latest_terminal_result_replaces_only_original_slot_without_mutation(repo):
    root = run(repo, cases=[case("CT042"), case("CT043", status="FAIL")])
    first = run(repo, 2, cases=[case(status="FAIL")], parent=(root, "CT042"))
    latest = run(repo, 3, cases=[case(status="BLOCKED", screenshot="new-evidence.png")], parent=(root, "CT042"), status="partial")
    raw = repo.get_exploration(root)
    before = deepcopy(raw)
    output = project_scenario_revisions(repo, raw)
    assert raw == before == repo.get_exploration(root)
    selected, unrelated = output["scenarios"]
    assert selected["id"] == "CT042" and selected["status"] == "BLOCKED"
    assert selected["evidence_exploration_id"] == latest
    assert selected["screenshots"] == ["new-evidence.png"]
    revision = selected["execution_revision"]
    assert revision["scenario_id"] == "CT001" and revision["root_scenario_id"] == "CT042"
    assert revision["root_exploration_id"] == root and revision["version"] == 2
    assert [entry["exploration_id"] for entry in selected["execution_history"]] == [root, first, latest]
    assert [entry["current"] for entry in selected["execution_history"]] == [False, False, True]
    assert unrelated["id"] == "CT043" and unrelated["status"] == "FAIL"
    assert len(unrelated["execution_history"]) == 1
    assert output["summary"] == {"total_scenarios": 2, "pass": 0, "fail": 1, "blocked": 1, "skipped": 0, "issues": 0}
    selected["execution_history"][0]["scenario"]["evidence"]["timeline"][0]["title"] = "Changed"
    assert repo.get_exploration(root) == before


def test_nested_lineage_preserves_stable_root_and_rerun_view_excludes_siblings(repo):
    root = run(repo, cases=[case("CT147")])
    first = run(repo, 2, cases=[case(status="FAIL")], parent=(root, "CT147"))
    nested = run(repo, 3, cases=[case(status="BLOCKED")], parent=(first, "CT001"), status="partial")
    sibling = run(repo, 4, parent=(root, "CT147"))
    original = project_scenario_revisions(repo, repo.get_exploration(root))["scenarios"][0]
    assert original["execution_revision"]["exploration_id"] == sibling
    assert [row["exploration_id"] for row in original["execution_history"]] == [root, first, nested, sibling]
    child_view = project_scenario_revisions(repo, repo.get_exploration(first))["scenarios"][0]
    assert child_view["id"] == "CT001" and child_view["status"] == "BLOCKED"
    assert child_view["execution_revision"]["root_exploration_id"] == root
    assert child_view["execution_revision"]["root_scenario_id"] == "CT147"
    assert child_view["execution_revision"]["version"] == 1
    assert [row["exploration_id"] for row in child_view["execution_history"]] == [first, nested]


@pytest.mark.parametrize("status", ["running", "waiting_input", "queued", "postprocessing"])
def test_active_rerun_cannot_mask_terminal_result_even_with_interim_scenario(repo, status):
    root = run(repo)
    finished = run(repo, 2, cases=[case(status="FAIL")], parent=(root, "CT001"))
    active = run(repo, 3, cases=[case(status="PASS")], parent=(root, "CT001"), status=status)
    projected = project_scenario_revisions(repo, repo.get_exploration(root))["scenarios"][0]
    assert projected["status"] == "FAIL" and projected["execution_revision"]["exploration_id"] == finished
    pending = projected["execution_history"][-1]
    assert pending["exploration_id"] == active and pending["run_status"] == status
    assert pending["has_result"] is False and pending["scenario"] is None and pending["status"] is None
    assert pending["current"] is False


@pytest.mark.parametrize("status", ["cancelled", "failed", "partial", "completed"])
def test_terminal_without_a_persisted_scenario_is_history_only(repo, status):
    root = run(repo)
    empty = run(repo, 2, cases=[], parent=(root, "CT001"), status=status)
    projected = project_scenario_revisions(repo, repo.get_exploration(root))["scenarios"][0]
    assert projected["execution_revision"]["exploration_id"] == root
    assert projected["execution_history"][0]["current"] is True
    attempt = projected["execution_history"][-1]
    assert attempt["exploration_id"] == empty and attempt["scenario_id"] is None
    assert attempt["has_result"] is False and attempt["scenario"] is None


@pytest.mark.parametrize("verdict", ["PASS", "FAIL", "BLOCKED", "SKIPPED"])
def test_every_persisted_terminal_verdict_is_a_version_not_only_success(repo, verdict):
    root = run(repo)
    latest = run(repo, 2, parent=(root, "CT001"), cases=[case(status=verdict)], status="cancelled")
    selected = project_scenario_revisions(repo, repo.get_exploration(root))["scenarios"][0]
    assert selected["status"] == verdict and selected["execution_revision"]["exploration_id"] == latest


def test_identical_duplicate_links_are_deduplicated_but_conflicting_parents_are_rejected(repo):
    root = run(repo, cases=[case("CT001"), case("CT002")])
    valid = run(repo, 2, parent=(root, "CT001"), cases=[case(status="FAIL")])
    link(repo, valid, root)
    conflicting = run(repo, 3, parent=(root, "CT001"), cases=[case(status="BLOCKED")])
    link(repo, conflicting, root, "CT002")
    selected = project_scenario_revisions(repo, repo.get_exploration(root))["scenarios"][0]
    assert selected["execution_revision"]["exploration_id"] == valid
    assert [row["exploration_id"] for row in selected["execution_history"]] == [root, valid]


def test_cycle_orphan_wrong_case_and_ambiguous_result_cannot_reassign_a_ct(repo):
    root = run(repo)
    cyclic = run(repo, 2, parent=(root, "CT001"))
    link(repo, root, cyclic)
    assert len(project_scenario_revisions(repo, repo.get_exploration(root))["scenarios"][0]["execution_history"]) == 1
    other = run(repo, 3)
    orphan = run(repo, 4, parent=("missing-run", "CT001"))
    run(repo, 5, parent=(other, "missing-case"))
    ambiguous = run(repo, 6, parent=(other, "CT001"), cases=[case("CT001"), case("CT002")])
    selected = project_scenario_revisions(repo, repo.get_exploration(other))["scenarios"][0]
    assert selected["execution_revision"]["exploration_id"] == other
    assert [row["exploration_id"] for row in selected["execution_history"]] == [other, ambiguous]
    assert selected["execution_history"][-1]["has_result"] is False
    assert project_scenario_revisions(repo, repo.get_exploration(orphan))["scenarios"][0]["execution_revision"]["exploration_id"] == orphan


def test_screenshot_ownership_and_action_preview_do_not_cross_between_step_one_runs(repo):
    root = run(repo)
    latest = run(repo, 2, parent=(root, "CT001"))
    for identity, label in ((root, "original"), (latest, "latest")):
        repo.add_action(identity, 1, {"action": "select"}, {
            "started_at": "2026-10-18T10:00:00Z", "finished_at": "2026-10-18T10:00:01Z",
            "evidence": {"action_preview": {"screenshot": label + "-preview.png", "version": label}},
        })
    selected = project_scenario_revisions(repo, repo.get_exploration(root))["scenarios"][0]
    assert selected["evidence_exploration_id"] == latest
    history = selected["execution_history"]
    assert history[0]["scenario"]["evidence_exploration_id"] == root
    assert history[1]["scenario"]["evidence_exploration_id"] == latest
    assert history[0]["scenario"]["screenshots"][0] == history[1]["scenario"]["screenshots"][0] == "same-name.png"
    assert history[0]["scenario"]["evidence"]["timeline"][1]["action_preview"]["version"] == "original"
    assert selected["evidence"]["timeline"][1]["action_preview"]["version"] == "latest"
    assert selected["execution_actions"] == repo.get_exploration(latest)["actions"]
    for entry in history:
        assert "execution_history" not in entry["scenario"]
    # Report generation calls the normalizer again; it must not backfill the
    # original step 1 preview if this replay has no preview of its own.
    foreign = deepcopy(selected)
    foreign["evidence"]["timeline"][1].pop("action_preview")
    foreign["execution_actions"] = []
    rehydrated = prepare_scenario_timelines({**repo.get_exploration(root), "timeline_version": 2, "scenarios": [foreign]})
    assert "action_preview" not in rehydrated["scenarios"][0]["evidence"]["timeline"][1]


def test_creation_order_not_completion_order_and_same_time_uses_run_id(repo):
    root = run(repo)
    earlier = run(repo, 2, parent=(root, "CT001"), cases=[case(status="FAIL")])
    same_day = run(repo, 2, parent=(root, "CT001"), cases=[case(status="BLOCKED")])
    repo.update_exploration(earlier, finished_at="2026-10-25T10:01:00+00:00")
    history = project_scenario_revisions(repo, repo.get_exploration(root))["scenarios"][0]["execution_history"]
    assert [row["exploration_id"] for row in history[1:]] == sorted([earlier, same_day])


def test_fingerprint_changes_for_same_verdict_new_evidence_and_idempotent_projection(repo):
    root = run(repo)
    latest = run(repo, 2, parent=(root, "CT001"))
    initial = project_scenario_revisions(repo, repo.get_exploration(root))
    assert project_scenario_revisions(repo, initial) == initial
    assert scenario_revisions_key(initial) == initial["scenario_revisions_key"]
    repo.add_scenario(latest, 1, case(screenshot="new-same-verdict-image.png"))
    changed = project_scenario_revisions(repo, repo.get_exploration(root))
    assert initial["summary"] == changed["summary"]
    assert initial["scenario_revisions_key"] != changed["scenario_revisions_key"]


def test_repository_source_query_has_no_recent_hundred_run_cutoff(repo):
    root = run(repo)
    child = run(repo, 2, parent=(root, "CT001"), cases=[case(status="FAIL")])
    for _ in range(105):
        run(repo, 3, cases=[])
    rows = repo.list_scenario_rerun_sources()
    assert [row["exploration_id"] for row in rows] == [child]
    assert project_scenario_revisions(repo, repo.get_exploration(root))["scenarios"][0]["status"] == "FAIL"


def test_malformed_source_json_does_not_break_an_unrelated_report(repo):
    root = run(repo)
    child = run(repo, 2, parent=(root, "CT001"))
    with sqlite3.connect(repo.database_path) as connection:
        connection.execute("UPDATE exploration_events SET payload_json = ? WHERE exploration_id = ?",
                           ("not json", child))
    output = project_scenario_revisions(repo, repo.get_exploration(root))
    assert len(output["scenarios"][0]["execution_history"]) == 1
    assert json.dumps(output, sort_keys=True)


def test_action_copies_scale_with_case_links_not_every_action_in_owner_run(repo):
    single = case("CT001")
    multi = case("CT002", action_steps=[2, 3])
    multi.evidence["action_step"] = 2
    multi.evidence["timeline"][1]["step_number"] = 2
    multi.evidence["timeline"].append({"id": "third", "kind": "action", "action_step": 3})
    root = run(repo, cases=[single, multi])
    for step in range(1, 25):
        repo.add_action(root, step, {"action": "click"}, {"success": True, "message": f"action {step}"})
    first, second = project_scenario_revisions(repo, repo.get_exploration(root))["scenarios"]
    assert [action["step_number"] for action in first["execution_actions"]] == [1]
    assert [action["step_number"] for action in second["execution_actions"]] == [2, 3]
    assert first["execution_history"][0]["actions"] == first["execution_actions"]
    assert second["execution_history"][0]["actions"] == second["execution_actions"]
    assert len(repo.get_exploration(root)["actions"]) == 24


def test_report_origin_walks_only_minimal_ancestor_slices_and_supports_active_without_result(repo, monkeypatch):
    root = run(repo, cases=[case("CT147"), case("CT148")])
    parent = run(repo, 2, parent=(root, "CT147"))
    active = run(repo, 3, parent=(parent, "CT001"), cases=[], status="waiting_input")
    original_reader = repo.get_scenario_revision_ancestry
    reads = []

    def tracked(identity):
        reads.append(identity)
        result = original_reader(identity)
        assert set(result) == {"id", "sources", "scenarios"}
        assert all(set(scenario) == {"id", "status", "source_exploration_id", "source_scenario_id"}
                   for scenario in result["scenarios"])
        return result

    def forbidden(*args, **kwargs):
        pytest.fail("A live report link must not read all actions, project descendants, or scan unrelated runs")

    monkeypatch.setattr(repo, "get_scenario_revision_ancestry", tracked)
    monkeypatch.setattr(repo, "get_exploration", forbidden)
    monkeypatch.setattr(repo, "list_scenario_rerun_sources", forbidden)
    assert scenario_report_origin(repo, active) == {"report_exploration_id": root, "report_scenario_id": "CT147"}
    assert reads == [active, parent, root]


@pytest.mark.parametrize("invalid", ["conflicting", "orphan", "wrong_case", "cycle", "malformed", "source_mismatch"])
def test_report_origin_invalid_ancestry_never_redirects_to_another_report(repo, invalid):
    root = run(repo, cases=[case("CT147"), case("CT148")])
    child = run(repo, 2, parent=(root, "CT147"))
    if invalid == "conflicting":
        link(repo, child, root, "CT148")
    elif invalid == "orphan":
        with sqlite3.connect(repo.database_path) as connection:
            connection.execute("UPDATE exploration_events SET payload_json = ? WHERE exploration_id = ?", (
                json.dumps({"source_exploration_id": "missing", "source_scenario_id": "CT147"}), child))
    elif invalid == "wrong_case":
        with sqlite3.connect(repo.database_path) as connection:
            connection.execute("UPDATE exploration_events SET payload_json = ? WHERE exploration_id = ?", (
                json.dumps({"source_exploration_id": root, "source_scenario_id": "CT999"}), child))
    elif invalid == "cycle":
        # A second multi-case check must not be the only reason for stopping.
        repo.delete_scenario(root, "CT148")
        link(repo, root, child)
    elif invalid == "malformed":
        with sqlite3.connect(repo.database_path) as connection:
            connection.execute("UPDATE exploration_events SET payload_json = 'invalid json' WHERE exploration_id = ?", (child,))
    else:
        repo.add_scenario(child, 1, case(source_exploration_id=root, source_scenario_id="CT148"))
    assert scenario_report_origin(repo, child) == {"report_exploration_id": child, "report_scenario_id": "CT001"}


def test_report_origin_accepts_identical_duplicate_source_events_and_original_report(repo):
    root = run(repo, cases=[case("CT147")])
    child = run(repo, 2, parent=(root, "CT147"))
    link(repo, child, root, "CT147")
    assert scenario_report_origin(repo, child) == {"report_exploration_id": root, "report_scenario_id": "CT147"}
    assert scenario_report_origin(repo, root) == {"report_exploration_id": root, "report_scenario_id": "CT147"}
