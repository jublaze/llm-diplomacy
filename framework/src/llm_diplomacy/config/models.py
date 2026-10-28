from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExperimentConfig:
    schema_version: str
    experiment_id: str
    games: int
    agents: list[dict[str, Any]]
    rule_engine: dict[str, Any]
    negotiation: dict[str, Any]
    seed: int
    raw: dict[str, Any]
