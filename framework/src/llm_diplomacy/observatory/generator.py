from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .api import (
    load_game,
    summarize_agent,
    summarize_board_evolution,
    summarize_diplomacy_metrics,
    summarize_negotiations,
)
from .strategic_consistency import (
    strategic_consistency_markdown,
    summarize_strategic_consistency,
)
from .event_index import build_canonical_event_index, phase_sort_key
from .reporter import build_omniscient_reports
from llm_diplomacy.reporter import build_secret_history_report


def build_game_observatory(game_dir: str | Path) -> Path:
    game_root = Path(game_dir)
    data = load_game(game_root)
    observatory_dir = game_root / "observatory"
    observatory_dir.mkdir(parents=True, exist_ok=True)

    # Per-agent/power timelines and reflection history.
    for power in sorted(data.assignments_by_power.keys()):
        timeline = _power_timeline(data, power)
        (observatory_dir / f"{power}.md").write_text(timeline, encoding="utf-8")
        reflection = _reflection_history(data, power)
        (observatory_dir / f"{power}_reflections.md").write_text(reflection, encoding="utf-8")

    # Conversation views reconstructed from message artifacts.
    conversations = summarize_negotiations(data)
    convo_dir = observatory_dir / "conversations"
    convo_dir.mkdir(parents=True, exist_ok=True)
    for convo_id, content in sorted(conversations.items()):
        (convo_dir / f"{convo_id}.md").write_text(content, encoding="utf-8")
    pair_files = sorted([name for name in conversations.keys() if name != "global"])
    (convo_dir / "index.md").write_text(
        "\n".join(
            [
                "# Private Conversation Index",
                "",
                *([f"- [{name}]({name}.md)" for name in pair_files] if pair_files else ["- No private conversations."]),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    # Board evolution deltas.
    deltas = summarize_board_evolution(data)
    (observatory_dir / "board_evolution.json").write_text(
        json.dumps({"schema_version": "1.0", "deltas": deltas}, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    (observatory_dir / "board_evolution.md").write_text(_board_delta_markdown(deltas), encoding="utf-8")
    diplomacy_metrics = summarize_diplomacy_metrics(data)
    (observatory_dir / "diplomacy_metrics.json").write_text(
        json.dumps(diplomacy_metrics, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    (observatory_dir / "diplomacy_metrics.md").write_text(
        _diplomacy_metrics_markdown(diplomacy_metrics),
        encoding="utf-8",
    )
    reflection_diag = _reflection_diagnostics(data)
    (observatory_dir / "reflection_diagnostics.json").write_text(
        json.dumps(reflection_diag, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    (observatory_dir / "reflection_diagnostics.md").write_text(
        _reflection_diagnostics_markdown(reflection_diag),
        encoding="utf-8",
    )
    prompt_diag = _prompt_diagnostics(data)
    (observatory_dir / "prompt_diagnostics.json").write_text(
        json.dumps(prompt_diag, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    (observatory_dir / "prompt_diagnostics.md").write_text(
        _prompt_diagnostics_markdown(prompt_diag),
        encoding="utf-8",
    )
    belief_evidence = summarize_strategic_consistency(data, board_deltas=deltas)
    (observatory_dir / "belief_evidence_observability.json").write_text(
        json.dumps(belief_evidence, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    coordination = _candidate_coordination_evidence(data)
    (observatory_dir / "candidate_coordination_evidence.json").write_text(
        json.dumps(coordination, sort_keys=True, indent=2), encoding="utf-8"
    )
    (observatory_dir / "belief_evidence_observability.md").write_text(
        strategic_consistency_markdown(belief_evidence),
        encoding="utf-8",
    )
    report_paths = build_omniscient_reports(
        data=data,
        observatory_dir=observatory_dir,
        diplomacy_metrics=diplomacy_metrics,
        board_deltas=deltas,
    )
    secret_history_paths = build_secret_history_report(
        game_root,
        data=data,
        board_deltas=deltas,
        diplomacy_metrics=diplomacy_metrics,
    )
    report_paths.update(secret_history_paths)

    # Lightweight static html report.
    (observatory_dir / "index.html").write_text(
        _html_report(game_root, data, deltas, diplomacy_metrics, report_paths),
        encoding="utf-8",
    )
    return observatory_dir


def _candidate_coordination_evidence(data: Any) -> dict[str, Any]:
    """Reference-linked chronology, deliberately without causal or quality scoring."""
    keywords = ("support", "coordinate", "shared target", "defend", "request", "dmz")
    events = build_canonical_event_index(data)
    resolutions = [event for event in events if event.get("event_type") == "board_resolution"]
    rows: list[dict[str, Any]] = []
    for message in [event for event in events if event.get("event_type") == "negotiation_message"]:
        payload = message.get("payload", {}) if isinstance(message.get("payload"), dict) else {}
        body = str(payload.get("body", ""))
        if not any(word in body.lower() for word in keywords):
            continue
        phase_id = str(message.get("phase_id", ""))
        later = next((event for event in resolutions if phase_sort_key(str(event.get("phase_id", ""))) > phase_sort_key(phase_id)), None)
        target = None
        if later is not None:
            target_phase = str(later.get("phase_id", ""))
            target = {
                "label": "followed_by_public_action",
                "phase_id": target_phase,
                "event_rank": {"communication": list(phase_sort_key(phase_id)), "public_action": list(phase_sort_key(target_phase))},
                "adjudication_artifact_path": later.get("artifact_path"),
                "public_final_orders_artifact_path": f"phases/{target_phase}/orders/validated_orders.json",
            }
        rows.append({
            "label": "candidate_coordination_evidence",
            "communication": {"phase_id": phase_id, "artifact_path": message.get("artifact_path"), "text": body},
            "followed_by_public_action": target,
            "no_later_public_action_in_scope": target is None,
            "directly_evidenced": "The message and any later public adjudication artifact are chronological records.",
            "interpretive_limit": "Chronology does not establish that the communication caused any later order or action.",
        })
    return {"schema_version": "1.0", "report_version": "candidate_coordination_evidence_v2_canonical_chronology", "rows": rows, "absence_note": "No candidate communication-to-public-action linkage evidence was found." if not rows else None}


def _power_timeline(data, power: str) -> str:
    base = summarize_agent(data, power)
    agent_id = data.assignments_by_power.get(power, "")
    sections: list[str] = [base]
    for phase_id in data.phases:
        submitted = [row for row in data.phase_submitted_orders.get(phase_id, []) if row.get("power") == power]
        resolution = data.phase_resolutions.get(phase_id, {})
        output_state = resolution.get("output_state", {})
        board_summary = _board_summary_for_power(output_state, power)
        private_inbox = [
            msg
            for msg in data.private_messages
            if (msg.get("to") or {}).get("to_agent_id") == agent_id and msg.get("phase_id") == phase_id
        ]
        sent = [msg for msg in data.global_messages if msg.get("from_agent_id") == agent_id and msg.get("phase_id") == phase_id]
        reflection = data.phase_reflections.get(f"{phase_id}:{agent_id}", {})
        explain = ((reflection.get("result") or {}).get("explanation") if isinstance(reflection, dict) else None) or {}
        negotiate_key = f"{phase_id}_negotiate_tick"
        submit_key = f"{phase_id}_submit_orders"
        negotiate_output = data.runtime_outputs_by_agent.get(agent_id, {}).get(negotiate_key, {})
        submit_output = data.runtime_outputs_by_agent.get(agent_id, {}).get(submit_key, {})
        communication_plan = {}
        order_rationale = {}
        passivity_review = {}
        if isinstance(negotiate_output, dict):
            llm_meta = negotiate_output.get("llm_meta", {})
            if isinstance(llm_meta, dict):
                parsed = llm_meta.get("parsed_payload", {})
                if isinstance(parsed, dict):
                    communication_plan = parsed.get("communication_plan", {}) if isinstance(parsed.get("communication_plan"), dict) else {}
        if isinstance(submit_output, dict):
            llm_meta = submit_output.get("llm_meta", {})
            if isinstance(llm_meta, dict):
                parsed = llm_meta.get("parsed_payload", {})
                if isinstance(parsed, dict):
                    order_rationale = parsed.get("order_rationale", {}) if isinstance(parsed.get("order_rationale"), dict) else {}
                    passivity_review = parsed.get("passivity_review", {}) if isinstance(parsed.get("passivity_review"), dict) else {}
        order_validation_status = {}
        submitted_orders = []
        effective_orders = []
        invalid_order_reasons = []
        legal_adjustment_options = []
        adjustment_obligation = {}
        retry_count = 0
        fallback_applied = False
        if isinstance(submit_output, dict):
            llm_meta = submit_output.get("llm_meta", {})
            if isinstance(llm_meta, dict):
                order_grounding = llm_meta.get("order_grounding", {})
                if isinstance(order_grounding, dict):
                    order_validation_status = {"status": order_grounding.get("status", "")}
                    submitted_orders = order_grounding.get("submitted_orders", []) if isinstance(order_grounding.get("submitted_orders"), list) else []
                    effective_orders = order_grounding.get("effective_orders", []) if isinstance(order_grounding.get("effective_orders"), list) else []
                    invalid_order_reasons = order_grounding.get("invalid_reasons", []) if isinstance(order_grounding.get("invalid_reasons"), list) else []
                    legal_context = order_grounding.get("legal_order_context", {}) if isinstance(order_grounding.get("legal_order_context"), dict) else {}
                    adjustment = legal_context.get("adjustment", {}) if isinstance(legal_context, dict) else {}
                    if isinstance(adjustment, dict):
                        adjustment_obligation = {
                            "obligation": adjustment.get("obligation"),
                            "required_count": adjustment.get("required_count"),
                        }
                        legal_adjustment_options = adjustment.get("legal_options", []) if isinstance(adjustment.get("legal_options"), list) else []
                retry_count = int(llm_meta.get("retry_count", 0)) if isinstance(llm_meta.get("retry_count"), int) else 0
                fallback_applied = bool(llm_meta.get("fallback_applied", False))
        previous_commitments = {}
        reflection_payload = (reflection.get("result") or {}).get("reflection", {}) if isinstance(reflection, dict) else {}
        if isinstance(reflection_payload, dict):
            previous_commitments = {
                "strategic_intent": reflection_payload.get("strategic_intent", {}),
                "belief_updates": reflection_payload.get("belief_updates", []),
            }
        sections.append(
            "\n".join(
                [
                    f"### {phase_id}",
                    f"- phase: `{phase_id}`",
                    f"- tick: `{_phase_tick_max(data, phase_id)}`",
                    f"- board_summary: `{board_summary}`",
                    f"- messages_received: `{len(private_inbox)}`",
                    f"- messages_sent: `{len(sent)}`",
                    f"- orders_submitted: `{len(submitted)}`",
                    f"- resolution_received: `phases/{phase_id}/resolution/resolution.json`",
                    f"- reflection_produced: `phases/{phase_id}/reflection/{agent_id}.json`",
                    f"- explainability: `{explain if explain else {}}`",
                    f"- previous_commitments: `{previous_commitments}`",
                    f"- communication_plan: `{communication_plan}`",
                    f"- order_rationale: `{order_rationale}`",
                    f"- passivity_review: `{passivity_review}`",
                    f"- order_validation_status: `{order_validation_status}`",
                    f"- submitted_orders: `{submitted_orders}`",
                    f"- effective_orders: `{effective_orders}`",
                    f"- invalid_order_reasons: `{invalid_order_reasons}`",
                    f"- adjustment_obligation: `{adjustment_obligation}`",
                    f"- legal_adjustment_options: `{legal_adjustment_options}`",
                    f"- repair_attempts: `{retry_count}`",
                    f"- fallback_applied: `{fallback_applied}`",
                ]
            )
        )
    return "\n\n".join(sections).strip() + "\n"


def _reflection_history(data, power: str) -> str:
    agent_id = data.assignments_by_power.get(power, "")
    parts = [f"# Reflection History: {power}", f"Assigned agent: `{agent_id}`", ""]
    for phase_id in data.phases:
        key = f"{phase_id}:{agent_id}"
        if key not in data.phase_reflections:
            continue
        payload = data.phase_reflections[key]
        reflection = (
            ((payload.get("result") or {}).get("reflection"))
            if isinstance(payload, dict)
            else {}
        ) or {}
        objective_observations = reflection.get("objective_observations", []) if isinstance(reflection, dict) else []
        phase_summary = str(reflection.get("phase_summary", "")) if isinstance(reflection, dict) else ""
        belief_updates = reflection.get("belief_updates", []) if isinstance(reflection, dict) else []
        objective_update = reflection.get("objective_update", {}) if isinstance(reflection, dict) else {}
        if not isinstance(objective_update, dict):
            objective_update = {}
        relationship_updates = reflection.get("relationship_updates", []) if isinstance(reflection, dict) else []
        if not isinstance(relationship_updates, list):
            relationship_updates = []
        action_lessons = reflection.get("action_lessons", []) if isinstance(reflection, dict) else []
        if not isinstance(action_lessons, list):
            action_lessons = []
        memory_candidates = reflection.get("memory_candidates", []) if isinstance(reflection, dict) else []
        if not isinstance(memory_candidates, list):
            memory_candidates = []
        strategic_intent = reflection.get("strategic_intent", {}) if isinstance(reflection, dict) else {}
        memory_update = reflection.get("memory_update", "") if isinstance(reflection, dict) else ""
        explanation = reflection.get("explanation", {}) if isinstance(reflection, dict) else {}
        belief_lines: list[str] = []
        for row in belief_updates:
            if not isinstance(row, dict):
                continue
            belief_lines.extend(
                [
                    f"- subject: {row.get('subject', '')} | confidence: {row.get('confidence', '')}",
                    f"  belief: {row.get('belief', '')}",
                    f"  basis: {row.get('basis', '')}",
                ]
            )
        parts.extend(
            [
                f"## {phase_id}",
                "",
                "### Phase Summary",
                f"- {phase_summary if phase_summary else '(none)'}",
                "",
                "### Objective Update",
                f"- current_objective: {objective_update.get('current_objective', '')}",
                f"- why_it_matters: {objective_update.get('why_it_matters', '')}",
                "",
                "### Objective Observations",
                *([f"- {row}" for row in objective_observations] if objective_observations else ["- (none)"]),
                "",
                "### Belief Updates",
                *(belief_lines if belief_lines else ["- (none)"]),
                "",
                "### Relationship Updates",
                *(
                    [
                        f"- {str(row.get('agent_id', ''))} [{str(row.get('status', ''))}] value={str(row.get('value_to_my_objectives', ''))} risk={str(row.get('risk_or_limit', ''))}"
                        for row in relationship_updates
                        if isinstance(row, dict)
                    ]
                    if relationship_updates
                    else ["- (none)"]
                ),
                "",
                "### Action Lessons",
                *([f"- {row}" for row in action_lessons if isinstance(row, str)] if action_lessons else ["- (none)"]),
                "",
                "### Memory Candidates",
                *([f"- {row}" for row in memory_candidates if isinstance(row, str)] if memory_candidates else ["- (none)"]),
                "",
                "### Strategic Intent",
                f"- short_term: {strategic_intent.get('short_term', '') if isinstance(strategic_intent, dict) else ''}",
                f"- medium_term: {strategic_intent.get('medium_term', '') if isinstance(strategic_intent, dict) else ''}",
                f"- risk_notes: {strategic_intent.get('risk_notes', []) if isinstance(strategic_intent, dict) else []}",
                "",
                "### Memory Update",
                f"- {memory_update if memory_update else '(none)'}",
                "",
                "### Explanation Summary",
                f"- {explanation.get('summary', '') if isinstance(explanation, dict) else ''}",
                "",
                "```json",
                json.dumps(payload, sort_keys=True, indent=2),
                "```",
                "",
            ]
        )
    return "\n".join(parts).strip() + "\n"


def _board_delta_markdown(deltas: list[dict[str, Any]]) -> str:
    lines = ["# Board Evolution", ""]
    for row in deltas:
        lines.append(f"## {row.get('phase_id', '')}")
        lines.append(f"- moves: {row.get('moves', [])}")
        lines.append(f"- center_changes: {row.get('center_changes', [])}")
        lines.append(f"- builds: {row.get('builds', [])}")
        lines.append(f"- disbands: {row.get('disbands', [])}")
        for power, delta in sorted((row.get("powers") or {}).items()):
            lines.append(f"- {power}:")
            lines.append(f"  - units_gained: {delta.get('units_gained', [])}")
            lines.append(f"  - units_lost: {delta.get('units_lost', [])}")
            lines.append(f"  - centers_gained: {delta.get('centers_gained', [])}")
            lines.append(f"  - centers_lost: {delta.get('centers_lost', [])}")
            lines.append(f"  - dislodgements: {delta.get('dislodgements', 0)}")
            lines.append(f"  - retreats_required: {delta.get('retreats_required', 0)}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _html_report(
    game_root: Path,
    data,
    deltas: list[dict[str, Any]],
    diplomacy_metrics: dict[str, Any],
    report_paths: dict[str, str],
) -> str:
    events_count = len(data.events)
    global_count = len(data.global_messages)
    private_count = len(data.private_messages)
    cost_summary = game_root / "metrics" / "cost_summary.json"
    cost = {}
    if cost_summary.exists():
        cost = json.loads(cost_summary.read_text(encoding="utf-8"))
    leaderboard_overall = game_root.parents[1] / "leaderboard" / "overall.json"
    leaderboard = {}
    if leaderboard_overall.exists():
        leaderboard = json.loads(leaderboard_overall.read_text(encoding="utf-8"))

    links = "".join(
        f"<li><a href='{power}.md'>{power} timeline</a> | <a href='{power}_reflections.md'>{power} reflections</a></li>"
        for power in sorted(data.assignments_by_power.keys())
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Game Observatory Report</title>
  <style>
    body {{ font-family: Helvetica, Arial, sans-serif; margin: 2rem; }}
    h1, h2 {{ margin-bottom: 0.2rem; }}
    pre {{ background: #f4f4f4; padding: 1rem; overflow-x: auto; }}
  </style>
</head>
<body>
  <h1>Game Observatory</h1>
  <p>Derived read-only from existing game artifacts.</p>
  <h2>Timelines</h2>
  <ul>{links}</ul>
  <h2>Conversations</h2>
  <ul>
    <li><a href="conversations/global.md">Global channel transcript</a></li>
    <li><a href="conversations/index.md">Private pair conversation index</a></li>
  </ul>
  <h2>Diplomacy Metrics</h2>
  <p><a href="diplomacy_metrics.md">Open diplomacy metrics report</a></p>
  <pre>{json.dumps(diplomacy_metrics, sort_keys=True, indent=2)}</pre>
  <h2>Diagnostics</h2>
  <ul>
    <li><a href="reflection_diagnostics.md">Reflection Diagnostics</a></li>
    <li><a href="prompt_diagnostics.md">Prompt Diagnostics</a></li>
    <li><a href="belief_evidence_observability.md">Belief and Evidence Observability</a></li>
  </ul>
  <h2>Omniscient Reports</h2>
  <ul>
    <li><a href="reports/aar_public.md">Public After-Action Report</a></li>
    <li><a href="reports/secret_history.md">Secret History</a></li>
    <li><a href="../reporter/secret_history.md">Secret History (Historian)</a></li>
    <li><a href="../reporter/secret_history.json">Secret History JSON</a></li>
    <li><a href="reports/agent_profiles.md">Agent Profiles</a></li>
    <li><a href="reports/key_moments.md">Key Moments</a></li>
    <li><a href="reports/reporter_summary.json">Reporter Summary JSON</a></li>
  </ul>
  <pre>{json.dumps(report_paths, sort_keys=True, indent=2)}</pre>
  <h2>Event Counts</h2>
  <pre>{json.dumps({"events": events_count, "global_messages": global_count, "private_messages": private_count}, sort_keys=True, indent=2)}</pre>
  <h2>Leaderboard</h2>
  <pre>{json.dumps(leaderboard, sort_keys=True, indent=2)}</pre>
  <h2>Cost Summary</h2>
  <pre>{json.dumps(cost, sort_keys=True, indent=2)}</pre>
  <h2>Board Evolution</h2>
  <pre>{json.dumps(deltas, sort_keys=True, indent=2)}</pre>
</body>
</html>
"""


def _phase_tick_max(data, phase_id: str) -> int:
    relevant = [
        row
        for row in data.events
        if row.get("event_type") == "negotiation_tick_committed" and str(row.get("phase_id", "")).startswith(phase_id.split("_movement")[0].split("_adjustment")[0].split("_retreat")[0])
    ]
    ticks = [int((row.get("payload") or {}).get("tick_index", 0)) for row in relevant]
    return max(ticks) if ticks else 0


def _board_summary_for_power(state: dict[str, Any], power: str) -> str:
    units = [unit for unit in state.get("units", []) if unit.get("owner") == power and unit.get("status") == "active"]
    centers = [
        province_id
        for province_id, owner in (state.get("supply_ownership", {}) or {}).items()
        if owner == power
    ]
    dislodged = [unit for unit in state.get("units", []) if unit.get("owner") == power and unit.get("status") == "dislodged"]
    return f"active_units={len(units)}, centers={len(centers)}, dislodged={len(dislodged)}"


def _diplomacy_metrics_markdown(metrics: dict[str, Any]) -> str:
    lines = [
        "# Bilateral Diplomacy Analysis",
        "",
        f"- private_messages_occurred: {metrics.get('private_messages_occurred', False)}",
        f"- global_messages_sent: {metrics.get('global_messages_sent', 0)}",
        f"- private_messages_sent: {metrics.get('private_messages_sent', 0)}",
        f"- private_message_reply_rate: {metrics.get('private_message_reply_rate', 0.0)}",
        f"- global_message_rate: {metrics.get('global_message_rate', 0.0)}",
        f"- private_vs_global_ratio: {metrics.get('private_vs_global_ratio', None)}",
        f"- zero_message_agents: {metrics.get('zero_message_agents', [])}",
        "",
        "## Pairwise Private Counts",
    ]
    by_pair = metrics.get("private_messages_by_pair", {})
    if isinstance(by_pair, dict) and by_pair:
        for pair, count in sorted(by_pair.items()):
            lines.append(f"- {pair}: {count}")
    else:
        lines.append("- (none)")
    lines.extend(["", "## Per-Agent Communication Summary"])
    for row in metrics.get("per_agent", []):
        if not isinstance(row, dict):
            continue
        lines.append(
            "- "
            + f"{row.get('agent_id', '')} ({row.get('country', '')}): "
            + f"global={row.get('global_messages_sent', 0)}, "
            + f"private={row.get('private_messages_sent', 0)}, "
            + f"contacts={row.get('unique_private_contacts', 0)}, "
            + f"replies={row.get('private_replies_received', 0)}, "
            + f"reply_rate={row.get('private_reply_rate', 0.0)}"
        )
    lines.extend(
        [
            "",
            "## Heuristic Signals",
            f"- communication_in_reflections: {metrics.get('communication_in_reflections_heuristic', {})}",
            f"- communication_affecting_orders: {metrics.get('communication_affecting_orders_heuristic', {})}",
            f"- conversation_quality_heuristics: {metrics.get('conversation_quality_heuristics', {})}",
            "",
        ]
    )
    return "\n".join(lines)


def _reflection_diagnostics(data) -> dict[str, Any]:
    by_agent: dict[str, dict[str, Any]] = {}
    failure_records: list[dict[str, Any]] = []
    for agent_id, outputs in data.runtime_outputs_by_agent.items():
        for key, payload in outputs.items():
            if not key.endswith("_reflect"):
                continue
            llm_meta = payload.get("llm_meta", {}) if isinstance(payload, dict) else {}
            if not isinstance(llm_meta, dict):
                continue
            bucket = by_agent.setdefault(
                agent_id,
                {"total_reflections": 0, "fallback_count": 0, "parse_error_count": 0, "phases": []},
            )
            bucket["total_reflections"] += 1
            if bool(llm_meta.get("fallback_applied", False)):
                bucket["fallback_count"] += 1
            if bool(llm_meta.get("parse_error", False)):
                bucket["parse_error_count"] += 1
            bucket["phases"].append(
                {
                    "phase_key": key,
                    "fallback_applied": bool(llm_meta.get("fallback_applied", False)),
                    "parse_error": bool(llm_meta.get("parse_error", False)),
                    "retry_count": int(llm_meta.get("retry_count", 0)) if isinstance(llm_meta.get("retry_count"), int) else 0,
                    "parse_error_category": llm_meta.get("parse_error_category"),
                    "validation_error_category": llm_meta.get("validation_error_category"),
                    "validation_error_detail": llm_meta.get("validation_error_detail"),
                    "likely_truncated": bool(llm_meta.get("likely_truncated", False)),
                    "raw_output_paths": llm_meta.get("raw_output_paths", []),
                }
            )
            failure = llm_meta.get("reflection_failure")
            if isinstance(failure, dict):
                failure_records.append(failure)
    return {
        "schema_version": "1.0",
        "by_agent": dict(sorted(by_agent.items())),
        "failure_records": failure_records,
    }


def _reflection_diagnostics_markdown(payload: dict[str, Any]) -> str:
    lines = ["# Reflection Diagnostics", ""]
    by_agent = payload.get("by_agent", {})
    if not isinstance(by_agent, dict) or not by_agent:
        lines.append("- no reflection diagnostics available")
        return "\n".join(lines).strip() + "\n"
    for agent_id, row in sorted(by_agent.items()):
        if not isinstance(row, dict):
            continue
        lines.extend(
            [
                f"## {agent_id}",
                f"- total_reflections: {row.get('total_reflections', 0)}",
                f"- fallback_count: {row.get('fallback_count', 0)}",
                f"- parse_error_count: {row.get('parse_error_count', 0)}",
                "",
            ]
        )
    failures = payload.get("failure_records", [])
    lines.extend(["## Failure Records", ""])
    if isinstance(failures, list) and failures:
        for row in failures:
            lines.append(f"- {row}")
    else:
        lines.append("- no reflection failures recorded")
    return "\n".join(lines).strip() + "\n"


def _prompt_diagnostics(data) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    aggregate_checks: dict[str, bool] = {
        "contains_strategic_imperatives": False,
        "contains_diplomacy_primer": False,
        "contains_strategic_self_interest": False,
        "contains_instrumental_diplomacy": False,
        "contains_elimination_consequence": False,
        "contains_performance_evaluation": False,
        "contains_negotiation_inference": False,
        "contains_conversations_should_evolve": False,
    }
    for agent_id, outputs in data.runtime_outputs_by_agent.items():
        for key, payload in outputs.items():
            llm_meta = payload.get("llm_meta", {}) if isinstance(payload, dict) else {}
            if not isinstance(llm_meta, dict):
                continue
            prompt_chars = llm_meta.get("prompt_size_chars")
            if not isinstance(prompt_chars, int):
                continue
            context_chars = llm_meta.get("context_size_chars")
            prompt_text = llm_meta.get("prompt")
            checks = _prompt_check_flags(str(prompt_text)) if isinstance(prompt_text, str) else {}
            for check_key, check_value in checks.items():
                if check_key in aggregate_checks and check_value:
                    aggregate_checks[check_key] = True
            rows.append(
                {
                    "agent_id": agent_id,
                    "phase_key": key,
                    "method": payload.get("method") if isinstance(payload, dict) else None,
                    "prompt_size_chars": prompt_chars,
                    "context_size_chars": context_chars if isinstance(context_chars, int) else None,
                    "estimated_input_tokens": llm_meta.get("estimated_input_tokens"),
                    "context_warnings": llm_meta.get("context_warnings", []),
                    "prompt_checks": checks,
                }
            )
    largest = sorted(rows, key=lambda row: int(row.get("prompt_size_chars", 0)), reverse=True)[0] if rows else None
    return {"schema_version": "1.0", "largest_prompt": largest, "aggregate_prompt_checks": aggregate_checks, "records": rows}


def _prompt_diagnostics_markdown(payload: dict[str, Any]) -> str:
    lines = ["# Prompt Diagnostics", ""]
    largest = payload.get("largest_prompt")
    if isinstance(largest, dict):
        lines.append(f"- largest_prompt: {largest}")
    else:
        lines.append("- largest_prompt: none")
    checks = payload.get("aggregate_prompt_checks", {})
    if isinstance(checks, dict):
        lines.extend(["", "## Aggregate Prompt Checks"])
        for key, value in sorted(checks.items()):
            lines.append(f"- {key}: {value}")
    return "\n".join(lines).strip() + "\n"


def _prompt_check_flags(prompt: str) -> dict[str, bool]:
    lowered = prompt.lower()
    return {
        "contains_strategic_imperatives": "# strategic imperatives" in lowered,
        "contains_diplomacy_primer": "# diplomacy primer" in lowered,
        "contains_strategic_self_interest": (
            "strategic self-interest and instrumental diplomacy" in lowered
            or (
                "all reasoning must be grounded in your own strategic position" in lowered
                and "do not treat diplomacy as social harmony" in lowered
            )
        ),
        "contains_instrumental_diplomacy": (
            "a relationship is valuable only if it helps you" in lowered
            or "translate social observations into strategic consequences" in lowered
        ),
        "contains_elimination_consequence": (
            "if your country is eliminated" in lowered
            and "elimination is a strategic failure" in lowered
        ),
        "contains_performance_evaluation": (
            "across games, your persistent identity is evaluated by outcomes" in lowered
            or ("message volume is not success" in lowered and "being liked is not success" in lowered)
        ),
        "contains_negotiation_inference": "negotiation is strategic inference" in lowered,
        "contains_conversations_should_evolve": "conversations should evolve" in lowered,
    }
