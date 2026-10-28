from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentIdentity:
    worldview: str
    priorities: tuple[str, ...]
    alliance_philosophy: str
    communication_philosophy: str
    information_philosophy: str
    evidence_philosophy: str
    planning_horizon: str


@dataclass(frozen=True)
class AgentBlueprint:
    schema_version: str
    agent_id: str
    display_name: str
    runtime: str
    description: str
    identity: AgentIdentity
    notes: str
