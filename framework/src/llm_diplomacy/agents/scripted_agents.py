from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Any

from llm_diplomacy.map.model import MapDefinition


@dataclass(frozen=True)
class ScriptedAgentContext:
    schema_version: str
    agent_id: str
    country: str
    phase_id: str
    state: dict[str, Any]


class BaseScriptedAgent:
    def negotiate_tick(self, context: ScriptedAgentContext) -> list[dict[str, Any]]:
        return []

    def submit_orders(self, context: ScriptedAgentContext) -> list[dict[str, Any]]:
        raise NotImplementedError

    def reflect(self, context: ScriptedAgentContext) -> dict[str, Any]:
        return {
            "schema_version": context.schema_version,
            "agent_id": context.agent_id,
            "phase_id": context.phase_id,
            "note": "scripted_reflection",
        }

    def game_end_cleanup(self, context: ScriptedAgentContext) -> dict[str, Any]:
        return {
            "schema_version": context.schema_version,
            "agent_id": context.agent_id,
            "game_id": context.state.get("game_id", ""),
            "summary": {"result": "completed"},
        }


class HoldAgent(BaseScriptedAgent):
    def submit_orders(self, context: ScriptedAgentContext) -> list[dict[str, Any]]:
        phase = str(context.state.get("phase", "movement"))
        units = _country_units(context.state, context.country)
        if phase == "movement":
            return [
                _order(context, unit_id=unit["unit_id"], order_type="HOLD", payload={"at": unit["province"]})
                for unit in sorted(units, key=lambda row: str(row["unit_id"]))
            ]
        if phase == "retreat":
            return []
        if phase == "adjustment":
            return _deterministic_disbands_if_required(context, units)
        return []


class RandomLegalAgent(BaseScriptedAgent):
    def __init__(self, *, map_def: MapDefinition, seed: int) -> None:
        self.map_def = map_def
        self.seed = seed
        self._province_by_id = {province.id: province for province in map_def.provinces}

    def submit_orders(self, context: ScriptedAgentContext) -> list[dict[str, Any]]:
        phase = str(context.state.get("phase", "movement"))
        units = sorted(_country_units(context.state, context.country), key=lambda row: str(row["unit_id"]))
        rng = random.Random(_stable_seed(self.seed, context.agent_id, context.country, context.phase_id))
        if phase == "movement":
            out: list[dict[str, Any]] = []
            for unit in units:
                options = self._movement_options(unit)
                pick = options[rng.randrange(len(options))]
                out.append(_order(context, unit_id=str(unit["unit_id"]), order_type=pick["type"], payload=pick["payload"]))
            return out
        if phase == "retreat":
            out = []
            for unit in units:
                if not _needs_retreat(unit):
                    continue
                legal = self._movement_destinations(unit)
                if not legal:
                    continue
                to_node = legal[rng.randrange(len(legal))]
                out.append(_order(context, unit_id=str(unit["unit_id"]), order_type="RETREAT", payload={"to": to_node}))
            return out
        if phase == "adjustment":
            return _deterministic_disbands_if_required(context, units)
        return []

    def _movement_options(self, unit: dict[str, Any]) -> list[dict[str, Any]]:
        options = [{"type": "HOLD", "payload": {"at": unit["province"]}}]
        for destination in self._movement_destinations(unit):
            payload: dict[str, Any] = {"from": unit["province"], "to": destination}
            if unit["type"] == "A":
                payload["via_convoy"] = False
            options.append({"type": "MOVE", "payload": payload})
        return options

    def _movement_destinations(self, unit: dict[str, Any]) -> list[str]:
        province = self._province_by_id.get(str(unit["province"]))
        if province is None:
            return []
        if unit.get("type") == "A":
            return sorted(province.army_adjacency)
        return sorted(province.fleet_adjacency)


