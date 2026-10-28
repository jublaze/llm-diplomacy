from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .api import GameObservatoryData


def build_omniscient_reports(
    *,
    data: GameObservatoryData,
    observatory_dir: Path,
    diplomacy_metrics: dict[str, Any],
    board_deltas: list[dict[str, Any]],
) -> dict[str, str]:
    reports_dir = observatory_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    result = _game_result(data)
    leader = _winner_or_leader(result)
    alliance_candidates = _alliance_candidates(data)
    betrayal_candidates = _betrayal_candidates(data)
    key_moments = _key_moments(data, diplomacy_metrics, board_deltas)

    aar_public = _aar_public_markdown(
        data=data,
        result=result,
        winner_or_leader=leader,
        alliance_candidates=alliance_candidates,
        board_deltas=board_deltas,
    )
    secret_history = _secret_history_markdown(
        data=data,
        alliance_candidates=alliance_candidates,
        betrayal_candidates=betrayal_candidates,
    )
    agent_profiles = _agent_profiles_markdown(
        data=data,
        diplomacy_metrics=diplomacy_metrics,
    )
    key_moments_md = _key_moments_markdown(key_moments)
    summary = {
        "schema_version": "1.0",
        "game_id": data.game_dir.name,
        "result": result,
        "winner_or_leader": leader,
        "agents": _agents_summary(data),
        "communication_summary": {
            "global_messages_sent": diplomacy_metrics.get("global_messages_sent", 0),
            "private_messages_sent": diplomacy_metrics.get("private_messages_sent", 0),
            "private_message_reply_rate": diplomacy_metrics.get("private_message_reply_rate", 0.0),
            "zero_message_agents": diplomacy_metrics.get("zero_message_agents", []),
        },
        "alliance_candidates": alliance_candidates,
        "betrayal_candidates": betrayal_candidates,
        "key_moments": key_moments,
        "confidence_notes": [
            "high: direct artifact facts (messages, orders, outcomes, counts)",
            "medium: inferred alignment/coordination patterns",
            "low: intent-level inferences from reflections or language cues",
        ],
    }

    aar_path = reports_dir / "aar_public.md"
    secret_path = reports_dir / "secret_history.md"
    profiles_path = reports_dir / "agent_profiles.md"
    moments_path = reports_dir / "key_moments.md"
    summary_path = reports_dir / "reporter_summary.json"
    aar_path.write_text(aar_public, encoding="utf-8")
    secret_path.write_text(secret_history, encoding="utf-8")
    profiles_path.write_text(agent_profiles, encoding="utf-8")
    moments_path.write_text(key_moments_md, encoding="utf-8")
    summary_path.write_text(json.dumps(summary, sort_keys=True, indent=2), encoding="utf-8")

    return {
        "aar_public": str(aar_path),
        "secret_history": str(secret_path),
        "agent_profiles": str(profiles_path),
        "key_moments": str(moments_path),
        "reporter_summary": str(summary_path),
    }


def _game_result(data: GameObservatoryData) -> dict[str, Any]:
    for event in sorted(data.events, key=lambda row: str(row.get("event_id", "")), reverse=True):
        if event.get("event_type") == "phase_completed" and event.get("phase_id") == "game_end":
            payload = event.get("payload", {})
            result = payload.get("result", {}) if isinstance(payload, dict) else {}
            return result if isinstance(result, dict) else {}
    return {}


