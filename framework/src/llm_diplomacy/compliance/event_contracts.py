"""Offline, producer-compatible contracts for first-party event streams."""

from __future__ import annotations

import math
from typing import Any


STREAMS = {"events.jsonl", "experiment_events.jsonl", "budget_events.jsonl", "provider_attempts.jsonl"}
METHODS = {"negotiate_tick", "submit_orders", "reflect", "game_end_cleanup"}
POLICIES = {"stop_experiment", "stop_game_after_phase", "switch_to_fallback"}
PHASES = {"negotiation", "movement", "retreat", "adjustment"}
PROVIDER_KINDS = {"request", "blocked", "lifecycle", "eliminated_skip"}


# Field specs: required fields plus optional fields whose type is checked when present.
S, NS, I, NI, N, NN, B, D, ND, L, SL, DL, SM = (
    "str", "nullable_str", "int", "nullable_int", "number", "nullable_number",
    "bool", "dict", "nullable_dict", "list", "string_list", "dict_list", "string_map",
)

_EXPERIMENT_FIELDS: dict[str, tuple[dict[str, str], dict[str, str]]] = {
    "experiment_started": ({"games_requested": I}, {"max_years": I, "negotiation_ticks": I, "provider": D}),
    "experiment_stopped": ({"reason": S}, {"stop_policy": NS, "completed_games": I}),
    "experiment_error": ({"error": S}, {"game_id": S, "games_completed": I, "fallback_provider": S}),
    "systemic_provider_failure": ({"game_id": S, "provider_calls": I, "provider_errors": I, "provider": D}, {"phase_id": NS, "tick": NI, "error": S}),
    "game_started": ({"game_id": S, "game_index": I}, {"assignments": SM}),
    "game_completed": ({"game_id": S, "outcome": S}, {"winners": SL}),
    "game_stopped": ({"game_id": S, "reason": S}, {"stop_policy": NS}),
    "game_error": ({"game_id": S, "error": S}, {}),
    "game_summary": ({
        "game_id": S, "leader_country": NS, "leader_agent_id": NS, "leader_centers": NI,
        "most_active_diplomat": NS, "private_messages": I, "global_messages": I,
        "largest_private_pair": NS, "largest_private_pair_count": I, "fallbacks": I,
        "parse_failures": I, "negotiation_calls": I, "negotiation_parse_failures": I,
        "negotiation_schema_failures": I, "negotiation_truncated_outputs": I,
        "negotiation_fallbacks": I, "negotiation_fallback_rate": N,
        "negotiation_outputs_trimmed": I, "reflection_first_attempt_valid": I,
        "reflection_repair_success": I, "reflection_fallbacks": I, "result": S,
    }, {"log_lines": SL}),
    "observatory_generated": ({"game_id": S, "observatory_path": S}, {}),
    "orders_validated": ({}, {}),
    "phase_started": ({"game_id": S, "phase_id": S, "phase": S}, {"season": S, "year": I}),
    "phase_completed": ({"game_id": S, "phase_id": S, "phase": S}, {"snapshot_id": S}),
    "board_resolution_summary": ({"game_id": S, "phase_id": S, "phase": S, "summary": D, "resolution_artifact_path": S}, {"log_lines": SL}),
    "phase_resolved": ({"game_id": S, "phase_id": S, "valid_orders": I, "invalid_orders": I, "output_state_snapshot": S}, {}),
    "agent_invocation_started": ({"game_id": S, "phase_id": S, "agent_id": S, "country": NS, "runtime_kind": S, "method": S}, {"tick": NI, "postgame_eliminated_cleanup": B}),
    "agent_invocation_failed": ({"game_id": S, "phase_id": S, "agent_id": S, "country": NS, "runtime_kind": S, "method": S, "error": S}, {"tick": NI}),
    "agent_invocation_completed": ({
        "game_id": S, "phase_id": S, "agent_id": S, "country": NS, "runtime_kind": S,
        "method": S, "tick": NI, "ok": B, "fallback": B, "parse_error": B,
        "repair_count": I, "provider_model": NS, "provider_error": NS,
        "provider": D, "usage": D, "output_artifact_path": S,
    }, {
        "provider_error_details": ND, "log_lines": SL, "prompt_chars": NI,
        "estimated_input_tokens": NI, "prompt_hash": NS, "prompt_checks": ND,
        "prompt_assets": ND,
    }),
    "agent_invocation_skipped_eliminated": ({
        "game_id": S, "phase_id": S, "agent_id": S, "country": NS, "method": S,
        "elimination_lifecycle_version": S, "legacy_artifact": B,
        "skipped_provider_call": B, "elimination_records": L,
    }, {"tick": NI}),
    "eliminated_agent_final_cleanup_completed": ({"game_id": S, "phase_id": S, "agent_id": S, "elimination": L, "final_cleanup_usage": D}, {}),
    "power_eliminated": ({"game_id": S, "phase_id": S, "elimination": D}, {}),
    "prompt_assets_loaded": ({"game_id": S, "prompt_profile": S, "prompt_assets": D}, {}),
    "prompt_check": ({
        "game_id": S, "phase_id": S, "agent_id": S, "runtime_kind": S, "method": S,
        "tick": NI, "prompt_chars": I, "prompt_hash": S, "estimated_input_tokens": NI,
        "checks": D, "prompt_assets": D, "prompt_profile": S, "output_artifact_path": S,
    }, {}),
    "reflection_generated": ({"game_id": S, "phase_id": S, "agent_id": S, "country": NS, "reflection_summary": D, "output_artifact_path": S}, {"log_lines": SL}),
    "reflection_validated": ({"game_id": S, "phase_id": S, "agent_id": S, "country": NS, "fallback": B, "parse_error": B, "output_artifact_path": S}, {}),
    "reflection_fallback_applied": ({
        "game_id": S, "phase_id": S, "agent_id": S, "country": NS,
        "parse_error_category": NS, "schema_validation_error_category": NS,
        "likely_truncated": B, "repair_attempted": B, "repair_succeeded": B,
        "output_artifact_path": S,
    }, {}),
    "reflection_repaired": ({"game_id": S, "phase_id": S, "agent_id": S, "country": NS, "repair_attempts": I, "repair_success": B, "output_artifact_path": S}, {}),
    "orders_generated": ({"game_id": S, "phase_id": S, "agent_id": S, "country": NS, "submitted_count": I, "effective_count": I, "order_summaries": DL, "output_artifact_path": S}, {"log_lines": SL}),
    "orders_repaired": ({"game_id": S, "phase_id": S, "agent_id": S, "country": NS, "repair_attempts": I, "repair_success": B, "output_artifact_path": S}, {}),
    "orders_fallback_applied": ({"game_id": S, "phase_id": S, "agent_id": S, "country": NS, "invalid_reasons": L, "output_artifact_path": S}, {}),
    "relationship_snapshot": ({"game_id": S, "phase_id": S, "agent_id": S, "country": NS, "tick": I, "relationships": DL, "output_artifact_path": S}, {"log_lines": SL}),
    "negotiation_tick_participants": ({"game_id": S, "phase_id": S, "tick": I, "active_agent_ids": SL, "active_participant_count": I}, {}),
    "messages_generated": ({"game_id": S, "phase_id": S, "tick": I, "agent_id": S, "country": NS, "generated_count": I, "dropped_count": I, "message_summaries": DL, "output_artifact_path": S}, {"log_lines": SL}),
    "message_dropped": ({"game_id": S, "phase_id": S, "tick": I, "agent_id": S, "country": NS, "reason": S, "to_agent_id": NS, "output_artifact_path": S}, {}),
    "message_committed": ({"game_id": S, "phase_id": S, "tick": I, "released_count": I, "released_global": I, "released_private": I, "global_artifact_path": S, "private_artifact_root": S}, {}),
    "budget_state": ({"provider_calls": I, "stop_policy": S, "input_tokens": NI, "output_tokens": NI, "estimated_cost_usd": NN}, {"game_id": NS, "phase_id": NS, "stop_reason": NS}),
    "budget_stop_triggered": ({"provider_calls": I, "stop_policy": S, "stop_reason": S, "input_tokens": NI, "output_tokens": NI, "estimated_cost_usd": NN}, {"game_id": NS, "phase_id": NS}),
    "budget_warning": ({"game_id": NS, "phase_id": NS, "metric": S, "usage": N, "limit": N, "ratio": N}, {}),
    "budget_milestone": ({"game_id": NS, "phase_id": NS, "metric": S, "milestone": S, "usage": N, "limit": N, "ratio": N}, {}),
}


