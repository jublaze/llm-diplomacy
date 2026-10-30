import copy
import json
import math
import pytest
from llm_diplomacy.runner.reservation_currency_trace import verify_pending_currency_trace


def fixture():
    old = {"reservation_id":"old", "status":"uncertain", "cost_usd":0.1,
           "input_tokens":10, "output_tokens":20}
    base = {"experiment_id":"exp", "total_provider_calls":1, "pending_reservations":{"old":old},
            "pending_input_tokens":10, "pending_output_tokens":20, "pending_cost_usd":0.1,
            "known_input_tokens":5, "known_output_tokens":4, "known_estimated_cost_usd":0.3,
            "budget_admission_estimated_input_tokens":15, "budget_admission_estimated_output_tokens":24,
            "budget_admission_estimated_cost_usd":0.3+0.1}
    ctx = {"game_id":"g", "phase_id":"p", "agent_id":"a", "method":"reflect"}
    reserve = {**ctx, "reservation_id":"new", "status":"pending", "cost_usd":0.2,
               "input_tokens":30, "output_tokens":40}
    usage = {"input_tokens":7, "output_tokens":8, "estimated_cost_usd":0.04}
    request = {**ctx, **usage}
    final = {**base, "total_provider_calls":2, "pending_cost_usd":(0.1+0.2)-0.2,
             "known_input_tokens":12, "known_output_tokens":12, "known_estimated_cost_usd":0.3+0.04,
             "budget_admission_estimated_input_tokens":22, "budget_admission_estimated_output_tokens":32,
             "budget_admission_estimated_cost_usd":(0.3+0.04)+((0.1+0.2)-0.2)}
    events = [{"experiment_id":"exp", "event_type":"budget_usage_update", "payload":{"totals":base}},
              {"experiment_id":"exp", "event_type":"provider_reservation", "payload":reserve},
              {"experiment_id":"exp", "event_type":"budget_usage_update", "payload":{**ctx,"usage":usage,"totals":final}}]
    return base, final, events, [request]


def test_exact_replay_accepts_real_binary_drift():
    base, final, events, requests = fixture()
    assert base["pending_cost_usd"] != final["pending_cost_usd"]
    verify_pending_currency_trace(base, final, events, requests)


def test_incomplete_usage_snapshot_precedes_uncertain_baseline():
    base, final, events, requests = (copy.deepcopy(x) for x in fixture())
    context = {"game_id": "old-game", "phase_id": "old-phase", "agent_id": "old-agent", "method": "reflect"}
    base["pending_reservations"]["old"].update(context)
    final["pending_reservations"]["old"].update(context)
    snapshot = copy.deepcopy(base)
    snapshot["pending_reservations"]["old"]["status"] = "pending"
    events[0]["payload"] = {**context, "usage": {}, "totals": snapshot}
    events[-1]["payload"]["totals"]["pending_reservations"]["old"].update(context)
    verify_pending_currency_trace(base, final, events, requests)
    events[0]["payload"]["agent_id"] = "other"
    with pytest.raises(ValueError, match="baseline"):
        verify_pending_currency_trace(base, final, events, requests)


def test_real_incomplete_usage_lifecycle_and_float_drift(tmp_path):
    from llm_diplomacy.guardrails.budget import BudgetConfig, BudgetController
    budget = BudgetController(schema_version="1.0", experiment_id="exp", artifact_root=tmp_path,
        config=BudgetConfig(max_provider_calls=5, max_input_tokens=10000, max_output_tokens=10000,
                            max_estimated_cost_usd=1.0))
    context = dict(game_id="game_001", phase_id="phase", agent_id="agent", method="reflect")
    budget.reserve_before_call(reservation_id="old", **context, input_tokens=10, output_tokens=20, cost_usd=.1)
    budget.settle_reservation(reservation_id="old", usage={"input_tokens": 1, "output_tokens": None, "estimated_cost_usd": None})
    baseline = json.loads((tmp_path / "budget_state.json").read_bytes())
    budget.reserve_before_call(reservation_id="new", **context, input_tokens=30, output_tokens=40, cost_usd=.2)
    budget.settle_reservation(reservation_id="new", usage={"input_tokens": 7, "output_tokens": 8, "estimated_cost_usd": .04})
    current = json.loads((tmp_path / "budget_state.json").read_bytes())
    events = [json.loads(line) for line in (tmp_path / "budget_events.jsonl").read_bytes().splitlines()]
    assert baseline["pending_cost_usd"] != current["pending_cost_usd"]
    assert not any(event["event_type"] == "provider_reservation_uncertain" for event in events)
    verify_pending_currency_trace(baseline, current, events,
        [{**context, "input_tokens": 7, "output_tokens": 8, "estimated_cost_usd": .04}])


