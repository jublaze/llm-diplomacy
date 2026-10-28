from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GameObservatoryData:
    game_dir: Path
    phases: list[str]
    events: list[dict[str, Any]]
    global_messages: list[dict[str, Any]]
    private_messages: list[dict[str, Any]]
    phase_submitted_orders: dict[str, list[dict[str, Any]]]
    phase_resolutions: dict[str, dict[str, Any]]
    phase_reflections: dict[str, dict[str, Any]]
    snapshots: list[dict[str, Any]]
    assignments_by_power: dict[str, str]
    runtime_outputs_by_agent: dict[str, dict[str, dict[str, Any]]]


def load_game(game_dir: str | Path) -> GameObservatoryData:
    root = Path(game_dir)
    events = _read_jsonl(root / "logs" / "events.jsonl")
    global_messages = _read_jsonl(root / "messages" / "global" / "global.jsonl")
    private_messages: list[dict[str, Any]] = []
    private_root = root / "messages" / "private"
    if private_root.exists():
        for inbox in sorted(private_root.rglob("inbox.jsonl")):
            private_messages.extend(_read_jsonl(inbox))

    phase_submitted_orders: dict[str, list[dict[str, Any]]] = {}
    phase_resolutions: dict[str, dict[str, Any]] = {}
    phase_reflections: dict[str, dict[str, Any]] = {}
    phases_root = root / "phases"
    phases = []
    if phases_root.exists():
        # The canonical phase key orders spring before fall and retreat before
        # later seasonal phases; lexical names incorrectly put fall first.
        from .event_index import phase_sort_key

        for phase_dir in sorted(
            [p for p in phases_root.iterdir() if p.is_dir()], key=lambda p: phase_sort_key(p.name)
        ):
            phase_id = phase_dir.name
            phases.append(phase_id)
            submitted_file = phase_dir / "orders" / "submitted_orders.json"
            resolution_file = phase_dir / "resolution" / "resolution.json"
            reflection_dir = phase_dir / "reflection"
            if submitted_file.exists():
                submitted_payload = json.loads(submitted_file.read_text(encoding="utf-8"))
                phase_submitted_orders[phase_id] = list(submitted_payload.get("orders", []))
            if resolution_file.exists():
                phase_resolutions[phase_id] = json.loads(resolution_file.read_text(encoding="utf-8"))
            if reflection_dir.exists():
                for reflection_file in sorted(reflection_dir.glob("*.json")):
                    agent_id = reflection_file.stem
                    phase_reflections[f"{phase_id}:{agent_id}"] = json.loads(
                        reflection_file.read_text(encoding="utf-8")
                    )

    snapshots = []
    snapshots_dir = root / "state" / "snapshots"
    if snapshots_dir.exists():
        for snapshot_file in sorted(snapshots_dir.glob("*.json")):
            snapshots.append(json.loads(snapshot_file.read_text(encoding="utf-8")))

    assignments_by_power = _extract_assignments(events)
    runtime_outputs_by_agent = _load_runtime_outputs(root)
    return GameObservatoryData(
        game_dir=root,
        phases=phases,
        events=events,
        global_messages=global_messages,
        private_messages=private_messages,
        phase_submitted_orders=phase_submitted_orders,
        phase_resolutions=phase_resolutions,
        phase_reflections=phase_reflections,
        snapshots=snapshots,
        assignments_by_power=assignments_by_power,
        runtime_outputs_by_agent=runtime_outputs_by_agent,
    )


