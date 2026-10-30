"""Fail-closed post-activation accounting check for a reviewed baseline.

Not installed by recovery or an authorization to dispatch provider requests.
"""
from __future__ import annotations

import json
import math
from typing import Any


def validate_postactivation_accounting(*, baseline_ledger: bytes, current_ledger: bytes,
        baseline_budget: dict[str, Any], current_budget: dict[str, Any],
        rows: list[dict[str, Any]], activation_sequence: int,
        budget_events: list[dict[str, Any]] | None = None,
        verified_imports: dict[str, Any] | None = None) -> None:
    if not current_ledger.startswith(baseline_ledger) or (baseline_ledger and not baseline_ledger.endswith(b"\n")):
        raise ValueError("provider ledger prefix changed")
    static = ("schema_version", "experiment_id", "config_fingerprint", "max_provider_calls",
              "max_input_tokens", "max_output_tokens", "max_estimated_cost_usd", "stop_policy")
    if any(current_budget.get(key) != baseline_budget.get(key) for key in static):
        raise ValueError("budget configuration changed")
    old_reserves = baseline_budget.get("pending_reservations")
    reserves = current_budget.get("pending_reservations")
    if not isinstance(old_reserves, dict) or not isinstance(reserves, dict) or any(
            reserves.get(key) != value for key, value in old_reserves.items()):
        raise ValueError("historical reserves changed")
    additional_reserves = {key: value for key, value in reserves.items() if key not in old_reserves}
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
    imports = verified_imports or {}
    extra_import_outcomes = sum(1 for item in imports.values() if item['received'])
    missing = [d for d in dispatches if not any(o.get('attempt_id') == d.get('attempt_id') for o in outcomes)]
    if (len(missing) > 1 or (missing and missing[0] != dispatches[-1])
            or len(prepared) != len(dispatches) or len(outcomes) + len(missing) != len(dispatches) + extra_import_outcomes):
        raise ValueError("post-activation prepared, dispatch and response records do not match")
    if len({row.get("attempt_id") for row in dispatches}) != len(dispatches):
        raise ValueError("post-activation dispatch identities are duplicated")
    totals = {"input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0}
    effective_requests = list(requests)
    failures = []
    for index, (request, dispatch) in enumerate(zip(requests, dispatches)):
        parts = dispatch.get("decision_id", "").split("/")
        if len(parts) != 6 or any(request.get(key) != value for key, value in
                {"game_id": parts[0], "phase_id": parts[1], "agent_id": parts[4], "method": parts[5]}.items()):
            raise ValueError("post-activation request and dispatch identity mismatch")
        results = [row for row in rows if row.get("kind") == "attempt" and row.get("attempt_id") == dispatch.get("attempt_id")]
        starts = [row for row in prepared if row.get("attempt_id") == dispatch.get("attempt_id")]
        if not results:
            if request.get('error_type') != 'TimeoutError' or any(request.get(key) is not None for key in totals):
                raise ValueError('unrecorded outcome requires retained unknown request evidence')
            results = [{**dispatch, 'sequence': dispatch['sequence'] + .5, 'outcome': 'unknown',
                        'error_category': 'provider_timeout', 'response': None, 'usage': None}]
        imported = imports.get(dispatch.get("attempt_id"))
        if imported and imported['settled']:
            if (request.get('error_type') != 'TimeoutError' or any(request.get(key) is not None for key in totals)
                    or not results or results[0].get('outcome') != 'unknown'):
                raise ValueError('provider import does not match the original request')
            observation = imported['observation']
            results = [{**results[0], 'outcome': 'received', 'usage': observation['usage']}]
            request = {**request, 'error_type': None, **{key: observation['usage'][key] for key in totals}}
            effective_requests[index] = request
        if (len(results) != 1 or len(starts) != 1 or starts[0].get("decision_id") != dispatch.get("decision_id")
                or starts[0].get("prompt_hash") != dispatch.get("prompt_hash")
                or not (activation_sequence < starts[0].get("sequence", -1) < dispatch.get("sequence", -1)
                        < results[0].get("sequence", -1))
                or results[0].get("decision_id") != dispatch.get("decision_id")
                or results[0].get("prompt_hash") != dispatch.get("prompt_hash")):
            raise ValueError("post-activation provider outcome requires review")
        result = results[0]
        if result.get("outcome") in {"known_failed", "unknown"}:
            expected_error = {"known_failed": ("provider_transient_http", "ProviderHTTPError"),
                              "unknown": ("provider_timeout", "TimeoutError")}[result["outcome"]]
            if (((result.get("error_category"), request.get("error_type")) != expected_error
                    and not (result.get("outcome") == "unknown" and result.get("error_category") == "process_interrupted"
                             and request.get("error_type") == "TimeoutError"))
                    or result.get("response") is not None or result.get("usage") is not None
                    or any(request.get(key) is not None for key in totals)):
                raise ValueError("post-activation provider outcome requires review")
            next_start = next((item.get("started_at") for item in prepared[index + 1:]
                               if item.get("started_at")), None)
            failures.append({"attempt_id": dispatch["attempt_id"], "experiment_id": current_budget["experiment_id"],
                             "identity": {"game_id": parts[0], "phase_id": parts[1], "agent_id": parts[4], "method": parts[5]},
                             "started_at": starts[0]["started_at"], "next_started_at": next_start})
            continue
        if request.get("error_type") or result.get("outcome") != "received":
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
                    or actual != value):
                raise ValueError("post-activation journal usage differs from ledger")
            totals[key] += value
    from .known_http_reservations import match_http_reservations
    match_http_reservations(failures, additional_reserves, budget_events or [])
    reserve_increments = {"input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0}
    for reserve in additional_reserves.values():
        for key, field in (("input_tokens", "input_tokens"), ("output_tokens", "output_tokens"),
                           ("estimated_cost_usd", "cost_usd")):
            value = reserve.get(field)
            if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
                    or key != "estimated_cost_usd" and not isinstance(value, int)):
                raise ValueError("retained HTTP reservation is invalid")
            reserve_increments[key] += value
    for key, field in (("input_tokens", "pending_input_tokens"), ("output_tokens", "pending_output_tokens"),
                       ("estimated_cost_usd", "pending_cost_usd")):
        before, after = baseline_budget.get(field), current_budget.get(field)
        if (any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
                or key != "estimated_cost_usd" and not isinstance(value, int) for value in (before, after))
                or (key != "estimated_cost_usd" or additional_reserves)
                   and abs(after - before - reserve_increments[key]) > 1e-8):
            raise ValueError("historical reserved totals changed")
        if key == "estimated_cost_usd" and not additional_reserves and after != before:
            if not budget_events:
                raise ValueError("reservation trace required for changed pending cost")
            from .reservation_currency_trace import verify_pending_currency_trace
            verify_pending_currency_trace(baseline_budget, current_budget, budget_events, effective_requests,
                imported_reservations={item['observation']['reservation_id'] for item in imports.values() if item['settled']})
    if current_budget.get("total_provider_calls") != baseline_budget.get("total_provider_calls", -1) + len(requests):
        raise ValueError("post-activation provider call count differs from budget")
    for key, increment in totals.items():
        before, after = baseline_budget.get("known_" + key), current_budget.get("known_" + key)
        if (isinstance(before, bool) or not isinstance(before, (int, float)) or not math.isfinite(before)
                or isinstance(after, bool) or not isinstance(after, (int, float)) or not math.isfinite(after)
                or abs(after - before - increment) > 1e-8):
            raise ValueError("post-activation known usage differs from ledger")
        old_total, new_total = baseline_budget.get("total_" + key), current_budget.get("total_" + key)
        if old_total is None or additional_reserves:
            if new_total is not None:
                raise ValueError("uncertain historical total was cleared")
        elif new_total is None and any(item['settled'] for item in imports.values()):
            # A verified import settles its reserve before reconciling previously unknown totals.
            pass
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
              or abs(new_admission - old_admission - increment - reserve_increments[key]) > 1e-8):
            raise ValueError("post-activation budget admission differs from known usage and reserves")
        limit = current_budget.get("max_" + key)
        if limit is not None and new_admission > limit:
            raise ValueError("post-activation budget limit is exceeded")
    call_limit = current_budget.get("max_provider_calls")
    if call_limit is not None and current_budget["total_provider_calls"] > call_limit:
        raise ValueError("post-activation call limit is exceeded")
