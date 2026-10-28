from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class RuleEngine(ABC):
    """Game-agnostic rule engine contract.

    Implementations own deterministic game-specific legality and resolution logic.
    """

    @abstractmethod
    def initial_state(self, game_config: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def validate_orders(
        self, state_snapshot: dict[str, Any], submitted_orders: list[dict[str, Any]]
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def resolve_phase(
        self, state_snapshot: dict[str, Any], validated_orders: list[dict[str, Any]]
    ) -> dict[str, Any]:
        raise NotImplementedError
