from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .api import GameObservatoryData, summarize_board_evolution
from .event_index import build_canonical_event_index, phase_sort_key, reflection_body


_PROVINCE_TOKEN = re.compile(r"\b[a-z]{3}\b", flags=re.IGNORECASE)

_SUPPORT_REQUEST_TOKENS = (
    "support",
    "support me",
    "can you support",
    "support into",
)
_SUPPORT_OFFER_TOKENS = (
    "i can support",
    "i will support",
    "support you",
    "offer support",
)
_DMZ_TOKENS = ("dmz", "demilitarized", "border", "non-aggression", "red line")
_THREAT_TOKENS = ("or else", "otherwise", "retaliat", "consequence", "threat")
_WARNING_TOKENS = ("warn", "warning", "heard", "rumor", "intel", "report")
_CLAIM_TEST_TOKENS = ("confirm", "verify", "evidence", "prove", "is that true")
_CONFIRMATION_TOKENS = (
    "continue building trust",
    "stay coordinated",
    "maintain coordination",
    "aligned",
    "continue cooperation",
)
_ASSURANCE_TOKENS = (
    "i will not attack",
    "i won't attack",
    "non-aggression",
    "dmz",
    "respect your border",
    "do not enter",
)


@dataclass(frozen=True)
class _AgentPhaseContext:
    phase_id: str
    agent_id: str
    power: str
    objective_summary: str
    objective_source: str
    objective_excerpt: str
    relationship_use: dict[str, Any]
    diplomatic_action: dict[str, Any]
    order_alignment: dict[str, Any]
    board_consequence: dict[str, Any]
    memory_update: dict[str, Any]
    belief_revision: dict[str, Any]
    belief_summary: dict[str, Any]
    confidence: str
    basis: str


