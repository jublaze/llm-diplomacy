"""Replay durable reservation operations to verify binary-float aggregates exactly."""
import math


def verify_pending_currency_trace(baseline, current, events, requests):
    keys = ("total_provider_calls", "pending_reservations", "pending_input_tokens", "pending_output_tokens",
            "pending_cost_usd", "known_input_tokens", "known_output_tokens", "known_estimated_cost_usd",
            "budget_admission_estimated_input_tokens", "budget_admission_estimated_output_tokens",
            "budget_admission_estimated_cost_usd")
    matches = [i for i, e in enumerate(events) if e.get("event_type") == "budget_usage_update"
               and isinstance(e.get("payload", {}).get("totals"), dict)
               and all(e["payload"]["totals"].get(k) == baseline.get(k) for k in keys)]
    if len(matches) != 1:
        raise ValueError("reservation trace baseline is missing or ambiguous")
    pending = {k: dict(v) for k, v in baseline["pending_reservations"].items()}
    cost = baseline["pending_cost_usd"]
    in_tokens, out_tokens = baseline["pending_input_tokens"], baseline["pending_output_tokens"]
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
        raise ValueError("invalid baseline reservation cost")
    calls = baseline["total_provider_calls"]
    totals = baseline
    known_in, known_out, known_cost = (baseline["known_input_tokens"], baseline["known_output_tokens"],
                                       baseline["known_estimated_cost_usd"])
    offset = 0
    active = None
    for event in events[matches[0] + 1:]:
        if event.get("experiment_id") != baseline.get("experiment_id"):
            raise ValueError("reservation trace experiment mismatch")
        kind, payload = event.get("event_type"), event.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("invalid reservation trace event")
        if kind == "provider_reservation":
            if active is not None or offset >= len(requests):
                raise ValueError("ambiguous reservation trace")
            key = payload.get("reservation_id")
            request = requests[offset]
            if (not isinstance(key, str) or not key or key in pending or payload.get("status") != "pending"
                    or any(payload.get(k) != request.get(k) for k in ("game_id", "phase_id", "agent_id", "method"))):
                raise ValueError("reservation trace request mismatch")
            value = payload.get("cost_usd")
            if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
                    or any(isinstance(payload.get(k), bool) or not isinstance(payload.get(k), int) or payload[k] < 0
                           for k in ("input_tokens", "output_tokens"))):
                raise ValueError("invalid reservation trace amount")
            pending[key] = dict(payload)
            in_tokens += payload["input_tokens"]
            out_tokens += payload["output_tokens"]
            cost += value
            calls += 1
            active = key
        elif kind == "budget_usage_update":
            if active is None:
                raise ValueError("reservation trace usage lacks its reservation")
            reserve, request = pending.pop(active), requests[offset]
            usage, totals = payload.get("usage"), payload.get("totals")
            if (not isinstance(usage, dict) or not isinstance(totals, dict)
                    or any(payload.get(k) != reserve.get(k) for k in ("game_id", "phase_id", "agent_id", "method"))
                    or any(usage.get(k) != request.get(k) for k in ("input_tokens", "output_tokens", "estimated_cost_usd"))):
                raise ValueError("reservation trace usage differs from ledger")
            in_tokens -= reserve["input_tokens"]
            out_tokens -= reserve["output_tokens"]
            cost -= reserve["cost_usd"]
            known_in += usage["input_tokens"]
            known_out += usage["output_tokens"]
            known_cost += usage["estimated_cost_usd"]
            if (totals.get("pending_reservations") != pending or totals.get("pending_input_tokens") != in_tokens
                    or totals.get("pending_output_tokens") != out_tokens or totals.get("pending_cost_usd") != cost
                    or totals.get("total_provider_calls") != calls or totals.get("known_input_tokens") != known_in
                    or totals.get("known_output_tokens") != known_out or totals.get("known_estimated_cost_usd") != known_cost
                    or totals.get("budget_admission_estimated_input_tokens") != known_in + in_tokens
                    or totals.get("budget_admission_estimated_output_tokens") != known_out + out_tokens
                    or totals.get("budget_admission_estimated_cost_usd") != known_cost + cost):
                raise ValueError("reservation trace totals do not follow recorded operations")
            active = None
            offset += 1
        else:
            raise ValueError("unresolved or unexpected reservation trace event")
    if (active is not None or offset != len(requests) or pending != current.get("pending_reservations")
            or in_tokens != current.get("pending_input_tokens") or out_tokens != current.get("pending_output_tokens")
            or cost != current.get("pending_cost_usd") or calls != current.get("total_provider_calls")
            or any(totals.get(key) != current.get(key) for key in keys)):
        raise ValueError("reservation trace does not reconcile final budget")
