from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import re

import yaml

from llm_diplomacy.agents.llm_provider import canonical_provider_config
from llm_diplomacy.guardrails.budget import BudgetConfig


_SAFE_PLAYER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class PopulationPlayer:
    agent_id: str
    blueprint: str
    provider_config: dict[str, Any] | None = None
    object_form: bool = False


@dataclass(frozen=True)
class PopulationExperimentConfig:
    schema_version: str
    experiment_id: str
    players: list[PopulationPlayer]
    runtime_kind: str
    provider_kind: str
    game_count: int
    max_year: int
    country_assignment: str
    seed: int
    observatory: bool
    summary: bool
    agent_state_mode: str
    dry_run: bool
    budget: dict[str, Any] | None
    raw: dict[str, Any]

    @property
    def agent_blueprints(self) -> list[str]:
        """Legacy view retained for callers that only understand blueprints."""
        return [player.blueprint for player in self.players]


def load_population_experiment_config(path: str | Path) -> PopulationExperimentConfig:
    p = Path(path)
    raw = _load_raw(p)
    return _to_config(raw, source=str(p))


def _load_raw(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(text)
    elif suffix in {".yaml", ".yml"}:
        payload = yaml.safe_load(text)
    else:
        raise ValueError(f"Unsupported population config format: {suffix}")
    if not isinstance(payload, dict):
        raise ValueError("Population config root must be object.")
    return payload


def _to_config(raw: dict[str, Any], *, source: str) -> PopulationExperimentConfig:
    required = ["schema_version", "experiment_id", "agents", "runtime", "games", "outputs"]
    missing = [field for field in required if field not in raw]
    if missing:
        raise ValueError(f"Missing required population config fields in {source}: {missing}")
    agents = raw.get("agents")
    runtime = raw.get("runtime")
    games = raw.get("games")
    outputs = raw.get("outputs")
    if not isinstance(agents, list):
        raise ValueError("'agents' must be a list of blueprint strings or player objects.")
    players = _normalize_players(agents)
    if not isinstance(runtime, dict):
        raise ValueError("'runtime' must be object.")
    if not isinstance(games, dict):
        raise ValueError("'games' must be object.")
    if not isinstance(outputs, dict):
        raise ValueError("'outputs' must be object.")
    budget = raw.get("budget")
    if budget is not None and not isinstance(budget, dict):
        raise ValueError("'budget' must be object when provided.")
    if isinstance(budget, dict):
        if not isinstance(budget.get("stop_policy", "stop_experiment"), str):
            raise ValueError("budget.stop_policy must be a string.")
        stop_policy = budget.get("stop_policy", "stop_experiment").strip().lower()
        if stop_policy not in {"stop_experiment", "stop_game_after_phase", "switch_to_fallback"}:
            raise ValueError(f"Unsupported budget stop_policy: {stop_policy}")
        budget_values = {key: budget[key] for key in (
            "max_provider_calls", "max_input_tokens", "max_output_tokens",
            "max_estimated_cost_usd", "safe_reserve_usd_per_call",
        ) if key in budget}
        BudgetConfig(**budget_values, stop_policy=stop_policy)
    return PopulationExperimentConfig(
        schema_version=str(raw.get("schema_version")),
        experiment_id=str(raw.get("experiment_id")),
        players=players,
        runtime_kind=str(runtime.get("kind", "baseline_llm")),
        provider_kind=str(runtime.get("provider", "fake")),
        game_count=int(games.get("count", 1)),
        max_year=int(games.get("max_year", 1901)),
        country_assignment=str(games.get("country_assignment", "round_robin")),
        seed=int(games.get("seed", 0)),
        observatory=bool(outputs.get("observatory", True)),
        summary=bool(outputs.get("summary", True)),
        agent_state_mode=str(raw.get("agent_state_mode", "fresh")).strip().lower(),
        dry_run=bool(raw.get("dry_run", False)),
        budget=dict(budget) if isinstance(budget, dict) else None,
        raw=raw,
    )


def _normalize_players(entries: list[Any]) -> list[PopulationPlayer]:
    players: list[PopulationPlayer] = []
    ids: set[str] = set()
    for index, entry in enumerate(entries):
        if isinstance(entry, str):
            blueprint = entry.strip()
            if not blueprint:
                raise ValueError(f"agents[{index}] blueprint must be non-empty.")
            # String entries retain the source blueprint id; uniqueness is checked after load.
            players.append(PopulationPlayer(agent_id="", blueprint=blueprint))
            continue
        if not isinstance(entry, dict):
            raise ValueError(f"agents[{index}] must be a string or object.")
        unknown = set(entry) - {"agent_id", "blueprint", "provider_config"}
        if unknown:
            raise ValueError(f"agents[{index}] has unsupported fields: {sorted(unknown)}")
        agent_id = str(entry.get("agent_id", "")).strip()
        blueprint = str(entry.get("blueprint", "")).strip()
        if not agent_id or not _SAFE_PLAYER_ID.fullmatch(agent_id):
            raise ValueError(f"agents[{index}].agent_id is missing or unsafe.")
        if not blueprint:
            raise ValueError(f"agents[{index}].blueprint must be non-empty.")
        provider_config = entry.get("provider_config")
        if provider_config is not None and not isinstance(provider_config, dict):
            raise ValueError(f"agents[{index}].provider_config must be an object.")
        if isinstance(provider_config, dict):
            # Resolve at load time so unknown profiles and unsafe configs fail before folios.
            canonical_provider_config(provider_config, require_selection=True)
            provider_config = dict(provider_config)
        if agent_id in ids:
            raise ValueError(f"Duplicate player agent_id: {agent_id}")
        ids.add(agent_id)
        players.append(PopulationPlayer(agent_id=agent_id, blueprint=blueprint, provider_config=provider_config, object_form=True))
    return players
