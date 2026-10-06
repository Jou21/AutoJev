from copy import deepcopy

import pytest

from autojev.services.coverage_ledger import coverage_restriction_ledger


def recovered(identity="route-1", *, passed=True):
    return {
        "id": identity, "name": "Continue Shopping", "reason": "no_safe_restore_route",
        "status": "resolved", "source_url": "https://shop.test/cart",
        "resolution": {
            "source": "actual_semantic_move_execution", "action_step": 47,
            "occurred_at": "2026-10-04T12:00:00+00:00", "source_url": "https://shop.test/cart",
            "action": "click", "target": "e4", "screenshot": "047.png",
            "success": True, "assertions_passed": passed,
        },
    }


def test_explicit_resolution_retires_only_its_exact_id_and_preserves_history():
    blocked = {"id": "route-1", "reason": "no_safe_restore_route", "name": "Continue Shopping"}
    masked = {"id": "postal-1", "reason": "required_field_validation_masked", "name": "Continue"}
    events = [
        {"type": "coverage_restriction_recorded", "payload": blocked},
        {"type": "coverage_restriction_recorded", "payload": masked},
        {"type": "coverage_restriction_resolved", "payload": recovered(passed=False)},
        {"type": "coverage_complete_with_restrictions", "payload": {"restrictions": [blocked, masked]}},
    ]
    before = deepcopy(events)
    ledger = coverage_restriction_ledger({}, events)
    assert ledger["restrictions"] == [masked]
    assert ledger["restriction_count"] == ledger["resolved_restriction_count"] == 1
    assert ledger["resolved_restrictions"][0]["resolution"]["assertions_passed"] is False
    assert events == before


def test_later_pass_or_same_url_does_not_infer_resolution():
    blocked = {"id": "r1", "reason": "no_safe_restore_route", "name": "Continue Shopping"}
    events = [
        {"type": "coverage_restriction_recorded", "payload": blocked},
        {"type": "action_completed", "payload": {"success": True, "url": "https://shop.test/cart"}},
        {"type": "scenario_created", "payload": {"status": "PASS", "name": "Continue Shopping"}},
    ]
    assert coverage_restriction_ledger({}, events)["restrictions"] == [blocked]


@pytest.mark.parametrize("change", [
    {"source": "url_reached"}, {"action_step": None}, {"action_step": True},
    {"action_step": 0}, {"success": False}, {"assertions_passed": None},
    {"source_url": ""}, {"occurred_at": ""},
])
def test_resolution_without_actual_execution_provenance_cannot_hide_a_block(change):
    block = {"id": "route-1", "reason": "no_safe_restore_route"}
    invalid = recovered()
    invalid["resolution"].update(change)
    ledger = coverage_restriction_ledger({"restrictions": [block]}, [
        {"type": "coverage_restriction_resolved", "payload": invalid},
    ])
    assert ledger["restrictions"] == [block]
    assert ledger["resolved_restrictions"] == []


def test_explicit_terminal_snapshot_supports_history_api_without_events():
    item = recovered()
    coverage = {"restrictions": [], "resolved_restrictions": [item]}
    before = deepcopy(coverage)
    ledger = coverage_restriction_ledger(coverage, [])
    assert ledger["restrictions"] == []
    assert ledger["resolved_restrictions"] == [item]
    assert coverage == before


def test_invalid_resolved_snapshot_stays_visible_as_unverified_coverage_gap():
    invalid = {"id": "r1", "status": "resolved", "name": "Click", "resolution": {"success": True}}
    ledger = coverage_restriction_ledger({"resolved_restrictions": [invalid]}, [])
    assert ledger["restrictions"] == [invalid]
    assert ledger["resolved_restrictions"] == []


def test_recorded_duplicates_are_deduplicated_even_without_legacy_ids():
    block = {"name": "Click", "reason": "no_safe_restore_route"}
    events = [{"type": "coverage_restriction_recorded", "payload": block},
              {"type": "coverage_restriction_recorded", "payload": dict(reversed(list(block.items())))}]
    assert coverage_restriction_ledger({}, events)["restrictions"] == [block]


def test_same_named_other_restriction_is_not_retired():
    other = {"id": "route-2", "name": "Continue Shopping", "reason": "no_safe_restore_route"}
    ledger = coverage_restriction_ledger({"restrictions": [other]}, [
        {"type": "coverage_restriction_resolved", "payload": recovered()},
    ])
    assert ledger["restrictions"] == [other]
    assert ledger["resolved_restriction_count"] == 1


def test_explicit_dependency_update_supplements_only_known_record_and_keeps_original_cause_and_status():
    block = {"id": "route-1", "name": "Add to cart", "status": "active", "reason": "no_safe_restore_route"}
    dependency = {"kind": "failed_inverse_cart_toggle", "source": "actual_business_assertion", "action_step": 22}
    events = [
        {"type": "coverage_restriction_recorded", "payload": block},
        {"type": "coverage_restriction_updated", "payload": {
            "id": "route-1", "reason": "different_reason", "status": "resolved", "resolution": recovered()["resolution"],
            "dependency": dependency,
        }},
        {"type": "coverage_restriction_updated", "payload": {"id": "unknown", "dependency": dependency}},
    ]
    before = deepcopy(events)
    ledger = coverage_restriction_ledger({}, events)
    assert ledger["restrictions"] == [{**block, "dependency": dependency}]
    assert ledger["restriction_count"] == 1
    assert ledger["resolved_restrictions"] == []
    assert events == before


def test_late_dependency_update_cannot_reopen_resolved_history():
    item = recovered()
    dependency = {"kind": "failed_inverse_cart_toggle", "source": "actual_business_assertion"}
    ledger = coverage_restriction_ledger({}, [
        {"type": "coverage_restriction_resolved", "payload": item},
        {"type": "coverage_restriction_updated", "payload": {
            "id": item["id"], "status": "active", "reason": "changed", "dependency": dependency,
        }},
    ])
    assert ledger["restrictions"] == []
    assert ledger["resolved_restrictions"] == [{**item, "dependency": dependency}]
