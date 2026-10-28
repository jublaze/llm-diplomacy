from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llm_diplomacy.observatory.api import load_game, summarize_board_evolution


MODE_TO_METHOD = {
    "reflection": "reflect",
    "negotiation": "negotiate_tick",
    "orders": "submit_orders",
}


@dataclass(frozen=True)
class Sample:
    game_id: str
    phase_id: str
    agent_id: str
    mode: str
    reason: str


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _list_game_dirs(experiment_root: Path) -> list[Path]:
    games_root = experiment_root / "games"
    if not games_root.exists():
        return []
    return sorted([p for p in games_root.iterdir() if p.is_dir()], key=lambda p: p.name)


def _phase_compatible(mode: str, phase_id: str) -> bool:
    if mode == "negotiation":
        return phase_id.endswith("_negotiation")
    return not phase_id.endswith("_negotiation")


def _resolve_phase_for_mode(mode: str, phase_id: str, available: set[str]) -> str | None:
    if phase_id in available and _phase_compatible(mode, phase_id):
        return phase_id
    parts = phase_id.split("_")
    if len(parts) < 3:
        return None
    prefix = "_".join(parts[:2])
    candidates: list[str]
    if mode == "negotiation":
        candidates = [f"{prefix}_negotiation"]
    else:
        candidates = [f"{prefix}_movement", f"{prefix}_adjustment", f"{prefix}_retreat"]
    for candidate in candidates:
        if candidate in available:
            return candidate
    return None


def _runtime_output_for_sample(
    *,
    game_dir: Path,
    sample: Sample,
) -> tuple[dict[str, Any] | None, str]:
    method = MODE_TO_METHOD[sample.mode]
    stem = f"{sample.phase_id}_{method}"
    path = (
        game_dir
        / "agents"
        / sample.agent_id
        / "runtime"
        / "phase_outputs"
        / f"{stem}.json"
    )
    return (_safe_load_json(path), str(path))


def _visible_messages_for_agent(
    *,
    game_data: Any,
    sample: Sample,
) -> dict[str, list[dict[str, Any]]]:
    global_rows = [
        row
        for row in game_data.global_messages
        if str(row.get("phase_id", "")) == sample.phase_id
    ]
    private_rows = [
        row
        for row in game_data.private_messages
        if str(row.get("phase_id", "")) == sample.phase_id
        and (
            str((row.get("to") or {}).get("to_agent_id", "")) == sample.agent_id
            or str(row.get("from_agent_id", "")) == sample.agent_id
        )
    ]
    return {"global": global_rows, "private": private_rows}


def _phase_resolution(game_dir: Path, phase_id: str) -> dict[str, Any]:
    resolution_path = game_dir / "phases" / phase_id / "resolution" / "resolution.json"
    payload = _safe_load_json(resolution_path)
    return payload or {}


def _phase_orders(game_dir: Path, phase_id: str) -> dict[str, Any]:
    submitted = game_dir / "phases" / phase_id / "orders" / "submitted_orders.json"
    payload = _safe_load_json(submitted)
    return payload or {}


def _phase_reflection(game_dir: Path, phase_id: str, agent_id: str) -> dict[str, Any]:
    path = game_dir / "phases" / phase_id / "reflection" / f"{agent_id}.json"
    payload = _safe_load_json(path)
    return payload or {}


def _sample_context(
    *,
    experiment_id: str,
    game_dir: Path,
    game_data: Any,
    sample: Sample,
    runtime_payload: dict[str, Any] | None,
    runtime_path: str,
) -> dict[str, Any]:
    llm_meta = runtime_payload.get("llm_meta", {}) if isinstance(runtime_payload, dict) else {}
    if not isinstance(llm_meta, dict):
        llm_meta = {}
    request_context = llm_meta.get("request_context", {})
    if not isinstance(request_context, dict):
        request_context = {}

    assignments = dict(sorted(game_data.assignments_by_power.items()))
    country = ""
    for power, assigned_agent in assignments.items():
        if assigned_agent == sample.agent_id:
            country = power
            break

    context_sources = {
        "runtime_request_context": bool(request_context),
        "phase_resolution": (game_dir / "phases" / sample.phase_id / "resolution" / "resolution.json").exists(),
        "phase_orders": (game_dir / "phases" / sample.phase_id / "orders" / "submitted_orders.json").exists(),
        "phase_reflection": (game_dir / "phases" / sample.phase_id / "reflection" / f"{sample.agent_id}.json").exists(),
    }

    board_summary = request_context.get("board_summary", {})
    if not isinstance(board_summary, dict):
        board_summary = {}
    visible_messages = request_context.get("visible_messages", {})
    if not isinstance(visible_messages, dict):
        visible_messages = _visible_messages_for_agent(game_data=game_data, sample=sample)
    legal_order_context = request_context.get("legal_order_context", {})
    if not isinstance(legal_order_context, dict):
        legal_order_context = {}
    previous_commitments = request_context.get("previous_commitments", {})
    if not isinstance(previous_commitments, dict):
        previous_commitments = {}

    return {
        "schema_version": "1.0",
        "experiment_id": experiment_id,
        "game_id": sample.game_id,
        "phase_id": sample.phase_id,
        "agent_id": sample.agent_id,
        "country": country,
        "mode": sample.mode,
        "context_sources": context_sources,
        "assignments_by_power": assignments,
        "board_summary": board_summary,
        "visible_messages": visible_messages,
        "legal_order_context": legal_order_context,
        "previous_commitments": previous_commitments,
        "phase_resolution": _phase_resolution(game_dir, sample.phase_id),
        "phase_orders": _phase_orders(game_dir, sample.phase_id),
        "phase_reflection": _phase_reflection(game_dir, sample.phase_id, sample.agent_id),
        "runtime_output_path": runtime_path,
        "original_runtime_llm_meta": {
            "provider_model": llm_meta.get("provider_model"),
            "fallback_applied": bool(llm_meta.get("fallback_applied", False)),
            "parse_error": bool(llm_meta.get("parse_error", False)),
            "validation_error_category": llm_meta.get("validation_error_category"),
            "prompt_hash": f"sha256:{hashlib.sha256(str(llm_meta.get('prompt', '')).encode('utf-8')).hexdigest()}",
        },
    }


