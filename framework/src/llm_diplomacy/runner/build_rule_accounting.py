"""Fail-closed post-activation accounting check for a reviewed baseline.

Not installed by recovery or an authorization to dispatch provider requests.
"""
from __future__ import annotations

import json
import math
from typing import Any


def validate_postactivation_accounting(*, baseline_ledger: bytes, current_ledger: bytes,
        baseline_budget: dict[str, Any], current_budget: dict[str, Any],
        rows: list[dict[str, Any]], activation_sequence: int) -> None:
    if not current_ledger.startswith(baseline_ledger) or (baseline_ledger and not baseline_ledger.endswith(b"\n")):
        raise ValueError("provider ledger prefix changed")
    static = ("schema_version", "experiment_id", "config_fingerprint", "max_provider_calls",
              "max_input_tokens", "max_output_tokens", "max_estimated_cost_usd", "stop_policy")
    if any(current_budget.get(key) != baseline_budget.get(key) for key in static):
        raise ValueError("budget configuration changed")
    if current_budget.get("pending_reservations") != baseline_budget.get("pending_reservations"):
        raise ValueError("historical reserves changed or new unresolved exposure exists")
    for field in ("pending_input_tokens", "pending_output_tokens", "pending_cost_usd"):
        if current_budget.get(field) != baseline_budget.get(field):
            raise ValueError("historical reserved totals changed")
    if any(current_budget.get(key) for key in ("stop_experiment", "stop_game_after_phase", "force_fallback")):
        raise ValueError("budget is stopped or fallback is active")
    try:
        additions = [json.loads(line) for line in current_ledger[len(baseline_ledger):].splitlines() if line]
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("post-activation provider ledger is invalid") from exc
    if any(not isinstance(row, dict) or row.get("kind") not in {"request", "blocked", "lifecycle", "eliminated_skip"}
           for row in additions):
        raise ValueError("post-activation provider ledger contains an unknown row")
    requests = [row for row in additions if row.get("kind") == "request"]
    if any(row.get("kind") == "blocked" for row in additions):
        raise ValueError("post-activation blocked provider request requires review")
    dispatches = [row for row in rows if row.get("kind") == "dispatch" and row.get("sequence", -1) > activation_sequence]
    prepared = [row for row in rows if row.get("kind") == "dispatch_prepared" and row.get("sequence", -1) > activation_sequence]
    outcomes = [row for row in rows if row.get("kind") == "attempt" and row.get("sequence", -1) > activation_sequence]
    if len(requests) != len(dispatches):
        raise ValueError("post-activation requests do not match durable dispatches")
    if len(prepared) != len(dispatches) or len(outcomes) != len(dispatches):
        raise ValueError("post-activation prepared, dispatch and response records do not match")
    if len({row.get("attempt_id") for row in dispatches}) != len(dispatches):
        raise ValueError("post-activation dispatch identities are duplicated")
    totals = {"input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0}
    for request, dispatch in zip(requests, dispatches):
        parts = dispatch.get("decision_id", "").split("/")
        if len(parts) != 6 or any(request.get(key) != value for key, value in
                {"game_id": parts[0], "phase_id": parts[1], "agent_id": parts[4], "method": parts[5]}.items()):
            raise ValueError("post-activation request and dispatch identity mismatch")
        results = [row for row in rows if row.get("kind") == "attempt" and row.get("attempt_id") == dispatch.get("attempt_id")]
        starts = [row for row in prepared if row.get("attempt_id") == dispatch.get("attempt_id")]
        if (request.get("error_type") or len(results) != 1 or results[0].get("outcome") != "received"
                or len(starts) != 1 or starts[0].get("decision_id") != dispatch.get("decision_id")
                or starts[0].get("prompt_hash") != dispatch.get("prompt_hash")
                or not (activation_sequence < starts[0].get("sequence", -1) < dispatch.get("sequence", -1)
                        < results[0].get("sequence", -1))
                or results[0].get("decision_id") != dispatch.get("decision_id")
                or results[0].get("prompt_hash") != dispatch.get("prompt_hash")):
            raise ValueError("post-activation provider outcome requires review")
        usage = results[0].get("usage")
        if not isinstance(usage, dict):
            raise ValueError("post-activation provider usage is incomplete")
        for key in totals:
            value = request.get(key)
            if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
                    or key != "estimated_cost_usd" and not isinstance(value, int)):
                raise ValueError("post-activation provider usage is incomplete")
            actual = usage.get(key)
            if (isinstance(actual, bool) or not isinstance(actual, (int, float)) or not math.isfinite(actual)
                    or abs(actual - value) > 1e-8):
                raise ValueError("post-activation journal usage differs from ledger")
            totals[key] += value
    if current_budget.get("total_provider_calls") != baseline_budget.get("total_provider_calls", -1) + len(requests):
        raise ValueError("post-activation provider call count differs from budget")
    for key, increment in totals.items():
        before, after = baseline_budget.get("known_" + key), current_budget.get("known_" + key)
        if (isinstance(before, bool) or not isinstance(before, (int, float)) or not math.isfinite(before)
                or isinstance(after, bool) or not isinstance(after, (int, float)) or not math.isfinite(after)
                or abs(after - before - increment) > 1e-8):
            raise ValueError("post-activation known usage differs from ledger")
        old_total, new_total = baseline_budget.get("total_" + key), current_budget.get("total_" + key)
        if old_total is None:
            if new_total is not None:
                raise ValueError("uncertain historical total was cleared")
        elif (isinstance(old_total, bool) or not isinstance(old_total, (int, float))
              or isinstance(new_total, bool) or not isinstance(new_total, (int, float))
              or not math.isfinite(old_total) or not math.isfinite(new_total)
              or abs(new_total - old_total - increment) > 1e-8):
            raise ValueError("post-activation total usage differs from ledger")
        admission_key = ("budget_admission_estimated_cost_usd" if key == "estimated_cost_usd"
                         else "budget_admission_estimated_" + key)
        old_admission, new_admission = baseline_budget.get(admission_key), current_budget.get(admission_key)
        if (isinstance(old_admission, bool) or not isinstance(old_admission, (int, float))
                or isinstance(new_admission, bool) or not isinstance(new_admission, (int, float))
                or not math.isfinite(old_admission) or not math.isfinite(new_admission)
                or abs(new_admission - old_admission - increment) > 1e-8):
            raise ValueError("post-activation budget admission differs from known usage and reserves")
        limit = current_budget.get("max_" + key)
        if limit is not None and new_admission > limit:
            raise ValueError("post-activation budget limit is exceeded")
    call_limit = current_budget.get("max_provider_calls")
    if call_limit is not None and current_budget["total_provider_calls"] > call_limit:
        raise ValueError("post-activation call limit is exceeded")