_EXPERIMENT_CHOICES = {
    "phase_started.phase": PHASES,
    "phase_completed.phase": PHASES,
    "board_resolution_summary.phase": {"movement", "retreat", "adjustment"},
    "agent_invocation_started.method": METHODS,
    "agent_invocation_started.runtime_kind": {"scripted", "llm"},
    "agent_invocation_failed.method": METHODS,
    "agent_invocation_failed.runtime_kind": {"scripted", "llm"},
    "agent_invocation_completed.method": METHODS,
    "agent_invocation_completed.runtime_kind": {"scripted", "llm"},
    "prompt_check.method": METHODS,
    "prompt_check.runtime_kind": {"llm"},
    "agent_invocation_skipped_eliminated.method": METHODS,
    "game_completed.outcome": {"completed", "solo_victory", "draw"},
    "budget_state.stop_policy": POLICIES,
    "budget_stop_triggered.stop_policy": POLICIES,
    "budget_warning.metric": {"provider_calls", "input_tokens", "output_tokens", "estimated_cost_usd"},
    "budget_milestone.metric": {"provider_calls", "input_tokens", "output_tokens", "estimated_cost_usd"},
    "budget_milestone.milestone": {"10%", "25%", "50%", "75%", "90%", "95%", "100%"},
}


def _valid(value: Any, spec: str) -> bool:
    if spec == S:
        return isinstance(value, str) and bool(value.strip())
    if spec == NS:
        return value is None or _valid(value, S)
    if spec == I:
        return type(value) is int and value >= 0
    if spec == NI:
        return value is None or _valid(value, I)
    if spec == N:
        if type(value) is int:
            return value >= 0
        return type(value) is float and math.isfinite(value) and value >= 0
    if spec == NN:
        return value is None or _valid(value, N)
    if spec == B:
        return type(value) is bool
    if spec == D:
        return isinstance(value, dict)
    if spec == ND:
        return value is None or _valid(value, D)
    if spec == L:
        return isinstance(value, list)
    if spec == SL:
        return isinstance(value, list) and all(_valid(item, S) for item in value)
    if spec == DL:
        return isinstance(value, list) and all(isinstance(item, dict) for item in value)
    if spec == SM:
        return isinstance(value, dict) and all(_valid(key, S) and _valid(item, S) for key, item in value.items())
    raise ValueError(f"unknown contract field type {spec}")


