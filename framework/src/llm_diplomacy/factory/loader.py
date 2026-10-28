from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .models import AgentBlueprint, AgentIdentity


def load_blueprint(path: str | Path) -> AgentBlueprint:
    p = Path(path)
    raw = _load_raw(p)
    return _to_blueprint(raw, source=str(p))


def _load_raw(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")
    if suffix == ".json":
        payload = json.loads(text)
    elif suffix in {".yaml", ".yml"}:
        payload = yaml.safe_load(text)
    else:
        raise ValueError(f"Unsupported blueprint format: {suffix}")
    if not isinstance(payload, dict):
        raise ValueError("Blueprint root must be an object.")
    return payload


def _to_blueprint(raw: dict[str, Any], *, source: str) -> AgentBlueprint:
    required = ["agent_id", "display_name", "runtime", "description", "identity"]
    missing = [field for field in required if field not in raw]
    if missing:
        raise ValueError(f"Missing blueprint fields in {source}: {missing}")
    identity = raw.get("identity")
    if not isinstance(identity, dict):
        raise ValueError(f"'identity' must be an object in {source}.")
    id_required = [
        "worldview",
        "priorities",
        "alliance_philosophy",
        "communication_philosophy",
        "information_philosophy",
        "evidence_philosophy",
        "planning_horizon",
    ]
    id_missing = [field for field in id_required if field not in identity]
    if id_missing:
        raise ValueError(f"Missing identity fields in {source}: {id_missing}")
    priorities = identity.get("priorities")
    if not isinstance(priorities, list) or not all(isinstance(row, str) for row in priorities):
        raise ValueError(f"'identity.priorities' must be a list of strings in {source}.")

    return AgentBlueprint(
        schema_version=str(raw.get("schema_version", "1.0")),
        agent_id=str(raw["agent_id"]).strip(),
        display_name=str(raw["display_name"]).strip(),
        runtime=str(raw["runtime"]).strip(),
        description=str(raw["description"]).strip(),
        identity=AgentIdentity(
            worldview=str(identity["worldview"]).strip(),
            priorities=tuple(str(row).strip() for row in priorities),
            alliance_philosophy=str(identity["alliance_philosophy"]).strip(),
            communication_philosophy=str(identity["communication_philosophy"]).strip(),
            information_philosophy=str(identity["information_philosophy"]).strip(),
            evidence_philosophy=str(identity["evidence_philosophy"]).strip(),
            planning_horizon=str(identity["planning_horizon"]).strip(),
        ),
        notes=str(raw.get("notes", "")).strip(),
    )