class GreedyCenterAgent(BaseScriptedAgent):
    def __init__(self, *, map_def: MapDefinition, seed: int) -> None:
        self.map_def = map_def
        self.seed = seed
        self._province_by_id = {province.id: province for province in map_def.provinces}
        self._all_centers = {province.id for province in map_def.provinces if province.supply_center}

    def submit_orders(self, context: ScriptedAgentContext) -> list[dict[str, Any]]:
        phase = str(context.state.get("phase", "movement"))
        units = sorted(_country_units(context.state, context.country), key=lambda row: str(row["unit_id"]))
        if phase == "movement":
            out: list[dict[str, Any]] = []
            for unit in units:
                preferred = self._preferred_destination(context.state, unit, context.country)
                if preferred is None:
                    out.append(_order(context, unit_id=unit["unit_id"], order_type="HOLD", payload={"at": unit["province"]}))
                    continue
                payload: dict[str, Any] = {"from": unit["province"], "to": preferred}
                if unit.get("type") == "A":
                    payload["via_convoy"] = False
                out.append(_order(context, unit_id=unit["unit_id"], order_type="MOVE", payload=payload))
            return out
        if phase == "retreat":
            return []
        if phase == "adjustment":
            return _deterministic_disbands_if_required(context, units)
        return []

    def _preferred_destination(
        self,
        state: dict[str, Any],
        unit: dict[str, Any],
        country: str,
    ) -> str | None:
        options = self._adjacent(unit)
        if not options:
            return None
        ownership = state.get("supply_ownership", {})
        unowned_or_enemy = sorted(
            [dest for dest in options if dest in self._all_centers and ownership.get(dest) != country]
        )
        if unowned_or_enemy:
            return unowned_or_enemy[0]
        return sorted(options)[0]

    def _adjacent(self, unit: dict[str, Any]) -> list[str]:
        province = self._province_by_id.get(str(unit["province"]))
        if province is None:
            return []
        if unit.get("type") == "A":
            return list(province.army_adjacency)
        return list(province.fleet_adjacency)


def build_scripted_agent(kind: str, *, map_def: MapDefinition, seed: int) -> BaseScriptedAgent:
    normalized = kind.strip().lower()
    if normalized == "random_legal":
        return RandomLegalAgent(map_def=map_def, seed=seed)
    if normalized == "hold":
        return HoldAgent()
    if normalized == "greedy_center":
        return GreedyCenterAgent(map_def=map_def, seed=seed)
    raise ValueError(f"Unknown scripted agent kind: {kind}")


def _order(
    context: ScriptedAgentContext,
    *,
    unit_id: str,
    order_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": context.schema_version,
        "order_id": f"{context.phase_id}_{context.country}_{unit_id}_{order_type.lower()}",
        "unit_ref": unit_id,
        "power": context.country,
        "type": order_type,
        "payload": payload,
    }


def _country_units(state: dict[str, Any], country: str) -> list[dict[str, Any]]:
    return [unit for unit in state.get("units", []) if unit.get("owner") == country]


def _needs_retreat(unit: dict[str, Any]) -> bool:
    return bool(unit.get("must_retreat")) or unit.get("status") == "dislodged"


def _deterministic_disbands_if_required(
    context: ScriptedAgentContext, units: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    active = [unit for unit in units if unit.get("status") == "active"]
    owned = sorted(
        province
        for province, owner in (context.state.get("supply_ownership", {}) or {}).items()
        if owner == context.country
    )
    delta = len(owned) - len(active)
    if delta >= 0:
        return []
    to_disband = sorted(active, key=lambda row: (str(row.get("province")), str(row.get("unit_id"))), reverse=True)[: abs(delta)]
    return [
        _order(
            context,
            unit_id=str(unit["unit_id"]),
            order_type="DISBAND",
            payload={"unit_ref": str(unit["unit_id"])},
        )
        for unit in to_disband
    ]


def _stable_seed(seed: int, agent_id: str, country: str, phase_id: str) -> int:
    digest = hashlib.sha256(f"{seed}|{agent_id}|{country}|{phase_id}".encode("utf-8")).hexdigest()
    return int(digest[:16], 16)
