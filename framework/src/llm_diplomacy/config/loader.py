from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import ExperimentConfig
from llm_diplomacy.agents.llm_provider import canonical_provider_config

REQUIRED_FIELDS = (
    "schema_version",
    "experiment_id",
    "games",
    "agents",
    "rule_engine",
    "negotiation",
    "seed",
)


def load_experiment_config(path: str | Path) -> ExperimentConfig:
    config_path = Path(path)
    data = _load_raw(config_path)
    _validate_basic_shape(data, source=str(config_path))
    return ExperimentConfig(
        schema_version=str(data["schema_version"]),
        experiment_id=str(data["experiment_id"]),
        games=int(data["games"]),
        agents=list(data["agents"]),
        rule_engine=dict(data["rule_engine"]),
        negotiation=dict(data["negotiation"]),
        seed=int(data["seed"]),
        raw=data,
    )


def _load_raw(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")

    if suffix in (".json",):
        loaded = json.loads(text)
    elif suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ModuleNotFoundError as exc:
            raise ValueError(
                "YAML config requested but PyYAML is not installed. "
                "Use JSON config or install PyYAML."
            ) from exc
        loaded = yaml.safe_load(text)
    else:
        raise ValueError(f"Unsupported config format: {suffix}")

    if not isinstance(loaded, dict):
        raise ValueError("Config root must be an object.")
    return loaded


def _validate_basic_shape(data: dict[str, Any], source: str) -> None:
    missing = [field for field in REQUIRED_FIELDS if field not in data]
    if missing:
        raise ValueError(f"Missing required config fields in {source}: {missing}")

    if not isinstance(data["games"], int) or data["games"] < 1:
        raise ValueError("'games' must be a positive integer.")
    if not isinstance(data["agents"], list):
        raise ValueError("'agents' must be a list.")
    seen_agent_ids: set[str] = set()
    for index, agent in enumerate(data["agents"]):
        if not isinstance(agent, dict):
            raise ValueError(f"agents[{index}] must be an object.")
        agent_id = str(agent.get("agent_id", "")).strip()
        if not agent_id:
            raise ValueError(f"agents[{index}].agent_id is required.")
        if agent_id in seen_agent_ids:
            raise ValueError(f"Duplicate agent_id: {agent_id}")
        seen_agent_ids.add(agent_id)
        provider_config = agent.get("provider_config")
        if provider_config is not None:
            if not isinstance(provider_config, dict):
                raise ValueError(f"agents[{index}].provider_config must be an object.")
            if str(agent.get("runtime_kind", "scripted")).strip().lower() != "llm":
                raise ValueError(f"provider_config is not allowed on scripted agent: {agent_id}")
            canonical_provider_config(provider_config, require_selection=True)
    if not isinstance(data["rule_engine"], dict):
        raise ValueError("'rule_engine' must be an object.")
    if not isinstance(data["negotiation"], dict):
        raise ValueError("'negotiation' must be an object.")
    if "llm_provider" in data:
        llm = data["llm_provider"]
        if not isinstance(llm, (dict, str)):
            raise ValueError("'llm_provider' must be a string or object when provided.")
        if isinstance(llm, dict):
            kind = llm.get("kind")
            if kind in {"openai_compatible", "real_openai_compatible"}:
                if not isinstance(llm.get("model"), str) or not str(llm.get("model", "")).strip():
                    raise ValueError("Real 'llm_provider' config requires non-empty 'model'.")