def _fields(
    obj: dict[str, Any], required: dict[str, str], optional: dict[str, str], prefix: str, errors: list[str]
) -> None:
    for field, spec in required.items():
        if field not in obj:
            errors.append(prefix + field + ": missing")
        elif not _valid(obj[field], spec):
            errors.append(prefix + field + ": invalid " + spec)
    for field, spec in optional.items():
        if field in obj and not _valid(obj[field], spec):
            errors.append(prefix + field + ": invalid " + spec)


def _choice(obj: dict[str, Any], field: str, choices: set[str], errors: list[str], prefix: str = "") -> None:
    if field in obj and _valid(obj[field], S) and obj[field] not in choices:
        errors.append(prefix + field + ": unsupported value")


def _terminal(result: dict[str, Any], errors: list[str], prefix: str) -> None:
    _fields(result, {"result": S, "winners": L, "country_results": D}, {"partial": B}, prefix, errors)
    _choice(result, "result", {"completed", "stopped", "solo_victory", "draw"}, errors, prefix)
    winners = result.get("winners")
    if isinstance(winners, list) and any(not _valid(winner, S) for winner in winners):
        errors.append(prefix + "winners: expected player IDs")
    countries = result.get("country_results")
    if isinstance(countries, dict):
        for index, (power, entry) in enumerate(countries.items()):
            location = prefix + "country_results/entry[" + str(index) + "]/"
            if not _valid(power, S):
                errors.append(prefix + "country_results: invalid power key")
            if not isinstance(entry, dict):
                errors.append(location + "invalid dict")
                continue
            _fields(entry, {"agent_id": S}, {"owned_centers": I}, location, errors)
    if result.get("result") == "stopped":
        _fields(result, {"stop_reason": S}, {}, prefix, errors)
        if result.get("partial") is not True or result.get("winners") != []:
            errors.append(prefix + "result: stopped requires partial=true and no winners")
    elif result.get("partial") is True:
        errors.append(prefix + "result: completed result cannot be partial")


