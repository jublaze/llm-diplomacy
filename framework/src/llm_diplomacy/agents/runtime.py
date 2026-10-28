from __future__ import annotations

from abc import ABC, abstractmethod

from .contracts import AgentContext, CleanupResult, OrderSet, ReflectionResult, TickResult


class AgentRuntime(ABC):
    """Invocation boundary for opaque agents."""

    @abstractmethod
    def negotiate_tick(self, context: AgentContext) -> TickResult:
        raise NotImplementedError

    @abstractmethod
    def submit_orders(self, context: AgentContext) -> OrderSet:
        raise NotImplementedError

    @abstractmethod
    def reflect(self, context: AgentContext) -> ReflectionResult:
        raise NotImplementedError

    @abstractmethod
    def game_end_cleanup(self, context: AgentContext) -> CleanupResult:
        raise NotImplementedError