def summarize_agent(data: GameObservatoryData, power: str) -> str:
    agent_id = data.assignments_by_power.get(power, "unknown_agent")
    phase_rows = []
    baseline = _initial_baseline_state(data)
    previous_units = {
        str(unit.get("unit_id"))
        for unit in baseline.get("units", [])
        if isinstance(unit, dict) and unit.get("owner") == power and unit.get("status") == "active"
    }
    previous_centers = {
        province_id
        for province_id, owner in (baseline.get("supply_ownership", {}) or {}).items()
        if owner == power
    }
    for phase_id in data.phases:
        submitted = [row for row in data.phase_submitted_orders.get(phase_id, []) if row.get("power") == power]
        resolution = data.phase_resolutions.get(phase_id, {})
        output_state = resolution.get("output_state", {})
        units = {
            str(unit.get("unit_id"))
            for unit in output_state.get("units", [])
            if unit.get("owner") == power and unit.get("status") == "active"
        }
        centers = {
            province_id
            for province_id, owner in (output_state.get("supply_ownership", {}) or {}).items()
            if owner == power
        }
        gained_units = sorted(units - previous_units)
        lost_units = sorted(previous_units - units)
        gained_centers = sorted(centers - previous_centers)
        lost_centers = sorted(previous_centers - centers)
        dislodgements = len(
            [
                row
                for row in resolution.get("movement_outcomes", [])
                if row.get("result") == "dislodged"
                and any(
                    unit.get("unit_id") == row.get("unit_id") and unit.get("owner") == power
                    for unit in output_state.get("units", [])
                )
            ]
        )
        retreats_required = len(
            [
                unit
                for unit in output_state.get("units", [])
                if unit.get("owner") == power and (unit.get("must_retreat") or unit.get("status") == "dislodged")
            ]
        )
        previous_units = units
        previous_centers = centers

        phase_rows.append(
            "\n".join(
                [
                    f"## {phase_id}",
                    f"- `agent_id`: `{agent_id}`",
                    f"- `orders_submitted`: {len(submitted)}",
                    f"- `units_gained`: {gained_units if gained_units else '[]'}",
                    f"- `units_lost`: {lost_units if lost_units else '[]'}",
                    f"- `centers_gained`: {gained_centers if gained_centers else '[]'}",
                    f"- `centers_lost`: {lost_centers if lost_centers else '[]'}",
                    f"- `dislodgements`: {dislodgements}",
                    f"- `retreats_required`: {retreats_required}",
                    f"- `orders_artifact`: `phases/{phase_id}/orders/submitted_orders.json`",
                    f"- `resolution_artifact`: `phases/{phase_id}/resolution/resolution.json`",
                    f"- `reflection_artifact`: `phases/{phase_id}/reflection/{agent_id}.json`",
                ]
            )
        )
        llm_key = f"{phase_id}_submit_orders"
        llm_output = data.runtime_outputs_by_agent.get(agent_id, {}).get(llm_key, {})
        if isinstance(llm_output, dict) and isinstance(llm_output.get("llm_meta"), dict):
            llm_meta = llm_output["llm_meta"]
            order_grounding = llm_meta.get("order_grounding", {})
            submitted_orders = []
            effective_orders = []
            invalid_reasons = []
            if isinstance(order_grounding, dict):
                submitted_orders = order_grounding.get("submitted_orders", []) if isinstance(order_grounding.get("submitted_orders"), list) else []
                effective_orders = order_grounding.get("effective_orders", []) if isinstance(order_grounding.get("effective_orders"), list) else []
                invalid_reasons = order_grounding.get("invalid_reasons", []) if isinstance(order_grounding.get("invalid_reasons"), list) else []
            phase_rows.append(
                "\n".join(
                    [
                        f"### {phase_id} LLM Meta",
                        f"- `runtime_output_path`: `agents/{agent_id}/runtime/phase_outputs/{llm_key}.json`",
                        f"- `prompt`: `{_trim(str(llm_meta.get('prompt', '')), 180)}`",
                        f"- `parsed_output`: `{_trim(json.dumps(llm_meta.get('parsed_payload', {}), sort_keys=True), 180)}`",
                        f"- `raw_output_present`: `{bool(str(llm_meta.get('raw_content', '')).strip())}`",
                        f"- `fallback_applied`: `{bool(llm_meta.get('fallback_applied', False))}`",
                        f"- `order_validation_status`: `{order_grounding.get('status') if isinstance(order_grounding, dict) else ''}`",
                        f"- `invalid_order_reasons`: `{invalid_reasons}`",
                        f"- `submitted_orders`: `{_trim(json.dumps(submitted_orders, sort_keys=True), 180)}`",
                        f"- `effective_orders`: `{_trim(json.dumps(effective_orders, sort_keys=True), 180)}`",
                    ]
                )
            )
    header = f"# Timeline: {power}\n\nAssigned agent: `{agent_id}`\n\n## Invocation Records\n"
    return header + ("\n\n".join(phase_rows) if phase_rows else "\nNo phase records.")


