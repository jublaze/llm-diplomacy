from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from llm_diplomacy.observatory.api import (
    GameObservatoryData,
    load_game,
    summarize_board_evolution,
    summarize_diplomacy_metrics,
)


def build_secret_history_report(
    game_dir: str | Path,
    *,
    data: GameObservatoryData | None = None,
    board_deltas: list[dict[str, Any]] | None = None,
    diplomacy_metrics: dict[str, Any] | None = None,
) -> dict[str, str]:
    game_root = Path(game_dir)
    report_dir = game_root / "reporter"
    report_dir.mkdir(parents=True, exist_ok=True)

    report_data = data if data is not None else load_game(game_root)
    deltas = board_deltas if board_deltas is not None else summarize_board_evolution(report_data)
    metrics = diplomacy_metrics if diplomacy_metrics is not None else summarize_diplomacy_metrics(report_data)

    result = _game_result(report_data)
    outcome = _winner_or_leader(result)
    attacks = _attack_events(report_data)
    chronology = _chronology(report_data, deltas)
    turning_points = _turning_points(report_data, deltas, attacks)
    relationships = _relationship_summaries(report_data, attacks)
    information_events = _information_flow_events(report_data)
    strategy_evolution = _strategy_evolution(report_data, attacks)
    lessons = _lessons_learned(
        report_data=report_data,
        diplomacy_metrics=metrics,
        relationships=relationships,
        turning_points=turning_points,
    )

    executive_summary = _executive_summary(
        report_data=report_data,
        outcome=outcome,
        turning_points=turning_points,
        relationships=relationships,
        information_events=information_events,
    )

    payload = {
        "schema_version": "1.0",
        "game_id": game_root.name,
        "result": result,
        "outcome": outcome,
        "executive_summary": executive_summary,
        "chronology": chronology,
        "turning_points": turning_points,
        "relationship_summaries": relationships,
        "information_events": information_events,
        "strategy_evolution": strategy_evolution,
        "lessons_learned": lessons,
    }

    md = _markdown(
        payload=payload,
        report_data=report_data,
    )
    md_path = report_dir / "secret_history.md"
    json_path = report_dir / "secret_history.json"
    index_path = report_dir / "index.md"
    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
    index_path.write_text(
        "\n".join(
            [
                "# Reporter Artifacts",
                "",
                "- [Secret History](secret_history.md)",
                "- [Secret History JSON](secret_history.json)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return {
        "secret_history_md": str(md_path),
        "secret_history_json": str(json_path),
        "reporter_index": str(index_path),
    }


def _game_result(data: GameObservatoryData) -> dict[str, Any]:
    for event in sorted(data.events, key=lambda row: str(row.get("event_id", "")), reverse=True):
        if event.get("event_type") == "phase_completed" and event.get("phase_id") == "game_end":
            payload = event.get("payload", {})
            result = payload.get("result", {}) if isinstance(payload, dict) else {}
            if isinstance(result, dict):
                return result
    return {}


def _winner_or_leader(result: dict[str, Any]) -> dict[str, Any]:
    winners = result.get("winners", []) if isinstance(result.get("winners"), list) else []
    country_results = result.get("country_results", {}) if isinstance(result.get("country_results"), dict) else {}
    if len(winners) == 1:
        winner_agent = str(winners[0])
        winner_centers = 0
        winner_country = ""
        for country, payload in sorted(country_results.items()):
            if not isinstance(payload, dict):
                continue
            if str(payload.get("agent_id", "")) != winner_agent:
                continue
            winner_country = str(country)
            winner_centers = int(payload.get("owned_centers", 0)) if isinstance(payload.get("owned_centers"), int) else 0
            break
        if winner_centers >= 18:
            return {
                "kind": "solo_winner",
                "country": winner_country,
                "agent_id": winner_agent,
                "owned_centers": winner_centers,
            }
    leader_country = ""
    leader_agent = ""
    leader_centers = -1
    for country, payload in sorted(country_results.items()):
        if not isinstance(payload, dict):
            continue
        centers = int(payload.get("owned_centers", 0)) if isinstance(payload.get("owned_centers"), int) else 0
        if centers > leader_centers:
            leader_country = str(country)
            leader_agent = str(payload.get("agent_id", ""))
            leader_centers = centers
    return {
        "kind": "leader_at_cap",
        "country": leader_country,
        "agent_id": leader_agent,
        "owned_centers": max(0, leader_centers),
    }


def _executive_summary(
    *,
    report_data: GameObservatoryData,
    outcome: dict[str, Any],
    turning_points: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    information_events: list[dict[str, Any]],
) -> str:
    private_count = len(report_data.private_messages)
    global_count = len(report_data.global_messages)
    leading = f"{outcome.get('country', '')}/{outcome.get('agent_id', '')}".strip("/")
    relationship_note = "No durable private relationship signal was detected."
    if relationships:
        strongest = max(relationships, key=lambda row: int(row.get("message_count", 0)))
        pair = strongest.get("pair", [])
        relationship_note = (
            f"Strongest private relationship signal was between {pair[0]} and {pair[1]} "
            f"({strongest.get('message_count', 0)} private messages)."
            if isinstance(pair, list) and len(pair) == 2
            else relationship_note
        )
    info_note = (
        f"{len(information_events)} information-flow events were identified."
        if information_events
        else "No clear information-flow events were detected from message content."
    )
    betrayal_turns = [
        row
        for row in turning_points
        if str(row.get("category", "")) in {"betrayal", "plausible_betrayal"}
    ]
    betrayal_note = (
        "No betrayal was labeled because no explicit promise-to-contradiction evidence was found."
        if not betrayal_turns
        else "At least one betrayal candidate met explicit promise-to-action evidence thresholds."
    )
    return (
        f"Game outcome was {outcome.get('kind', 'unknown')} with leader/winner {leading}. "
        f"Message traffic was private={private_count}, global={global_count}. "
        f"{relationship_note} {info_note} {betrayal_note}"
    ).strip()


def _chronology(data: GameObservatoryData, deltas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    delta_by_phase = {str(row.get("phase_id", "")): row for row in deltas if isinstance(row, dict)}
    rows: list[dict[str, Any]] = []
    for phase_id in data.phases:
        private_msgs = [msg for msg in data.private_messages if str(msg.get("phase_id", "")) == phase_id]
        global_msgs = [msg for msg in data.global_messages if str(msg.get("phase_id", "")) == phase_id]
        reflections = _phase_reflection_briefs(data, phase_id)
        delta = delta_by_phase.get(phase_id, {})
        center_changes = delta.get("center_changes", []) if isinstance(delta, dict) else []
        moves = delta.get("moves", []) if isinstance(delta, dict) else []
        builds = delta.get("builds", []) if isinstance(delta, dict) else []
        disbands = delta.get("disbands", []) if isinstance(delta, dict) else []
        summary_parts = []
        negotiation_context_phase = _preceding_negotiation_phase_id(phase_id)
        negotiation_private = []
        negotiation_global = []
        if negotiation_context_phase:
            negotiation_private = [msg for msg in data.private_messages if str(msg.get("phase_id", "")) == negotiation_context_phase]
            negotiation_global = [msg for msg in data.global_messages if str(msg.get("phase_id", "")) == negotiation_context_phase]
        if private_msgs or global_msgs:
            summary_parts.append(
                f"Messages this phase: private={len(private_msgs)}, global={len(global_msgs)}."
            )
        elif negotiation_context_phase and (negotiation_private or negotiation_global):
            summary_parts.append(
                f"No same-phase diplomacy messages. Preceding negotiation context ({negotiation_context_phase}) had private={len(negotiation_private)}, global={len(negotiation_global)}."
            )
        else:
            summary_parts.append("No diplomacy messages were recorded this phase.")
        if center_changes or builds or disbands:
            summary_parts.append(
                f"Board changed (center_changes={len(center_changes)}, builds={len(builds)}, disbands={len(disbands)})."
            )
        elif moves:
            summary_parts.append(f"Units moved ({len(moves)} moves) without center ownership changes.")
        else:
            summary_parts.append("No significant board changes were detected.")
        if reflections:
            summary_parts.append("Reflections showed: " + "; ".join(reflections[:2]))
        else:
            summary_parts.append("No reflection evidence was available for this phase.")
        rows.append(
            {
                "phase_id": phase_id,
                "summary": " ".join(summary_parts),
                "negotiation_context_phase": negotiation_context_phase,
                "diplomacy": {
                    "private_messages": len(private_msgs),
                    "global_messages": len(global_msgs),
                    "preceding_negotiation_private_messages": len(negotiation_private),
                    "preceding_negotiation_global_messages": len(negotiation_global),
                },
                "board": {
                    "moves": len(moves),
                    "center_changes": len(center_changes),
                    "builds": len(builds),
                    "disbands": len(disbands),
                },
                "reflection_signals": reflections,
                "evidence": [
                    item
                    for item in [
                        f"phases/{phase_id}/orders/submitted_orders.json",
                        f"phases/{phase_id}/resolution/resolution.json",
                        f"messages/* (phase_id={phase_id})",
                        f"messages/* (phase_id={negotiation_context_phase})" if negotiation_context_phase else None,
                    ]
                    if item
                ],
            }
        )
    return rows


def _turning_points(
    data: GameObservatoryData,
    deltas: list[dict[str, Any]],
    attacks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    private_sorted = sorted(data.private_messages, key=_msg_sort_key)
    if private_sorted:
        first = private_sorted[0]
        points.append(
            {
                "category": "first_private_contact",
                "phase_id": first.get("phase_id"),
                "summary": f"First private message: {first.get('from_agent_id')} -> {(first.get('to') or {}).get('to_agent_id')}.",
                "why_it_mattered": "Marks the earliest bilateral channel evidence.",
                "confidence": "high",
                "evidence": [f"messages/private/*/inbox.jsonl:message_id={first.get('message_id')}"],
            }
        )
    proposal = _first_concrete_proposal(data)
    if proposal is not None:
        points.append(proposal)
    swing = _largest_swing(deltas)
    if swing is not None:
        points.append(swing)
    betrayal = _first_evidenced_betrayal(data, attacks)
    if betrayal is not None:
        points.append(betrayal)
    tension = _first_boundary_tension(data, attacks)
    if tension is not None:
        points.append(tension)
    return points


def _first_concrete_proposal(data: GameObservatoryData) -> dict[str, Any] | None:
    keywords = ["support", "dmz", "bounce", "non-aggression", "coordinate", "move to", "hold "]
    merged = sorted(data.private_messages + data.global_messages, key=_msg_sort_key)
    for msg in merged:
        body = str(msg.get("body", "")).lower()
        if not any(token in body for token in keywords):
            continue
        return {
            "category": "first_concrete_proposal",
            "phase_id": msg.get("phase_id"),
            "summary": "First concrete proposal detected in message content.",
            "why_it_mattered": "Concrete requests usually indicate transition from signaling to coordination attempts.",
            "confidence": "medium",
            "evidence": [f"messages/*:message_id={msg.get('message_id')}"],
        }
    return None


def _largest_swing(deltas: list[dict[str, Any]]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_score = -1
    for row in deltas:
        if not isinstance(row, dict):
            continue
        center_changes = row.get("center_changes", [])
        builds = row.get("builds", [])
        disbands = row.get("disbands", [])
        powers = row.get("powers", {})
        dislodgements = 0
        if isinstance(powers, dict):
            for payload in powers.values():
                if isinstance(payload, dict):
                    dislodgements += int(payload.get("dislodgements", 0))
        score = (
            (len(center_changes) if isinstance(center_changes, list) else 0) * 3
            + (len(builds) if isinstance(builds, list) else 0)
            + (len(disbands) if isinstance(disbands, list) else 0)
            + dislodgements
        )
        if score <= 0:
            continue
        if score > best_score:
            best_score = score
            best = {
                "category": "largest_tactical_swing",
                "phase_id": row.get("phase_id"),
                "summary": f"Largest tactical swing score={score}.",
                "why_it_mattered": "Center changes/dislodgements/build shifts alter long-term power projection.",
                "confidence": "high",
                "evidence": [f"observatory/board_evolution.json:phase_id={row.get('phase_id')}"],
            }
    return best


def _first_evidenced_betrayal(
    data: GameObservatoryData,
    attacks: list[dict[str, Any]],
) -> dict[str, Any] | None:
    explicit_commitment_terms = [
        "i won't attack",
        "i will not attack",
        "non-aggression",
        "dmz",
        "do not enter",
        "respect your border",
        "i will hold",
    ]
    power_to_agent = data.assignments_by_power
    agent_to_power = {agent: power for power, agent in power_to_agent.items()}
    private_sorted = sorted(data.private_messages, key=_msg_sort_key)
    for attack in attacks:
        attacker_power = str(attack.get("attacker_power", ""))
        defender_power = str(attack.get("defender_power", ""))
        if not attacker_power or not defender_power:
            continue
        attacker_agent = power_to_agent.get(attacker_power, "")
        defender_agent = power_to_agent.get(defender_power, "")
        if not attacker_agent or not defender_agent:
            continue
        for msg in private_sorted:
            phase_id = str(msg.get("phase_id", ""))
            if phase_id >= str(attack.get("phase_id", "")):
                break
            if str(msg.get("from_agent_id", "")) != attacker_agent:
                continue
            if str((msg.get("to") or {}).get("to_agent_id", "")) != defender_agent:
                continue
            body = str(msg.get("body", "")).lower()
            if not any(token in body for token in explicit_commitment_terms):
                continue
            consequence = "boundary_tension"
            confidence = "low"
            why = "Explicit commitment-style language preceded contradictory military action."
            if bool(attack.get("defender_was_dislodged", False)):
                consequence = "plausible_betrayal"
                confidence = "medium"
                why = "Explicit commitment-style language preceded contradictory military action with dislodgement evidence."
            return {
                "category": consequence,
                "phase_id": attack.get("phase_id"),
                "summary": f"{attacker_power} attacked {defender_power} after prior explicit boundary/cooperation language.",
                "why_it_mattered": why,
                "confidence": confidence,
                "evidence": [
                    f"messages/private/*:message_id={msg.get('message_id')}",
                    f"phases/{attack.get('phase_id')}/resolution/resolution.json",
                ],
            }
    return None


def _first_boundary_tension(
    data: GameObservatoryData,
    attacks: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not attacks:
        return None
    first = attacks[0]
    return {
        "category": "boundary_tension",
        "phase_id": first.get("phase_id"),
        "summary": f"{first.get('attacker_power')} attacked {first.get('defender_power')} with no explicit promise evidence found.",
        "why_it_mattered": "This appears as ordinary conflict/opportunism unless commitment evidence emerges.",
        "confidence": "low",
        "evidence": [f"phases/{first.get('phase_id')}/resolution/resolution.json"],
    }


def _relationship_summaries(data: GameObservatoryData, attacks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_pair: dict[str, dict[str, Any]] = {}
    for msg in sorted(data.private_messages, key=_msg_sort_key):
        frm = str(msg.get("from_agent_id", ""))
        to = str((msg.get("to") or {}).get("to_agent_id", ""))
        if not frm or not to or frm == to:
            continue
        pair_key = "__".join(sorted([frm, to]))
        row = by_pair.setdefault(
            pair_key,
            {
                "pair": pair_key.split("__"),
                "messages": 0,
                "phases": set(),
                "commitment_language": 0,
            },
        )
        row["messages"] += 1
        row["phases"].add(str(msg.get("phase_id", "")))
        body = str(msg.get("body", "")).lower()
        if any(token in body for token in ["ally", "support", "dmz", "non-aggression", "coordinate"]):
            row["commitment_language"] += 1

    power_to_agent = data.assignments_by_power
    attack_pairs = {
        "__".join(sorted([power_to_agent.get(str(row.get("attacker_power", "")), ""), power_to_agent.get(str(row.get("defender_power", "")), "")]))
        for row in attacks
        if isinstance(row, dict)
    }

    out: list[dict[str, Any]] = []
    for _, row in sorted(by_pair.items(), key=lambda item: (-int(item[1]["messages"]), item[0])):
        pair = row["pair"]
        pair_key = "__".join(sorted([pair[0], pair[1]]))
        conflict = pair_key in attack_pairs
        message_count = int(row["messages"])
        summary = (
            "Communication stayed active and did not show direct military conflict evidence."
            if not conflict
            else "Communication existed, but direct military conflict evidence also occurred."
        )
        out.append(
            {
                "pair": pair,
                "message_count": message_count,
                "phases_active": sorted([phase for phase in row["phases"] if phase]),
                "commitment_language_count": int(row["commitment_language"]),
                "direct_conflict_evidence": conflict,
                "summary": summary,
                "confidence": "medium" if message_count >= 3 else "low",
                "evidence": ["messages/private/*/inbox.jsonl", "phases/*/resolution/resolution.json"],
            }
        )
    return out


def _information_flow_events(data: GameObservatoryData) -> list[dict[str, Any]]:
    powers = sorted(data.assignments_by_power.keys())
    power_pattern = re.compile(r"\b(" + "|".join(re.escape(power) for power in powers) + r")\b", flags=re.IGNORECASE) if powers else None
    tags = [
        ("warning", ["warn", "careful", "threat"]),
        ("rumor", ["rumor", "heard", "apparently", "reports"]),
        ("interpretation", ["i think", "seems", "likely"]),
        ("deterrence", ["if you", "otherwise", "consequence"]),
    ]
    out: list[dict[str, Any]] = []
    merged = sorted(data.private_messages + data.global_messages, key=_msg_sort_key)
    for msg in merged:
        body = str(msg.get("body", ""))
        body_lower = body.lower()
        matched_tags = [name for name, needles in tags if any(token in body_lower for token in needles)]
        if power_pattern is None:
            mentioned = []
        else:
            mentioned = sorted(set(match.group(1).lower() for match in power_pattern.finditer(body_lower)))
        if not matched_tags and not mentioned:
            continue
        out.append(
            {
                "phase_id": msg.get("phase_id"),
                "from_agent_id": msg.get("from_agent_id"),
                "to": msg.get("to"),
                "message_id": msg.get("message_id"),
                "tags": matched_tags,
                "mentioned_powers": mentioned,
                "summary": "Potential information transfer event detected from message content.",
                "confidence": "low" if not matched_tags else "medium",
                "evidence": [f"messages/*:message_id={msg.get('message_id')}"],
            }
        )
    return out


def _strategy_evolution(data: GameObservatoryData, attacks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for power, agent_id in sorted(data.assignments_by_power.items()):
        intents: list[tuple[str, str]] = []
        for phase_id in data.phases:
            key = f"{phase_id}:{agent_id}"
            payload = data.phase_reflections.get(key, {})
            if not isinstance(payload, dict):
                continue
            reflection = (payload.get("result") or {}).get("reflection", {}) if isinstance(payload.get("result"), dict) else {}
            if not isinstance(reflection, dict):
                continue
            strategic = reflection.get("strategic_intent", {})
            if not isinstance(strategic, dict):
                continue
            short_term = str(strategic.get("short_term", "")).strip()
            if short_term:
                intents.append((phase_id, short_term))
        transitions = _collapse_intent_transitions(intents)
        attacked = any(str(row.get("attacker_power", "")) == power for row in attacks if isinstance(row, dict))
        conflict_note = "direct attacks observed" if attacked else "no direct attack evidence observed"
        if transitions:
            summary = f"{len(transitions)} strategy transition(s) observed; {conflict_note}."
        else:
            summary = f"No clear strategic transition extracted from reflections; {conflict_note}."
        out.append(
            {
                "power": power,
                "agent_id": agent_id,
                "transitions": transitions,
                "summary": summary,
                "confidence": "medium" if transitions else "low",
                "evidence": [f"phases/*/reflection/{agent_id}.json", "phases/*/resolution/resolution.json"],
            }
        )
    return out


def _collapse_intent_transitions(intents: list[tuple[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    last = ""
    for phase_id, intent in intents:
        normalized = re.sub(r"\s+", " ", intent.strip().lower())
        if normalized == last:
            continue
        out.append({"phase_id": phase_id, "intent": intent})
        last = normalized
    return out


def _lessons_learned(
    *,
    report_data: GameObservatoryData,
    diplomacy_metrics: dict[str, Any],
    relationships: list[dict[str, Any]],
    turning_points: list[dict[str, Any]],
) -> list[str]:
    lessons: list[str] = []
    private_count = int(diplomacy_metrics.get("private_messages_sent", 0))
    global_count = int(diplomacy_metrics.get("global_messages_sent", 0))
    if private_count > global_count:
        lessons.append("Private channels carried more traffic than global signaling in this game.")
    elif global_count > private_count:
        lessons.append("Global signaling dominated over private bilateral exchanges in this game.")
    else:
        lessons.append("Private and global message traffic were balanced.")

    strong_relationships = [row for row in relationships if int(row.get("message_count", 0)) >= 3]
    if strong_relationships:
        lessons.append("At least one relationship sustained repeated bilateral communication.")
    else:
        lessons.append("No relationship showed sustained bilateral communication volume.")

    if any(str(row.get("category", "")) in {"betrayal", "plausible_betrayal"} for row in turning_points):
        lessons.append("At least one betrayal candidate met explicit evidence thresholds.")
    else:
        lessons.append("No betrayal label was assigned without explicit promise-to-action contradiction evidence.")

    if report_data.phase_reflections:
        lessons.append("Reflection artifacts provided strategic-intent evidence for post-game analysis.")
    else:
        lessons.append("Reflection evidence was limited, reducing confidence in intent-level interpretation.")
    return lessons


def _phase_reflection_briefs(data: GameObservatoryData, phase_id: str) -> list[str]:
    out: list[str] = []
    for power, agent_id in sorted(data.assignments_by_power.items()):
        payload = data.phase_reflections.get(f"{phase_id}:{agent_id}", {})
        if not isinstance(payload, dict):
            continue
        reflection = (payload.get("result") or {}).get("reflection", {}) if isinstance(payload.get("result"), dict) else {}
        if not isinstance(reflection, dict):
            continue
        short_term = str((reflection.get("strategic_intent") or {}).get("short_term", "")).strip() if isinstance(reflection.get("strategic_intent"), dict) else ""
        if short_term:
            out.append(f"{power}: {short_term}")
    return out


def _attack_events(data: GameObservatoryData) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    previous_state = _initial_state(data)
    for phase_id in data.phases:
        resolution = data.phase_resolutions.get(phase_id, {})
        if not isinstance(resolution, dict):
            continue
        output_state = resolution.get("output_state", {})
        if not isinstance(output_state, dict):
            continue
        previous_by_province = {
            str(unit.get("province", "")): unit
            for unit in previous_state.get("units", [])
            if isinstance(unit, dict) and unit.get("status", "active") == "active"
        }
        output_by_unit_id = {
            str(unit.get("unit_id", "")): unit
            for unit in output_state.get("units", [])
            if isinstance(unit, dict)
        }
        movement_outcomes = resolution.get("movement_outcomes", [])
        dislodged_ids = {
            str(row.get("unit_id", ""))
            for row in movement_outcomes
            if isinstance(row, dict) and str(row.get("result", "")) == "dislodged"
        } if isinstance(movement_outcomes, list) else set()
        if isinstance(movement_outcomes, list):
            for row in movement_outcomes:
                if not isinstance(row, dict):
                    continue
                if str(row.get("result", "")) != "moved":
                    continue
                unit_id = str(row.get("unit_id", ""))
                destination = str(row.get("to", ""))
                moved_unit = output_by_unit_id.get(unit_id)
                if moved_unit is None:
                    continue
                attacker_power = str(moved_unit.get("owner", ""))
                defender = previous_by_province.get(destination)
                if defender is None:
                    continue
                defender_power = str(defender.get("owner", ""))
                if not defender_power or defender_power == attacker_power:
                    continue
                defender_unit_id = str(defender.get("unit_id", ""))
                events.append(
                    {
                        "phase_id": phase_id,
                        "attacker_power": attacker_power,
                        "defender_power": defender_power,
                        "destination": destination,
                        "defender_unit_id": defender_unit_id,
                        "defender_was_dislodged": defender_unit_id in dislodged_ids,
                    }
                )
        previous_state = output_state
    return events


def _initial_state(data: GameObservatoryData) -> dict[str, Any]:
    for snapshot in data.snapshots:
        if not isinstance(snapshot, dict):
            continue
        if str(snapshot.get("snapshot_id", "")) != "initial":
            continue
        payload = snapshot.get("payload", {})
        if isinstance(payload, dict):
            return payload
    if data.phases:
        first = data.phase_resolutions.get(data.phases[0], {})
        if isinstance(first, dict) and isinstance(first.get("output_state"), dict):
            return first.get("output_state", {})
    return {}


def _markdown(payload: dict[str, Any], report_data: GameObservatoryData) -> str:
    lines = [
        "# Secret History",
        "",
        "This historian report is generated after game completion from existing artifacts only.",
        "It is read-only and never visible to agents during gameplay.",
        "",
        "## Executive Summary",
        "",
        str(payload.get("executive_summary", "")).strip(),
        "",
        "## Chronological Narrative",
        "",
    ]
    for row in payload.get("chronology", []):
        if not isinstance(row, dict):
            continue
        lines.extend(
            [
                f"### {row.get('phase_id', '')}",
                str(row.get("summary", "")),
                f"Evidence: {row.get('evidence', [])}",
                "",
            ]
        )
    lines.extend(["## Turning Points", ""])
    turning_points = payload.get("turning_points", [])
    if isinstance(turning_points, list) and turning_points:
        for row in turning_points:
            if not isinstance(row, dict):
                continue
            lines.extend(
                [
                    f"- [{row.get('category', 'event')}] {row.get('summary', '')}",
                    f"  why_it_mattered: {row.get('why_it_mattered', '')}",
                    f"  confidence: {row.get('confidence', 'low')}",
                    f"  evidence: {row.get('evidence', [])}",
                ]
            )
    else:
        lines.append("- No major turning point met evidence threshold.")
    lines.extend(["", "## Relationship Evolution", ""])
    relationships = payload.get("relationship_summaries", [])
    if isinstance(relationships, list) and relationships:
        for row in relationships:
            if not isinstance(row, dict):
                continue
            pair = row.get("pair", [])
            pair_label = f"{pair[0]} <-> {pair[1]}" if isinstance(pair, list) and len(pair) == 2 else str(pair)
            lines.extend(
                [
                    f"### {pair_label}",
                    f"- summary: {row.get('summary', '')}",
                    f"- message_count: {row.get('message_count', 0)}",
                    f"- commitment_language_count: {row.get('commitment_language_count', 0)}",
                    f"- direct_conflict_evidence: {row.get('direct_conflict_evidence', False)}",
                    f"- confidence: {row.get('confidence', 'low')}",
                    f"- evidence: {row.get('evidence', [])}",
                    "",
                ]
            )
    else:
        lines.append("- No relationship summary met minimum evidence.")
    lines.extend(["## Information Flow", ""])
    info_events = payload.get("information_events", [])
    if isinstance(info_events, list) and info_events:
        for row in info_events[:24]:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"- {row.get('phase_id')}: {row.get('from_agent_id')} -> {row.get('to')} "
                f"tags={row.get('tags', [])} mentioned={row.get('mentioned_powers', [])} "
                f"(confidence={row.get('confidence', 'low')})"
            )
        if len(info_events) > 24:
            lines.append(f"- ... {len(info_events) - 24} additional information events omitted for brevity.")
    else:
        lines.append("- No strong information-flow events were detected.")
    lines.extend(["", "## Strategy Evolution", ""])
    for row in payload.get("strategy_evolution", []):
        if not isinstance(row, dict):
            continue
        lines.extend(
            [
                f"### {row.get('power', '')}",
                f"- summary: {row.get('summary', '')}",
                f"- transitions: {row.get('transitions', [])}",
                f"- confidence: {row.get('confidence', 'low')}",
                f"- evidence: {row.get('evidence', [])}",
                "",
            ]
        )
    lines.extend(["## Lessons Learned", ""])
    for lesson in payload.get("lessons_learned", []):
        lines.append(f"- {lesson}")
    lines.extend(
        [
            "",
            "## Confidence Notes",
            "- high confidence: direct artifact facts (messages, orders, board state transitions).",
            "- medium confidence: repeated patterns supported by multiple artifact classes.",
            "- low confidence: intent-level inference from language without corroborating action evidence.",
            "",
            "## Outcome",
            f"- result: {payload.get('result', {})}",
            f"- outcome: {payload.get('outcome', {})}",
            f"- phases_analyzed: {len(report_data.phases)}",
            "",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _msg_sort_key(msg: dict[str, Any]) -> tuple[str, int, str]:
    tick = msg.get("tick")
    tick_int = int(tick) if isinstance(tick, int) else 0
    return (str(msg.get("phase_id", "")), tick_int, str(msg.get("message_id", "")))


def _preceding_negotiation_phase_id(phase_id: str) -> str | None:
    parts = phase_id.split("_")
    if len(parts) < 3:
        return None
    year = parts[0]
    season = parts[1]
    phase = parts[2]
    if phase not in {"movement", "retreat", "adjustment"}:
        return None
    return f"{year}_{season}_negotiation"