def _validate_events(row: dict[str, Any], event: str, errors: list[str]) -> None:
    _fields(row, {"event_id": S, "game_id": S, "phase_id": NS, "payload": D}, {}, "", errors)
    payload = row.get("payload")
    if not isinstance(payload, dict):
        return
    if event == "game_initialized" or event == "snapshot_committed":
        _fields(payload, {"snapshot_id": S}, {}, "payload/", errors)
    elif event == "phase_started":
        _fields(row, {"phase_id": S}, {}, "", errors)
    elif event == "phase_completed":
        _fields(row, {"phase_id": S}, {}, "", errors)
        if row.get("phase_id") == "game_end":
            _fields(payload, {"result": D}, {}, "payload/", errors)
            if isinstance(payload.get("result"), dict):
                _terminal(payload["result"], errors, "payload/result/")
        else:
            _fields(payload, {"snapshot_id": S, "phase": S}, {}, "payload/", errors)
            _choice(payload, "phase", {"movement", "retreat", "adjustment"}, errors, "payload/")
    elif event == "negotiation_tick_staged":
        _fields(row, {"phase_id": S}, {}, "", errors)
        _fields(payload, {"tick_index": I, "agent_id": S, "staged_count": I}, {}, "payload/", errors)
    elif event == "negotiation_tick_committed":
        _fields(row, {"phase_id": S}, {}, "", errors)
        _fields(payload, {"tick_index": I, "released_count": I}, {}, "payload/", errors)
    elif event == "game_end_cleanup_applied":
        _fields(payload, {"applied_count": I, "skipped_count": I}, {}, "payload/", errors)


def _validate_budget_event(event: str, row: dict[str, Any], errors: list[str]) -> None:
    payload = row.get("payload")
    if not isinstance(payload, dict):
        errors.append("payload: invalid dict")
        return
    common = {"game_id": S, "phase_id": S, "agent_id": S, "method": S}
    if event in {"budget_check", "budget_exceeded", "budget_usage_update", "provider_error"}:
        _fields(payload, common, {}, "payload/", errors)
        _choice(payload, "method", METHODS, errors, "payload/")
    if event == "budget_check":
        _fields(payload, {"allow_provider_call": B}, {}, "payload/", errors)
    elif event == "budget_exceeded":
        _fields(payload, {"reason": S, "stop_policy": S}, {}, "payload/", errors)
        _choice(payload, "stop_policy", POLICIES, errors, "payload/")
    elif event == "provider_error":
        _fields(payload, {"error": S}, {"provider_error": D}, "payload/", errors)
    elif event == "budget_usage_update":
        _fields(payload, {"usage": D, "totals": D}, {}, "payload/", errors)
        usage = payload.get("usage")
        if isinstance(usage, dict):
            for field, spec in (("prompt_tokens", NI), ("completion_tokens", NI), ("total_tokens", NI), ("input_tokens", NI), ("output_tokens", NI), ("estimated_cost_usd", NN)):
                if field in usage and not _valid(usage[field], spec):
                    errors.append(f"payload/usage/{field}: invalid {spec}")
        totals = payload.get("totals")
        if isinstance(totals, dict):
            _fields(totals, {"total_provider_calls": I, "total_input_tokens": NI, "total_output_tokens": NI, "total_estimated_cost_usd": NN}, {}, "payload/totals/", errors)
    elif event == "provider_reservation":
        _fields(payload, {"reservation_id": S, "game_id": S, "phase_id": S, "agent_id": S,
                          "method": S, "input_tokens": I, "output_tokens": I,
                          "cost_usd": NN, "status": S}, {}, "payload/", errors)
        _choice(payload, "method", METHODS, errors, "payload/")
        _choice(payload, "status", {"pending"}, errors, "payload/")
    elif event == "provider_reservation_uncertain":
        _fields(payload, {"reservation_id": S}, {}, "payload/", errors)