def _winner_or_leader(result: dict[str, Any]) -> dict[str, Any]:
    winners = result.get("winners", []) if isinstance(result.get("winners"), list) else []
    country_results = result.get("country_results", {}) if isinstance(result.get("country_results"), dict) else {}
    if len(winners) == 1:
        winner_id = str(winners[0])
        winner_centers = 0
        for _, row in sorted(country_results.items()):
            if not isinstance(row, dict):
                continue
            if str(row.get("agent_id", "")) != winner_id:
                continue
            winner_centers = int(row.get("owned_centers", 0)) if isinstance(row.get("owned_centers", 0), int) else 0
            break
        if winner_centers >= 18:
            return {"kind": "solo_winner", "agent_id": winner_id, "owned_centers": winner_centers}
    leader_country = ""
    leader_agent = ""
    leader_centers = -1
    for country, row in sorted(country_results.items()):
        if not isinstance(row, dict):
            continue
        centers = int(row.get("owned_centers", 0)) if isinstance(row.get("owned_centers", 0), int) else 0
        if centers > leader_centers:
            leader_centers = centers
            leader_country = str(country)
            leader_agent = str(row.get("agent_id", ""))
    return {
        "kind": "leader_at_cap",
        "country": leader_country,
        "agent_id": leader_agent,
        "owned_centers": leader_centers if leader_centers >= 0 else 0,
    }


def _alliance_candidates(data: GameObservatoryData) -> list[dict[str, Any]]:
    by_pair: dict[str, dict[str, Any]] = {}
    for msg in sorted(data.private_messages, key=lambda row: _msg_sort_key(row)):
        frm = str(msg.get("from_agent_id", ""))
        to = str((msg.get("to") or {}).get("to_agent_id", ""))
        if not frm or not to:
            continue
        pair_id = "__".join(sorted([frm, to]))
        row = by_pair.setdefault(pair_id, {"pair": pair_id.split("__"), "messages": 0, "phases": set()})
        row["messages"] += 1
        row["phases"].add(str(msg.get("phase_id", "")))
    out: list[dict[str, Any]] = []
    for pair_id, row in sorted(by_pair.items()):
        count = int(row["messages"])
        confidence = "high" if count >= 4 else "medium" if count >= 2 else "low"
        out.append(
            {
                "pair": row["pair"],
                "message_count": count,
                "phases": sorted([p for p in row["phases"] if p]),
                "confidence": confidence,
            }
        )
    return out


def _betrayal_candidates(data: GameObservatoryData) -> list[dict[str, Any]]:
    contradiction_agents = {
        str(row.get("agent_id", ""))
        for row in _public_private_contradictions_detail(data)
        if isinstance(row, dict)
    }
    candidates: list[dict[str, Any]] = []
    for key, payload in sorted(data.phase_reflections.items()):
        text = json.dumps(payload, sort_keys=True).lower()
        if not any(term in text for term in ["betray", "deceiv", "lied", "backstab"]):
            continue
        phase_id, _, agent_id = key.partition(":")
        if agent_id not in contradiction_agents:
            continue
        candidates.append(
            {
                "phase_id": phase_id,
                "agent_id": agent_id,
                "reason": "reflection language + public/private contradiction evidence",
                "confidence": "medium",
            }
        )
    return candidates


def _key_moments(
    data: GameObservatoryData,
    diplomacy_metrics: dict[str, Any],
    board_deltas: list[dict[str, Any]],
) -> dict[str, Any]:
    betrayal_candidates = _betrayal_candidates(data)
    first_private = _first_private_message(data)
    first_proposal = _first_concrete_proposal(data)
    most_influential_message = _most_influential_message(data)
    most_consequential_order = _most_consequential_order(data)
    largest_swing = _largest_tactical_swing(board_deltas)
    most_passive = _most_passive_player(data)
    most_active = _most_active_negotiator(diplomacy_metrics)
    turning_point = largest_swing["phase_id"] if largest_swing.get("phase_id") else None
    strangest = _strangest_decision(data)

    return {
        "first_private_alliance": first_private,
        "first_concrete_proposal": first_proposal,
        "first_betrayal": "no clear betrayal signal detected" if not betrayal_candidates else betrayal_candidates[0],
        "most_influential_message": most_influential_message,
        "most_consequential_order": most_consequential_order,
        "largest_tactical_swing": largest_swing,
        "most_passive_player": most_passive,
        "most_active_negotiator": most_active,
        "strangest_decision": strangest,
        "decisive_turning_point": turning_point or "no decisive turning point detected",
    }


