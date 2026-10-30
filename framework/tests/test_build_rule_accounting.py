import copy
import json

import pytest

from llm_diplomacy.runner.build_rule_accounting import validate_postactivation_accounting


def setup():
    old_ledger = b'{"kind":"request","old":true}\n'
    baseline = {"experiment_id": "game", "config_fingerprint": "fixed", "max_provider_calls": 20,
        "max_input_tokens": 10000, "max_output_tokens": 10000, "max_estimated_cost_usd": 20,
        "schema_version": "1.0", "stop_policy": "stop_experiment", "total_provider_calls": 1,
        "known_input_tokens": 10, "known_output_tokens": 3, "known_estimated_cost_usd": .1,
        "total_input_tokens": None, "total_output_tokens": None, "total_estimated_cost_usd": None,
        "budget_admission_estimated_input_tokens": 30, "budget_admission_estimated_output_tokens": 13,
        "budget_admission_estimated_cost_usd": .2, "pending_input_tokens": 20,
        "pending_output_tokens": 10, "pending_cost_usd": .1,
        "pending_reservations": {"old": {"status": "uncertain"}}}
    current = {**copy.deepcopy(baseline), "total_provider_calls": 2,
               "known_input_tokens": 30, "known_output_tokens": 7, "known_estimated_cost_usd": .15,
               "budget_admission_estimated_input_tokens": 50, "budget_admission_estimated_output_tokens": 17,
               "budget_admission_estimated_cost_usd": .25}
    decision = "game_001/1909_spring_movement/once/france/agent/submit_orders"
    request = {"kind": "request", "game_id": "game_001", "phase_id": "1909_spring_movement",
        "agent_id": "agent", "method": "submit_orders", "input_tokens": 20, "output_tokens": 4,
        "estimated_cost_usd": .05, "error_type": None}
    ledger = old_ledger + (json.dumps({"kind": "lifecycle"}) + "\n" + json.dumps(request) + "\n").encode()
    rows = [{"kind": "dispatch_prepared", "sequence": 11, "decision_id": decision, "attempt_id": "new", "prompt_hash": "p"},
            {"kind": "dispatch", "sequence": 12, "decision_id": decision, "attempt_id": "new", "prompt_hash": "p"},
            {"kind": "attempt", "sequence": 13, "decision_id": decision, "attempt_id": "new",
             "prompt_hash": "p", "outcome": "received", "usage": {"input_tokens": 20, "output_tokens": 4, "estimated_cost_usd": .05}}]
    return dict(baseline_ledger=old_ledger, current_ledger=ledger, baseline_budget=baseline,
                current_budget=current, rows=rows, activation_sequence=10)


def test_postactivation_known_usage_and_uncertain_reserve_are_reconciled():
    validate_postactivation_accounting(**setup())


def test_unchanged_uncertain_baseline_needs_no_matching_usage_snapshot():
    data = setup()
    data["current_ledger"] = data["baseline_ledger"]
    data["current_budget"] = copy.deepcopy(data["baseline_budget"])
    data["rows"] = []
    snapshot = copy.deepcopy(data["baseline_budget"])
    snapshot["pending_reservations"]["old"]["status"] = "pending"
    data["budget_events"] = [{"event_type": "budget_usage_update", "payload": {"totals": snapshot, "usage": {}}}]
    validate_postactivation_accounting(**data)

    data["current_budget"]["pending_reservations"]["old"]["status"] = "pending"
    with pytest.raises(ValueError, match="reserves"):
        validate_postactivation_accounting(**data)


def test_changed_pending_cost_still_requires_reconciled_trace():
    data = setup()
    data["current_ledger"] = data["baseline_ledger"]
    data["current_budget"] = copy.deepcopy(data["baseline_budget"])
    data["current_budget"]["pending_cost_usd"] += 1e-13
    data["rows"] = []
    with pytest.raises(ValueError, match="trace required"):
        validate_postactivation_accounting(**data)
    data["budget_events"] = [{"event_type": "budget_usage_update", "payload": {"totals": {}}}]
    with pytest.raises(ValueError, match="baseline is missing or ambiguous"):
        validate_postactivation_accounting(**data)


@pytest.mark.parametrize("mutation,match", [
    ("prefix", "prefix"), ("reserve", "reserves"), ("usage", "known usage"),
    ("config", "configuration"), ("missing", "dispatches"), ("unknown", "requires review"),
    ("total", "historical total"), ("extra", "records do not match"), ("journal_usage", "journal usage")])
def test_postactivation_mismatch_is_a_hold(mutation, match):
    data = setup()
    if mutation == "prefix": data["current_ledger"] = b"changed\n" + data["current_ledger"]
    if mutation == "reserve": data["current_budget"]["pending_reservations"] = {}
    if mutation == "usage": data["current_budget"]["known_input_tokens"] = 31
    if mutation == "config": data["current_budget"]["max_provider_calls"] = 21
    if mutation == "missing": data["rows"] = []
    if mutation == "unknown": data["rows"][-1]["outcome"] = "unknown"
    if mutation == "total": data["current_budget"]["total_input_tokens"] = 30
    if mutation == "extra": data["rows"].append({"kind": "attempt", "sequence": 14, "attempt_id": "unmatched"})
    if mutation == "journal_usage": data["rows"][-1]["usage"]["input_tokens"] = 21
    with pytest.raises(ValueError, match=match): validate_postactivation_accounting(**data)


def test_unmatched_received_attempt_cannot_bypass_budget_or_dispatch(tmp_path):
    data = setup()
    data["current_ledger"] = data["baseline_ledger"]
    data["current_budget"] = copy.deepcopy(data["baseline_budget"])
    data["rows"] = [{"kind": "attempt", "sequence": 11, "attempt_id": "forged", "outcome": "received"}]
    with pytest.raises(ValueError, match="records do not match"):
        validate_postactivation_accounting(**data)