def _validate_provider_attempt(row: dict[str, Any], errors: list[str]) -> None:
    kind = row.get("kind")
    _fields(row, {"kind": S, "agent_id": S, "game_id": S, "phase_id": S, "method": S, "post_elimination_cleanup": B}, {}, "", errors)
    _choice(row, "kind", PROVIDER_KINDS, errors)
    _choice(row, "method", METHODS, errors)
    if row.get("post_elimination_cleanup") is True and row.get("method") != "game_end_cleanup":
        errors.append("post_elimination_cleanup: requires cleanup method")
    if kind in ("request", "blocked"):
        _fields(row, {"attempt_index": I, "provider": D, "error_type": NS, "resolved_model": NS, "input_tokens": NI, "output_tokens": NI, "estimated_cost_usd": NN}, {"cached_input_tokens": NI, "reasoning_tokens": NI}, "", errors)
        if kind == "blocked" and any(row.get(key) is not None for key in ("error_type", "resolved_model", "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "estimated_cost_usd")):
            errors.append("kind: blocked attempts cannot record a response or billed usage")
    elif kind == "lifecycle":
        _fields(row, {"fallback": B}, {}, "", errors)


def _validate_experiment_event(event: str, row: dict[str, Any], errors: list[str]) -> None:
    if event == "experiment_completed":
        alternatives = [field for field in ("completed_games", "games_completed") if field in row]
        if not alternatives:
            errors.append("completed_games/games_completed: missing")
        for field in alternatives:
            _fields(row, {field: I}, {}, "", errors)
        if len(alternatives) == 2 and row[alternatives[0]] != row[alternatives[1]]:
            errors.append("completed_games/games_completed: conflicting counters")
        if "games_requested" in row:
            _fields(row, {}, {"games_requested": I}, "", errors)
        return
    spec = _EXPERIMENT_FIELDS.get(event)
    if spec is None:
        return
    required, optional = spec
    _fields(row, required, optional, "", errors)
    for path, choices in _EXPERIMENT_CHOICES.items():
        target_event, field = path.split(".", 1)
        if event == target_event:
            _choice(row, field, choices, errors)
    if event == "experiment_stopped" and row.get("stop_policy") is not None:
        _choice(row, "stop_policy", POLICIES, errors)
    elif event == "game_stopped" and row.get("stop_policy") is not None:
        _choice(row, "stop_policy", POLICIES, errors)
    elif event == "game_completed":
        if row.get("partial") is True:
            errors.append("partial: completed event cannot be partial")
        if "partial" in row and not _valid(row["partial"], B):
            errors.append("partial: invalid bool")
        if "winners" in row and not _valid(row["winners"], SL):
            errors.append("winners: invalid string_list")
    elif event == "phase_started":
        _fields(row, {"phase_id": S}, {}, "", errors)
        _choice(row, "phase", PHASES, errors)
    elif event == "phase_completed":
        _choice(row, "phase", PHASES, errors)
    elif event == "board_resolution_summary":
        _choice(row, "phase", {"movement", "retreat", "adjustment"}, errors)
    elif event == "agent_invocation_started" or event == "agent_invocation_failed" or event == "agent_invocation_completed":
        _choice(row, "method", METHODS, errors)
        _choice(row, "runtime_kind", {"scripted", "llm"}, errors)
    elif event == "agent_invocation_skipped_eliminated":
        _choice(row, "method", METHODS, errors)
    elif event == "budget_state" or event == "budget_stop_triggered":
        _choice(row, "stop_policy", POLICIES, errors)
    elif event in {"budget_warning", "budget_milestone"}:
        _choice(row, "metric", {"provider_calls", "input_tokens", "output_tokens", "estimated_cost_usd"}, errors)
        if event == "budget_milestone":
            _choice(row, "milestone", {"10%", "25%", "50%", "75%", "90%", "95%", "100%"}, errors)

    if event == "game_started" and "assignments" in row and isinstance(row["assignments"], dict):
        if any(not _valid(power, S) or not _valid(agent_id, S) for power, agent_id in row["assignments"].items()):
            errors.append("assignments: expected power-to-player IDs")
    elif event == "orders_validated":
        _fields(row, {"game_id": S, "phase_id": S}, {}, "", errors)
        if "agent_id" in row:
            _fields(row, {"agent_id": S, "country": NS, "invalid_reasons": L, "output_artifact_path": S}, {}, "", errors)
        else:
            _fields(row, {"submitted_count": I, "validated_count": I, "invalid_count": I}, {}, "", errors)
    elif event == "systemic_provider_failure" and "phase_id" in row and row["phase_id"] is not None:
        _fields(row, {}, {"phase_id": S}, "", errors)
    elif event == "budget_state" or event == "budget_stop_triggered":
        if row.get("stop_reason") is not None and event == "budget_state":
            _fields(row, {}, {"stop_reason": S}, "", errors)


def validate_core_event(row: dict[str, Any], stream: str) -> list[str]:
    """Validate one core event row; unknown event types receive envelope checks only."""
    if not isinstance(stream, str) or stream not in STREAMS:
        return []
    errors: list[str] = []
    if "event_contract_version" in row and row["event_contract_version"] != "core_events_v1":
        errors.append("event_contract_version: unsupported version")

    if stream == "provider_attempts.jsonl":
        _validate_provider_attempt(row, errors)
        return errors

    _fields(row, {"event_type": S, "experiment_id": S, "timestamp": S}, {}, "", errors)
    event = row.get("event_type")
    if not _valid(event, S):
        return errors
    if stream == "events.jsonl":
        _validate_events(row, event, errors)
    elif stream == "budget_events.jsonl":
        payload = row.get("payload")
        if not _valid(payload, D):
            errors.append("payload: invalid dict")
        else:
            _validate_budget_event(event, row, errors)
    else:
        _validate_experiment_event(event, row, errors)
    return errors
