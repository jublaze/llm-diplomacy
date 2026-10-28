from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from llm_diplomacy.map.model import MapDefinition

from .contracts import AgentContext, CleanupResult, OrderSet, ReflectionResult, TickResult
from .runtime import AgentRuntime
from .scripted_agents import BaseScriptedAgent, ScriptedAgentContext, build_scripted_agent


class ScriptedAgentRuntime(AgentRuntime):
    """Filesystem-backed deterministic runtime for non-LLM scripted agents."""

    def __init__(
        self,
        *,
        schema_version: str,
        runtime_root: str | Path,
        map_def: MapDefinition,
        seed: int,
    ) -> None:
        self.schema_version = schema_version
        self.runtime_root = Path(runtime_root)
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.map_def = map_def
        self.seed = seed
        self._agent_kinds: dict[str, str] = {}
        self._agents: dict[str, BaseScriptedAgent] = {}

    def register_agent(self, *, agent_id: str, kind: str) -> None:
        self._agent_kinds[agent_id] = kind
        self._agents[agent_id] = build_scripted_agent(kind, map_def=self.map_def, seed=self.seed)
        self._agent_dirs(agent_id)

    def negotiate_tick(self, context: AgentContext) -> TickResult:
        self._write_phase_input(context, method="negotiate_tick")
        agent = self._agent_for(context.agent_id)
        scripted_ctx = self._to_scripted_context(context)
        messages = agent.negotiate_tick(scripted_ctx)
        result = TickResult(
            schema_version=self.schema_version,
            ok=True,
            messages=messages,
            usage={"tokens": 0, "estimated_cost": 0.0, "calls": 1},
            explanation={
                "objectives_considered": ["maintain_channels", "gather_signals"],
                "perceived_threats": [],
                "candidate_plans": ["send_no_messages"],
                "final_rationale": "scripted baseline emits deterministic no-op negotiation.",
            },
        )
        self._write_phase_output(context, method="negotiate_tick", payload=asdict(result))
        return result

    def submit_orders(self, context: AgentContext) -> OrderSet:
        self._write_phase_input(context, method="submit_orders")
        agent = self._agent_for(context.agent_id)
        scripted_ctx = self._to_scripted_context(context)
        orders = agent.submit_orders(scripted_ctx)
        result = OrderSet(
            schema_version=self.schema_version,
            ok=True,
            orders=orders,
            usage={"tokens": 0, "estimated_cost": 0.0, "calls": 1},
            explanation={
                "objectives_considered": ["submit_valid_orders", "deterministic_behavior"],
                "perceived_threats": [],
                "candidate_plans": [self._agent_kinds.get(context.agent_id, "unknown")],
                "final_rationale": "scripted policy selected orders from phase context.",
            },
        )
        self._write_phase_output(context, method="submit_orders", payload=asdict(result))
        return result

    def reflect(self, context: AgentContext) -> ReflectionResult:
        self._write_phase_input(context, method="reflect")
        agent = self._agent_for(context.agent_id)
        scripted_ctx = self._to_scripted_context(context)
        reflection = agent.reflect(scripted_ctx)
        result = ReflectionResult(
            schema_version=self.schema_version,
            ok=True,
            reflection=reflection,
            usage={"tokens": 0, "estimated_cost": 0.0, "calls": 1},
            explanation={
                "objectives_considered": ["record_phase_summary"],
                "candidate_plans": ["append_structured_reflection"],
                "final_rationale": "scripted reflection is lightweight and deterministic.",
            },
        )
        self._write_phase_output(context, method="reflect", payload=asdict(result))
        return result

    def game_end_cleanup(self, context: AgentContext) -> CleanupResult:
        self._write_phase_input(context, method="game_end_cleanup")
        agent = self._agent_for(context.agent_id)
        scripted_ctx = self._to_scripted_context(context)
        summary = agent.game_end_cleanup(scripted_ctx)
        result = CleanupResult(
            schema_version=self.schema_version,
            ok=True,
            summary=summary,
            usage={"tokens": 0, "estimated_cost": 0.0, "calls": 1},
            explanation={
                "objectives_considered": ["emit_game_summary", "idempotent_cleanup_support"],
                "candidate_plans": ["structured_cleanup_payload"],
                "final_rationale": "scripted cleanup emits reproducible summary envelope.",
            },
        )
        self._write_phase_output(context, method="game_end_cleanup", payload=asdict(result))
        return result

    def _to_scripted_context(self, context: AgentContext) -> ScriptedAgentContext:
        return ScriptedAgentContext(
            schema_version=context.schema_version,
            agent_id=context.agent_id,
            country=context.country_assignment or "",
            phase_id=context.phase_id,
            state=context.visible_state,
        )

    def _agent_for(self, agent_id: str) -> BaseScriptedAgent:
        agent = self._agents.get(agent_id)
        if agent is None:
            raise ValueError(f"Agent not registered in runtime: {agent_id}")
        return agent

    def _agent_dirs(self, agent_id: str) -> tuple[Path, Path, Path]:
        base = self.runtime_root / agent_id / "runtime"
        inputs = base / "phase_inputs"
        outputs = base / "phase_outputs"
        errors = base / "errors"
        inputs.mkdir(parents=True, exist_ok=True)
        outputs.mkdir(parents=True, exist_ok=True)
        errors.mkdir(parents=True, exist_ok=True)
        return inputs, outputs, errors

    def _write_phase_input(self, context: AgentContext, *, method: str) -> None:
        inputs, _, _ = self._agent_dirs(context.agent_id)
        path = inputs / f"{context.phase_id}_{method}.json"
        payload = {
            "schema_version": self.schema_version,
            "method": method,
            "context": asdict(context),
        }
        path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")

    def _write_phase_output(self, context: AgentContext, *, method: str, payload: dict[str, Any]) -> None:
        _, outputs, _ = self._agent_dirs(context.agent_id)
        path = outputs / f"{context.phase_id}_{method}.json"
        out = {
            "schema_version": self.schema_version,
            "method": method,
            "result": payload,
        }
        path.write_text(json.dumps(out, sort_keys=True, indent=2), encoding="utf-8")