def summarize_negotiations(data: GameObservatoryData) -> dict[str, str]:
    pair_rows: dict[tuple[str, str], list[str]] = {}
    for row in sorted(data.private_messages, key=lambda msg: _msg_sort_key(msg)):
        frm = str(row.get("from_agent_id", ""))
        to = str((row.get("to") or {}).get("to_agent_id", ""))
        if not frm or not to:
            continue
        pair = tuple(sorted([frm, to]))
        phase = str(row.get("phase_id", ""))
        tick = row.get("tick")
        pair_rows.setdefault(pair, []).append(
            f"{phase} Tick {tick} | {frm} -> {to}: {str(row.get('body', ''))}"
        )

    pair_docs = {
        f"{a}__{b}": "\n".join([f"# {a} <-> {b}", "", *lines]) for (a, b), lines in sorted(pair_rows.items())
    }

    global_lines = [
        f"{str(row.get('phase_id', ''))} Tick {row.get('tick')} | {str(row.get('from_agent_id', ''))}: {str(row.get('body', ''))}"
        for row in sorted(data.global_messages, key=lambda msg: _msg_sort_key(msg))
    ]
    pair_docs["global"] = "\n".join(["# Global Channel", "", *global_lines]) if global_lines else "# Global Channel\n\nNo global messages."
    return pair_docs


