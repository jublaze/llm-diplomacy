import copy
import json
from pathlib import Path
import runpy
import pytest

from llm_diplomacy.runner.build_rule_accounting import validate_postactivation_accounting


from test_build_rule_accounting import setup


def failure_data():
    data = setup()
    decision = "game_001/1909_spring_movement/once/france/agent/submit_orders"
    start = "2026-01-01T00:00:00+00:00"
    for row in data["rows"]: row["started_at"] = start
    data["rows"][-1].update(outcome="known_failed", error_category="provider_transient_http", response=None, usage=None)
    request = {"kind":"request", "game_id":"game_001", "phase_id":"1909_spring_movement", "agent_id":"agent",
               "method":"submit_orders", "input_tokens":None, "output_tokens":None, "estimated_cost_usd":None,
               "error_type":"ProviderHTTPError"}
    data["current_ledger"] = data["baseline_ledger"] + (json.dumps(request) + "\n").encode()
    budget = copy.deepcopy(data["baseline_budget"])
    reserve = {"reservation_id":"r", "game_id":"game_001", "phase_id":"1909_spring_movement",
               "agent_id":"agent", "method":"submit_orders", "input_tokens":20, "output_tokens":10,
               "cost_usd":0.1, "status":"pending"}
    budget["pending_reservations"]["r"] = {**reserve, "status":"uncertain"}
    budget["pending_input_tokens"] += 20
    budget["pending_output_tokens"] += 10
    budget["pending_cost_usd"] += 0.1
    budget["budget_admission_estimated_input_tokens"] += 20
    budget["budget_admission_estimated_output_tokens"] += 10
    budget["budget_admission_estimated_cost_usd"] += 0.1
    budget["total_provider_calls"] += 1
    data["current_budget"] = budget
    data["budget_events"] = [
        {"event_type":"provider_reservation", "experiment_id":"game", "timestamp":"2026-01-01T00:00:01+00:00", "payload":reserve},
        {"event_type":"provider_reservation_uncertain", "experiment_id":"game", "timestamp":"2026-01-01T00:00:02+00:00", "payload":{"reservation_id":"r"}}]
    return data


def test_known_failure_preserves_reserved_exposure():
    validate_postactivation_accounting(**failure_data())


def test_received_retry_keeps_the_failed_attempt_reserve():
    data = failure_data()
    success = setup()
    request = success["current_ledger"].splitlines()[-1] + b"\n"
    data["current_ledger"] += request
    for row in success["rows"]:
        row["attempt_id"] = "retry"
        row["sequence"] += 3
        row["started_at"] = "2026-01-01T00:00:03+00:00"
        data["rows"].append(row)
    budget = data["current_budget"]
    budget["total_provider_calls"] += 1
    for key, amount in (("input_tokens",20), ("output_tokens",4), ("estimated_cost_usd",.05)):
        budget["known_" + key] += amount
        admission = "budget_admission_estimated_cost_usd" if key == "estimated_cost_usd" else "budget_admission_estimated_" + key
        budget[admission] += amount
    validate_postactivation_accounting(**data)


@pytest.mark.parametrize("mutation", ["unknown", "quota", "reserve_removed", "reserve_altered", "missing_event", "extra_outcome", "budget", "usage", "nan_pending", "fractional_tokens", "event_status"])
def test_known_failure_never_bypasses_guards(mutation):
    d = failure_data()
    if mutation == "unknown": d["rows"][-1]["outcome"] = "unknown"
    if mutation == "quota": d["rows"][-1]["error_category"] = "provider_quota"
    if mutation == "reserve_removed": del d["current_budget"]["pending_reservations"]["r"]
    if mutation == "reserve_altered": d["current_budget"]["pending_reservations"]["r"]["cost_usd"] = 0
    if mutation == "missing_event": d["budget_events"].pop()
    if mutation == "extra_outcome": d["rows"].append({"kind":"attempt", "sequence":14, "attempt_id":"x"})
    if mutation == "budget": d["current_budget"]["budget_admission_estimated_cost_usd"] = 0
    if mutation == "usage": d["rows"][-1]["usage"] = {"input_tokens":0}
    if mutation == "nan_pending": d["current_budget"]["pending_cost_usd"] = float("nan")
    if mutation == "fractional_tokens": d["current_budget"]["pending_input_tokens"] = 40.5
    if mutation == "event_status": d["budget_events"][0]["payload"]["status"] = "uncertain"
    with pytest.raises(ValueError): validate_postactivation_accounting(**d)
