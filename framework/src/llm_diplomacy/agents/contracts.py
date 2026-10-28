from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentContext:
    schema_version: str
    experiment_id: str
    game_id: str
    phase_id: str
    agent_id: str
    country_assignment: str | None = None
    visible_state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TickResult:
    schema_version: str
    ok: bool
    messages: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    explanation: dict[str, Any] | None = None


@dataclass(frozen=True)
class OrderSet:
    schema_version: str
    ok: bool
    orders: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    explanation: dict[str, Any] | None = None


@dataclass(frozen=True)
class ReflectionResult:
    schema_version: str
    ok: bool
    reflection: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    explanation: dict[str, Any] | None = None


@dataclass(frozen=True)
class CleanupResult:
    schema_version: str
    ok: bool
    summary: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    explanation: dict[str, Any] | None = None