def _original_output(runtime_payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(runtime_payload, dict):
        return None
    result = runtime_payload.get("result", {})
    return result if isinstance(result, dict) else None


def _prompt_for_mode(mode: str, context: dict[str, Any]) -> str:
    common = (
        "You are producing an offline counterfactual output for a Diplomacy AI experiment.\n\n"
        "You are not changing the original game. You are given the same or similar context available "
        "to one agent at one phase.\n\n"
        "Use only the supplied context. Do not invent private facts. Distinguish public facts, message "
        "claims, beliefs, and inferences.\n\n"
        "Your objective is to produce a strategically stronger output from this agent's perspective.\n\n"
        "Prioritize survival, centers, leverage, defense, information advantage, solo-victory prospects, "
        "or favorable draw prospects.\n\n"
        "Return exactly one JSON object and nothing else.\n\n"
    )
    if mode == "reflection":
        schema = {
            "belief_updates": [
                {
                    "subject": "string",
                    "belief": "string",
                    "confidence": "low|medium|high",
                    "basis": "public_fact|message_claim|inference|mixed",
                    "strategic_consequence": "string",
                    "next_probe_or_action": "string",
                }
            ],
            "strategic_position": "string",
            "missed_or_ignored_events": [{"event": "string", "why_it_matters": "string"}],
            "relationship_updates": [
                {
                    "agent_id": "string",
                    "status": "string",
                    "value_to_my_objectives": "string",
                    "risk_or_limit": "string",
                    "next_use": "string",
                }
            ],
            "memory_candidates": ["string"],
        }
        instruction = (
            "Mode: reflection/belief update. Produce high-signal belief and strategic updates.\\n"
            "Return only valid JSON matching this exact shape.\\n"
            "Maximum lengths:\\n"
            "- belief_updates: at most 3\\n"
            "- missed_or_ignored_events: at most 2\\n"
            "- relationship_updates: at most 6\\n"
            "- memory_candidates: at most 3\\n"
            "- strategic_position: at most 150 words\\n"
            "Prefer the most strategically important items.\\n"
            "Keep each string concise.\\n"
            "Do not include markdown. Do not include commentary outside JSON.\\n"
            "If there is not enough evidence for a field, use an empty array or concise uncertainty.\\n"
            "Do not invent hidden facts."
        )
    elif mode == "negotiation":
        schema = {
            "private_messages": [
                {
                    "recipient": "string",
                    "body": "string",
                    "purpose": (
                        "support_request|support_offer|claim_test|warning|information_trade|"
                        "dmz|red_line|relationship_repair|other"
                    ),
                    "strategic_reason": "string",
                }
            ],
            "global_messages": [
                {"body": "string", "purpose": "string", "strategic_reason": "string"}
            ],
            "withheld_information": [{"summary": "string", "reason": "string"}],
        }
        instruction = "Mode: negotiation. Produce concrete, strategic diplomacy proposals."
    else:
        schema = {
            "orders": ["string"],
            "strategic_objective": "string",
            "rationale": "string",
            "relationship_dependencies": [
                {"agent_id": "string", "dependency": "string", "risk": "string"}
            ],
            "alternative_considered": "string",
            "risk_assessment": "string",
        }
        legal_warning = ""
        request_ctx = context.get("request_context", {}) if isinstance(context, dict) else {}
        legal_rows = request_ctx.get("your_units", []) if isinstance(request_ctx, dict) else []
        has_units = isinstance(legal_rows, list) and any(isinstance(row, dict) and row.get("unit_ref") for row in legal_rows)
        has_legal_orders = isinstance(legal_rows, list) and any(
            isinstance(row, dict) and isinstance(row.get("legal_orders"), list) and bool(row.get("legal_orders"))
            for row in legal_rows
        )
        if not has_units or not has_legal_orders:
            legal_warning = (
                "\nLEGAL_CONTEXT_WARNING: Exact legal order list was unavailable. "
                "Use only the supplied board/unit context and avoid unsupported order syntax."
            )
        instruction = (
            "Mode: orders.\n"
            "The order list is the primary output. Keep all explanations short.\n"
            "Task order:\n"
            "1. Identify the agent's current units and legal order context from the supplied context.\n"
            "2. Produce the order list.\n"
            "3. Give a concise strategic objective.\n"
            "4. Give a concise rationale.\n"
            "5. Mention only the most important relationship dependencies.\n"
            "6. Mention one alternative and one risk assessment.\n"
            "Return only valid JSON.\n"
            "Do not include markdown.\n"
            "Do not include commentary outside the JSON.\n"
            "Prefer legal orders over strategic explanation.\n"
            "Do not write a tactical essay.\n"
            "Use only the supplied context.\n"
            "Do not invent units, provinces, supports, convoys, or hidden facts.\n"
            "If legal-order context is incomplete, submit the best orders supported by the provided context and state uncertainty briefly in risk_assessment.\n"
            "Hard limits:\n"
            "- orders: exact list of submitted orders only\n"
            "- strategic_objective: max 75 words\n"
            "- rationale: max 150 words\n"
            "- relationship_dependencies: max 6\n"
            "- alternative_considered: max 75 words\n"
            "- risk_assessment: max 125 words"
            f"{legal_warning}"
        )
    return (
        common
        + instruction
        + "\n\nRequired JSON shape:\n"
        + json.dumps(schema, sort_keys=True, indent=2)
        + "\n\nContext:\n"
        + json.dumps(context, sort_keys=True, indent=2)
    )


def _extract_response_text(payload: dict[str, Any]) -> str:
    output = payload.get("output", [])
    if not isinstance(output, list):
        return ""
    texts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if str(item.get("type", "")) != "message":
            continue
        content = item.get("content", [])
        if not isinstance(content, list):
            continue
        for row in content:
            if not isinstance(row, dict):
                continue
            if str(row.get("type", "")) == "output_text":
                texts.append(str(row.get("text", "")))
    return "\n".join([t for t in texts if t.strip()]).strip()


def call_responses_api(
    *,
    model: str,
    input_text: str,
    max_output_tokens: int,
    reasoning_effort: str,
    base_url: str,
    api_key: str,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/responses"
    body = {
        "model": model,
        "input": input_text,
        "max_output_tokens": max_output_tokens,
        "reasoning": {"effort": reasoning_effort},
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        method="POST",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw_text = resp.read().decode("utf-8")
            status_code = int(resp.status)
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8")
        elapsed = round(time.perf_counter() - started, 3)
        return {
            "ok": False,
            "status_code": int(exc.code),
            "error": f"HTTPError: {exc.code}",
            "error_body": error_body,
            "response_json": _try_json(error_body),
            "elapsed_seconds": elapsed,
        }
    except urllib.error.URLError as exc:
        elapsed = round(time.perf_counter() - started, 3)
        return {
            "ok": False,
            "status_code": None,
            "error": f"URLError: {exc.reason}",
            "error_body": "",
            "response_json": None,
            "elapsed_seconds": elapsed,
        }
    elapsed = round(time.perf_counter() - started, 3)
    payload = _try_json(raw_text)
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "status_code": status_code,
            "error": "Non-JSON response",
            "error_body": raw_text,
            "response_json": None,
            "elapsed_seconds": elapsed,
        }
    output_text = _extract_response_text(payload)
    return {
        "ok": True,
        "status_code": status_code,
        "error": None,
        "error_body": "",
        "response_json": payload,
        "elapsed_seconds": elapsed,
        "response_id": payload.get("id"),
        "status": payload.get("status"),
        "requested_model": model,
        "reasoning_effort": reasoning_effort,
        "resolved_model": payload.get("model"),
        "usage": payload.get("usage"),
        "output_text": output_text,
    }


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _comparison_heuristics(
    *,
    mode: str,
    context: dict[str, Any],
    original_output: dict[str, Any] | None,
    advanced_text: str,
    advanced_json: dict[str, Any] | None,
) -> dict[str, Any]:
    advanced_blob = advanced_text.lower()
    original_blob = json.dumps(original_output or {}, sort_keys=True).lower()
    province_pattern = re.compile(r"\b[a-z]{3}\b", flags=re.IGNORECASE)
    specific_order_or_province = bool(province_pattern.search(advanced_text))
    specific_order_or_province_orig = bool(province_pattern.search(original_blob))
    concrete_markers = ["support", "dmz", "border", "if you", "then i", "confirm", "warning", "rumor"]
    advanced_concrete = sum(1 for m in concrete_markers if m in advanced_blob)
    original_concrete = sum(1 for m in concrete_markers if m in original_blob)
    basis_markers = ["public_fact", "message_claim", "belief", "inference", "mixed"]
    distinguishes = sum(1 for m in basis_markers if m in advanced_blob) >= 2
    identifies_stagnation = ("stagn" in advanced_blob) or ("low_value" in advanced_blob)
    identifies_risk = "risk" in advanced_blob or "threat" in advanced_blob
    probe_tokens = [
        "ask",
        "probe",
        "request",
        "demand",
        "warn",
        "offer",
        "test",
        "verify",
        "coordinate",
        "press",
        "seek explanation",
        "negotiate",
        "support_request",
        "claim_test",
    ]
    contains_probe = any(token in advanced_blob for token in probe_tokens)
    if not contains_probe:
        contains_probe = "?" in advanced_text
    if mode == "reflection" and isinstance(advanced_json, dict):
        for row in advanced_json.get("belief_updates", []):
            if isinstance(row, dict) and any(token in str(row.get("next_probe_or_action", "")).lower() for token in probe_tokens):
                contains_probe = True
                break
        if not contains_probe:
            for row in advanced_json.get("relationship_updates", []):
                if isinstance(row, dict) and any(token in str(row.get("next_use", "")).lower() for token in probe_tokens):
                    contains_probe = True
                    break
        if not contains_probe and any(token in str(advanced_json.get("strategic_position", "")).lower() for token in probe_tokens):
            contains_probe = True
    board_markers = ["center", "supply", "dislodg", "retreat", "moved", "ownership"]
    mentions_board = any(marker in advanced_blob for marker in board_markers)
    unsupported = any(
        marker in advanced_blob for marker in ["hidden channel", "omniscient motive", "secretly knows all"]
    )
    if mode == "orders":
        orders_rows = advanced_json.get("orders", []) if isinstance(advanced_json, dict) else []
        contains_orders = isinstance(orders_rows, list) and any(str(row).strip() for row in orders_rows)
        contains_rationale = isinstance(advanced_json, dict) and bool(str(advanced_json.get("rationale", "")).strip())
        relationship_rows = advanced_json.get("relationship_dependencies", []) if isinstance(advanced_json, dict) else []
        mentions_relationship_dependency = isinstance(relationship_rows, list) and any(
            isinstance(row, dict) and any(str(row.get(k, "")).strip() for k in ("agent_id", "dependency", "risk"))
            for row in relationship_rows
        )
        mentions_risk = (
            (isinstance(advanced_json, dict) and bool(str(advanced_json.get("risk_assessment", "")).strip()))
            or ("risk" in advanced_blob)
        )
        return {
            "contains_orders": contains_orders,
            "contains_specific_order_or_province": specific_order_or_province,
            "contains_rationale": contains_rationale,
            "mentions_relationship_dependency": mentions_relationship_dependency,
            "mentions_risk": mentions_risk,
            "heuristic": True,
        }
    return {
        "more_concrete": advanced_concrete > original_concrete,
        "mentions_public_board_events": mentions_board,
        "distinguishes_fact_claim_belief": distinguishes,
        "contains_specific_diplomatic_probe": contains_probe,
        "contains_specific_order_or_province": specific_order_or_province,
        "identifies_stagnation": identifies_stagnation,
        "identifies_risk": identifies_risk,
        "unsupported_claims_detected": unsupported,
        "original_had_specific_order_or_province": specific_order_or_province_orig,
        "heuristic": True,
    }


def _sample_markdown(sample_payload: dict[str, Any]) -> str:
    context = sample_payload.get("context", {})
    original = sample_payload.get("original_output")
    advanced = sample_payload.get("advanced_model_output", {})
    comparison = sample_payload.get("comparison", {})
    return "\n".join(
        [
            f"# Advanced Model Comparison - {sample_payload.get('sample_id', '')}",
            "",
            "## Context Summary",
            f"- experiment_id: {context.get('experiment_id') if isinstance(context, dict) else ''}",
            f"- game_id: {context.get('game_id') if isinstance(context, dict) else ''}",
            f"- phase_id: {context.get('phase_id') if isinstance(context, dict) else ''}",
            f"- agent_id: {context.get('agent_id') if isinstance(context, dict) else ''}",
            f"- country: {context.get('country') if isinstance(context, dict) else ''}",
            f"- mode: {context.get('mode') if isinstance(context, dict) else ''}",
            "",
            "## Original Agent Output",
            "```json",
            json.dumps(original, sort_keys=True, indent=2) if isinstance(original, dict) else "null",
            "```",
            "",
            "## GPT-5.5 Counterfactual Output",
            "```json",
            json.dumps(advanced, sort_keys=True, indent=2) if isinstance(advanced, dict) else "null",
            "```",
            "",
            "## Key Differences",
            f"- comparison: `{comparison}`",
            "",
            "## Strategic Quality Notes",
            "- Heuristic comparison only; requires human review.",
            "",
            "## Concrete Improvements",
            "- See `comparison` booleans for signal direction.",
            "",
            "## Risks / Hallucinations / Unsupported Claims",
            f"- unsupported_claims_detected: `{comparison.get('unsupported_claims_detected') if isinstance(comparison, dict) else None}`",
            "",
            "## Human Review Notes",
            "- Offline counterfactual only; does not alter original game events.",
            "",
        ]
    ).strip() + "\n"


def _sample_json_name(sample: Sample) -> str:
    return f"{sample.game_id}_{sample.phase_id}_{sample.agent_id}_{sample.mode}"


def _interesting_candidates_for_game(
    *,
    game_dir: Path,
    mode: str,
) -> list[Sample]:
    game_data = load_game(game_dir)
    available_phases = set(game_data.phases)
    agents = sorted(set(game_data.assignments_by_power.values()))
    if not agents:
        agents = sorted(game_data.runtime_outputs_by_agent.keys())
    out: list[Sample] = []
    if not agents or not available_phases:
        return out
    first_agent = agents[0]
    opener = _resolve_phase_for_mode(mode, "1901_spring_movement", available_phases)
    if opener is not None:
        out.append(
            Sample(
                game_id=game_dir.name,
                phase_id=opener,
                agent_id=first_agent,
                mode=mode,
                reason="opening_phase",
            )
        )

    if game_data.private_messages:
        by_phase: dict[str, int] = {}
        by_phase_agent: dict[str, str] = {}
        for row in game_data.private_messages:
            phase = str(row.get("phase_id", ""))
            by_phase[phase] = by_phase.get(phase, 0) + 1
            by_phase_agent.setdefault(phase, str(row.get("from_agent_id", first_agent)))
        most_private_phase = sorted(by_phase.items(), key=lambda item: (-item[1], item[0]))[0][0]
        resolved = _resolve_phase_for_mode(mode, most_private_phase, available_phases)
        if resolved is not None:
            out.append(
                Sample(
                    game_id=game_dir.name,
                    phase_id=resolved,
                    agent_id=by_phase_agent.get(most_private_phase, first_agent),
                    mode=mode,
                    reason="many_private_messages",
                )
            )

    deltas = summarize_board_evolution(game_data)
    for row in deltas:
        if not isinstance(row, dict):
            continue
        if not row.get("center_changes"):
            continue
        phase_id = str(row.get("phase_id", ""))
        resolved = _resolve_phase_for_mode(mode, phase_id, available_phases)
        if resolved is not None:
            agent_id = first_agent
            if isinstance(row.get("center_changes"), list) and row.get("center_changes"):
                to_owner = (row["center_changes"][0] or {}).get("to_owner")
                if isinstance(to_owner, str):
                    agent_id = game_data.assignments_by_power.get(to_owner, first_agent)
            out.append(
                Sample(
                    game_id=game_dir.name,
                    phase_id=resolved,
                    agent_id=agent_id,
                    mode=mode,
                    reason="center_change",
                )
            )
            break

    sc_path = game_dir / "observatory" / "strategic_consistency.json"
    sc = _safe_load_json(sc_path) or {}
    broken = sc.get("broken_commitment_candidates", []) if isinstance(sc, dict) else []
    if isinstance(broken, list) and broken:
        row = broken[0] if isinstance(broken[0], dict) else {}
        phase_id = str(row.get("phase_id", ""))
        resolved = _resolve_phase_for_mode(mode, phase_id, available_phases)
        if resolved is not None:
            agent_id = str(row.get("attacker_agent_id", first_agent))
            out.append(
                Sample(
                    game_id=game_dir.name,
                    phase_id=resolved,
                    agent_id=agent_id or first_agent,
                    mode=mode,
                    reason="broken_commitment_candidate",
                )
            )

    stagn = sc.get("stagnation_loop_candidates", []) if isinstance(sc, dict) else []
    if isinstance(stagn, list) and stagn:
        row = stagn[0] if isinstance(stagn[0], dict) else {}
        phase_range = str(row.get("phase_range", ""))
        candidate_phase = phase_range.split("->")[-1] if "->" in phase_range else ""
        resolved = _resolve_phase_for_mode(mode, candidate_phase, available_phases)
        pair = row.get("pair", [])
        candidate_agent = first_agent
        if isinstance(pair, list) and pair and isinstance(pair[0], str):
            candidate_agent = pair[0]
        if resolved is not None:
            out.append(
                Sample(
                    game_id=game_dir.name,
                    phase_id=resolved,
                    agent_id=candidate_agent,
                    mode=mode,
                    reason="stagnation_candidate",
                )
            )
    return out




def _samples_from_runtime_outputs(*, game_dir: Path, mode: str) -> list[Sample]:
    method = MODE_TO_METHOD[mode]
    out: list[Sample] = []
    agents_root = game_dir / "agents"
    if not agents_root.exists():
        return out
    pattern = re.compile(r"^(?P<phase>.+)_(?P<method>negotiate_tick|submit_orders|reflect)\.json$")
    for agent_dir in sorted([row for row in agents_root.iterdir() if row.is_dir()], key=lambda row: row.name):
        phase_outputs = agent_dir / "runtime" / "phase_outputs"
        if not phase_outputs.exists():
            continue
        for file in sorted(phase_outputs.glob("*.json"), key=lambda row: row.name):
            match = pattern.match(file.name)
            if match is None:
                continue
            file_method = str(match.group("method"))
            if file_method != method:
                continue
            phase_id = str(match.group("phase"))
            if not _phase_compatible(mode, phase_id):
                continue
            out.append(
                Sample(
                    game_id=game_dir.name,
                    phase_id=phase_id,
                    agent_id=agent_dir.name,
                    mode=mode,
                    reason="runtime_phase_output",
                )
            )
    return out
def _dedupe_samples(samples: list[Sample]) -> list[Sample]:
    seen: set[tuple[str, str, str, str]] = set()
    out: list[Sample] = []
    for sample in samples:
        key = (sample.game_id, sample.phase_id, sample.agent_id, sample.mode)
        if key in seen:
            continue
        seen.add(key)
        out.append(sample)
    return out


def _manual_sample(
    *,
    experiment_root: Path,
    game_id: str,
    phase_id: str,
    agent_id: str,
    mode: str,
) -> Sample:
    game_dir = experiment_root / "games" / game_id
    game_data = load_game(game_dir)
    resolved = _resolve_phase_for_mode(mode, phase_id, set(game_data.phases))
    if resolved is None:
        raise ValueError(f"Could not resolve phase '{phase_id}' for mode '{mode}' in game '{game_id}'.")
    return Sample(
        game_id=game_id,
        phase_id=resolved,
        agent_id=agent_id,
        mode=mode,
        reason="manual_selection",
    )


def _auto_samples(
    *,
    experiment_root: Path,
    modes: list[str],
    max_samples: int,
) -> list[Sample]:
    out: list[Sample] = []
    for mode in modes:
        for game_dir in _list_game_dirs(experiment_root):
            runtime_rows = _samples_from_runtime_outputs(game_dir=game_dir, mode=mode)
            if runtime_rows:
                out.extend(runtime_rows)
            else:
                out.extend(_interesting_candidates_for_game(game_dir=game_dir, mode=mode))
    return _dedupe_samples(out)[:max_samples]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")


def _summary_payload(
    *,
    experiment_id: str,
    requested_model: str,
    reasoning_effort: str,
    sample_results: list[dict[str, Any]],
) -> dict[str, Any]:
    completed = [row for row in sample_results if row.get("advanced_model_status") == "completed"]
    failed = [row for row in sample_results if row.get("advanced_model_status") != "completed"]
    resolved_models = sorted(
        {
            str(row.get("resolved_model", ""))
            for row in completed
            if isinstance(row.get("resolved_model"), str) and str(row.get("resolved_model")).strip()
        }
    )
    usage_input = 0
    usage_output = 0
    for row in completed:
        usage = row.get("usage", {})
        if isinstance(usage, dict):
            if isinstance(usage.get("input_tokens"), int):
                usage_input += int(usage["input_tokens"])
            if isinstance(usage.get("output_tokens"), int):
                usage_output += int(usage["output_tokens"])
    improvements = {
        "more_concrete": 0,
        "mentions_public_board_events": 0,
        "distinguishes_fact_claim_belief": 0,
        "contains_specific_diplomatic_probe": 0,
        "contains_specific_order_or_province": 0,
        "identifies_stagnation": 0,
        "identifies_risk": 0,
    }
    unsupported = 0
    api_completed = 0
    api_failed = 0
    valid_json = 0
    invalid_json = 0
    truncated_output = 0
    empty_output = 0
    for row in sample_results:
        status = str(row.get("output_status", ""))
        if bool(row.get("api_completed", False)):
            api_completed += 1
        if status == "api_failed":
            api_failed += 1
        elif status == "valid_json":
            valid_json += 1
        elif status == "invalid_json":
            invalid_json += 1
        elif status == "truncated_output":
            truncated_output += 1
        elif status == "empty_output":
            empty_output += 1
    for row in completed:
        comp = row.get("comparison", {})
        if not isinstance(comp, dict):
            continue
        for key in improvements:
            if bool(comp.get(key, False)):
                improvements[key] += 1
        if bool(comp.get("unsupported_claims_detected", False)):
            unsupported += 1
    return {
        "schema_version": "1.0",
        "heuristic": True,
        "experiment_id": experiment_id,
        "samples_attempted": len(sample_results),
        "samples_completed": len(completed),
        "samples_failed": len(failed),
        "api_completed": api_completed,
        "api_failed": api_failed,
        "valid_json": valid_json,
        "invalid_json": invalid_json,
        "truncated_output": truncated_output,
        "empty_output": empty_output,
        "requested_model": requested_model,
        "reasoning_effort": reasoning_effort,
        "resolved_models_observed": resolved_models,
        "usage": {
            "input_tokens": usage_input,
            "output_tokens": usage_output,
        },
        "common_improvement_signals": improvements,
        "unsupported_claim_risk_count": unsupported,
        "recommendation": (
            "Based on sampled phases, advanced-model substitution appears promising where "
            "concrete strategic signals increase. This does not prove full-game superiority; "
            "next step is limited runtime integration testing."
        ),
        "sample_results": sample_results,
    }


def _summary_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Advanced Model Phase Substitution Summary - {payload.get('experiment_id', '')}",
        "",
        "Offline counterfactual comparison. Not original game history.",
        "",
        f"- samples_attempted: {payload.get('samples_attempted', 0)}",
        f"- samples_completed: {payload.get('samples_completed', 0)}",
        f"- samples_failed: {payload.get('samples_failed', 0)}",
        f"- requested_model: {payload.get('requested_model', '')}",
        f"- reasoning_effort: {payload.get('reasoning_effort', '')}",
        f"- resolved_models_observed: {payload.get('resolved_models_observed', [])}",
        "",
        "## Aggregate Usage",
        f"- input_tokens: {(payload.get('usage') or {}).get('input_tokens')}",
        f"- output_tokens: {(payload.get('usage') or {}).get('output_tokens')}",
        "",
        "## Output Quality",
        f"- API completed: {payload.get('api_completed', 0)} / {payload.get('samples_attempted', 0)}",
        f"- Valid JSON: {payload.get('valid_json', 0)} / {payload.get('samples_attempted', 0)}",
        f"- Truncated: {payload.get('truncated_output', 0)} / {payload.get('samples_attempted', 0)}",
        f"- Empty output: {payload.get('empty_output', 0)} / {payload.get('samples_attempted', 0)}",
        f"- API failed: {payload.get('api_failed', 0)} / {payload.get('samples_attempted', 0)}",
        "",
        "## Improvement Signals (Heuristic)",
    ]
    improvements = payload.get("common_improvement_signals", {})
    if isinstance(improvements, dict):
        for key, value in sorted(improvements.items()):
            lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            f"- unsupported_claim_risk_count: {payload.get('unsupported_claim_risk_count', 0)}",
            "",
            "## Bottom Line",
            payload.get("recommendation", ""),
            "",
            "Suggests potential gains on sampled phases, but does not prove broad runtime improvement.",
            "",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _classify_output_status(*, response: dict[str, Any] | None, output_text: str, parsed_obj: dict[str, Any] | None) -> dict[str, Any]:
    if response is None or not bool(response.get("ok", False)):
        return {
            "api_completed": False,
            "visible_output_present": False,
            "valid_json": False,
            "truncated_output": False,
            "empty_output": False,
            "parse_error": None,
            "output_status": "api_failed",
        }
    stripped = (output_text or "").strip()
    if not stripped:
        return {
            "api_completed": True,
            "visible_output_present": False,
            "valid_json": False,
            "truncated_output": False,
            "empty_output": True,
            "parse_error": "empty_output",
            "output_status": "empty_output",
        }
    if parsed_obj is not None:
        return {
            "api_completed": True,
            "visible_output_present": True,
            "valid_json": True,
            "truncated_output": False,
            "empty_output": False,
            "parse_error": None,
            "output_status": "valid_json",
        }
    lowered = stripped.lower()
    looks_truncated = False
    if stripped.count("{") > stripped.count("}") or stripped.count("[") > stripped.count("]"):
        looks_truncated = True
    if lowered.endswith(":") or lowered.endswith(","):
        looks_truncated = True
    if "unterminated" in lowered:
        looks_truncated = True
    if bool(response.get("status")) and str(response.get("status")).lower() in {"incomplete", "max_output_tokens"}:
        looks_truncated = True
    return {
        "api_completed": True,
        "visible_output_present": True,
        "valid_json": False,
        "truncated_output": looks_truncated,
        "empty_output": False,
        "parse_error": "likely_truncated_json" if looks_truncated else "json_decode_failed",
        "output_status": "truncated_output" if looks_truncated else "invalid_json",
    }


def run(args: argparse.Namespace) -> int:
    experiment_root = Path(args.experiment)
    if not experiment_root.exists():
        raise FileNotFoundError(f"Experiment path does not exist: {experiment_root}")
    if experiment_root.name != "experiments" and (experiment_root / "games").exists():
        pass
    elif (Path("experiments") / args.experiment).exists():
        experiment_root = Path("experiments") / args.experiment

    experiment_id = experiment_root.name
    output_root = experiment_root / "advanced_model_comparison"
    samples_dir = output_root / "samples"
    output_root.mkdir(parents=True, exist_ok=True)
    samples_dir.mkdir(parents=True, exist_ok=True)
    for stale in samples_dir.glob("*.json"):
        stale.unlink(missing_ok=True)
    for stale in samples_dir.glob("*.md"):
        stale.unlink(missing_ok=True)

    modes = list(dict.fromkeys(args.mode))
    total_planned = args.max_samples
    if not args.dry_run and not args.yes:
        raise ValueError("Refusing live API calls without --yes. Use --dry-run for prompt-only output.")

    if args.game and args.phase and args.agent:
        samples = [
            _manual_sample(
                experiment_root=experiment_root,
                game_id=args.game,
                phase_id=args.phase,
                agent_id=args.agent,
                mode=mode,
            )
            for mode in modes
        ][: args.max_samples]
    else:
        samples = _auto_samples(
            experiment_root=experiment_root,
            modes=modes,
            max_samples=args.max_samples,
        )
    if not samples:
        raise RuntimeError(
            f"No samples found for modes={modes} in experiment {experiment_root}. "
            "Expected runtime artifacts under games/*/agents/*/runtime/phase_outputs/."
        )

    sample_results: list[dict[str, Any]] = []
    reasoning_effort = args.reasoning_effort or ("low" if "orders" in modes and len(modes) == 1 else "medium")
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    for sample in samples[:total_planned]:
        game_dir = experiment_root / "games" / sample.game_id
        game_data = load_game(game_dir)
        runtime_payload, runtime_path = _runtime_output_for_sample(game_dir=game_dir, sample=sample)
        context = _sample_context(
            experiment_id=experiment_id,
            game_dir=game_dir,
            game_data=game_data,
            sample=sample,
            runtime_payload=runtime_payload,
            runtime_path=runtime_path,
        )
        prompt = _prompt_for_mode(sample.mode, context)
        original = _original_output(runtime_payload)
        sample_id = _sample_json_name(sample)
        sample_payload: dict[str, Any] = {
            "schema_version": "1.0",
            "sample_id": sample_id,
            "mode": sample.mode,
            "reason": sample.reason,
            "original_output_found": isinstance(original, dict),
            "context": context,
            "prompt": {
                "chars": len(prompt),
                "estimated_input_tokens": max(1, (len(prompt) + 3) // 4),
                "hash": f"sha256:{hashlib.sha256(prompt.encode('utf-8')).hexdigest()}",
            },
            "original_output": original,
            "advanced_model_status": "dry_run" if args.dry_run else "pending",
            "requested_model": args.model,
            "reasoning_effort": reasoning_effort,
            "resolved_model": None,
            "comparison": {},
            "human_review_recommended": True,
        }
        if args.dry_run:
            sample_payload["prompt"]["text"] = prompt
            sample_payload["advanced_model_output"] = None
            sample_payload.update(_classify_output_status(response=None, output_text="", parsed_obj=None))
        else:
            if not api_key:
                sample_payload["advanced_model_status"] = "failed_no_api_key"
                sample_payload["api_error"] = "Missing OPENAI_API_KEY"
                sample_payload.update(_classify_output_status(response=None, output_text="", parsed_obj=None))
            else:
                response = call_responses_api(
                    model=args.model,
                    input_text=prompt,
                    max_output_tokens=args.max_output_tokens,
                    reasoning_effort=reasoning_effort,
                    base_url=args.base_url,
                    api_key=api_key,
                )
                if not bool(response.get("ok", False)):
                    sample_payload["advanced_model_status"] = "api_error"
                    sample_payload["api_error"] = response.get("error")
                    sample_payload["api_error_body"] = response.get("error_body", "")
                    sample_payload["api_status_code"] = response.get("status_code")
                    sample_payload["elapsed_seconds"] = response.get("elapsed_seconds")
                    sample_payload["api_response_json"] = response.get("response_json")
                    sample_payload["advanced_model_output"] = None
                    sample_payload.update(_classify_output_status(response=response, output_text="", parsed_obj=None))
                else:
                    output_text = str(response.get("output_text", "")).strip()
                    parsed = _try_json(output_text)
                    parsed_obj = parsed if isinstance(parsed, dict) else None
                    sample_payload["advanced_model_status"] = "completed"
                    sample_payload["resolved_model"] = response.get("resolved_model")
                    sample_payload["response_id"] = response.get("response_id")
                    sample_payload["response_status"] = response.get("status")
                    sample_payload["usage"] = response.get("usage")
                    sample_payload["elapsed_seconds"] = response.get("elapsed_seconds")
                    sample_payload["advanced_model_output"] = {
                        "raw_text": output_text,
                        "parsed_json": parsed_obj,
                    }
                    sample_payload.update(_classify_output_status(response=response, output_text=output_text, parsed_obj=parsed_obj))
                    sample_payload["comparison"] = _comparison_heuristics(
                        mode=sample.mode,
                        context=context,
                        original_output=original,
                        advanced_text=output_text,
                        advanced_json=parsed_obj,
                    )
                    sample_payload["human_review_recommended"] = True
        _write_json(samples_dir / f"{sample_id}.json", sample_payload)
        (samples_dir / f"{sample_id}.md").write_text(_sample_markdown(sample_payload), encoding="utf-8")
        sample_results.append(
            {
                "sample_id": sample_id,
                "mode": sample.mode,
                "game_id": sample.game_id,
                "phase_id": sample.phase_id,
                "agent_id": sample.agent_id,
                "reason": sample.reason,
                "advanced_model_status": sample_payload.get("advanced_model_status"),
                "requested_model": sample_payload.get("requested_model"),
                "reasoning_effort": sample_payload.get("reasoning_effort"),
                "resolved_model": sample_payload.get("resolved_model"),
                "usage": sample_payload.get("usage"),
                "comparison": sample_payload.get("comparison", {}),
                "api_completed": sample_payload.get("api_completed", False),
                "visible_output_present": sample_payload.get("visible_output_present", False),
                "valid_json": sample_payload.get("valid_json", False),
                "truncated_output": sample_payload.get("truncated_output", False),
                "empty_output": sample_payload.get("empty_output", False),
                "parse_error": sample_payload.get("parse_error"),
                "output_status": sample_payload.get("output_status"),
            }
        )

    summary = _summary_payload(
        experiment_id=experiment_id,
        requested_model=args.model,
        reasoning_effort=reasoning_effort,
        sample_results=sample_results,
    )
    _write_json(output_root / "comparison_summary.json", summary)
    (output_root / "comparison_summary.md").write_text(_summary_markdown(summary), encoding="utf-8")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Offline advanced-model phase substitution harness. "
            "Generates counterfactual outputs for selected phases."
        )
    )
    parser.add_argument("--experiment", required=True, help="Path to experiment root (e.g. experiments/<id>).")
    parser.add_argument("--game", help="Optional game id for manual selection (e.g. game_004).")
    parser.add_argument("--phase", help="Optional phase id for manual selection (e.g. 1906_fall_movement).")
    parser.add_argument("--agent", help="Optional agent id for manual selection (e.g. helena).")
    parser.add_argument(
        "--mode",
        action="append",
        choices=["reflection", "negotiation", "orders"],
        default=None,
        help="Comparison mode. Repeatable. Default: reflection.",
    )
    parser.add_argument(
        "--sample-preset",
        choices=["interesting"],
        default="interesting",
        help="Automatic sample strategy when manual selection is not provided.",
    )
    parser.add_argument("--max-samples", type=int, default=5, help="Maximum total samples/calls.")
    parser.add_argument("--model", default="gpt-5.5", help="Responses API model id.")
    parser.add_argument("--max-output-tokens", type=int, default=700, help="Responses API max_output_tokens.")
    parser.add_argument("--base-url", default="https://api.openai.com/v1", help="Responses API base URL.")
    parser.add_argument(
        "--reasoning-effort",
        choices=["low", "medium", "high"],
        default=None,
        help="Responses API reasoning.effort. Defaults to low for orders mode, otherwise medium.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Write prompts and metadata without API calls.")
    parser.add_argument("--yes", action="store_true", help="Confirm live API calls.")
    parser.epilog = (
        "Examples:\n"
        "  # Dry run prompt generation only\n"
        "  python scripts/advanced_model_phase_substitution.py "
        "--experiment experiments/seven_llm_persistent_round_robin_5x_1910 "
        "--sample-preset interesting --mode reflection --max-samples 3 --dry-run\n\n"
        "  # Run a small GPT-5.5 reflection comparison\n"
        "  python scripts/advanced_model_phase_substitution.py "
        "--experiment experiments/seven_llm_persistent_round_robin_5x_1910 "
        "--sample-preset interesting --mode reflection --max-samples 3 "
        "--model gpt-5.5 --max-output-tokens 500 --yes\n\n"
        "  # Manually compare orders for a single agent-phase\n"
        "  python scripts/advanced_model_phase_substitution.py "
        "--experiment experiments/seven_llm_persistent_round_robin_5x_1910 "
        "--game game_004 --phase 1906_fall_movement --agent helena "
        "--mode orders --model gpt-5.5 --yes"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.mode is None:
        args.mode = ["reflection"]
    if args.max_samples < 1:
        raise ValueError("--max-samples must be >= 1")
    if (args.game or args.phase or args.agent) and not (args.game and args.phase and args.agent):
        raise ValueError("Manual mode requires --game, --phase, and --agent together.")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())