def _aar_public_markdown(
    *,
    data: GameObservatoryData,
    result: dict[str, Any],
    winner_or_leader: dict[str, Any],
    alliance_candidates: list[dict[str, Any]],
    board_deltas: list[dict[str, Any]],
) -> str:
    capped_no_winner = str(winner_or_leader.get("kind", "")) != "solo_winner"
    largest_swing = _largest_tactical_swing(board_deltas)
    underdeveloped = int(largest_swing.get("swing_score", 0)) == 0
    by_year = _yearly_phase_ids(data.phases)
    lines = [
        "# Public After-Action Report",
        "",
        "This report uses public artifacts and objective outcomes.",
        "",
        "## Final Result",
        f"- result: {result.get('result', 'unknown') if isinstance(result, dict) else 'unknown'}",
        f"- outcome: {winner_or_leader}",
        f"- capped_no_solo: {capped_no_winner}",
        "",
        "## Year-by-Year Narrative",
    ]
    for year, phase_ids in sorted(by_year.items()):
        lines.append(f"### {year}")
        lines.append(f"- phases: {phase_ids}")
        swing = _largest_swing_for_year(year, board_deltas)
        lines.append(f"- major board shift: {swing if swing else 'none detected'}")
        lines.append(
            "- alliance timeline (public inference): "
            + (
                ", ".join(["/".join(row.get("pair", [])) for row in alliance_candidates[:3]])
                if alliance_candidates
                else "no public alliance signal"
            )
        )
    lines.extend(
        [
            "",
            "## Strategic Shifts",
            f"- largest tactical swing: {largest_swing}",
            f"- confidence: {'high' if int(largest_swing.get('swing_score', 0)) > 0 else 'low'}",
            "",
            "## Outcome Interpretation",
            "- no solo winner recorded; reporting leader-at-cap state." if capped_no_winner else "- solo winner recorded in artifact results.",
            "- game appears strategically underdeveloped by center-change/dislodgement/build indicators."
            if underdeveloped
            else "- game shows non-trivial strategic development.",
            "",
            "## Why Powers Succeeded or Stalled",
            "- derived from actual center changes, dislodgements, retreats, and build/disband outcomes (medium confidence).",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _secret_history_markdown(
    *,
    data: GameObservatoryData,
    alliance_candidates: list[dict[str, Any]],
    betrayal_candidates: list[dict[str, Any]],
) -> str:
    lines = [
        "# Secret History",
        "",
        "This report includes private negotiations and reflection artifacts.",
        "",
        "## Private Alliance Formation",
    ]
    if alliance_candidates:
        for row in alliance_candidates:
            lines.append(
                f"- pair={row.get('pair')} messages={row.get('message_count')} confidence={row.get('confidence')}"
            )
    else:
        lines.append("- no private alliance signal detected")

    lines.extend(["", "## Hidden Contradictions"])
    contradiction_rows = _public_private_contradictions(data)
    if contradiction_rows:
        lines.extend([f"- {row}" for row in contradiction_rows])
    else:
        lines.append("- no clear contradiction detected")

    lines.extend(["", "## Planned Betrayals"])
    if betrayal_candidates:
        for row in betrayal_candidates:
            lines.append(f"- evidence={row} (confidence: {row.get('confidence', 'low') if isinstance(row, dict) else 'low'})")
    else:
        lines.append("- no explicit betrayal planning detected")

    lines.extend(["", "## Reflection vs Behavior"])
    lines.extend([f"- {row}" for row in _reflection_behavior_mismatches(data)])

    lines.extend(["", "## Selected Private Transcript Evidence"])
    private_rows = sorted(data.private_messages, key=lambda row: _msg_sort_key(row))[:8]
    if private_rows:
        for row in private_rows:
            frm = str(row.get("from_agent_id", ""))
            to = str((row.get("to") or {}).get("to_agent_id", ""))
            lines.append(f"- {row.get('phase_id')} T{row.get('tick')}: {frm}->{to}: {str(row.get('body', ''))}")
    else:
        lines.append("- no private messages")
    return "\n".join(lines).strip() + "\n"


def _agent_profiles_markdown(
    *,
    data: GameObservatoryData,
    diplomacy_metrics: dict[str, Any],
) -> str:
    per_agent = diplomacy_metrics.get("per_agent", [])
    country_by_agent = {agent: power for power, agent in data.assignments_by_power.items()}
    lines = ["# Agent Profiles", ""]
    for row in per_agent if isinstance(per_agent, list) else []:
        if not isinstance(row, dict):
            continue
        agent_id = str(row.get("agent_id", ""))
        country = country_by_agent.get(agent_id, str(row.get("country", "")))
        global_count = int(row.get("global_messages_sent", 0))
        private_count = int(row.get("private_messages_sent", 0))
        style = "private-heavy" if private_count > global_count else "global-heavy" if global_count > private_count else "balanced"
        likely_allies = _likely_allies_for_agent(data, agent_id)
        rivals = _likely_rivals_for_agent(data, agent_id)
        profile_conf = "high" if (global_count + private_count) >= 3 else "medium" if (global_count + private_count) > 0 else "low"
        lines.extend(
            [
                f"## {agent_id} ({country})",
                f"- diplomatic_style: {style} (confidence: {profile_conf})",
                f"- communication_volume: global={global_count}, private={private_count}",
                f"- private_global_balance: {private_count}:{global_count}",
                f"- likely_allies: {likely_allies} (confidence: {'medium' if likely_allies else 'low'})",
                f"- likely_rivals: {rivals} (confidence: {'medium' if rivals else 'low'})",
                "- commitments_detected: low-confidence heuristic from message language",
                f"- order_quality: {_order_quality_for_agent(data, agent_id)}",
                f"- expansion_success: {_expansion_for_country(data, country)}",
                f"- consistency_soul_messages_reflections_orders: {_consistency_note(data, agent_id)}",
                "",
            ]
        )
    if len(lines) == 2:
        lines.append("- no agent communication records available")
    return "\n".join(lines).strip() + "\n"


def _key_moments_markdown(moments: dict[str, Any]) -> str:
    lines = ["# Key Moments", ""]
    for key in [
        "first_private_alliance",
        "first_concrete_proposal",
        "first_betrayal",
        "most_influential_message",
        "most_consequential_order",
        "largest_tactical_swing",
        "most_passive_player",
        "most_active_negotiator",
        "strangest_decision",
        "decisive_turning_point",
    ]:
        value = moments.get(key, "not applicable")
        lines.append(f"- {key}: {value if value else 'not applicable'}")
    return "\n".join(lines).strip() + "\n"


def _agents_summary(data: GameObservatoryData) -> list[dict[str, Any]]:
    out = []
    for power, agent in sorted(data.assignments_by_power.items()):
        out.append({"agent_id": agent, "country": power})
    return out


def _yearly_phase_ids(phases: list[str]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for phase_id in phases:
        match = re.match(r"^(\d{4})_", phase_id)
        if match is None:
            continue
        year = int(match.group(1))
        out.setdefault(year, []).append(phase_id)
    return out


def _largest_swing_for_year(year: int, deltas: list[dict[str, Any]]) -> dict[str, Any] | None:
    tagged = [row for row in deltas if str(row.get("phase_id", "")).startswith(f"{year}_")]
    if not tagged:
        return None
    return _largest_tactical_swing(tagged)


def _largest_tactical_swing(deltas: list[dict[str, Any]]) -> dict[str, Any]:
    best = {"phase_id": "", "country": "", "center_delta": 0, "swing_score": 0}
    for row in deltas:
        phase_id = str(row.get("phase_id", ""))
        powers = row.get("powers", {})
        if not isinstance(powers, dict):
            continue
        center_changes = row.get("center_changes", [])
        builds = row.get("builds", [])
        disbands = row.get("disbands", [])
        swing_score = 0
        if isinstance(center_changes, list):
            swing_score += len(center_changes) * 3
        if isinstance(builds, list):
            swing_score += len(builds)
        if isinstance(disbands, list):
            swing_score += len(disbands)
        for country, payload in powers.items():
            if not isinstance(payload, dict):
                continue
            gained = len(payload.get("centers_gained", [])) if isinstance(payload.get("centers_gained", []), list) else 0
            lost = len(payload.get("centers_lost", [])) if isinstance(payload.get("centers_lost", []), list) else 0
            delta = gained - lost
            score = swing_score + int(payload.get("dislodgements", 0)) * 2 + int(payload.get("retreats_required", 0))
            if score > int(best["swing_score"]):
                best = {
                    "phase_id": phase_id,
                    "country": str(country),
                    "center_delta": delta,
                    "swing_score": score,
                }
    return best


def _first_private_message(data: GameObservatoryData) -> dict[str, Any] | str:
    rows = sorted(data.private_messages, key=lambda row: _msg_sort_key(row))
    if not rows:
        return "no private alliance signal detected"
    first = rows[0]
    return {
        "phase_id": first.get("phase_id"),
        "from_agent_id": first.get("from_agent_id"),
        "to_agent_id": (first.get("to") or {}).get("to_agent_id"),
    }


def _first_concrete_proposal(data: GameObservatoryData) -> dict[str, Any] | str:
    keywords = ["support", "ally", "alliance", "dmz", "bounce", "move to", "coordinate"]
    merged = sorted(data.global_messages + data.private_messages, key=lambda row: _msg_sort_key(row))
    for row in merged:
        body = str(row.get("body", "")).lower()
        if any(keyword in body for keyword in keywords):
            return {
                "phase_id": row.get("phase_id"),
                "from_agent_id": row.get("from_agent_id"),
                "body": row.get("body"),
            }
    return "no concrete proposal detected"


def _most_influential_message(data: GameObservatoryData) -> dict[str, Any] | str:
    merged = [row for row in (data.global_messages + data.private_messages) if isinstance(row, dict)]
    if not merged:
        return "no messages"
    best = sorted(merged, key=lambda row: len(str(row.get("body", ""))), reverse=True)[0]
    return {
        "phase_id": best.get("phase_id"),
        "from_agent_id": best.get("from_agent_id"),
        "to": best.get("to"),
        "body": best.get("body"),
    }


def _most_consequential_order(data: GameObservatoryData) -> dict[str, Any] | str:
    for phase_id in data.phases:
        resolution = data.phase_resolutions.get(phase_id, {})
        for row in resolution.get("movement_outcomes", []):
            if isinstance(row, dict) and row.get("result") == "dislodged":
                return {"phase_id": phase_id, "outcome": row}
    return "no dislodgement event detected"


def _most_passive_player(data: GameObservatoryData) -> dict[str, Any] | str:
    by_power: dict[str, dict[str, int]] = {}
    for phase_id, orders in data.phase_submitted_orders.items():
        if "movement" not in phase_id:
            continue
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in orders:
            if not isinstance(row, dict):
                continue
            grouped.setdefault(str(row.get("power", "")), []).append(row)
        for power, rows in grouped.items():
            if not power or not rows:
                continue
            bucket = by_power.setdefault(power, {"all_hold_phases": 0, "movement_phases": 0})
            bucket["movement_phases"] += 1
            if all(str(row.get("type", "")) == "HOLD" for row in rows):
                bucket["all_hold_phases"] += 1
    if not by_power:
        return "no movement orders found"
    ranked = sorted(
        by_power.items(),
        key=lambda item: (
            item[1]["all_hold_phases"] / item[1]["movement_phases"] if item[1]["movement_phases"] else 0.0,
            item[1]["all_hold_phases"],
        ),
        reverse=True,
    )[0]
    power, stats = ranked
    return {"country": power, **stats}


def _most_active_negotiator(metrics: dict[str, Any]) -> dict[str, Any] | str:
    per_agent = metrics.get("per_agent", [])
    if not isinstance(per_agent, list) or not per_agent:
        return "no communication stats"
    best = sorted(
        [row for row in per_agent if isinstance(row, dict)],
        key=lambda row: int(row.get("private_messages_sent", 0)) + int(row.get("global_messages_sent", 0)),
        reverse=True,
    )[0]
    return {
        "agent_id": best.get("agent_id"),
        "country": best.get("country"),
        "messages_sent_total": int(best.get("private_messages_sent", 0)) + int(best.get("global_messages_sent", 0)),
    }


def _strangest_decision(data: GameObservatoryData) -> dict[str, Any] | str:
    for phase_id in data.phases:
        if "movement" not in phase_id:
            continue
        orders = data.phase_submitted_orders.get(phase_id, [])
        by_power: dict[str, list[dict[str, Any]]] = {}
        for row in orders:
            if isinstance(row, dict):
                by_power.setdefault(str(row.get("power", "")), []).append(row)
        for power, rows in sorted(by_power.items()):
            if rows and all(str(row.get("type", "")) == "HOLD" for row in rows):
                return {
                    "phase_id": phase_id,
                    "country": power,
                    "reason": "all units submitted HOLD during movement",
                    "confidence": "medium",
                }
    return "no unusual decision detected"


def _public_private_contradictions(data: GameObservatoryData) -> list[str]:
    return [str(row.get("summary", "")) for row in _public_private_contradictions_detail(data)]


def _public_private_contradictions_detail(data: GameObservatoryData) -> list[dict[str, Any]]:
    public_by_agent: dict[str, list[str]] = {}
    private_by_agent: dict[str, list[str]] = {}
    for row in data.global_messages:
        agent = str(row.get("from_agent_id", ""))
        if agent:
            public_by_agent.setdefault(agent, []).append(str(row.get("body", "")))
    for row in data.private_messages:
        agent = str(row.get("from_agent_id", ""))
        if agent:
            private_by_agent.setdefault(agent, []).append(str(row.get("body", "")))
    contradictions: list[dict[str, Any]] = []
    for agent in sorted(set(public_by_agent.keys()) & set(private_by_agent.keys())):
        public_text = " ".join(public_by_agent.get(agent, [])).lower()
        private_text = " ".join(private_by_agent.get(agent, [])).lower()
        public_coop = any(term in public_text for term in ["peace", "ally", "trust", "cooperate"])
        private_aggressive = any(term in private_text for term in ["attack", "strike", "betray", "stab", "deceiv"])
        if public_coop and private_aggressive:
            contradictions.append(
                {
                    "agent_id": agent,
                    "summary": f"{agent}: public cooperative language with private aggressive language",
                    "confidence": "medium",
                }
            )
    return contradictions


def _reflection_behavior_mismatches(data: GameObservatoryData) -> list[str]:
    out: list[str] = []
    for key, payload in sorted(data.phase_reflections.items()):
        reflection_text = json.dumps(payload, sort_keys=True).lower()
        phase_id, _, agent_id = key.partition(":")
        orders_key = f"{phase_id}_submit_orders"
        runtime_output = data.runtime_outputs_by_agent.get(agent_id, {}).get(orders_key, {})
        parsed = {}
        if isinstance(runtime_output, dict):
            llm_meta = runtime_output.get("llm_meta", {})
            if isinstance(llm_meta, dict) and isinstance(llm_meta.get("parsed_payload"), dict):
                parsed = llm_meta["parsed_payload"]
        orders = parsed.get("orders", []) if isinstance(parsed.get("orders"), list) else []
        all_hold = bool(orders) and all(str(row.get("type", "")).upper() == "HOLD" for row in orders if isinstance(row, dict))
        if "advance" in reflection_text and all_hold:
            out.append(f"{phase_id}:{agent_id} reflection favored advance but submitted all HOLD orders")
    if not out:
        out.append("no clear reflection-behavior mismatch detected")
    return out


def _likely_allies_for_agent(data: GameObservatoryData, agent_id: str) -> list[str]:
    contacts: dict[str, int] = {}
    for row in data.private_messages:
        frm = str(row.get("from_agent_id", ""))
        to = str((row.get("to") or {}).get("to_agent_id", ""))
        if frm == agent_id and to:
            contacts[to] = contacts.get(to, 0) + 1
        if to == agent_id and frm:
            contacts[frm] = contacts.get(frm, 0) + 1
    return [agent for agent, _ in sorted(contacts.items(), key=lambda item: item[1], reverse=True)[:3]]


def _likely_rivals_for_agent(data: GameObservatoryData, agent_id: str) -> list[str]:
    country = ""
    for power, assigned in data.assignments_by_power.items():
        if assigned == agent_id:
            country = power
            break
    if not country:
        return []
    rivals: dict[str, int] = {}
    for phase_id, resolution in data.phase_resolutions.items():
        output_state = resolution.get("output_state", {})
        units = [row for row in output_state.get("units", []) if isinstance(row, dict)]
        own_provinces = {str(unit.get("province", "")) for unit in units if unit.get("owner") == country}
        for unit in units:
            if unit.get("owner") == country:
                continue
            if str(unit.get("province", "")) in own_provinces:
                rivals[str(unit.get("owner", ""))] = rivals.get(str(unit.get("owner", "")), 0) + 1
    return [power for power, _ in sorted(rivals.items(), key=lambda item: item[1], reverse=True)[:3] if power]


def _order_quality_for_agent(data: GameObservatoryData, agent_id: str) -> str:
    accepted = 0
    total = 0
    for phase_id, resolution in data.phase_resolutions.items():
        submitted = data.phase_submitted_orders.get(phase_id, [])
        country = ""
        for power, assigned in data.assignments_by_power.items():
            if assigned == agent_id:
                country = power
                break
        if not country:
            continue
        submitted_count = len([row for row in submitted if isinstance(row, dict) and row.get("power") == country])
        total += submitted_count
        validated = resolution.get("validated_orders", [])
        for row in validated:
            if not isinstance(row, dict):
                continue
            if row.get("validation") == "accepted":
                accepted += 1
    if total == 0:
        return "low confidence (no orders observed)"
    ratio = accepted / total
    if ratio >= 0.8:
        return "high"
    if ratio >= 0.5:
        return "medium"
    return "low"


def _expansion_for_country(data: GameObservatoryData, country: str) -> str:
    if not country:
        return "unknown"
    first = None
    last = None
    for phase_id in data.phases:
        resolution = data.phase_resolutions.get(phase_id, {})
        supply = resolution.get("output_state", {}).get("supply_ownership", {})
        if not isinstance(supply, dict):
            continue
        count = sum(1 for owner in supply.values() if owner == country)
        if first is None:
            first = count
        last = count
    if first is None or last is None:
        return "unknown"
    delta = last - first
    if delta > 0:
        return f"expanded (+{delta})"
    if delta < 0:
        return f"contracted ({delta})"
    return "stalled (0)"


def _consistency_note(data: GameObservatoryData, agent_id: str) -> str:
    mismatches = 0
    total = 0
    for key, payload in data.phase_reflections.items():
        _, _, reflect_agent = key.partition(":")
        if reflect_agent != agent_id:
            continue
        total += 1
        text = json.dumps(payload, sort_keys=True).lower()
        if "advance" in text or "attack" in text:
            phase_id = key.split(":")[0]
            orders = data.phase_submitted_orders.get(phase_id, [])
            power = next((p for p, a in data.assignments_by_power.items() if a == agent_id), "")
            own = [row for row in orders if isinstance(row, dict) and row.get("power") == power]
            if own and all(str(row.get("type", "")).upper() == "HOLD" for row in own):
                mismatches += 1
    if total == 0:
        return "low confidence (no reflection samples)"
    if mismatches == 0:
        return "mostly aligned (medium confidence)"
    return f"mixed alignment ({mismatches}/{total} mismatches, low confidence)"


def _msg_sort_key(msg: dict[str, Any]) -> tuple[str, int, str]:
    tick = msg.get("tick")
    tick_int = int(tick) if isinstance(tick, int) else 0
    return (str(msg.get("phase_id", "")), tick_int, str(msg.get("message_id", "")))
