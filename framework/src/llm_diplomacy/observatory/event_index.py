from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .api import GameObservatoryData


_PHASE_RE = re.compile(
    r"(?P<year>\d{4})_(?P<season>spring|fall|winter)_(?P<kind>negotiation|movement|retreat|adjustment)",
    flags=re.IGNORECASE,
)
_PHASE_STEP = {
    ("spring", "negotiation"): 0,
    ("spring", "movement"): 1,
    ("spring", "retreat"): 2,
    ("fall", "negotiation"): 3,
    ("fall", "movement"): 4,
    ("fall", "retreat"): 5,
    ("winter", "negotiation"): 6,
    ("winter", "adjustment"): 7,
}


def phase_metadata(phase_id: str | None) -> tuple[int, int, str, str]:
    """Return a stable chronological key and parsed phase fields.

    Legacy fixtures sometimes use opaque phase IDs.  They remain deterministic but
    are intentionally marked as linkage-insufficient by callers that need a real
    board phase.
    """
    value = str(phase_id or "").strip()
    match = _PHASE_RE.search(value)
    if not match:
        legacy = re.fullmatch(r"p(?P<index>\d+)", value, flags=re.IGNORECASE)
        if legacy:
            return (0, int(legacy.group("index")), "", "")
        return (10**9, 10**9, "", "")
    year = int(match.group("year"))
    season = match.group("season").lower()
    kind = match.group("kind").lower()
    return (year, _PHASE_STEP[(season, kind)], season, kind)


def phase_sort_key(phase_id: str | None) -> tuple[int, int, str]:
    year, step, _, _ = phase_metadata(phase_id)
    return (year, step, str(phase_id or ""))


def reflection_body(payload: dict[str, Any]) -> dict[str, Any]:
    """Read both current root-level and historical nested reflection shapes."""
    direct = payload.get("reflection")
    if isinstance(direct, dict):
        return direct
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("reflection"), dict):
        return result["reflection"]
    return {}