def summarize_strategic_consistency(
    data: GameObservatoryData,
    *,
    board_deltas: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return _summarize_belief_evidence_observability(data, board_deltas=board_deltas)


def _summarize_belief_evidence_observability(
    data: GameObservatoryData,
    *,
    board_deltas: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    events = build_canonical_event_index(data)
    reflections = [event for event in events if event["event_type"] == "reflection"]
    belief_rows = [
        _canonical_belief_summary(events, event, reflection_body(event["payload"]))
        for event in reflections
    ]
    belief_metrics = _belief_observability_metrics(reflections, belief_rows)
    messages = [event for event in events if event["event_type"] == "negotiation_message"]
    orders = [event for event in events if event["event_type"] in {"order_submission", "retreat_submission", "adjustment_build_submission"}]
    resolutions = [event for event in events if event["event_type"] == "board_resolution"]
    deltas = board_deltas if board_deltas is not None else summarize_board_evolution(data)
    valid_orders = sum(
        1
        for resolution in data.phase_resolutions.values()
        for row in resolution.get("validated_orders", [])
        if isinstance(row, dict) and row.get("validation") in {"accepted", "substituted"}
    )
    rejected_orders = sum(
        1
        for resolution in data.phase_resolutions.values()
        for row in resolution.get("validated_orders", [])
        if isinstance(row, dict) and row.get("validation") == "rejected"
    )
    return {
        "schema_version": "1.0",
        "report_type": "belief_and_evidence_observability",
        "heuristic": True,
        "game_id": data.game_dir.name,
        "summary": {
            "agents_analyzed": len({event["agent_id"] for event in events if event["agent_id"]}),
            "phases_observed": len({event["phase_id"] for event in events if event["phase_id"] != "artifact_linkage_insufficient"}),
            "negotiation_messages_observed": len(messages),
            "order_submissions_observed": len(orders),
            "valid_orders": valid_orders,
            "rejected_orders": rejected_orders,
            "board_resolutions_observed": len(resolutions),
            "board_state_changes_observed": sum(len(row.get("moves", [])) + len(row.get("center_changes", [])) + len(row.get("builds", [])) + len(row.get("disbands", [])) for row in deltas if isinstance(row, dict)),
            "belief_summaries_detected": belief_metrics["summaries_present"],
            "belief_summaries_persisted": belief_metrics["summaries_persisted"],
            "belief_summaries_reused": belief_metrics["summaries_reused"],
            "memory_updates_observed": sum(event["event_type"] == "memory_update" for event in events),
        },
        "belief_summary_metrics": belief_metrics,
        "per_agent": belief_metrics["per_agent"],
        "message_reliability": {
            "messages_with_visible_body": sum(bool(str(event["payload"].get("body", "")).strip()) for event in messages),
            "messages_missing_phase_linkage": sum(event["phase_id"] == "artifact_linkage_insufficient" for event in messages),
        },
        "order_validity": {"valid_orders": valid_orders, "rejected_orders": rejected_orders},
        "board_resolution_deltas": deltas,
        "raw_evidence": {
            "event_index": events,
            "artifact_categories": [
                "raw reflection JSON", "belief summaries", "negotiation messages", "submitted and validated orders",
                "adjudicated resolutions", "board snapshots", "public resolution deltas", "persistent memory",
            ],
        },
        "method_notes": {
            "summary": "Observability only; this report does not score or infer strategic-chain completion.",
            "review_guidance": "Use linked raw artifacts for narrative reconstruction of communication, interpretation, orders, consequences, and adaptation.",
        },
    }


def _belief_observability_metrics(reflections: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    present = [row for row in rows if row.get("present")]
    per_agent: dict[str, dict[str, int]] = {}
    for reflection, row in zip(reflections, rows):
        agent_id = str(reflection.get("agent_id", ""))
        bucket = per_agent.setdefault(agent_id, {"reflections_observed": 0, "belief_summaries_detected": 0, "belief_summaries_persisted": 0, "belief_summaries_reused": 0})
        bucket["reflections_observed"] += 1
        bucket["belief_summaries_detected"] += int(bool(row.get("present")))
        bucket["belief_summaries_persisted"] += int(bool(row.get("persisted")))
        bucket["belief_summaries_reused"] += int(bool(row.get("reused")))
    count = len(present)
    return {
        "reflection_count": len(reflections),
        "summaries_present": count,
        "presence_rate": round(count / len(reflections), 4) if reflections else 0.0,
        "average_summary_length": round(sum(int(row.get("word_count", 0)) for row in present) / count, 2) if count else 0.0,
        "summaries_produced": sum(bool(row.get("produced")) for row in present),
        "summaries_maintained": sum(bool(row.get("maintained")) for row in present),
        "summaries_revised": sum(bool(row.get("revised")) for row in present),
        "summaries_persisted": sum(bool(row.get("persisted")) for row in present),
        "summaries_reused": sum(bool(row.get("reused")) for row in present),
        "explicit_uncertainty_count": sum(bool(row.get("explicit_uncertainty")) for row in present),
        "message_evidence_references": sum(bool(row.get("evidence_references", {}).get("messages")) for row in present),
        "action_order_evidence_references": sum(bool(row.get("evidence_references", {}).get("orders_or_resolutions")) for row in present),
        "board_state_evidence_references": sum(bool(row.get("evidence_references", {}).get("board_state")) for row in present),
        "say_do_comparison_count": sum(bool(row.get("say_do_comparison")) for row in present),
        "per_agent": per_agent,
    }


def _legacy_summarize_strategic_consistency(
    data: GameObservatoryData,
    *,
    board_deltas: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    deltas = board_deltas if board_deltas is not None else summarize_board_evolution(data)
    delta_by_phase = {
        str(row.get("phase_id", "")): row for row in deltas if isinstance(row, dict)
    }
    power_by_agent = {agent: power for power, agent in data.assignments_by_power.items()}
    all_agents = sorted(
        set(power_by_agent.keys())
        | set(_messages_by_agent(data).keys())
        | set(data.runtime_outputs_by_agent.keys())
    )

    chain_rows: list[dict[str, Any]] = []
    per_agent: dict[str, dict[str, Any]] = {}
    for agent_id in all_agents:
        power = power_by_agent.get(agent_id, "")
        agent_chains: list[_AgentPhaseContext] = []
        for phase_id in data.phases:
            context = _build_phase_context(
                data=data,
                agent_id=agent_id,
                power=power,
                phase_id=phase_id,
                delta=delta_by_phase.get(phase_id, {}),
            )
            if context is None:
                continue
            agent_chains.append(context)
            chain_rows.append(
                {
                    "chain_id": f"{data.game_dir.name}_{phase_id}_{agent_id}_{len(agent_chains):03d}",
                    "agent_id": agent_id,
                    "country": power,
                    "phase": phase_id,
                    "confidence": context.confidence,
                    "basis": context.basis,
                    "strategic_objective": {
                        "summary": context.objective_summary,
                        "source": context.objective_source,
                        "evidence_excerpt": context.objective_excerpt,
                    },
                    "relationship_use": context.relationship_use,
                    "diplomatic_action": context.diplomatic_action,
                    "order_alignment": context.order_alignment,
                    "board_consequence": context.board_consequence,
                    "memory_update": context.memory_update,
                    "belief_revision": context.belief_revision,
                    "belief_summary": context.belief_summary,
                    "analyst_note": _analyst_note(context),
                }
            )
        per_agent[agent_id] = _agent_summary(agent_id, power, agent_chains)

    stagnation_candidates = _stagnation_candidates(data)
    betrayal_candidates = _broken_commitment_candidates(data, deltas)

    total_chains = len(chain_rows)
    complete_chains = len(
        [
            row
            for row in chain_rows
            if row["diplomatic_action"].get("status") == "present"
            and row["order_alignment"].get("status") in {"aligned", "partially_aligned"}
            and row["board_consequence"].get("category") not in {"no_material_change", "unclear"}
            and row["memory_update"].get("status") == "present"
        ]
    )
    high_conf = len([row for row in chain_rows if row.get("confidence") == "high"])
    low_conf = len([row for row in chain_rows if row.get("confidence") == "low"])
    phases_analyzed = len(data.phases)
    belief_summary_metrics = _belief_summary_metrics(chain_rows)

    return {
        "schema_version": "1.0",
        "heuristic": True,
        "game_id": data.game_dir.name,
        "summary": {
            "agents_analyzed": len(all_agents),
            "phases_analyzed": phases_analyzed,
            "chains_detected": total_chains,
            "complete_chains": complete_chains,
            "partial_chains": max(0, total_chains - complete_chains),
            "high_confidence_chains": high_conf,
            "low_confidence_chains": low_conf,
            "belief_summary_presence_rate": belief_summary_metrics["presence_rate"],
            "belief_summary_replacement_rate": belief_summary_metrics["replacement_rate"],
            "belief_summary_reuse_rate": belief_summary_metrics["reuse_rate"],
        },
        "agent_summaries": {
            agent_id: {
                key: value
                for key, value in summary.items()
                if key not in {"country"}
            }
            for agent_id, summary in sorted(per_agent.items())
        },
        "chains": chain_rows,
        "belief_summary_metrics": belief_summary_metrics,
        "stagnation_loop_candidates": stagnation_candidates,
        "broken_commitment_candidates": betrayal_candidates,
        "method_notes": {
            "summary": "Derived from artifacts using conservative heuristics.",
            "limitations": [
                "Conversation/message classification is heuristic and not semantic certainty.",
                "Order-alignment inference is approximate and can be partial.",
                "Betrayal labels require explicit commitment + contradictory action + consequence evidence.",
            ],
        },
    }


def _summarize_from_canonical_events(
    data: GameObservatoryData,
    *,
    board_deltas: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Link strategy artifacts across phases without assuming one shared phase ID."""
    events = build_canonical_event_index(data)
    power_by_agent = {agent: power for power, agent in data.assignments_by_power.items()}
    reflections = [event for event in events if event["event_type"] == "reflection"]
    agents = sorted({event["agent_id"] for event in events if event["agent_id"]} | set(power_by_agent))
    chains: list[dict[str, Any]] = []
    for reflection in reflections:
        agent_id = reflection["agent_id"]
        body = reflection_body(reflection["payload"])
        objective = _canonical_objective(body)
        chain = _link_objective_chain(events, reflection, objective)
        chain["chain_id"] = f"{data.game_dir.name}_{reflection['phase_id']}_{agent_id}_{len(chains):03d}"
        chain["agent_id"] = agent_id
        chain["country"] = reflection.get("country") or power_by_agent.get(agent_id, "")
        chain["phase_id"] = reflection["phase_id"]
        chain["phase"] = reflection["phase_id"]  # compatibility with existing report readers
        chain["phase_order"] = reflection["phase_order"]
        chain["strategic_objective"] = {
            "summary": objective,
            "source": "reflection" if objective else "not_found",
            "evidence_excerpt": _trim(objective, 160),
            "artifact_path": reflection["artifact_path"],
        }
        evidence_reflection = _followup_reflection(events, reflection, chain["board_consequence"]) or reflection
        evidence_body = reflection_body(evidence_reflection["payload"])
        chain["belief_revision"] = _canonical_belief_revision(events, evidence_reflection, evidence_body)
        chain["belief_summary"] = _canonical_belief_summary(events, evidence_reflection, evidence_body)
        chain["relationship_use"] = _canonical_relationship_use(body)
        chain["memory_update"] = chain["belief_revision"].get("memory_update", {
            "status": "not_found", "summary": "No linked memory update found."
        })
        stages = chain["stages"]
        stages["belief_or_memory_update_present"] = chain["belief_revision"]["status"] != "not_produced" or chain["belief_summary"]["present"]
        stages["belief_or_memory_update_reused"] = (
            chain["belief_revision"]["status"] == "reused" or chain["belief_summary"].get("reused") is True
        )
        complete = all(
            stages[key]
            for key in ("objective_present", "message_present", "order_present", "order_aligned", "board_consequence_present", "belief_or_memory_update_present")
        )
        chain["complete_chain"] = complete
        chain["chain_status"] = "confirmed" if complete else _chain_status(stages)
        chain["confidence"] = "high" if complete else "medium" if sum(bool(value) for value in stages.values()) >= 3 else "low"
        chain["basis"] = "canonical chronological event linkage"
        chain["analyst_note"] = _canonical_note(chain)
        chains.append(chain)

    belief_metrics = _canonical_belief_metrics(events, reflections, chains)
    per_agent = {
        agent: _canonical_agent_summary(agent, power_by_agent.get(agent, ""), chains, belief_metrics.get("per_agent", {}).get(agent, {}))
        for agent in agents
    }
    deltas = board_deltas if board_deltas is not None else summarize_board_evolution(data)
    complete_count = sum(1 for row in chains if row["complete_chain"])
    return {
        "schema_version": "1.0",
        "heuristic": True,
        "game_id": data.game_dir.name,
        "event_index": events,
        "summary": {
            "agents_analyzed": len(agents),
            "phases_analyzed": len({event["phase_id"] for event in events if event["phase_id"] != "artifact_linkage_insufficient"}),
            "chains_detected": len(chains),
            "complete_chains": complete_count,
            "partial_chains": len(chains) - complete_count,
            "high_confidence_chains": sum(1 for row in chains if row["confidence"] == "high"),
            "low_confidence_chains": sum(1 for row in chains if row["confidence"] == "low"),
            "belief_summary_presence_rate": belief_metrics["presence_rate"],
            "belief_summary_replacement_rate": belief_metrics["replacement_rate"],
            "belief_summary_reuse_rate": belief_metrics["reuse_rate"],
            "belief_summaries_detected": belief_metrics["summaries_present"],
            "belief_summaries_persisted": belief_metrics["summaries_persisted"],
            "belief_summaries_reused": belief_metrics["summaries_reused"],
            "objectives_with_concrete_messages": sum(1 for row in chains if row["stages"]["message_present"]),
            "memory_updates_after_consequence": sum(1 for row in chains if row["stages"]["belief_or_memory_update_present"] and row["stages"]["board_consequence_present"]),
        },
        "agent_summaries": per_agent,
        "chains": chains,
        "belief_summary_metrics": belief_metrics,
        "stagnation_loop_candidates": _stagnation_candidates(data),
        "broken_commitment_candidates": _broken_commitment_candidates(data, deltas),
        "method_notes": {
            "summary": "Canonical event linkage is chronological and conservative.",
            "limitations": [
                "Artifact linkage insufficiency is reported separately from missing strategic behavior.",
                "Order alignment requires more than one shared province token.",
                "Reuse requires a later prompt or artifact with lexical evidence of the belief.",
            ],
        },
    }


def _event_time(event: dict[str, Any]) -> tuple[int, int, int, str]:
    priority = {"reflection": 0, "negotiation_message": 1, "order_submission": 2, "retreat_submission": 2, "adjustment_build_submission": 2, "board_resolution": 3, "memory_update": 4, "belief_summary_update": 4, "prompt_context": 5}.get(event.get("event_type"), 9)
    year = event.get("year") if isinstance(event.get("year"), int) else 10**9
    return (year, int(event.get("phase_order", 10**9)), priority, str(event.get("event_id", "")))


def _followup_reflection(
    events: list[dict[str, Any]],
    source: dict[str, Any],
    consequence: dict[str, Any],
) -> dict[str, Any] | None:
    phase_id = str(consequence.get("phase_id", ""))
    if not phase_id:
        return None
    candidates = [
        event for event in events
        if event.get("event_type") == "reflection"
        and event.get("agent_id") == source.get("agent_id")
        and event.get("event_id") != source.get("event_id")
        and phase_sort_key(event.get("phase_id")) >= phase_sort_key(phase_id)
    ]
    return min(candidates, key=_event_time) if candidates else None


def _later(events: list[dict[str, Any]], source: dict[str, Any], *, event_types: set[str], agent_id: str | None = None) -> list[dict[str, Any]]:
    source_time = _event_time(source)
    return [
        event for event in events
        if event.get("event_type") in event_types
        and (agent_id is None or event.get("agent_id") == agent_id)
        and _event_time(event) > source_time
        and event.get("phase_id") != "artifact_linkage_insufficient"
    ]


def _canonical_objective(body: dict[str, Any]) -> str:
    objective = body.get("objective_update")
    if isinstance(objective, dict):
        value = str(objective.get("current_objective", "")).strip()
        if value:
            return value
    for key in ("next_objective", "strategic_position", "memory_update"):
        value = str(body.get(key, "")).strip()
        if value:
            return value
    intent = body.get("strategic_intent")
    return str(intent.get("short_term", "")).strip() if isinstance(intent, dict) else ""


def _link_objective_chain(events: list[dict[str, Any]], reflection: dict[str, Any], objective: str) -> dict[str, Any]:
    agent_id = str(reflection.get("agent_id", ""))
    messages = _later(events, reflection, event_types={"negotiation_message"}, agent_id=agent_id)
    message = messages[0] if messages else None
    anchor = message or reflection
    orders = _later(events, anchor, event_types={"order_submission", "retreat_submission", "adjustment_build_submission"}, agent_id=agent_id)
    order = orders[0] if orders else None
    resolutions = [
        event for event in events
        if event.get("event_type") == "board_resolution" and order and event.get("phase_id") == order.get("phase_id")
    ]
    resolution = resolutions[0] if resolutions else None
    action = _canonical_diplomatic_action(message)
    alignment = _canonical_order_alignment(objective, message, order)
    consequence = _canonical_board_consequence(resolution, order)
    stages = {
        "objective_present": bool(objective),
        "message_present": message is not None,
        "order_present": order is not None,
        "order_aligned": alignment["status"] == "aligned",
        "board_consequence_present": resolution is not None,
        "belief_or_memory_update_present": False,
        "belief_or_memory_update_reused": False,
    }
    return {
        "stages": stages,
        "diplomatic_action": action,
        "order_alignment": alignment,
        "board_consequence": consequence,
        "evidence_paths": {
            "reflection": reflection.get("artifact_path"),
            "message": message.get("artifact_path") if message else None,
            "order": order.get("artifact_path") if order else None,
            "resolution": resolution.get("artifact_path") if resolution else None,
        },
    }


def _canonical_diplomatic_action(event: dict[str, Any] | None) -> dict[str, Any]:
    if event is None:
        return {"status": "not_observed", "category": "none", "summary": "No later outbound negotiation message was linked."}
    classified = _classify_message(event["payload"])
    concrete = classified["category"] != "reassurance_only"
    return {"status": "present" if concrete else "present_not_concrete", **classified, "message_id": event["payload"].get("message_id"), "phase_id": event["phase_id"], "artifact_path": event["artifact_path"]}


def _canonical_order_alignment(objective: str, message: dict[str, Any] | None, order: dict[str, Any] | None) -> dict[str, Any]:
    if order is None:
        return {"status": "not_observed", "orders": [], "summary": "No subsequent order submission was linked."}
    text = _order_text(order["payload"])
    target_tokens = set(_PROVINCE_TOKEN.findall(objective.lower()))
    if message:
        target_tokens |= set(_PROVINCE_TOKEN.findall(str(message["payload"].get("body", "")).lower()))
    order_tokens = set(_PROVINCE_TOKEN.findall(text.lower()))
    overlap = sorted(target_tokens & order_tokens)
    action_words = {"move", "hold", "support", "build", "retreat", "convoy"}
    objective_actions = {word for word in action_words if word in f"{objective} {message['payload'].get('body', '') if message else ''}".lower()}
    order_action = str(order["payload"].get("type", "")).lower().replace("_", " ")
    action_match = not objective_actions or any(word in order_action for word in objective_actions)
    if len(overlap) >= 2 and action_match:
        status = "aligned"
    elif overlap:
        status = "partially_aligned"
    elif not target_tokens:
        status = "artifact_linkage_insufficient"
    else:
        status = "not_aligned"
    return {"status": status, "orders": [text], "order_id": order["payload"].get("order_id"), "phase_id": order["phase_id"], "artifact_path": order["artifact_path"], "summary": f"Objective/order province overlap: {overlap}."}


def _canonical_board_consequence(resolution: dict[str, Any] | None, order: dict[str, Any] | None) -> dict[str, Any]:
    if resolution is None:
        return {"category": "artifact_linkage_insufficient", "summary": "No resolution matching the submitted order phase was found.", "center_delta": 0}
    payload = resolution["payload"]
    outcomes = [row for key in ("movement_outcomes", "retreat_outcomes", "adjustment_outcomes") for row in payload.get(key, []) if isinstance(row, dict)]
    center_changes = _resolution_center_changes(payload)
    result_terms = [str(row.get("result", "")) for row in outcomes]
    category = "resolved_position"
    if center_changes:
        category = "center_changed"
    elif any(term in {"dislodged", "bounced", "failed"} for term in result_terms):
        category = "contested_or_failed"
    return {"category": category, "summary": f"Resolution recorded {len(outcomes)} outcomes and {len(center_changes)} center changes.", "center_delta": len(center_changes), "phase_id": resolution["phase_id"], "artifact_path": resolution["artifact_path"], "outcomes": outcomes[:12], "center_changes": center_changes}


def _resolution_center_changes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    before = payload.get("input_state", {}).get("supply_ownership", {}) if isinstance(payload.get("input_state"), dict) else {}
    after = payload.get("output_state", {}).get("supply_ownership", {}) if isinstance(payload.get("output_state"), dict) else {}
    if not isinstance(before, dict) or not isinstance(after, dict):
        return []
    return [{"province": key, "from_owner": before.get(key), "to_owner": after.get(key)} for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)]


def _canonical_belief_revision(events: list[dict[str, Any]], reflection: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    updates = [row for row in body.get("belief_updates", []) if _is_structured_belief_update(row)] if isinstance(body.get("belief_updates"), list) else []
    if not updates:
        return {"status": "not_produced", "summary": "No structured belief update was produced.", "memory_update": {"status": "not_found", "summary": "No structured belief update was produced."}}
    source = reflection
    later_memory = _later(events, source, event_types={"memory_update", "belief_summary_update"}, agent_id=source["agent_id"])
    new_belief = str(updates[0].get("new_belief", ""))
    persisted = any(event["artifact_path"].startswith("agents/") and _text_overlap(new_belief, json.dumps(event["payload"])) for event in later_memory)
    prompts = _later(events, source, event_types={"prompt_context"}, agent_id=source["agent_id"])
    reused = any(_text_overlap(new_belief, str(event["payload"].get("prompt", ""))) for event in prompts)
    status = "reused" if persisted and reused else "persisted_not_reused" if persisted and prompts else "artifact_linkage_insufficient" if persisted else "produced_not_persisted"
    evidence = " ".join(str(row.get("evidence", "")) for row in updates)
    prior_events = [event for event in events if event["agent_id"] == source["agent_id"] and _event_time(event) <= _event_time(source)]
    message_linked = _evidence_mentions_message_claim(evidence.lower()) or any(_text_overlap(evidence, str(event["payload"].get("body", ""))) for event in prior_events if event["event_type"] == "negotiation_message")
    consequence_seen = any(
        event["event_type"] == "board_resolution"
        and (_event_time(event) <= _event_time(source) or event.get("phase_id") == source.get("phase_id"))
        for event in events
    )
    return {"status": status, "update_count": len(updates), "linked_to_message_claim": message_linked, "linked_to_order_or_resolution": _evidence_mentions_order_or_resolution(evidence.lower()) or consequence_seen, "after_public_consequence": consequence_seen, "summary": _trim(new_belief, 160), "updates": updates[:3], "memory_update": {"status": "present" if persisted else "not_found", "summary": _trim(new_belief, 160), "artifact_paths": [event["artifact_path"] for event in later_memory[:3]]}}


def _canonical_belief_summary(events: list[dict[str, Any]], reflection: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    summary = str(body.get("belief_summary", "")).strip() or _legacy_belief_summary(body.get("belief_updates", []))
    if not summary:
        return {"status": "absent", "present": False, "produced": False, "maintained": False, "revised": False, "persisted": False, "reused": False, "summary": ""}
    source = reflection
    memory = _later(events, source, event_types={"belief_summary_update"}, agent_id=source["agent_id"])
    persisted = any(event["artifact_path"].startswith("agents/") and _text_overlap(summary, json.dumps(event["payload"])) for event in memory)
    prompts = _later(events, source, event_types={"prompt_context"}, agent_id=source["agent_id"])
    reused = persisted and any(_text_overlap(summary, str(event["payload"].get("prompt", ""))) for event in prompts)
    lifecycle = str(body.get("belief_summary_status", "")).strip() or "produced"
    lower = summary.lower()
    return {"status": lifecycle, "present": True, "summary": _trim(summary, 300), "word_count": len(summary.split()), "produced": True, "maintained": lifecycle == "maintained", "revised": lifecycle == "revised", "persisted": persisted, "reused": reused, "reuse_status": "reused" if reused else "persisted_not_reused" if persisted and prompts else "artifact_linkage_insufficient", "history_depth": sum(1 for event in memory if event["artifact_path"].startswith("agents/")), "evidence_references": {"messages": _evidence_mentions_message_claim(lower), "orders_or_resolutions": _evidence_mentions_order_or_resolution(lower), "board_state": any(token in lower for token in ("board", "center", "unit", "province", "position"))}, "explicit_uncertainty": any(token in lower for token in ("uncertain", "unknown", "unclear", "may ", "might ", "could ")), "say_do_comparison": any(token in lower for token in ("said", "claimed", "promise", "but", "contradict"))}


def _canonical_relationship_use(body: dict[str, Any]) -> dict[str, Any]:
    rows = body.get("relationship_updates", [])
    if not isinstance(rows, list) or not rows:
        return {"status": "not_found", "summary": "No relationship update present."}
    row = rows[0] if isinstance(rows[0], dict) else {}
    return {"status": "present", "other_agent_id": str(row.get("agent_id", "")), "summary": _trim(str(row.get("next_use", "")), 160), "source": "relationship_updates"}


def _canonical_belief_metrics(events: list[dict[str, Any]], reflections: list[dict[str, Any]], chains: list[dict[str, Any]]) -> dict[str, Any]:
    summaries = [row["belief_summary"] for row in chains if row["belief_summary"]["present"]]
    per_agent: dict[str, dict[str, Any]] = {}
    for row in chains:
        bucket = per_agent.setdefault(row["agent_id"], {"reflection_count": 0, "summaries_present": 0, "summaries_persisted": 0, "summaries_reused": 0})
        bucket["reflection_count"] += 1
        belief = row["belief_summary"]
        bucket["summaries_present"] += int(bool(belief["present"]))
        bucket["summaries_persisted"] += int(bool(belief.get("persisted")))
        bucket["summaries_reused"] += int(bool(belief.get("reused")))
    count = len(summaries)
    return {"reflection_count": len(reflections), "summaries_present": count, "presence_rate": round(count / len(reflections), 4) if reflections else 0.0, "average_summary_length": round(sum(row["word_count"] for row in summaries) / count, 2) if count else 0.0, "summaries_produced": sum(bool(row.get("produced")) for row in summaries), "summaries_maintained": sum(bool(row.get("maintained")) for row in summaries), "summaries_revised": sum(bool(row.get("revised")) for row in summaries), "summaries_persisted": sum(bool(row.get("persisted")) for row in summaries), "summaries_reused": sum(bool(row.get("reused")) for row in summaries), "history_depth": sum(int(row.get("history_depth", 0)) for row in summaries), "average_history_depth": round(sum(int(row.get("history_depth", 0)) for row in summaries) / count, 2) if count else 0.0, "explicit_uncertainty_count": sum(bool(row.get("explicit_uncertainty")) for row in summaries), "message_evidence_references": sum(bool(row.get("evidence_references", {}).get("messages")) for row in summaries), "action_order_evidence_references": sum(bool(row.get("evidence_references", {}).get("orders_or_resolutions")) for row in summaries), "board_state_evidence_references": sum(bool(row.get("evidence_references", {}).get("board_state")) for row in summaries), "say_do_comparison_count": sum(bool(row.get("say_do_comparison")) for row in summaries), "replacement_rate": round(sum(bool(row.get("revised")) for row in summaries) / count, 4) if count else 0.0, "reuse_rate": round(sum(bool(row.get("reused")) for row in summaries) / count, 4) if count else 0.0, "per_agent": per_agent}


def _canonical_agent_summary(agent: str, country: str, chains: list[dict[str, Any]], belief: dict[str, Any]) -> dict[str, Any]:
    rows = [row for row in chains if row["agent_id"] == agent]
    return {"country": country, "chains_detected": len(rows), "complete_chains": sum(bool(row["complete_chain"]) for row in rows), "objectives_with_concrete_messages": sum(row["diplomatic_action"].get("status") == "present" for row in rows), "objectives_with_aligned_orders": sum(row["order_alignment"].get("status") == "aligned" for row in rows), "objectives_with_board_consequence": sum(row["stages"]["board_consequence_present"] for row in rows), "memory_updates_after_consequence": sum(row["stages"]["board_consequence_present"] and row["stages"]["belief_or_memory_update_present"] for row in rows), "belief_summaries_detected": belief.get("summaries_present", 0), "belief_summaries_persisted": belief.get("summaries_persisted", 0), "belief_summaries_reused": belief.get("summaries_reused", 0), "stagnation_loops_detected": 0, "strategic_conversion_score": round(sum(sum(bool(value) for value in row["stages"].values()) for row in rows) / max(1, 7 * len(rows)), 4)}


def _chain_status(stages: dict[str, Any]) -> str:
    if not stages.get("objective_present"):
        return "not_observed"
    if any(value == "artifact_linkage_insufficient" for value in stages.values()):
        return "artifact_linkage_insufficient"
    return "partial"


def _canonical_note(chain: dict[str, Any]) -> str:
    missing = [key for key, value in chain["stages"].items() if not value]
    return "Confirmed chronological chain." if not missing else "Partial chain; missing or unlinked stages: " + ", ".join(missing) + "."


def _text_overlap(first: str, second: str) -> bool:
    left = {token for token in re.findall(r"[a-z]{4,}", first.lower()) if token not in {"that", "this", "with", "from", "will", "into"}}
    right = set(re.findall(r"[a-z]{4,}", second.lower()))
    return len(left & right) >= min(3, max(1, len(left))) if left else False


def strategic_consistency_markdown(payload: dict[str, Any]) -> str:
    if payload.get("report_type") == "belief_and_evidence_observability":
        summary = payload.get("summary", {})
        beliefs = payload.get("belief_summary_metrics", {})
        lines = [
            f"# Belief and Evidence Observability - {payload.get('game_id', '')}",
            "",
            "This report records artifact availability and belief/memory observability. It does not score strategic chains.",
            "",
            "## Summary",
            "",
        ]
        for key in ("negotiation_messages_observed", "order_submissions_observed", "valid_orders", "rejected_orders", "board_resolutions_observed", "board_state_changes_observed", "memory_updates_observed"):
            lines.append(f"- {key}: {summary.get(key, 0) if isinstance(summary, dict) else 0}")
        lines.extend(["", "## Belief Summaries", ""])
        for key in ("reflection_count", "summaries_present", "summaries_persisted", "summaries_reused", "presence_rate", "average_summary_length", "message_evidence_references", "action_order_evidence_references", "board_state_evidence_references"):
            lines.append(f"- {key}: {beliefs.get(key, 0) if isinstance(beliefs, dict) else 0}")
        lines.extend(["", "## Narrative Review", "", "- Raw phase artifacts remain the source for reconstructing communication, interpretation, orders, consequences, and adaptation."])
        return "\n".join(lines).strip() + "\n"
    lines = [
        f"# Strategic Consistency Report - {payload.get('game_id', '')}",
        "",
        "Heuristic, derived analysis. Not authoritative game state.",
        "",
        "## Executive Summary",
        "",
    ]
    summary = payload.get("summary", {})
    if isinstance(summary, dict):
        lines.append(
            "This report tracks whether stated strategic objectives were converted into concrete diplomacy, "
            "orders, board consequences, and memory updates."
        )
        lines.append("")
        lines.append("## Aggregate Metrics")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("| --- | --- |")
        for key in (
            "chains_detected",
            "complete_chains",
            "partial_chains",
            "high_confidence_chains",
            "low_confidence_chains",
        ):
            lines.append(f"| {key} | {summary.get(key, 0)} |")
    belief_metrics = payload.get("belief_summary_metrics", {})
    if isinstance(belief_metrics, dict):
        lines.extend(["", "## Belief Summary Metrics", ""])
        lines.append(f"- presence_rate: {belief_metrics.get('presence_rate', 0.0)}")
        lines.append(f"- average_summary_length: {belief_metrics.get('average_summary_length', 0.0)}")
        lines.append(f"- replacement_rate: {belief_metrics.get('replacement_rate', 0.0)}")
        lines.append(f"- reuse_rate: {belief_metrics.get('reuse_rate', 0.0)}")
        lines.append(f"- average_history_depth: {belief_metrics.get('average_history_depth', 0.0)}")
    lines.extend(["", "## Agent Summary", ""])
    lines.append(
        "| Agent | Chains | Complete | Concrete Diplomacy | Aligned Orders | "
        "Board Consequences | Stagnation Loops | Conversion Score |"
    )
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    agent_summaries = payload.get("agent_summaries", {})
    if isinstance(agent_summaries, dict):
        for agent_id, row in sorted(agent_summaries.items()):
            if not isinstance(row, dict):
                continue
            lines.append(
                f"| {agent_id} | {row.get('chains_detected', 0)} | {row.get('complete_chains', 0)} | "
                f"{row.get('objectives_with_concrete_messages', 0)} | "
                f"{row.get('objectives_with_aligned_orders', 0)} | "
                f"{row.get('objectives_with_board_consequence', 0)} | "
                f"{row.get('stagnation_loops_detected', 0)} | "
                f"{row.get('strategic_conversion_score', 0.0):.2f} |"
            )
    strong = [
        row
        for row in payload.get("chains", [])
        if isinstance(row, dict) and row.get("confidence") in {"high", "medium"}
    ]
    lines.extend(["", "## Strongest Strategic Chains", ""])
    if strong:
        for row in strong[:8]:
            lines.extend(_chain_markdown_block(row))
    else:
        lines.append("- No strong chain detected in available artifacts.")
    lines.extend(["", "## Stagnation Examples", ""])
    stagnation_rows = payload.get("stagnation_loop_candidates", [])
    if isinstance(stagnation_rows, list) and stagnation_rows:
        for row in stagnation_rows[:10]:
            lines.append(
                f"- pair={row.get('pair')} phase_range={row.get('phase_range')} "
                f"messages={row.get('message_count')} confirmation_markers={row.get('repeated_confirmation_markers')} "
                f"support_requests={row.get('concrete_support_requests')} "
                f"specific_order_mentions={row.get('specific_order_mentions')} "
                f"linked_board_consequences={row.get('linked_board_consequences')} "
                f"classification={row.get('classification')}"
            )
    else:
        lines.append("- No stagnation-loop candidate met threshold.")
    lines.extend(["", "## Broken or Contradictory Chains", ""])
    broken_rows = payload.get("broken_commitment_candidates", [])
    if isinstance(broken_rows, list) and broken_rows:
        for row in broken_rows[:10]:
            lines.append(
                f"- {row.get('classification')}: {row.get('summary')} "
                f"(confidence={row.get('confidence')}, evidence={row.get('evidence', [])})"
            )
    else:
        lines.append("- No broken-commitment candidate met conservative evidence threshold.")
    lines.extend(
        [
            "",
            "## Method Notes",
            "",
            "- Metrics are heuristic and derived from existing artifacts.",
            "- Classifications are confidence-labeled and conservative.",
            "- High message volume alone is not treated as strategic conversion.",
            "",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def summarize_experiment_strategic_consistency(experiment_root: str | Path) -> dict[str, Any]:
    return _summarize_experiment_belief_observability(experiment_root)


def _summarize_experiment_belief_observability(experiment_root: str | Path) -> dict[str, Any]:
    root = Path(experiment_root)
    payloads: list[dict[str, Any]] = []
    for game_dir in sorted((root / "games").glob("*/")) if (root / "games").exists() else []:
        path = game_dir / "observatory" / "belief_evidence_observability.json"
        if not path.exists():
            path = game_dir / "observatory" / "strategic_consistency.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    totals = {"belief_summaries_detected": 0, "belief_summaries_persisted": 0, "belief_summaries_reused": 0, "negotiation_messages_observed": 0, "order_submissions_observed": 0, "valid_orders": 0, "rejected_orders": 0, "board_resolutions_observed": 0, "memory_updates_observed": 0}
    per_agent: dict[str, dict[str, int]] = {}
    for payload in payloads:
        summary = payload.get("summary", {})
        if isinstance(summary, dict):
            for key in totals:
                totals[key] += int(summary.get(key, 0))
        for agent_id, row in (payload.get("per_agent", {}) or {}).items():
            if not isinstance(row, dict):
                continue
            bucket = per_agent.setdefault(str(agent_id), {"reflections_observed": 0, "belief_summaries_detected": 0, "belief_summaries_persisted": 0, "belief_summaries_reused": 0})
            for key in bucket:
                bucket[key] += int(row.get(key, 0))
    return {"schema_version": "1.0", "report_type": "belief_and_evidence_observability", "experiment_id": root.name, "games_analyzed": len(payloads), "summary": totals, "per_agent": per_agent, "method_notes": {"summary": "Aggregate artifact observability only; no strategic-chain scores or rankings are produced."}}


def _legacy_summarize_experiment_strategic_consistency(experiment_root: str | Path) -> dict[str, Any]:
    root = Path(experiment_root)
    game_payloads: list[dict[str, Any]] = []
    games_root = root / "games"
    if games_root.exists():
        for game_dir in sorted([p for p in games_root.iterdir() if p.is_dir()], key=lambda p: p.name):
            payload_path = game_dir / "observatory" / "strategic_consistency.json"
            if not payload_path.exists():
                continue
            try:
                payload = json.loads(payload_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                game_payloads.append(payload)

    total_chains = 0
    total_complete = 0
    by_agent: dict[str, dict[str, float]] = {}
    failure_modes: dict[str, int] = {}
    stagnation_rows: list[dict[str, Any]] = []
    broken_rows: list[dict[str, Any]] = []
    for payload in game_payloads:
        summary = payload.get("summary", {})
        if isinstance(summary, dict):
            total_chains += int(summary.get("chains_detected", 0))
            total_complete += int(summary.get("complete_chains", 0))
        agent_summary = payload.get("agent_summaries", {})
        if isinstance(agent_summary, dict):
            for agent_id, row in agent_summary.items():
                if not isinstance(row, dict):
                    continue
                bucket = by_agent.setdefault(
                    str(agent_id),
                    {
                        "games": 0.0,
                        "score_total": 0.0,
                        "chains": 0.0,
                        "complete": 0.0,
                    },
                )
                bucket["games"] += 1
                bucket["score_total"] += float(row.get("strategic_conversion_score", 0.0))
                bucket["chains"] += float(row.get("chains_detected", 0))
                bucket["complete"] += float(row.get("complete_chains", 0))
        for row in payload.get("chains", []):
            if not isinstance(row, dict):
                continue
            if row.get("diplomatic_action", {}).get("status") != "present":
                failure_modes["objective_without_concrete_message"] = (
                    failure_modes.get("objective_without_concrete_message", 0) + 1
                )
            if row.get("order_alignment", {}).get("status") not in {"aligned", "partially_aligned"}:
                failure_modes["concrete_message_without_aligned_order"] = (
                    failure_modes.get("concrete_message_without_aligned_order", 0) + 1
                )
            if row.get("board_consequence", {}).get("category") in {"no_material_change", "unclear"}:
                failure_modes["aligned_order_without_board_consequence"] = (
                    failure_modes.get("aligned_order_without_board_consequence", 0) + 1
                )
            if row.get("memory_update", {}).get("status") != "present":
                failure_modes["board_consequence_without_memory_update"] = (
                    failure_modes.get("board_consequence_without_memory_update", 0) + 1
                )
            belief_summary = row.get("belief_summary", {})
            if not isinstance(belief_summary, dict) or not belief_summary.get("present"):
                failure_modes["belief_summary_absent"] = failure_modes.get("belief_summary_absent", 0) + 1
            elif not belief_summary.get("persisted"):
                failure_modes["belief_summary_not_persisted"] = (
                    failure_modes.get("belief_summary_not_persisted", 0) + 1
                )
            elif belief_summary.get("reuse_status") == "persisted_not_reused":
                failure_modes["belief_summary_persisted_not_reused"] = (
                    failure_modes.get("belief_summary_persisted_not_reused", 0) + 1
                )
            elif belief_summary.get("reuse_status") == "stale_or_artifact_linkage_insufficient":
                failure_modes["belief_summary_artifact_linkage_insufficient"] = (
                    failure_modes.get("belief_summary_artifact_linkage_insufficient", 0) + 1
                )
        for row in payload.get("stagnation_loop_candidates", []):
            if isinstance(row, dict):
                stagnation_rows.append({"game_id": payload.get("game_id"), **row})
        for row in payload.get("broken_commitment_candidates", []):
            if isinstance(row, dict):
                broken_rows.append({"game_id": payload.get("game_id"), **row})

    rankings = []
    for agent_id, row in sorted(by_agent.items()):
        games = max(1.0, row["games"])
        rankings.append(
            {
                "agent_id": agent_id,
                "avg_conversion_score": round(row["score_total"] / games, 4),
                "avg_chains": round(row["chains"] / games, 3),
                "avg_complete_chains": round(row["complete"] / games, 3),
            }
        )
    rankings.sort(key=lambda row: (-float(row["avg_conversion_score"]), str(row["agent_id"])))

    return {
        "schema_version": "1.0",
        "heuristic": True,
        "experiment_id": root.name,
        "games_analyzed": len(game_payloads),
        "total_chains_detected": total_chains,
        "complete_chains": total_complete,
        "agent_rankings_by_conversion_score": rankings,
        "country_rankings_by_conversion_score": [],
        "most_common_failure_modes": [
            key for key, _ in sorted(failure_modes.items(), key=lambda item: (-item[1], item[0]))
        ],
        "notable_chains": _top_notable_chains(game_payloads),
        "notable_stagnation_loops": stagnation_rows[:12],
        "notable_broken_commitment_candidates": broken_rows[:12],
    }


def strategic_consistency_experiment_markdown(payload: dict[str, Any]) -> str:
    if payload.get("report_type") == "belief_and_evidence_observability":
        summary = payload.get("summary", {})
        lines = [
            f"# Belief and Evidence Observability Summary - {payload.get('experiment_id', '')}",
            "",
            "Aggregate artifact observability only; no strategic-chain scores or rankings are produced.",
            "",
            f"- games_analyzed: {payload.get('games_analyzed', 0)}",
        ]
        for key in ("belief_summaries_detected", "belief_summaries_persisted", "belief_summaries_reused", "negotiation_messages_observed", "order_submissions_observed", "valid_orders", "rejected_orders", "board_resolutions_observed", "memory_updates_observed"):
            lines.append(f"- {key}: {summary.get(key, 0) if isinstance(summary, dict) else 0}")
        return "\n".join(lines).strip() + "\n"
    lines = [
        f"# Strategic Consistency Summary - {payload.get('experiment_id', '')}",
        "",
        "Heuristic aggregate across game-level strategic consistency artifacts.",
        "",
        f"- games_analyzed: {payload.get('games_analyzed', 0)}",
        f"- total_chains_detected: {payload.get('total_chains_detected', 0)}",
        f"- complete_chains: {payload.get('complete_chains', 0)}",
        "",
        "## Agent Rankings by Conversion Score",
    ]
    rankings = payload.get("agent_rankings_by_conversion_score", [])
    if isinstance(rankings, list) and rankings:
        for row in rankings:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"- {row.get('agent_id')}: score={row.get('avg_conversion_score')}, "
                f"avg_chains={row.get('avg_chains')}, avg_complete={row.get('avg_complete_chains')}"
            )
    else:
        lines.append("- no agent rankings available")
    lines.extend(["", "## Most Common Failure Modes"])
    modes = payload.get("most_common_failure_modes", [])
    if isinstance(modes, list) and modes:
        for row in modes:
            lines.append(f"- {row}")
    else:
        lines.append("- none")
    lines.extend(["", "## Notable Stagnation Loops"])
    for row in payload.get("notable_stagnation_loops", [])[:10]:
        if not isinstance(row, dict):
            continue
        lines.append(
            f"- game={row.get('game_id')} pair={row.get('pair')} messages={row.get('message_count')} "
            f"classification={row.get('classification')}"
        )
    lines.extend(["", "## Notable Broken Commitment Candidates"])
    for row in payload.get("notable_broken_commitment_candidates", [])[:10]:
        if not isinstance(row, dict):
            continue
        lines.append(
            f"- game={row.get('game_id')} classification={row.get('classification')} "
            f"summary={row.get('summary')}"
        )
    lines.extend(
        [
            "",
            "## Method Notes",
            "- Metrics are heuristic and intended for experiment analysis only.",
            "- Scores do not affect gameplay and are not exposed to agents.",
            "",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _build_phase_context(
    *,
    data: GameObservatoryData,
    agent_id: str,
    power: str,
    phase_id: str,
    delta: dict[str, Any],
) -> _AgentPhaseContext | None:
    objective_summary, objective_source, objective_excerpt = _phase_objective(
        data=data, agent_id=agent_id, phase_id=phase_id
    )
    relationship_use = _relationship_use(data=data, agent_id=agent_id, phase_id=phase_id)
    diplomatic_action = _diplomatic_action(data=data, agent_id=agent_id, phase_id=phase_id)
    order_alignment = _order_alignment(
        data=data,
        power=power,
        phase_id=phase_id,
        objective_summary=objective_summary,
        diplomatic_action=diplomatic_action,
    )
    board_consequence = _board_consequence(power=power, phase_id=phase_id, delta=delta)
    memory_update = _memory_update(data=data, agent_id=agent_id, phase_id=phase_id)
    belief_revision = _belief_revision(
        data=data,
        agent_id=agent_id,
        phase_id=phase_id,
        board_consequence=board_consequence,
    )
    belief_summary = _belief_summary(
        data=data,
        agent_id=agent_id,
        phase_id=phase_id,
    )

    has_signal = any(
        [
            objective_summary,
            diplomatic_action.get("status") == "present",
            order_alignment.get("status") not in {"no_relevant_order", "unclear"},
            board_consequence.get("category") not in {"no_material_change", "unclear"},
            memory_update.get("status") == "present",
            belief_summary.get("present") is True,
        ]
    )
    if not has_signal:
        return None
    confidence, basis = _confidence(
        objective_summary=objective_summary,
        diplomatic_action=diplomatic_action,
        order_alignment=order_alignment,
        board_consequence=board_consequence,
        memory_update=memory_update,
        belief_revision=belief_revision,
    )
    return _AgentPhaseContext(
        phase_id=phase_id,
        agent_id=agent_id,
        power=power,
        objective_summary=objective_summary or "Objective not found.",
        objective_source=objective_source,
        objective_excerpt=objective_excerpt,
        relationship_use=relationship_use,
        diplomatic_action=diplomatic_action,
        order_alignment=order_alignment,
        board_consequence=board_consequence,
        memory_update=memory_update,
        belief_revision=belief_revision,
        belief_summary=belief_summary,
        confidence=confidence,
        basis=basis,
    )


def _messages_by_agent(data: GameObservatoryData) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in data.global_messages + data.private_messages:
        agent = str(row.get("from_agent_id", ""))
        if not agent:
            continue
        out.setdefault(agent, []).append(row)
    return out


def _phase_objective(
    *,
    data: GameObservatoryData,
    agent_id: str,
    phase_id: str,
) -> tuple[str, str, str]:
    reflection = data.phase_reflections.get(f"{phase_id}:{agent_id}", {})
    if isinstance(reflection, dict):
        result = reflection.get("result", {})
        if isinstance(result, dict):
            body = result.get("reflection", {})
            if isinstance(body, dict):
                for key in ("next_objective", "strategic_position", "memory_update"):
                    value = str(body.get(key, "")).strip()
                    if value:
                        return value, "reflection", _trim(value, 160)
                strategic_intent = body.get("strategic_intent", {})
                if isinstance(strategic_intent, dict):
                    short_term = str(strategic_intent.get("short_term", "")).strip()
                    if short_term:
                        return short_term, "reflection", _trim(short_term, 160)
    phase_output = data.runtime_outputs_by_agent.get(agent_id, {}).get(
        f"{phase_id}_submit_orders", {}
    )
    if isinstance(phase_output, dict):
        llm_meta = phase_output.get("llm_meta", {})
        if isinstance(llm_meta, dict):
            parsed = llm_meta.get("parsed_payload", {})
            if isinstance(parsed, dict):
                rationale = parsed.get("order_rationale", {})
                if isinstance(rationale, dict):
                    goal = str(rationale.get("goal_supported", "")).strip()
                    if goal:
                        return goal, "order_rationale", _trim(goal, 160)
    return "", "not_found", ""


def _relationship_use(
    *,
    data: GameObservatoryData,
    agent_id: str,
    phase_id: str,
) -> dict[str, Any]:
    phase_output = data.runtime_outputs_by_agent.get(agent_id, {}).get(
        f"{phase_id}_negotiate_tick", {}
    )
    if not isinstance(phase_output, dict):
        return {"status": "not_found", "summary": "No relationship analysis artifact found."}
    llm_meta = phase_output.get("llm_meta", {})
    if not isinstance(llm_meta, dict):
        return {"status": "not_found", "summary": "No relationship analysis artifact found."}
    parsed = llm_meta.get("parsed_payload", {})
    if not isinstance(parsed, dict):
        return {"status": "not_found", "summary": "No relationship analysis artifact found."}
    analysis = parsed.get("relationship_analysis", [])
    if not isinstance(analysis, list) or not analysis:
        return {"status": "not_found", "summary": "No relationship analysis entries present."}
    row = analysis[0] if isinstance(analysis[0], dict) else {}
    other = str(row.get("agent_id", "")).strip()
    summary = str(
        row.get("next_strategic_use_of_relationship")
        or row.get("how_this_serves_my_objectives")
        or row.get("next_conversation_move")
        or ""
    ).strip()
    evidence = str(row.get("specific_evidence", "")).strip()
    return {
        "status": "present",
        "other_agent_id": other,
        "summary": summary or "Relationship analysis present with limited detail.",
        "source": "relationship_analysis",
        "evidence_excerpt": _trim(evidence, 160),
    }


def _diplomatic_action(
    *,
    data: GameObservatoryData,
    agent_id: str,
    phase_id: str,
) -> dict[str, Any]:
    rows = [
        row
        for row in (data.private_messages + data.global_messages)
        if str(row.get("phase_id", "")) == phase_id and str(row.get("from_agent_id", "")) == agent_id
    ]
    if not rows:
        return {"status": "not_found", "category": "none", "summary": "No diplomatic action found in phase."}
    best = _classify_message(rows[0])
    return {
        "status": "present",
        "category": best["category"],
        "recipient": best["recipient"],
        "summary": best["summary"],
        "source": best["source"],
        "evidence_excerpt": best["evidence_excerpt"],
    }


def _classify_message(row: dict[str, Any]) -> dict[str, str]:
    body = str(row.get("body", "")).strip()
    lower = body.lower()
    target = row.get("to", {})
    to_agent = ""
    msg_type = "global"
    if isinstance(target, dict):
        msg_type = str(target.get("type", "global"))
        to_agent = str(target.get("to_agent_id", ""))
    category = "reassurance_only"
    if any(token in lower for token in _SUPPORT_REQUEST_TOKENS):
        category = "concrete_support_request"
    elif any(token in lower for token in _SUPPORT_OFFER_TOKENS):
        category = "concrete_support_offer"
    elif any(token in lower for token in _DMZ_TOKENS):
        category = "dmz_or_border_proposal"
    elif any(token in lower for token in _THREAT_TOKENS):
        category = "threat_or_deterrent"
    elif any(token in lower for token in _WARNING_TOKENS):
        category = "warning_about_third_party"
    elif any(token in lower for token in _CLAIM_TEST_TOKENS):
        category = "claim_test"
    elif _PROVINCE_TOKEN.search(lower):
        category = "specific_order_proposal"
    recipient = to_agent if msg_type == "private" and to_agent else "global"
    return {
        "category": category,
        "recipient": recipient,
        "summary": _trim(body, 140),
        "source": "private_message" if recipient != "global" else "global_message",
        "evidence_excerpt": _trim(body, 120),
    }


def _order_alignment(
    *,
    data: GameObservatoryData,
    power: str,
    phase_id: str,
    objective_summary: str,
    diplomatic_action: dict[str, Any],
) -> dict[str, Any]:
    submitted = [
        row
        for row in data.phase_submitted_orders.get(phase_id, [])
        if isinstance(row, dict) and str(row.get("power", "")) == power
    ]
    if not submitted:
        return {
            "status": "no_relevant_order",
            "orders": [],
            "summary": "No submitted order found for objective alignment.",
        }
    objective_tokens = {
        token.lower()
        for token in _PROVINCE_TOKEN.findall(objective_summary.lower())
        if len(token) == 3
    }
    action_tokens = {
        token.lower()
        for token in _PROVINCE_TOKEN.findall(str(diplomatic_action.get("summary", "")).lower())
        if len(token) == 3
    }
    targets = objective_tokens | action_tokens
    order_texts = [_order_text(row) for row in submitted]
    order_blob = " ".join(order_texts).lower()
    if not targets:
        return {
            "status": "partially_aligned",
            "orders": order_texts,
            "summary": "Objective had no explicit province token; order alignment inferred as partial.",
        }
    matched = [token for token in sorted(targets) if token in order_blob]
    if matched:
        status = "aligned" if len(matched) >= min(2, len(targets)) else "partially_aligned"
        return {
            "status": status,
            "orders": order_texts,
            "summary": f"Orders include objective-linked tokens: {matched}.",
        }
    return {
        "status": "contradictory",
        "orders": order_texts,
        "summary": "Orders do not appear to include objective-linked province/action tokens.",
    }


def _board_consequence(
    *,
    power: str,
    phase_id: str,
    delta: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(delta, dict):
        return {"category": "unclear", "summary": "No board delta available.", "center_delta": 0}
    center_changes = delta.get("center_changes", [])
    if isinstance(center_changes, list):
        gained = [row for row in center_changes if isinstance(row, dict) and row.get("to_owner") == power and row.get("from_owner") != power]
        lost = [row for row in center_changes if isinstance(row, dict) and row.get("from_owner") == power and row.get("to_owner") != power]
        if gained:
            return {
                "category": "center_gained",
                "summary": f"Center ownership gained: {[row.get('province') for row in gained]}.",
                "center_delta": len(gained),
            }
        if lost:
            return {
                "category": "center_lost",
                "summary": f"Center ownership lost: {[row.get('province') for row in lost]}.",
                "center_delta": -len(lost),
            }
    moves = delta.get("moves", [])
    moved = [
        row
        for row in (moves if isinstance(moves, list) else [])
        if isinstance(row, dict) and str(row.get("power", "")) == power and str(row.get("result", "")) == "moved"
    ]
    if moved:
        return {
            "category": "position_improved",
            "summary": f"Movement occurred without ownership change ({len(moved)} move(s)).",
            "center_delta": 0,
        }
    powers = delta.get("powers", {})
    if isinstance(powers, dict):
        row = powers.get(power, {})
        if isinstance(row, dict):
            if int(row.get("dislodgements", 0)) > 0:
                return {
                    "category": "unit_dislodged",
                    "summary": "At least one unit was dislodged.",
                    "center_delta": 0,
                }
    return {"category": "no_material_change", "summary": f"No material board change for {phase_id}.", "center_delta": 0}


def _memory_update(
    *,
    data: GameObservatoryData,
    agent_id: str,
    phase_id: str,
) -> dict[str, Any]:
    reflection = data.phase_reflections.get(f"{phase_id}:{agent_id}", {})
    if isinstance(reflection, dict):
        result = reflection.get("result", {})
        if isinstance(result, dict):
            body = result.get("reflection", {})
            if isinstance(body, dict):
                update = str(body.get("memory_update", "")).strip()
                if update:
                    return {
                        "status": "present",
                        "summary": _trim(update, 160),
                        "source": "reflection",
                    }
    return {"status": "not_found", "summary": "No memory/reflection update found."}


def _belief_revision(
    *,
    data: GameObservatoryData,
    agent_id: str,
    phase_id: str,
    board_consequence: dict[str, Any],
) -> dict[str, Any]:
    body = _reflection_body(data=data, agent_id=agent_id, phase_id=phase_id)
    if body is None:
        return {"status": "artifact_linkage_insufficient", "summary": "Reflection artifact unavailable."}
    raw_updates = body.get("belief_updates")
    if raw_updates is None or raw_updates == []:
        return {"status": "not_produced", "summary": "No structured belief update was produced."}
    if not isinstance(raw_updates, list):
        return {"status": "artifact_linkage_insufficient", "summary": "Belief update artifact has an invalid shape."}
    updates = [row for row in raw_updates if _is_structured_belief_update(row)]
    if not updates:
        return {"status": "artifact_linkage_insufficient", "summary": "No complete structured belief update was available."}

    evidence_blob = " ".join(str(row.get("evidence", "")) for row in updates).lower()
    phase_messages = [
        row
        for row in data.private_messages + data.global_messages
        if str(row.get("phase_id", "")) == phase_id
    ]
    order_rows = data.phase_submitted_orders.get(phase_id, [])
    resolution = data.phase_resolutions.get(phase_id, {})
    message_linked = _evidence_mentions_message_claim(evidence_blob) or _evidence_overlaps_rows(
        evidence_blob, phase_messages, fields=("body",)
    )
    order_linked = _evidence_mentions_order_or_resolution(evidence_blob) or _evidence_overlaps_rows(
        evidence_blob,
        [*order_rows, resolution],
        fields=("payload", "movement_outcomes", "output_state"),
    )
    public_consequence = board_consequence.get("category") not in {"no_material_change", "unclear"}
    persisted = _belief_updates_persisted(
        data=data,
        agent_id=agent_id,
        phase_id=phase_id,
        updates=updates,
    )
    if not persisted:
        status = "produced_not_persisted"
    else:
        reuse = _belief_update_reuse(data=data, agent_id=agent_id, phase_id=phase_id, updates=updates)
        status = reuse
    return {
        "status": status,
        "update_count": len(updates),
        "linked_to_message_claim": message_linked,
        "linked_to_order_or_resolution": order_linked,
        "after_public_consequence": public_consequence,
        "summary": _trim(str(updates[0].get("new_belief", "")), 160),
        "updates": [
            {
                "subject": _trim(str(row.get("subject", "")), 80),
                "new_belief": _trim(str(row.get("new_belief", "")), 160),
                "evidence": _trim(str(row.get("evidence", "")), 160),
                "next_test_or_action": _trim(str(row.get("next_test_or_action", "")), 160),
            }
            for row in updates[:3]
        ],
    }


def _belief_summary(
    *,
    data: GameObservatoryData,
    agent_id: str,
    phase_id: str,
) -> dict[str, Any]:
    body = _reflection_body(data=data, agent_id=agent_id, phase_id=phase_id)
    if body is None:
        return {
            "status": "stale_or_artifact_linkage_insufficient",
            "present": False,
            "summary": "Reflection artifact unavailable.",
        }
    raw_summary = body.get("belief_summary")
    summary = raw_summary.strip() if isinstance(raw_summary, str) else ""
    legacy = False
    if not summary:
        legacy = True
        summary = _legacy_belief_summary(body.get("belief_updates", []))
    fallback_status = str(body.get("belief_summary_status", ""))
    if not summary:
        return {
            "status": "fallback_absent" if fallback_status.startswith("fallback") else "absent",
            "present": False,
            "summary": "No current belief summary was available.",
            "produced": False,
            "maintained": False,
            "revised": False,
            "persisted": False,
            "reused": False,
            "legacy_normalized": legacy,
        }
    lifecycle = fallback_status or ("legacy_normalized" if legacy else "produced")
    persisted = _belief_summary_persisted(
        data=data,
        agent_id=agent_id,
        phase_id=phase_id,
        summary=summary,
    )
    reuse_status = _belief_summary_reuse(
        data=data,
        agent_id=agent_id,
        phase_id=phase_id,
        summary=summary,
    ) if persisted else "stale_or_artifact_linkage_insufficient"
    lower = summary.lower()
    return {
        "status": lifecycle,
        "present": True,
        "summary": _trim(summary, 300),
        "word_count": len(summary.split()),
        "produced": lifecycle in {"produced", "revised", "maintained", "legacy_normalized"},
        "maintained": lifecycle == "maintained",
        "revised": lifecycle == "revised",
        "persisted": persisted,
        "reused": reuse_status == "reused",
        "reuse_status": reuse_status,
        "history_depth": _belief_summary_history_depth(data=data, agent_id=agent_id),
        "legacy_normalized": legacy,
        "evidence_references": {
            "messages": _evidence_mentions_message_claim(lower),
            "orders_or_resolutions": _evidence_mentions_order_or_resolution(lower),
            "board_state": any(token in lower for token in ("board", "center", "unit", "province", "position")),
        },
        "explicit_uncertainty": any(token in lower for token in ("uncertain", "unknown", "unclear", "may ", "might ", "could ")),
        "say_do_comparison": any(token in lower for token in ("contradict", "violat", "despite", "but ", "words and actions")),
    }


def _legacy_belief_summary(rows: Any) -> str:
    if not isinstance(rows, list):
        return ""
    parts: list[str] = []
    for row in rows[:3]:
        if not isinstance(row, dict):
            continue
        subject = str(row.get("subject", "")).strip()
        belief = str(row.get("new_belief", row.get("belief", ""))).strip()
        if subject and belief:
            parts.append(f"{subject}: {belief}")
    return " ".join(parts)


def _belief_summary_persisted(
    *,
    data: GameObservatoryData,
    agent_id: str,
    phase_id: str,
    summary: str,
) -> bool:
    path = data.game_dir / "agents" / agent_id / "folio" / "game_memory" / f"{data.game_dir.name}.json"
    if not path.exists():
        return False
    try:
        memory = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    if not isinstance(memory, dict):
        return False
    current = str(memory.get("belief_summary", "")).strip()
    current_phase = str(memory.get("belief_summary_phase_id", "")).strip()
    history = memory.get("belief_summary_history", [])
    historical_match = isinstance(history, list) and any(
        isinstance(row, dict)
        and str(row.get("phase_id", "")) == phase_id
        and str(row.get("summary", "")).strip() == summary
        for row in history
    )
    return current == summary and (current_phase == phase_id or historical_match)


def _belief_summary_reuse(
    *,
    data: GameObservatoryData,
    agent_id: str,
    phase_id: str,
    summary: str,
) -> str:
    try:
        next_phase = data.phases[data.phases.index(phase_id) + 1]
    except (ValueError, IndexError):
        return "stale_or_artifact_linkage_insufficient"
    outputs = data.runtime_outputs_by_agent.get(agent_id, {})
    next_rows = [
        row for key, row in outputs.items()
        if key.startswith(f"{next_phase}_") and isinstance(row, dict)
    ]
    if not next_rows:
        return "stale_or_artifact_linkage_insufficient"
    visible_text = " ".join(
        str((row.get("llm_meta") or {}).get("prompt", ""))
        for row in next_rows
    )
    if len(_meaningful_tokens(summary) & _meaningful_tokens(visible_text)) >= 2:
        return "reused"
    return "persisted_not_reused"


def _belief_summary_history_depth(*, data: GameObservatoryData, agent_id: str) -> int:
    path = data.game_dir / "agents" / agent_id / "folio" / "game_memory" / f"{data.game_dir.name}.json"
    if not path.exists():
        return 0
    try:
        memory = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0
    rows = memory.get("belief_summary_history", []) if isinstance(memory, dict) else []
    return len(rows) if isinstance(rows, list) else 0


def _belief_summary_metrics(chain_rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row.get("belief_summary", {}) for row in chain_rows if isinstance(row.get("belief_summary"), dict)]
    present = [row for row in rows if row.get("present")]
    reused = [row for row in present if row.get("reused")]
    replacements = [row for row in present if row.get("revised")]
    return {
        "presence_rate": round(len(present) / len(rows), 4) if rows else 0.0,
        "average_summary_length": round(sum(int(row.get("word_count", 0)) for row in present) / len(present), 2) if present else 0.0,
        "replacement_rate": round(len(replacements) / len(present), 4) if present else 0.0,
        "reuse_rate": round(len(reused) / len(present), 4) if present else 0.0,
        "average_history_depth": _average_belief_summary_history_depth(chain_rows),
        "evidence_references": {
            key: sum(1 for row in present if (row.get("evidence_references") or {}).get(key))
            for key in ("messages", "orders_or_resolutions", "board_state")
        },
        "explicit_uncertainty_count": sum(1 for row in present if row.get("explicit_uncertainty")),
        "say_do_comparison_count": sum(1 for row in present if row.get("say_do_comparison")),
    }


def _average_belief_summary_history_depth(chain_rows: list[dict[str, Any]]) -> float:
    depths = [
        int((row.get("belief_summary") or {}).get("history_depth", 0))
        for row in chain_rows
        if isinstance(row.get("belief_summary"), dict)
    ]
    return round(sum(depths) / len(depths), 2) if depths else 0.0


def _reflection_body(*, data: GameObservatoryData, agent_id: str, phase_id: str) -> dict[str, Any] | None:
    reflection = data.phase_reflections.get(f"{phase_id}:{agent_id}", {})
    if not isinstance(reflection, dict):
        return None
    result = reflection.get("result", {})
    if not isinstance(result, dict):
        return None
    body = result.get("reflection", {})
    return body if isinstance(body, dict) else None


def _is_structured_belief_update(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    return all(isinstance(row.get(field), str) and row[field].strip() for field in (
        "subject",
        "prior_belief",
        "new_belief",
        "evidence",
        "strategic_consequence",
        "next_test_or_action",
    ))


def _evidence_mentions_message_claim(text: str) -> bool:
    return any(token in text for token in ("message", "claim", "said", "promised", "offered"))


def _evidence_mentions_order_or_resolution(text: str) -> bool:
    return any(token in text for token in ("order", "resolution", "moved", "held", "supported", "dislodged"))


def _evidence_overlaps_rows(text: str, rows: list[Any], *, fields: tuple[str, ...]) -> bool:
    evidence_tokens = _meaningful_tokens(text)
    if not evidence_tokens:
        return False
    row_text = " ".join(
        json.dumps({field: row.get(field) for field in fields}, sort_keys=True)
        for row in rows
        if isinstance(row, dict)
    )
    return len(evidence_tokens & _meaningful_tokens(row_text)) >= 2


def _belief_updates_persisted(
    *,
    data: GameObservatoryData,
    agent_id: str,
    phase_id: str,
    updates: list[dict[str, Any]],
) -> bool:
    path = data.game_dir / "agents" / agent_id / "folio" / "game_memory" / f"{data.game_dir.name}.json"
    if not path.exists():
        return False
    try:
        memory = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    rows = memory.get("belief_state", []) if isinstance(memory, dict) else []
    if not isinstance(rows, list):
        return False
    produced_beliefs = {str(row.get("new_belief", "")).strip() for row in updates}
    persisted_beliefs = {
        str(row.get("new_belief", row.get("belief", ""))).strip()
        for row in rows
        if isinstance(row, dict) and str(row.get("phase_id", "")) == phase_id
    }
    return bool(produced_beliefs) and produced_beliefs.issubset(persisted_beliefs)


def _belief_update_reuse(
    *,
    data: GameObservatoryData,
    agent_id: str,
    phase_id: str,
    updates: list[dict[str, Any]],
) -> str:
    try:
        next_phase = data.phases[data.phases.index(phase_id) + 1]
    except (ValueError, IndexError):
        return "artifact_linkage_insufficient"
    outputs = data.runtime_outputs_by_agent.get(agent_id, {})
    next_rows = [
        row for key, row in outputs.items()
        if key.startswith(f"{next_phase}_") and isinstance(row, dict)
    ]
    if not next_rows:
        return "artifact_linkage_insufficient"
    visible_text = " ".join(
        " ".join(
            [
                str((row.get("llm_meta") or {}).get("prompt", "")),
                json.dumps((row.get("llm_meta") or {}).get("parsed_payload", {}), sort_keys=True),
            ]
        )
        for row in next_rows
    )
    belief_tokens = set().union(*(
        _meaningful_tokens(str(update.get(field, "")))
        for update in updates
        for field in ("new_belief", "strategic_consequence", "next_test_or_action")
    ))
    if len(belief_tokens & _meaningful_tokens(visible_text)) >= 2:
        return "reused"
    return "persisted_not_reused"


def _meaningful_tokens(text: str) -> set[str]:
    ignored = {"that", "this", "with", "from", "into", "will", "should", "next", "belief", "evidence", "strategic", "action"}
    return {token.lower() for token in re.findall(r"[A-Za-z]{4,}", text) if token.lower() not in ignored}


def _confidence(
    *,
    objective_summary: str,
    diplomatic_action: dict[str, Any],
    order_alignment: dict[str, Any],
    board_consequence: dict[str, Any],
    memory_update: dict[str, Any],
    belief_revision: dict[str, Any],
) -> tuple[str, str]:
    has_objective = bool(objective_summary.strip())
    has_diplomacy = diplomatic_action.get("status") == "present"
    aligned = order_alignment.get("status") in {"aligned", "partially_aligned"}
    consequence = board_consequence.get("category") not in {"no_material_change", "unclear"}
    has_memory = memory_update.get("status") == "present"
    belief_grounded = belief_revision.get("status") in {"reused", "persisted_not_reused", "produced_not_persisted"}
    if has_objective and has_diplomacy and aligned and consequence:
        return "high", "explicit"
    if (has_objective and aligned) or (has_diplomacy and consequence) or (belief_grounded and consequence):
        return "medium", "heuristic"
    if has_objective or has_diplomacy or aligned:
        return "low", "inferred"
    return "low", "inferred"


def _analyst_note(row: _AgentPhaseContext) -> str:
    if (
        row.diplomatic_action.get("status") == "present"
        and row.order_alignment.get("status") in {"aligned", "partially_aligned"}
        and row.board_consequence.get("category") not in {"no_material_change", "unclear"}
    ):
        return "Meaningful chain detected: objective linked to diplomacy, order, and board impact."
    if row.diplomatic_action.get("status") == "present" and row.order_alignment.get("status") == "contradictory":
        return "Potential contradiction: diplomatic signal did not translate into matching order."
    if row.diplomatic_action.get("status") == "present":
        return "Diplomatic action visible, but strategic conversion remains partial."
    return "Partial chain: objective/action linkage is weak or indirect."


def _agent_summary(agent_id: str, power: str, rows: list[_AgentPhaseContext]) -> dict[str, Any]:
    chains = len(rows)
    concrete = len([row for row in rows if row.diplomatic_action.get("status") == "present"])
    aligned = len([row for row in rows if row.order_alignment.get("status") in {"aligned", "partially_aligned"}])
    consequence = len(
        [row for row in rows if row.board_consequence.get("category") not in {"no_material_change", "unclear"}]
    )
    memory = len([row for row in rows if row.memory_update.get("status") == "present"])
    belief_summaries = len([row for row in rows if row.belief_summary.get("present")])
    belief_summaries_persisted = len([row for row in rows if row.belief_summary.get("persisted")])
    belief_summaries_reused = len([row for row in rows if row.belief_summary.get("reused")])
    complete = len(
        [
            row
            for row in rows
            if row.diplomatic_action.get("status") == "present"
            and row.order_alignment.get("status") in {"aligned", "partially_aligned"}
            and row.board_consequence.get("category") not in {"no_material_change", "unclear"}
            and row.memory_update.get("status") == "present"
        ]
    )
    stagnation = 0
    score_raw = 0
    for row in rows:
        if row.objective_source != "not_found":
            score_raw += 1
        if row.diplomatic_action.get("status") == "present":
            score_raw += 1
        if row.order_alignment.get("status") in {"aligned", "partially_aligned"}:
            score_raw += 1
        if row.board_consequence.get("category") not in {"no_material_change", "unclear"}:
            score_raw += 1
        if row.memory_update.get("status") == "present":
            score_raw += 1
    max_points = max(1, chains * 5)
    score = max(0.0, min(1.0, score_raw / max_points))
    return {
        "country": power,
        "chains_detected": chains,
        "complete_chains": complete,
        "objectives_with_concrete_messages": concrete,
        "objectives_with_aligned_orders": aligned,
        "objectives_with_board_consequence": consequence,
        "memory_updates_after_consequence": memory,
        "belief_summaries_detected": belief_summaries,
        "belief_summaries_persisted": belief_summaries_persisted,
        "belief_summaries_reused": belief_summaries_reused,
        "stagnation_loops_detected": stagnation,
        "strategic_conversion_score": round(score, 4),
    }


def _stagnation_candidates(data: GameObservatoryData) -> list[dict[str, Any]]:
    pair_rows: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(data.private_messages, key=_msg_sort_key):
        frm = str(row.get("from_agent_id", ""))
        to = str((row.get("to") or {}).get("to_agent_id", ""))
        if not frm or not to or frm == to:
            continue
        pair = "__".join(sorted([frm, to]))
        pair_rows.setdefault(pair, []).append(row)
    out: list[dict[str, Any]] = []
    for pair, rows in sorted(pair_rows.items()):
        confirmations = 0
        support_requests = 0
        specific_mentions = 0
        for row in rows:
            text = str(row.get("body", "")).lower()
            if any(token in text for token in _CONFIRMATION_TOKENS):
                confirmations += 1
            if any(token in text for token in _SUPPORT_REQUEST_TOKENS):
                support_requests += 1
            if _PROVINCE_TOKEN.search(text):
                specific_mentions += 1
        message_count = len(rows)
        if message_count < 10:
            continue
        if confirmations < 5:
            continue
        if support_requests > 1:
            continue
        out.append(
            {
                "pair": pair.split("__"),
                "phase_range": f"{rows[0].get('phase_id')}->{rows[-1].get('phase_id')}",
                "message_count": message_count,
                "repeated_confirmation_markers": confirmations,
                "concrete_support_requests": support_requests,
                "specific_order_mentions": specific_mentions,
                "linked_board_consequences": 0,
                "classification": "stagnation_loop_candidate",
            }
        )
    return out


def _broken_commitment_candidates(
    data: GameObservatoryData,
    deltas: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    phase_index = {phase_id: idx for idx, phase_id in enumerate(data.phases)}
    power_by_agent = {agent: power for power, agent in data.assignments_by_power.items()}
    phase_delta = {
        str(row.get("phase_id", "")): row for row in deltas if isinstance(row, dict)
    }
    rows: list[dict[str, Any]] = []
    by_pair_msgs: dict[str, list[dict[str, Any]]] = {}
    for msg in sorted(data.private_messages, key=_msg_sort_key):
        frm = str(msg.get("from_agent_id", ""))
        to = str((msg.get("to") or {}).get("to_agent_id", ""))
        if not frm or not to or frm == to:
            continue
        pair = "__".join(sorted([frm, to]))
        by_pair_msgs.setdefault(pair, []).append(msg)

    for phase_id, orders in sorted(data.phase_submitted_orders.items()):
        delta = phase_delta.get(phase_id, {})
        center_changes = delta.get("center_changes", []) if isinstance(delta, dict) else []
        for order in orders:
            if not isinstance(order, dict):
                continue
            if str(order.get("type", "")) != "MOVE":
                continue
            attacker_power = str(order.get("power", ""))
            target = str((order.get("payload") or {}).get("to", "")).lower()
            if not attacker_power or not target:
                continue
            attacker_agent = data.assignments_by_power.get(attacker_power, "")
            if not attacker_agent:
                continue
            for pair, messages in by_pair_msgs.items():
                if attacker_agent not in pair.split("__"):
                    continue
                candidate_msgs = [
                    msg
                    for msg in messages
                    if phase_index.get(str(msg.get("phase_id", "")), -1) < phase_index.get(phase_id, -1)
                    and str(msg.get("from_agent_id", "")) == attacker_agent
                    and any(token in str(msg.get("body", "")).lower() for token in _ASSURANCE_TOKENS)
                ]
                if not candidate_msgs:
                    continue
                last_msg = candidate_msgs[-1]
                defender_agent = str((last_msg.get("to") or {}).get("to_agent_id", ""))
                defender_power = power_by_agent.get(defender_agent, "")
                meaningful = any(
                    isinstance(row, dict)
                    and str(row.get("province", "")).lower() == target
                    and (row.get("to_owner") == attacker_power or row.get("from_owner") == defender_power)
                    for row in center_changes
                )
                classification = "possible_broken_commitment"
                confidence = "low"
                if meaningful:
                    classification = "plausible_betrayal"
                    confidence = "medium"
                rows.append(
                    {
                        "classification": classification,
                        "confidence": confidence,
                        "summary": (
                            f"{attacker_power} moved toward {target} after explicit assurance-style language to {defender_agent}."
                        ),
                        "phase_id": phase_id,
                        "attacker_agent_id": attacker_agent,
                        "defender_agent_id": defender_agent,
                        "evidence": [
                            f"messages/private/*:message_id={last_msg.get('message_id')}",
                            f"phases/{phase_id}/orders/submitted_orders.json",
                            f"observatory/board_evolution.json:phase_id={phase_id}",
                        ],
                    }
                )
    dedup: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            str(row.get("phase_id", "")),
            str(row.get("attacker_agent_id", "")),
            str(row.get("defender_agent_id", "")),
        )
        existing = dedup.get(key)
        if existing is None:
            dedup[key] = row
            continue
        if existing.get("classification") != "plausible_betrayal" and row.get("classification") == "plausible_betrayal":
            dedup[key] = row
    return list(dedup.values())


def _chain_markdown_block(row: dict[str, Any]) -> list[str]:
    return [
        f"### {row.get('country', '')} / {row.get('agent_id', '')} - {row.get('phase', '')}",
        "",
        f"- Objective: {row.get('strategic_objective', {}).get('summary', '')}",
        f"- Relationship use: {row.get('relationship_use', {}).get('summary', '')}",
        f"- Diplomatic action: {row.get('diplomatic_action', {}).get('summary', '')}",
        f"- Orders: {row.get('order_alignment', {}).get('orders', [])}",
        f"- Board consequence: {row.get('board_consequence', {}).get('summary', '')}",
        f"- Memory update: {row.get('memory_update', {}).get('summary', '')}",
        f"- Confidence: {row.get('confidence', 'low')}",
        f"- Why it matters: {row.get('analyst_note', '')}",
        "",
    ]


def _order_text(order: dict[str, Any]) -> str:
    order_type = str(order.get("type", ""))
    unit = str(order.get("unit_ref", ""))
    payload = order.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}
    if order_type == "MOVE":
        return f"{unit} -> {payload.get('to')}"
    if order_type == "HOLD":
        return f"{unit} HOLD"
    if order_type == "SUPPORT":
        return f"{unit} SUPPORT {payload}"
    if order_type == "CONVOY":
        return f"{unit} CONVOY {payload}"
    if order_type == "BUILD":
        return f"BUILD {payload}"
    if order_type == "DISBAND":
        return f"DISBAND {unit}"
    return f"{order_type} {unit} {payload}"


def _top_notable_chains(game_payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for payload in game_payloads:
        game_id = payload.get("game_id")
        for row in payload.get("chains", []):
            if not isinstance(row, dict):
                continue
            if row.get("confidence") != "high":
                continue
            rows.append(
                {
                    "game_id": game_id,
                    "chain_id": row.get("chain_id"),
                    "agent_id": row.get("agent_id"),
                    "country": row.get("country"),
                    "phase": row.get("phase"),
                    "summary": row.get("analyst_note"),
                }
            )
    return rows[:20]


def _msg_sort_key(msg: dict[str, Any]) -> tuple[str, int, str]:
    tick = msg.get("tick")
    tick_int = int(tick) if isinstance(tick, int) else 0
    return (str(msg.get("phase_id", "")), tick_int, str(msg.get("message_id", "")))


def _trim(text: str, max_len: int) -> str:
    clean = re.sub(r"\s+", " ", text.strip())
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 3] + "..."