@pytest.mark.parametrize("mutation", ["reserve", "total", "ulp", "nan", "request", "usage", "missing", "extra", "known", "calls"])
def test_trace_rejects_real_changes(mutation):
    base, final, events, requests = (copy.deepcopy(x) for x in fixture())
    if mutation == "reserve": final["pending_reservations"]["old"]["cost_usd"] += 0.0000001
    if mutation == "total": final["pending_cost_usd"] += 0.0000000001
    if mutation == "ulp": final["pending_cost_usd"] = math.nextafter(final["pending_cost_usd"], math.inf)
    if mutation == "nan": final["pending_cost_usd"] = float("nan")
    if mutation == "request": requests[0]["estimated_cost_usd"] += 0.0000001
    if mutation == "usage": events[-1]["payload"]["usage"]["estimated_cost_usd"] += 0.0000001
    if mutation == "missing": events.pop(1)
    if mutation == "extra": events.append(copy.deepcopy(events[1]))
    if mutation == "known": final["known_estimated_cost_usd"] += 0.0000001
    if mutation == "calls": final["total_provider_calls"] += 1
    with pytest.raises(ValueError): verify_pending_currency_trace(base, final, events, requests)

def test_imported_timeout_reconciles_old_uncertain_reserve_with_float_drift(tmp_path):
    from llm_diplomacy.guardrails.budget import BudgetConfig, BudgetController
    budget = BudgetController(schema_version="1.0", experiment_id="exp", artifact_root=tmp_path,
        config=BudgetConfig(max_provider_calls=5, max_input_tokens=10000, max_output_tokens=10000, max_estimated_cost_usd=1.0))
    old = dict(game_id="g", phase_id="old", agent_id="a", method="reflect")
    new = dict(game_id="g", phase_id="new", agent_id="a", method="reflect")
    budget.reserve_before_call(reservation_id="old", **old, input_tokens=10, output_tokens=20, cost_usd=.1)
    budget.settle_reservation(reservation_id="old", usage={"input_tokens": 1, "output_tokens": None, "estimated_cost_usd": None})
    baseline = json.loads((tmp_path / "budget_state.json").read_bytes())
    budget.reserve_before_call(reservation_id="new", **new, input_tokens=30, output_tokens=40, cost_usd=.2)
    budget.settle_reservation(reservation_id="new", usage=None, uncertain=True)
    usage = {"input_tokens": 7, "output_tokens": 8, "estimated_cost_usd": .04}
    budget.settle_reservation(reservation_id="new", usage=usage)
    current = json.loads((tmp_path / "budget_state.json").read_bytes())
    events = [json.loads(line) for line in (tmp_path / "budget_events.jsonl").read_bytes().splitlines()]
    from llm_diplomacy.runner.build_rule_accounting import validate_postactivation_accounting
    decision = "g/new/once/france/a/reflect"
    request = {**new, "kind": "request", "error_type": "TimeoutError", "input_tokens": None,
               "output_tokens": None, "estimated_cost_usd": None}
    rows = [dict(kind="dispatch_prepared", sequence=1, attempt_id="a1", decision_id=decision, prompt_hash="h"),
            dict(kind="dispatch", sequence=2, attempt_id="a1", decision_id=decision, prompt_hash="h"),
            dict(kind="attempt", sequence=3, attempt_id="a1", decision_id=decision, prompt_hash="h", outcome="unknown"),
            dict(kind="attempt", sequence=5, attempt_id="a1", decision_id=decision, prompt_hash="h", outcome="received")]
    imports = {"a1": {"settled": True, "received": True, "observation": {"usage": usage, "reservation_id": "new"}}}
    validate_postactivation_accounting(baseline_ledger=b"", current_ledger=(json.dumps(request)+"\n").encode(),
        baseline_budget=baseline, current_budget=current, rows=rows, activation_sequence=0,
        budget_events=events, verified_imports=imports)