def build_canonical_event_index(data: GameObservatoryData) -> list[dict[str, Any]]:
    """Normalize persisted game artifacts into chronologically sortable events.

    The index is intentionally observational: malformed artifacts are retained as
    events where possible and identified with ``artifact_linkage_insufficient``
    rather than silently disappearing from analysis.
    """
    power_by_agent = {agent: power for power, agent in data.assignments_by_power.items()}
    events: list[dict[str, Any]] = []

    def add(
        event_type: str,
        *,
        agent_id: str = "",
        country: str = "",
        phase_id: str | None = None,
        artifact_path: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        recovered = _recover_phase_id(payload or {}, artifact_path, phase_id)
        phase_value = recovered or "artifact_linkage_insufficient"
        year, phase_order, season, _ = phase_metadata(phase_value)
        events.append(
            {
                "event_id": f"{data.game_dir.name}:{event_type}:{agent_id or country or 'public'}:{phase_value}:{len(events):04d}",
                "game_id": data.game_dir.name,
                "agent_id": agent_id,
                "country": country or power_by_agent.get(agent_id, ""),
                "event_type": event_type,
                "phase_id": phase_value,
                "phase_order": phase_order,
                "year": year if year != 10**9 else None,
                "season": season,
                "artifact_path": artifact_path,
                "payload": payload or {},
            }
        )

    for phase_id, artifact in data.phase_reflections.items():
        if not isinstance(artifact, dict):
            continue
        phase_hint, _, agent_id = phase_id.partition(":")
        path = f"phases/{phase_hint}/reflection/{agent_id}.json"
        body = reflection_body(artifact)
        add("reflection", agent_id=agent_id, phase_id=phase_hint, artifact_path=path, payload=artifact)
        if str(body.get("belief_summary", "")).strip() or isinstance(body.get("belief_updates"), list):
            add("belief_summary_update", agent_id=agent_id, phase_id=phase_hint, artifact_path=path, payload=body)
        if str(body.get("memory_update", "")).strip() or body.get("memory_candidates"):
            add("memory_update", agent_id=agent_id, phase_id=phase_hint, artifact_path=path, payload=body)

    for row in [*data.private_messages, *data.global_messages]:
        if not isinstance(row, dict):
            continue
        agent_id = str(row.get("from_agent_id", "")).strip()
        target = row.get("to") if isinstance(row.get("to"), dict) else {}
        channel = "private" if str(target.get("type", "")) == "private" else "global"
        add(
            "negotiation_message",
            agent_id=agent_id,
            phase_id=str(row.get("phase_id", "")),
            artifact_path=str(row.get("artifact_path", f"messages/{channel}/{channel}.jsonl")),
            payload=row,
        )

    for phase_id, orders in data.phase_submitted_orders.items():
        for row in orders:
            if not isinstance(row, dict):
                continue
            power = str(row.get("power", "")).strip()
            _, step, _, kind = phase_metadata(phase_id)
            event_type = (
                "retreat_submission" if kind == "retreat" else
                "adjustment_build_submission" if kind == "adjustment" else
                "order_submission"
            )
            add(
                event_type,
                agent_id=data.assignments_by_power.get(power, ""),
                country=power,
                phase_id=phase_id,
                artifact_path=f"phases/{phase_id}/orders/submitted_orders.json",
                payload=row,
            )

    for phase_id, resolution in data.phase_resolutions.items():
        if isinstance(resolution, dict):
            add(
                "board_resolution",
                phase_id=phase_id,
                artifact_path=f"phases/{phase_id}/resolution/resolution.json",
                payload=resolution,
            )

    _add_memory_events(data, add, power_by_agent)
    _add_runtime_prompt_events(data, add, power_by_agent)
    events.sort(key=lambda row: (phase_sort_key(row["phase_id"]), _event_priority(row["event_type"]), row["event_id"]))
    return events


def _add_memory_events(data: GameObservatoryData, add: Any, power_by_agent: dict[str, str]) -> None:
    agents_root = data.game_dir / "agents"
    if not agents_root.exists():
        return
    for path in sorted(agents_root.glob("*/folio/game_memory/*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        agent_id = path.parents[2].name
        relative = path.relative_to(data.game_dir).as_posix()
        for row in payload.get("memory_log", []):
            if isinstance(row, dict):
                add("memory_update", agent_id=agent_id, phase_id=str(row.get("phase_id", "")), artifact_path=relative, payload=row)
        for row in payload.get("belief_summary_history", []):
            if isinstance(row, dict):
                add("belief_summary_update", agent_id=agent_id, phase_id=str(row.get("phase_id", "")), artifact_path=relative, payload=row)
        current_summary = str(payload.get("belief_summary", "")).strip()
        if current_summary:
            add(
                "belief_summary_update",
                agent_id=agent_id,
                phase_id=str(payload.get("belief_summary_phase_id", "")),
                artifact_path=relative,
                payload={"summary": current_summary, "status": payload.get("belief_summary_status", "")},
            )
        for row in payload.get("belief_state", []):
            if isinstance(row, dict):
                add("memory_update", agent_id=agent_id, phase_id=str(row.get("phase_id", "")), artifact_path=relative, payload=row)


def _add_runtime_prompt_events(data: GameObservatoryData, add: Any, power_by_agent: dict[str, str]) -> None:
    for agent_id, outputs in data.runtime_outputs_by_agent.items():
        for output_key, payload in outputs.items():
            if not isinstance(payload, dict):
                continue
            phase_hint = output_key.rsplit("_", 1)[0]
            meta = payload.get("llm_meta") if isinstance(payload.get("llm_meta"), dict) else {}
            prompt = str(meta.get("prompt", ""))
            if prompt:
                add(
                    "prompt_context",
                    agent_id=agent_id,
                    phase_id=phase_hint,
                    artifact_path=f"agents/{agent_id}/runtime/phase_outputs/{output_key}.json",
                    payload={"prompt": prompt, "output_key": output_key},
                )


def _recover_phase_id(payload: dict[str, Any], artifact_path: str, hint: str | None) -> str:
    candidates: list[Any] = [hint, payload.get("phase_id"), payload.get("phase")]
    context = payload.get("context")
    if isinstance(context, dict):
        candidates.extend([context.get("phase_id"), context.get("phase")])
    for candidate in candidates:
        text = str(candidate or "").strip()
        if _PHASE_RE.search(text):
            return _PHASE_RE.search(text).group(0).lower()
        if text and candidate == hint:
            return text
    match = _PHASE_RE.search(str(artifact_path))
    return match.group(0).lower() if match else ""


def _event_priority(event_type: str) -> int:
    return {
        "reflection": 0,
        "negotiation_message": 1,
        "order_submission": 2,
        "retreat_submission": 2,
        "adjustment_build_submission": 2,
        "board_resolution": 3,
        "memory_update": 4,
        "belief_summary_update": 4,
        "prompt_context": 5,
    }.get(event_type, 9)