def summarize_board_evolution(data: GameObservatoryData) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    previous_state = _initial_baseline_state(data)
    previous_by_power: dict[str, set[str]] = _units_by_power(previous_state)
    previous_centers_by_power: dict[str, set[str]] = _centers_by_power(previous_state)
    previous_supply = dict(previous_state.get("supply_ownership", {})) if isinstance(previous_state, dict) else {}
    for phase_id in data.phases:
        resolution = data.phase_resolutions.get(phase_id, {})
        output_state = resolution.get("output_state", {})
        if not isinstance(output_state, dict):
            continue
        powers = sorted(data.assignments_by_power.keys())
        phase_delta: dict[str, Any] = {
            "phase_id": phase_id,
            "powers": {},
            "moves": [],
            "center_changes": [],
            "builds": [],
            "disbands": [],
        }

        units_by_id = {
            str(unit.get("unit_id")): unit
            for unit in output_state.get("units", [])
            if isinstance(unit, dict) and isinstance(unit.get("unit_id"), str)
        }
        movement_outcomes = resolution.get("movement_outcomes", [])
        if isinstance(movement_outcomes, list):
            for row in movement_outcomes:
                if not isinstance(row, dict):
                    continue
                if row.get("result") != "moved":
                    continue
                unit_id = str(row.get("unit_id", ""))
                if not unit_id:
                    continue
                owner = str((units_by_id.get(unit_id) or {}).get("owner", ""))
                phase_delta["moves"].append(
                    {
                        "power": owner,
                        "unit_id": unit_id,
                        "from": row.get("from"),
                        "to": row.get("to"),
                        "result": row.get("result"),
                    }
                )

        current_supply = output_state.get("supply_ownership", {})
        if isinstance(current_supply, dict):
            provinces = sorted(set(previous_supply.keys()) | set(current_supply.keys()))
            for province in provinces:
                prev_owner = previous_supply.get(province)
                curr_owner = current_supply.get(province)
                if prev_owner != curr_owner:
                    phase_delta["center_changes"].append(
                        {
                            "province": province,
                            "from_owner": prev_owner,
                            "to_owner": curr_owner,
                        }
                    )

        adjustment_outcomes = resolution.get("adjustment_outcomes", [])
        if isinstance(adjustment_outcomes, list):
            for row in adjustment_outcomes:
                if not isinstance(row, dict):
                    continue
                result_value = str(row.get("result", ""))
                if result_value == "built":
                    phase_delta["builds"].append(
                        {
                            "power": row.get("power"),
                            "unit_id": row.get("unit_id"),
                            "province": row.get("province"),
                            "order_id": row.get("order_id"),
                        }
                    )
                if result_value in {"disbanded", "auto_disbanded"}:
                    phase_delta["disbands"].append(
                        {
                            "power": row.get("power"),
                            "unit_id": row.get("unit_id"),
                            "result": result_value,
                            "order_id": row.get("order_id"),
                        }
                    )

        for power in powers:
            units = {
                str(unit.get("unit_id"))
                for unit in output_state.get("units", [])
                if unit.get("owner") == power and unit.get("status") == "active"
            }
            centers = {
                province_id
                for province_id, owner in (output_state.get("supply_ownership", {}) or {}).items()
                if owner == power
            }
            prev_units = previous_by_power.get(power, set())
            prev_centers = previous_centers_by_power.get(power, set())
            phase_delta["powers"][power] = {
                "units_gained": sorted(units - prev_units),
                "units_lost": sorted(prev_units - units),
                "centers_gained": sorted(centers - prev_centers),
                "centers_lost": sorted(prev_centers - centers),
                "dislodgements": len(
                    [
                        row
                        for row in resolution.get("movement_outcomes", [])
                        if row.get("result") == "dislodged"
                        and any(
                            unit.get("unit_id") == row.get("unit_id") and unit.get("owner") == power
                            for unit in output_state.get("units", [])
                        )
                    ]
                ),
                "retreats_required": len(
                    [
                        unit
                        for unit in output_state.get("units", [])
                        if unit.get("owner") == power and (unit.get("must_retreat") or unit.get("status") == "dislodged")
                    ]
                ),
            }
            previous_by_power[power] = set(units)
            previous_centers_by_power[power] = set(centers)
        previous_supply = dict(current_supply) if isinstance(current_supply, dict) else {}
        out.append(phase_delta)
    return out


def _initial_baseline_state(data: GameObservatoryData) -> dict[str, Any]:
    for row in data.snapshots:
        if not isinstance(row, dict):
            continue
        if str(row.get("snapshot_id", "")) != "initial":
            continue
        payload = row.get("payload", {})
        if isinstance(payload, dict):
            return payload
    for row in data.snapshots:
        if not isinstance(row, dict):
            continue
        if row.get("phase_id") is not None:
            continue
        payload = row.get("payload", {})
        if isinstance(payload, dict):
            return payload
    if data.phases:
        first = data.phase_resolutions.get(data.phases[0], {})
        output_state = first.get("output_state", {}) if isinstance(first, dict) else {}
        if isinstance(output_state, dict):
            return output_state
    return {}


def _units_by_power(state: dict[str, Any]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    rows = state.get("units", []) if isinstance(state, dict) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("status", "active")) != "active":
            continue
        power = str(row.get("owner", ""))
        unit_id = str(row.get("unit_id", ""))
        if not power or not unit_id:
            continue
        out.setdefault(power, set()).add(unit_id)
    return out


