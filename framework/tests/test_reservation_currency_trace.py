import copy
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