def _centers_by_power(state: dict[str, Any]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    ownership = state.get("supply_ownership", {}) if isinstance(state, dict) else {}
    if not isinstance(ownership, dict):
        return out
    for province, power in ownership.items():
        if not power:
            continue
        out.setdefault(str(power), set()).add(str(province))
    return out


def summarize_diplomacy_metrics(data: GameObservatoryData) -> dict[str, Any]:
    private = sorted(data.private_messages, key=_msg_sort_key)
    global_rows = sorted(data.global_messages, key=_msg_sort_key)
    ticks = {
        (str(row.get("phase_id", "")), int(row.get("tick", 0)) if isinstance(row.get("tick"), int) else 0)
        for row in (private + global_rows)
    }
    private_by_pair: dict[str, int] = {}
    sent_private_by_agent: dict[str, int] = {}
    sent_global_by_agent: dict[str, int] = {}
    contacts_by_agent: dict[str, set[str]] = {}
    incoming_private_by_agent: dict[str, list[dict[str, Any]]] = {}
    sent_private_to_by_agent: dict[str, set[str]] = {}
    for row in private:
        frm = str(row.get("from_agent_id", ""))
        to = str((row.get("to") or {}).get("to_agent_id", ""))
        if not frm or not to:
            continue
        if frm == to:
            continue
        pair_key = "__".join(sorted([frm, to]))
        private_by_pair[pair_key] = private_by_pair.get(pair_key, 0) + 1
        sent_private_by_agent[frm] = sent_private_by_agent.get(frm, 0) + 1
        contacts_by_agent.setdefault(frm, set()).add(to)
        sent_private_to_by_agent.setdefault(frm, set()).add(to)
        incoming_private_by_agent.setdefault(to, []).append(row)
    for row in global_rows:
        frm = str(row.get("from_agent_id", ""))
        if frm:
            sent_global_by_agent[frm] = sent_global_by_agent.get(frm, 0) + 1

    agent_ids = set(data.assignments_by_power.values())
    for row in list(sent_private_by_agent.keys()) + list(sent_global_by_agent.keys()) + list(incoming_private_by_agent.keys()):
        if row:
            agent_ids.add(row)
    countries_by_agent = {agent: power for power, agent in data.assignments_by_power.items()}

    per_agent: list[dict[str, Any]] = []
    zero_message_agents: list[str] = []
    replies_by_agent: dict[str, int] = {}
    for agent in sorted(agent_ids):
        private_sent = sent_private_by_agent.get(agent, 0)
        global_sent = sent_global_by_agent.get(agent, 0)
        contacts = contacts_by_agent.get(agent, set())
        replies = 0
        for incoming in incoming_private_by_agent.get(agent, []):
            sender = str(incoming.get("from_agent_id", ""))
            if sender and sender in sent_private_to_by_agent.get(agent, set()):
                replies += 1
        replies_by_agent[agent] = replies
        reply_rate = round(replies / private_sent, 2) if private_sent > 0 else 0.0
        if private_sent + global_sent == 0:
            zero_message_agents.append(agent)
        per_agent.append(
            {
                "agent_id": agent,
                "country": countries_by_agent.get(agent, ""),
                "global_messages_sent": global_sent,
                "private_messages_sent": private_sent,
                "unique_private_contacts": len(contacts),
                "agents_contacted": sorted(contacts),
                "private_replies_received": replies,
                "private_reply_rate": reply_rate,
            }
        )

    total_private = len(private)
    total_global = len(global_rows)
    total_messages = total_private + total_global
    messages_per_tick = round(total_messages / len(ticks), 2) if ticks else 0.0
    private_global_ratio = round(total_private / total_global, 3) if total_global > 0 else None
    global_rate = round(total_global / total_messages, 3) if total_messages > 0 else 0.0
    private_reply_rate_overall = (
        round(sum(replies_by_agent.values()) / total_private, 3) if total_private > 0 else 0.0
    )

    reflection_mentions = _communication_mentions_in_reflections(data)
    order_mentions = _communication_mentions_in_orders(data)
    quality_metrics = _conversation_quality_metrics(private + global_rows)
    return {
        "schema_version": "1.0",
        "global_messages_sent": total_global,
        "private_messages_sent": total_private,
        "private_messages_by_pair": dict(sorted(private_by_pair.items())),
        "agents_contacted_per_agent": {row["agent_id"]: row["agents_contacted"] for row in per_agent},
        "replies_received_per_agent": {row["agent_id"]: row["private_replies_received"] for row in per_agent},
        "private_message_reply_rate": private_reply_rate_overall,
        "global_message_rate": global_rate,
        "zero_message_agents": sorted(zero_message_agents),
        "messages_per_negotiation_tick": messages_per_tick,
        "private_vs_global_ratio": private_global_ratio,
        "per_agent": per_agent,
        "private_pairs_with_replies": sorted(
            [pair for pair, count in private_by_pair.items() if count > 1]
        ),
        "private_messages_occurred": total_private > 0,
        "agents_only_global": sorted(
            [row["agent_id"] for row in per_agent if row["global_messages_sent"] > 0 and row["private_messages_sent"] == 0]
        ),
        "communication_in_reflections_heuristic": reflection_mentions,
        "communication_affecting_orders_heuristic": order_mentions,
        "conversation_quality_heuristics": quality_metrics,
    }


def _conversation_quality_metrics(messages: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {
        "concrete_support_requests": 0,
        "concrete_support_offers": 0,
        "orders_with_specific_provinces_mentioned": 0,
        "conditional_bargains": 0,
        "third_party_claims": 0,
        "rumor_or_warning_events": 0,
        "claim_tests": 0,
        "explicit_red_lines": 0,
        "explicit_dmz_or_border_terms": 0,
        "relationship_stagnation_markers": 0,
        "repeated_confirmation_loops": 0,
        "broken_commitment_candidates": 0,
        "messages_with_question_marks": 0,
        "messages_with_conditional_language": 0,
        "messages_with_specific_orders_or_provinces": 0,
        "messages_with_third_party_information": 0,
        "messages_with_support_requests": 0,
        "messages_with_support_offers": 0,
        "messages_with_repeated_confirmation_language": 0,
    }
    province_pattern = re.compile(r"\b(aus|eng|fra|ger|ita|rus|tur|[a-z]{3})\b", flags=re.IGNORECASE)
    confirmation_tokens = ["let's continue", "build trust", "aligned", "coordination", "stay coordinated"]
    support_request_tokens = ["support me", "can you support", "need support", "support into", "support to"]
    support_offer_tokens = ["i can support", "i will support", "offer support", "support you"]
    conditional_tokens = ["if you", "then i", "provided that", "on condition", "unless"]
    third_party_tokens = ["reported", "heard", "rumor", "warn", "told me", "third party"]
    claim_test_tokens = ["can you confirm", "is that true", "verify", "evidence", "prove"]
    border_tokens = ["dmz", "demilitarized", "border", "red line", "non-aggression"]
    commitment_tokens = ["i will not attack", "won't attack", "non-aggression", "dmz agreed", "promise"]
    attack_tokens = ["attack", "move to", "support into", "take"]

    message_index_by_pair: dict[str, list[str]] = {}
    for row in sorted(messages, key=_msg_sort_key):
        body = str(row.get("body", ""))
        lower = body.lower()
        frm = str(row.get("from_agent_id", ""))
        target = row.get("to", {})
        to_agent = str(target.get("to_agent_id", "")) if isinstance(target, dict) else ""
        if "?" in body:
            counts["messages_with_question_marks"] += 1
        if any(token in lower for token in conditional_tokens):
            counts["conditional_bargains"] += 1
            counts["messages_with_conditional_language"] += 1
        if any(token in lower for token in support_request_tokens):
            counts["concrete_support_requests"] += 1
            counts["messages_with_support_requests"] += 1
        if any(token in lower for token in support_offer_tokens):
            counts["concrete_support_offers"] += 1
            counts["messages_with_support_offers"] += 1
        if province_pattern.search(body):
            counts["orders_with_specific_provinces_mentioned"] += 1
            counts["messages_with_specific_orders_or_provinces"] += 1
        if any(token in lower for token in third_party_tokens):
            counts["third_party_claims"] += 1
            counts["messages_with_third_party_information"] += 1
        if "rumor" in lower or "warn" in lower:
            counts["rumor_or_warning_events"] += 1
        if any(token in lower for token in claim_test_tokens):
            counts["claim_tests"] += 1
        if "red line" in lower:
            counts["explicit_red_lines"] += 1
        if any(token in lower for token in border_tokens):
            counts["explicit_dmz_or_border_terms"] += 1
        if any(token in lower for token in confirmation_tokens):
            counts["messages_with_repeated_confirmation_language"] += 1
            counts["relationship_stagnation_markers"] += 1
        if frm and to_agent and frm != to_agent:
            pair = "__".join(sorted([frm, to_agent]))
            message_index_by_pair.setdefault(pair, []).append(lower)
    for pair, texts in message_index_by_pair.items():
        joined = " ".join(texts)
        confirmations = sum(1 for token in confirmation_tokens if token in joined)
        if confirmations >= 2:
            counts["repeated_confirmation_loops"] += 1
        has_commitment = any(token in joined for token in commitment_tokens)
        has_attack_language = any(token in joined for token in attack_tokens)
        if has_commitment and has_attack_language:
            counts["broken_commitment_candidates"] += 1
    return {
        "schema_version": "1.0",
        "heuristic": True,
        **counts,
    }


def _extract_assignments(events: list[dict[str, Any]]) -> dict[str, str]:
    for row in sorted(events, key=lambda event: str(event.get("event_id", "")), reverse=True):
        if row.get("event_type") != "phase_completed" or row.get("phase_id") != "game_end":
            continue
        outcome = (row.get("payload") or {}).get("result", {})
        by_country = outcome.get("country_results", {}) if isinstance(outcome, dict) else {}
        if not isinstance(by_country, dict):
            continue
        assignments: dict[str, str] = {}
        for power, payload in by_country.items():
            if isinstance(payload, dict):
                assignments[str(power)] = str(payload.get("agent_id", ""))
        if assignments:
            return dict(sorted(assignments.items()))
    return {}


def _msg_sort_key(msg: dict[str, Any]) -> tuple[str, int, str]:
    tick = msg.get("tick")
    tick_int = int(tick) if isinstance(tick, int) else 0
    return (str(msg.get("phase_id", "")), tick_int, str(msg.get("message_id", "")))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _load_runtime_outputs(game_root: Path) -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, dict[str, Any]]] = {}
    agents_root = game_root / "agents"
    if not agents_root.exists():
        return out
    for agent_dir in sorted([p for p in agents_root.iterdir() if p.is_dir()], key=lambda p: p.name):
        outputs = agent_dir / "runtime" / "phase_outputs"
        if not outputs.exists():
            continue
        out[agent_dir.name] = {}
        for file in sorted(outputs.glob("*.json"), key=lambda p: p.name):
            payload = json.loads(file.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                out[agent_dir.name][file.stem] = payload
    return out


def _trim(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _communication_mentions_in_reflections(data: GameObservatoryData) -> dict[str, Any]:
    by_agent: dict[str, bool] = {}
    for key, payload in data.phase_reflections.items():
        agent_id = key.split(":")[-1]
        text = json.dumps(payload, sort_keys=True).lower()
        by_agent[agent_id] = by_agent.get(agent_id, False) or any(
            term in text for term in ["message", "private", "reply", "negot", "ally", "commit"]
        )
    return {"any": any(by_agent.values()), "by_agent": dict(sorted(by_agent.items()))}


def _communication_mentions_in_orders(data: GameObservatoryData) -> dict[str, Any]:
    by_agent: dict[str, bool] = {}
    for agent_id, outputs in data.runtime_outputs_by_agent.items():
        for key, payload in outputs.items():
            if not key.endswith("_submit_orders"):
                continue
            text = json.dumps(payload, sort_keys=True).lower()
            if any(term in text for term in ["coord", "support", "agreement", "communication", "ally"]):
                by_agent[agent_id] = True
            else:
                by_agent.setdefault(agent_id, False)
    return {"any": any(by_agent.values()), "by_agent": dict(sorted(by_agent.items()))}
