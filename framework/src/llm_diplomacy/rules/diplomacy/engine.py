from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from llm_diplomacy.map.loader import load_standard_map
from llm_diplomacy.map.model import MapDefinition
from llm_diplomacy.rules.base import RuleEngine


@dataclass(frozen=True)
class _ResolvedOrder:
    unit_id: str
    order_id: str
    owner: str
    unit_type: str
    order_type: str
    payload: dict[str, Any]
    from_province: str
    to_node: str | None = None


class DiplomacyRuleEngine(RuleEngine):
    """Drop 6 baseline: movement/support/convoys + retreat phase."""

    def __init__(self, schema_version: str = "1.0") -> None:
        self.schema_version = schema_version
        self.map_def: MapDefinition = load_standard_map()
        self.province_by_id = {province.id: province for province in self.map_def.provinces}
        self.coast_by_id = {coast.id: coast for coast in self.map_def.coasts}
        self.convoy_passable_seas = set(self.map_def.convoy_passable_seas)

    def initial_state(self, game_config: dict[str, Any]) -> dict[str, Any]:
        state = deepcopy(self.map_def.initial_state)
        units = state.get("units", [])
        if not isinstance(units, list):
            return state
        created_year = int(state.get("year", 1901))
        created_phase = f"{created_year}_{str(state.get('season', 'spring'))}_{str(state.get('phase', 'movement'))}"
        for index, unit in enumerate(units, start=1):
            if not isinstance(unit, dict):
                continue
            unit["unit_id"] = f"unit_{index:04d}"
            metadata = unit.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                unit["metadata"] = metadata
            metadata.setdefault("origin_province", unit.get("province"))
            metadata.setdefault("created_year", created_year)
            metadata.setdefault("created_phase", created_phase)
        return state

    def validate_orders(
        self, state_snapshot: dict[str, Any], submitted_orders: list[dict[str, Any]]
    ) -> dict[str, Any]:
        phase = str(state_snapshot.get("phase", "movement"))
        if phase == "adjustment":
            return self._validate_adjustment_orders(state_snapshot, submitted_orders)
        participant_units = self._participant_units(state_snapshot, phase)
        unit_by_id = {unit["unit_id"]: unit for unit in participant_units}

        submitted_sorted = sorted(submitted_orders, key=lambda row: row.get("order_id", ""))
        chosen_per_unit: dict[str, dict[str, Any]] = {}
        for order in submitted_sorted:
            unit_ref = order.get("unit_ref")
            if isinstance(unit_ref, str) and unit_ref in unit_by_id and unit_ref not in chosen_per_unit:
                chosen_per_unit[unit_ref] = order

        validated: list[dict[str, Any]] = []
        for unit in participant_units:
            submitted = chosen_per_unit.get(unit["unit_id"])
            if submitted is None:
                if phase == "retreat":
                    validated.append(
                        {
                            "schema_version": self.schema_version,
                            "order_id": f"missing_{unit['unit_id']}",
                            "unit_id": unit["unit_id"],
                            "validation": "rejected",
                            "reason": "missing_retreat_order",
                            "effective_order": None,
                        }
                    )
                else:
                    effective = self._hold_order(unit, order_id=f"auto_hold_{unit['unit_id']}")
                    validated.append(
                        {
                            "schema_version": self.schema_version,
                            "order_id": effective["order_id"],
                            "unit_id": unit["unit_id"],
                            "validation": "substituted",
                            "reason": "missing_order",
                            "effective_order": effective,
                        }
                    )
                continue
            validated.append(self._validate_single_order(unit, submitted, phase))

        if phase == "movement":
            resolved = self._extract_resolved_orders(validated, unit_by_id)
            fleets = {u["province"] for u in participant_units if u["type"] == "F" and u["province"] in self.convoy_passable_seas}
            for index, row in enumerate(validated):
                order = resolved.get(row["unit_id"])
                if order and self._move_requires_convoy(order) and self._find_convoy_route(order.from_province, self._node_to_province(order.to_node), fleets) is None:
                    unit = unit_by_id[row["unit_id"]]
                    validated[index] = self._validated_row(unit, row["order_id"], "substituted", "no_convoy_route", self._hold_order(unit, row["order_id"]))

        return {
            "schema_version": self.schema_version,
            "phase": phase,
            "validated_orders": sorted(validated, key=lambda row: row["unit_id"]),
        }

    def resolve_phase(
        self, state_snapshot: dict[str, Any], validated_orders: list[dict[str, Any]] | dict[str, Any]
    ) -> dict[str, Any]:
        phase = str(state_snapshot.get("phase", "movement"))
        validated_list = (
            validated_orders["validated_orders"]
            if isinstance(validated_orders, dict)
            else validated_orders
        )
        if phase == "retreat":
            return self._resolve_retreat_phase(state_snapshot, validated_list)
        if phase == "adjustment":
            return self._resolve_adjustment_phase(state_snapshot, validated_list)

        units = sorted(
            [u for u in state_snapshot.get("units", []) if u.get("status", "active") == "active"],
            key=lambda row: row["unit_id"],
        )
        unit_by_id = {unit["unit_id"]: unit for unit in units}
        unit_at_province = {unit["province"]: unit for unit in units}
        resolved = self._extract_resolved_orders(validated_list, unit_by_id)

        convoy_possible, convoy_routes = self._compute_convoy_possibility(
            resolved, excluded_convoy_fleets=set()
        )
        def resolve_moves(excluded: set[str]) -> dict[str, Any]:
            support_cut = self._compute_support_cut_flags(
                resolved, convoy_possible, excluded_attacker_unit_ids=excluded
            )
            pass_one = self._resolve_support_aware(
                resolved=resolved,
                unit_at_province=unit_at_province,
                support_cut=support_cut,
                excluded_supporters=set(),
                convoy_possible=convoy_possible,
                excluded_attacker_unit_ids=excluded,
            )
            pass_two = self._resolve_support_aware(
                resolved=resolved,
                unit_at_province=unit_at_province,
                support_cut=support_cut,
                excluded_supporters=pass_one["dislodged_units"],
                convoy_possible=convoy_possible,
                excluded_attacker_unit_ids=excluded,
            )

            for _ in range(len(resolved)):
                suppressed = {
                    uid for uid in pass_two["dislodged_units"]
                    if resolved[uid].order_type == "MOVE"
                    and self._node_to_province(resolved[uid].to_node) == pass_two["dislodged_from"].get(uid)
                    and not self._move_requires_convoy(resolved[uid])
                    and not any(self._move_requires_convoy(o) for o in resolved.values()
                                if o.from_province == pass_two["dislodged_from"].get(uid) and o.unit_id in pass_two["success_move_unit_ids"])
                }
                updated = self._resolve_support_aware(
                    resolved=resolved, unit_at_province=unit_at_province, support_cut=support_cut,
                    excluded_supporters=pass_two["dislodged_units"], convoy_possible=convoy_possible,
                    excluded_attacker_unit_ids=suppressed | excluded,
                )
                if updated == pass_two:
                    break
                pass_two = updated

            return pass_two

        # Each army's route is evaluated without that army's own effects.
        # Cyclic availability fails only the moves that vary in the cycle.
        failed_paradox: set[str] = set()
        seen: list[dict[str, bool]] = []
        while True:
            if convoy_possible in seen:
                cycle = seen[seen.index(convoy_possible):]
                failed_paradox.update(
                    uid for uid in convoy_possible
                    if len({state[uid] for state in cycle}) > 1
                )
                convoy_possible = {
                    uid: possible and uid not in failed_paradox
                    for uid, possible in convoy_possible.items()
                }
                seen = []
            seen.append(dict(convoy_possible))
            new_convoy_possible: dict[str, bool] = {}
            new_convoy_routes: dict[str, list[list[str]]] = {}
            for army_id in convoy_possible:
                independent = resolve_moves({army_id})
                possible, routes = self._compute_convoy_possibility(
                    resolved, excluded_convoy_fleets=independent["dislodged_units"]
                )
                new_convoy_possible[army_id] = possible[army_id] and army_id not in failed_paradox
                new_convoy_routes[army_id] = routes[army_id] if new_convoy_possible[army_id] else []
            convoy_routes = new_convoy_routes
            if new_convoy_possible == convoy_possible:
                break
            convoy_possible = new_convoy_possible
        pass_two = resolve_moves(set())

        success_move_unit_ids: set[str] = pass_two["success_move_unit_ids"]
        dislodged_units: set[str] = pass_two["dislodged_units"]
        support_outcomes: list[dict[str, Any]] = pass_two["support_outcomes"]

        output_units = deepcopy(state_snapshot.get("units", []))
        output_by_id = {unit["unit_id"]: unit for unit in output_units}
        movement_outcomes: list[dict[str, Any]] = []

        for unit_id in sorted(success_move_unit_ids):
            order = resolved[unit_id]
            destination = self._node_to_province(order.to_node or order.from_province)
            moving_unit = output_by_id[unit_id]
            source = moving_unit["province"]
            moving_unit["province"] = destination
            moving_unit["status"] = "active"
            moving_unit["must_retreat"] = False
            moving_unit["dislodged_from"] = None
            if moving_unit.get("type") == "F":
                moving_unit["coast"] = order.to_node if order.to_node in self.coast_by_id else None
            movement_outcomes.append(
                {
                    "schema_version": self.schema_version,
                    "unit_id": unit_id,
                    "result": "moved",
                    "from": source,
                    "to": destination,
                }
            )

        for unit in output_units:
            unit_id = unit["unit_id"]
            if unit_id in dislodged_units:
                unit["status"] = "dislodged"
                unit["must_retreat"] = True
                unit["dislodged_from"] = pass_two["dislodged_from"].get(unit_id)
                unit["dislodged_by_convoy"] = any(
                    uid in success_move_unit_ids and resolved[uid].from_province == unit["dislodged_from"]
                    and self._move_requires_convoy(resolved[uid]) for uid in resolved
                )
                movement_outcomes.append(
                    {
                        "schema_version": self.schema_version,
                        "unit_id": unit_id,
                        "result": "dislodged",
                        "from": unit["province"],
                        "to": unit["province"],
                    }
                )
            elif unit_id not in success_move_unit_ids:
                unit["must_retreat"] = False
                unit["dislodged_from"] = None
                if unit.get("status") != "destroyed":
                    unit["status"] = "active"
                movement_outcomes.append(
                    {
                        "schema_version": self.schema_version,
                        "unit_id": unit_id,
                        "result": "held",
                        "from": unit["province"],
                        "to": unit["province"],
                    }
                )

        convoy_outcomes: list[dict[str, Any]] = []
        for order in sorted(resolved.values(), key=lambda row: row.unit_id):
            if order.order_type != "MOVE" or not self._move_requires_convoy(order):
                continue
            route_list = convoy_routes.get(order.unit_id, [])
            convoy_outcomes.append(
                {
                    "schema_version": self.schema_version,
                    "army_unit_id": order.unit_id,
                    "status": "possible" if convoy_possible.get(order.unit_id, False) else "impossible",
                    "routes": route_list,
                }
            )

        movement_outcomes = sorted(movement_outcomes, key=lambda row: row["unit_id"])
        support_outcomes = sorted(support_outcomes, key=lambda row: row["unit_id"])
        convoy_outcomes = sorted(convoy_outcomes, key=lambda row: row["army_unit_id"])

        output_state = deepcopy(state_snapshot)
        output_state["schema_version"] = self.schema_version
        output_state["units"] = output_units
        output_state["standoff_provinces"] = sorted(pass_two["standoff_provinces"])

        return {
            "schema_version": self.schema_version,
            "phase": "movement",
            "validated_orders": sorted(validated_list, key=lambda row: row["unit_id"]),
            "support_outcomes": support_outcomes,
            "convoy_outcomes": convoy_outcomes,
            "movement_outcomes": movement_outcomes,
            "adjustment_outcomes": [],
            "output_state": output_state,
        }

    def _resolve_retreat_phase(
        self, state_snapshot: dict[str, Any], validated_list: list[dict[str, Any]]
    ) -> dict[str, Any]:
        retreat_units = sorted(
            [u for u in state_snapshot.get("units", []) if self._needs_retreat(u)],
            key=lambda row: row["unit_id"],
        )
        validated_by_unit = {row["unit_id"]: row for row in validated_list}
        active_occupancy = {
            unit["province"]
            for unit in state_snapshot.get("units", [])
            if unit.get("status", "active") == "active"
        }
        standoff_provinces = set(state_snapshot.get("standoff_provinces", []))

        accepted_destinations: dict[str, str] = {}
        for unit in retreat_units:
            row = validated_by_unit.get(unit["unit_id"])
            if row is None or row.get("validation") != "accepted":
                continue
            effective = row.get("effective_order")
            if not isinstance(effective, dict):
                continue
            payload = effective.get("payload")
            if not isinstance(payload, dict):
                continue
            to_node = payload.get("to")
            if not isinstance(to_node, str):
                continue
            destination = self._node_to_province(to_node)
            if destination in active_occupancy:
                continue
            if destination in standoff_provinces:
                continue
            if destination == unit.get("dislodged_from") and not unit.get("dislodged_by_convoy", False):
                continue
            accepted_destinations[unit["unit_id"]] = to_node

        destination_counts: dict[str, int] = {}
        for to_node in accepted_destinations.values():
            destination = self._node_to_province(to_node)
            destination_counts[destination] = destination_counts.get(destination, 0) + 1

        output_units = deepcopy(state_snapshot.get("units", []))
        out_by_id = {unit["unit_id"]: unit for unit in output_units}
        retreat_outcomes: list[dict[str, Any]] = []

        for unit in retreat_units:
            unit_id = unit["unit_id"]
            to_node = accepted_destinations.get(unit_id)
            if to_node is None:
                out_by_id[unit_id]["status"] = "destroyed"
                out_by_id[unit_id]["must_retreat"] = False
                out_by_id[unit_id]["dislodged_from"] = None
                retreat_outcomes.append(
                    {
                        "schema_version": self.schema_version,
                        "unit_id": unit_id,
                        "result": "destroyed",
                        "from": unit["province"],
                        "to": None,
                    }
                )
                continue

            destination = self._node_to_province(to_node)
            if destination_counts.get(destination, 0) > 1:
                out_by_id[unit_id]["status"] = "destroyed"
                out_by_id[unit_id]["must_retreat"] = False
                out_by_id[unit_id]["dislodged_from"] = None
                retreat_outcomes.append(
                    {
                        "schema_version": self.schema_version,
                        "unit_id": unit_id,
                        "result": "destroyed",
                        "from": unit["province"],
                        "to": destination,
                    }
                )
                continue

            moved = out_by_id[unit_id]
            moved["province"] = destination
            moved["status"] = "active"
            moved["must_retreat"] = False
            moved["dislodged_from"] = None
            if moved.get("type") == "F":
                moved["coast"] = to_node if to_node in self.coast_by_id else None
            retreat_outcomes.append(
                {
                    "schema_version": self.schema_version,
                    "unit_id": unit_id,
                    "result": "retreated",
                    "from": unit["province"],
                    "to": destination,
                }
            )

        output_state = deepcopy(state_snapshot)
        output_state["schema_version"] = self.schema_version
        output_state["units"] = output_units
        output_state["standoff_provinces"] = sorted(standoff_provinces)

        retreat_outcomes = sorted(retreat_outcomes, key=lambda row: row["unit_id"])
        return {
            "schema_version": self.schema_version,
            "phase": "retreat",
            "validated_orders": sorted(validated_list, key=lambda row: row["unit_id"]),
            "support_outcomes": [],
            "convoy_outcomes": [],
            "movement_outcomes": [],
            "retreat_outcomes": retreat_outcomes,
            "adjustment_outcomes": [],
            "output_state": output_state,
        }

    def _validate_adjustment_orders(
        self, state_snapshot: dict[str, Any], submitted_orders: list[dict[str, Any]]
    ) -> dict[str, Any]:
        validated: list[dict[str, Any]] = []
        for order in sorted(submitted_orders, key=lambda row: row.get("order_id", "")):
            order_id = str(order.get("order_id", "missing_order_id"))
            power = str(order.get("power", ""))
            order_type = str(order.get("type", "")).upper()
            payload = order.get("payload") if isinstance(order.get("payload"), dict) else {}

            if order_type == "BUILD":
                unit_type = payload.get("unit_type")
                province = payload.get("province")
                coast = payload.get("coast")
                if unit_type not in {"A", "F"} or not isinstance(province, str):
                    validated.append(
                        self._validated_adjustment_row(
                            order_id, power, "rejected", "malformed_build", None
                        )
                    )
                    continue
                effective = {
                    "schema_version": self.schema_version,
                    "order_id": order_id,
                    "power": power,
                    "type": "BUILD",
                    "payload": {
                        "unit_type": unit_type,
                        "province": province,
                        "coast": coast if isinstance(coast, str) else None,
                    },
                }
                validated.append(self._validated_adjustment_row(order_id, power, "accepted", None, effective))
                continue

            if order_type == "DISBAND":
                unit_ref = payload.get("unit_ref")
                if not isinstance(unit_ref, str):
                    validated.append(
                        self._validated_adjustment_row(
                            order_id, power, "rejected", "malformed_disband", None
                        )
                    )
                    continue
                effective = {
                    "schema_version": self.schema_version,
                    "order_id": order_id,
                    "power": power,
                    "type": "DISBAND",
                    "payload": {"unit_ref": unit_ref},
                }
                validated.append(self._validated_adjustment_row(order_id, power, "accepted", None, effective))
                continue

            validated.append(
                self._validated_adjustment_row(order_id, power, "rejected", "unknown_adjustment_order", None)
            )

        return {
            "schema_version": self.schema_version,
            "phase": "adjustment",
            "validated_orders": validated,
        }

    def _resolve_adjustment_phase(
        self, state_snapshot: dict[str, Any], validated_list: list[dict[str, Any]]
    ) -> dict[str, Any]:
        output_units = deepcopy(state_snapshot.get("units", []))
        out_by_id = {unit["unit_id"]: unit for unit in output_units}
        adjustment_outcomes: list[dict[str, Any]] = []

        accepted_orders = [
            row["effective_order"]
            for row in validated_list
            if row.get("validation") == "accepted" and isinstance(row.get("effective_order"), dict)
        ]

        powers = sorted(
            set(unit.get("owner", "") for unit in output_units if isinstance(unit.get("owner"), str))
            | set(order.get("power", "") for order in accepted_orders if isinstance(order.get("power"), str))
        )
        home_centers = self._home_centers_by_power()

        for power in powers:
            active_units = [
                unit for unit in output_units if unit.get("owner") == power and unit.get("status") == "active"
            ]
            owned_centers = [
                province_id
                for province_id, owner in (state_snapshot.get("supply_ownership", {}) or {}).items()
                if owner == power
            ]
            delta = len(owned_centers) - len(active_units)
            power_orders = [order for order in accepted_orders if order.get("power") == power]

            if delta > 0:
                builds = [order for order in power_orders if order.get("type") == "BUILD"]
                built = 0
                for idx, order in enumerate(builds, start=1):
                    if built >= delta:
                        break
                    payload = order.get("payload", {})
                    province_id = str(payload.get("province"))
                    unit_type = str(payload.get("unit_type"))
                    coast = payload.get("coast")
                    if not self._can_build(
                        power=power,
                        unit_type=unit_type,
                        province_id=province_id,
                        coast=coast if isinstance(coast, str) else None,
                        output_units=output_units,
                        owned_centers=owned_centers,
                        home_centers=home_centers.get(power, []),
                    ):
                        adjustment_outcomes.append(
                            {
                                "schema_version": self.schema_version,
                                "power": power,
                                "order_id": order["order_id"],
                                "result": "rejected",
                                "reason": "illegal_build",
                            }
                        )
                        continue
                    unit_id = self._new_unit_id(output_units, idx)
                    new_unit = {
                        "schema_version": self.schema_version,
                        "unit_id": unit_id,
                        "owner": power,
                        "type": unit_type,
                        "province": province_id,
                        "coast": coast if isinstance(coast, str) else None,
                        "status": "active",
                        "must_retreat": False,
                        "dislodged_from": None,
                        "metadata": {
                            "origin_province": province_id,
                            "created_year": int(state_snapshot.get("year", 1901)),
                            "created_phase": self._phase_id_for_state(state_snapshot),
                        },
                    }
                    built += 1
                    output_units.append(new_unit)
                    out_by_id[unit_id] = new_unit
                    adjustment_outcomes.append(
                        {
                            "schema_version": self.schema_version,
                            "power": power,
                            "order_id": order["order_id"],
                            "result": "built",
                            "unit_id": unit_id,
                            "province": province_id,
                        }
                    )

            if delta < 0:
                needed = abs(delta)
                disband_orders = [order for order in power_orders if order.get("type") == "DISBAND"]
                chosen: list[str] = []
                for order in disband_orders:
                    unit_ref = str(order.get("payload", {}).get("unit_ref", ""))
                    unit = out_by_id.get(unit_ref)
                    if (
                        unit is None
                        or unit.get("owner") != power
                        or unit.get("status") != "active"
                        or unit_ref in chosen
                    ):
                        adjustment_outcomes.append(
                            {
                                "schema_version": self.schema_version,
                                "power": power,
                                "order_id": order["order_id"],
                                "result": "rejected",
                                "reason": "illegal_disband",
                            }
                        )
                        continue
                    chosen.append(unit_ref)
                    adjustment_outcomes.append({"schema_version": self.schema_version, "power": power,
                                                "order_id": order["order_id"], "result": "disbanded", "unit_id": unit_ref})
                    if len(chosen) == needed:
                        break

                if len(chosen) < needed:
                    remaining_candidates = [
                        unit
                        for unit in output_units
                        if unit.get("owner") == power
                        and unit.get("status") == "active"
                        and unit.get("unit_id") not in chosen
                    ]
                    auto = self._auto_disband_selection(
                        remaining_candidates,
                        owned_home_centers=[
                            center for center in home_centers.get(power, []) if center in owned_centers
                        ],
                        count=needed - len(chosen),
                    )
                    for unit_id in auto:
                        chosen.append(unit_id)
                        adjustment_outcomes.append(
                            {
                                "schema_version": self.schema_version,
                                "power": power,
                                "order_id": None,
                                "result": "auto_disbanded",
                                "unit_id": unit_id,
                            }
                        )

                for unit_id in chosen[:needed]:
                    unit = out_by_id[unit_id]
                    unit["status"] = "destroyed"
                    unit["must_retreat"] = False
                    unit["dislodged_from"] = None
                    adjustment_outcomes.append(
                        {
                            "schema_version": self.schema_version,
                            "power": power,
                            "order_id": None,
                            "result": "disbanded",
                            "unit_id": unit_id,
                        }
                    )

        output_state = deepcopy(state_snapshot)
        output_state["schema_version"] = self.schema_version
        output_state["units"] = output_units

        return {
            "schema_version": self.schema_version,
            "phase": "adjustment",
            "validated_orders": validated_list,
            "support_outcomes": [],
            "convoy_outcomes": [],
            "movement_outcomes": [],
            "retreat_outcomes": [],
            "adjustment_outcomes": sorted(
                adjustment_outcomes, key=lambda row: (str(row.get("power")), str(row.get("unit_id", "")), str(row.get("result")))
            ),
            "output_state": output_state,
        }

    def _resolve_support_aware(
        self,
        *,
        resolved: dict[str, _ResolvedOrder],
        unit_at_province: dict[str, dict[str, Any]],
        support_cut: dict[str, bool],
        excluded_supporters: set[str],
        convoy_possible: dict[str, bool],
        excluded_attacker_unit_ids: set[str],
    ) -> dict[str, Any]:
        hold_support, move_support, support_outcomes = self._compute_support_strength(
            resolved, unit_at_province, support_cut, excluded_supporters, convoy_possible
        )

        attacks_by_destination = self._build_attacks_by_destination(
            resolved,
            convoy_possible,
            excluded_attacker_unit_ids,
        )
        candidates: dict[str, _ResolvedOrder] = {}
        strengths = {
            row.unit_id: 1 + move_support.get((row.from_province, destination), 0)
            for destination, attackers in attacks_by_destination.items() for row in attackers
        }
        standoff_provinces: set[str] = set()
        for destination, attackers in attacks_by_destination.items():
            best = max(strengths[row.unit_id] for row in attackers)
            winners = [row for row in attackers if strengths[row.unit_id] == best]
            if len(winners) == 1:
                candidates[winners[0].unit_id] = winners[0]
            else:
                standoff_provinces.add(destination)

        effective_supporters = {
            row["unit_id"] for row in support_outcomes if row.get("status") == "effective"
        }

        def dislodging_strength(attacker, defender):
            destination = self._node_to_province(attacker.to_node)
            forbidden = sum(
                1 for supporter_id in effective_supporters
                if (supporter := resolved[supporter_id]).owner == defender["owner"]
                and supporter.order_type == "SUPPORT_MOVE"
                and supporter.payload.get("supported_from") == attacker.from_province
                and self._node_to_province(str(supporter.payload.get("supported_to"))) == destination
            )
            return strengths[attacker.unit_id] - forbidden

        outcomes: dict[str, bool] = {}

        def succeeds(unit_id: str, visiting: tuple[str, ...] = ()) -> bool:
            if unit_id in outcomes:
                return outcomes[unit_id]
            if unit_id in visiting:
                # Direct swaps are handled before following dependencies.
                return True
            attacker = candidates[unit_id]
            destination = self._node_to_province(attacker.to_node)
            defender = unit_at_province.get(destination)
            if defender is None:
                outcomes[unit_id] = True
                return True
            defender_id = defender["unit_id"]
            defender_order = resolved.get(defender_id)
            head_to_head = (
                defender_order is not None
                and self._is_active_move(defender_order, convoy_possible)
                and self._node_to_province(defender_order.to_node) == attacker.from_province
                and not self._move_requires_convoy(attacker)
                and not self._move_requires_convoy(defender_order)
            )
            if not head_to_head and defender_id in candidates and succeeds(defender_id, (*visiting, unit_id)):
                outcomes[unit_id] = True
                return True
            if defender["owner"] == attacker.owner:
                outcomes[unit_id] = False
                return False
            strength = dislodging_strength(attacker, defender)
            defense = 1
            if head_to_head:
                defense = 1 + move_support.get((defender_order.from_province, attacker.from_province), 0)
            elif defender_order is None or defender_order.order_type != "MOVE":
                defense += hold_support.get(destination, 0)
            competitors = [strengths[row.unit_id] for row in attacks_by_destination[destination] if row.unit_id != unit_id]
            result = strength > max([defense, *competitors])
            outcomes[unit_id] = result
            return result

        success_move_unit_ids = {unit_id for unit_id in sorted(candidates) if succeeds(unit_id)}

        dislodged_units: set[str] = set()
        dislodged_from: dict[str, str] = {}
        for destination, attackers in sorted(attacks_by_destination.items()):
            defender = unit_at_province.get(destination)
            if defender is None or defender["unit_id"] in success_move_unit_ids:
                continue
            successful_attackers = [row for row in attackers if row.unit_id in success_move_unit_ids]
            if not successful_attackers:
                continue
            attacker = sorted(successful_attackers, key=lambda row: row.unit_id)[0]
            if attacker.owner == defender["owner"]:
                continue
            dislodged_units.add(defender["unit_id"])
            dislodged_from[defender["unit_id"]] = attacker.from_province

        return {
            "success_move_unit_ids": success_move_unit_ids,
            "dislodged_units": dislodged_units,
            "support_outcomes": support_outcomes,
            "standoff_provinces": standoff_provinces,
            "dislodged_from": dislodged_from,
        }

    def _validate_single_order(
        self, unit: dict[str, Any], submitted: dict[str, Any], phase: str
    ) -> dict[str, Any]:
        order_type = str(submitted.get("type", "")).upper()
        order_id = str(submitted.get("order_id", f"missing_order_id_{unit['unit_id']}"))
        payload = submitted.get("payload") if isinstance(submitted.get("payload"), dict) else {}

        submitted_power = submitted.get("power")
        if isinstance(submitted_power, str) and submitted_power and submitted_power != unit.get("owner"):
            if phase == "retreat":
                return self._validated_row(unit, order_id, "rejected", "wrong_power", None)
            return self._validated_row(
                unit, order_id, "substituted", "wrong_power", self._hold_order(unit, order_id)
            )

        if phase == "retreat":
            return self._validate_retreat_order(unit, submitted, order_id)
        if phase != "movement":
            return self._validated_row(
                unit, order_id, "substituted", "non_movement_phase", self._hold_order(unit, order_id)
            )

        if order_type == "HOLD":
            return self._validated_row(unit, order_id, "accepted", None, self._hold_order(unit, order_id))

        if order_type == "MOVE":
            to_node = payload.get("to")
            via_convoy = bool(payload.get("via_convoy", False))
            if not isinstance(to_node, str):
                return self._validated_row(
                    unit, order_id, "substituted", "malformed_move", self._hold_order(unit, order_id)
                )

            if unit["type"] == "A":
                destination = self._node_to_province(to_node)
                if destination not in self.province_by_id or destination == unit["province"]:
                    return self._validated_row(
                        unit, order_id, "substituted", "illegal_move", self._hold_order(unit, order_id)
                    )
                direct_legal = self._is_legal_move(unit, unit["province"], to_node)
                convoy_legal = (
                    via_convoy
                    and self._is_convoy_eligible_province(unit["province"])
                    and self._is_convoy_eligible_province(destination)
                )
                if not direct_legal and not convoy_legal:
                    return self._validated_row(
                        unit, order_id, "substituted", "illegal_move", self._hold_order(unit, order_id)
                    )
                effective = {
                    "schema_version": self.schema_version,
                    "order_id": order_id,
                    "unit_ref": unit["unit_id"],
                    "power": unit["owner"],
                    "type": "MOVE",
                    "payload": {"from": unit["province"], "to": to_node, "via_convoy": via_convoy},
                }
                return self._validated_row(unit, order_id, "accepted", None, effective)

            if not self._is_legal_move(unit, unit["province"], to_node):
                return self._validated_row(
                    unit, order_id, "substituted", "illegal_move", self._hold_order(unit, order_id)
                )
            effective = {
                "schema_version": self.schema_version,
                "order_id": order_id,
                "unit_ref": unit["unit_id"],
                "power": unit["owner"],
                "type": "MOVE",
                "payload": {"from": unit["province"], "to": to_node, "via_convoy": False},
            }
            return self._validated_row(unit, order_id, "accepted", None, effective)

        if order_type == "SUPPORT_HOLD":
            target = payload.get("target")
            if not isinstance(target, str) or target not in self.province_by_id:
                return self._validated_row(
                    unit,
                    order_id,
                    "substituted",
                    "illegal_support_hold_target",
                    self._hold_order(unit, order_id),
                )
            if not self._can_support_province(unit, target):
                return self._validated_row(
                    unit,
                    order_id,
                    "substituted",
                    "illegal_support_hold_reach",
                    self._hold_order(unit, order_id),
                )
            effective = {
                "schema_version": self.schema_version,
                "order_id": order_id,
                "unit_ref": unit["unit_id"],
                "power": unit["owner"],
                "type": "SUPPORT_HOLD",
                "payload": {"from": unit["province"], "target": target},
            }
            return self._validated_row(unit, order_id, "accepted", None, effective)

        if order_type == "SUPPORT_MOVE":
            supported_from = payload.get("supported_from")
            supported_to = payload.get("supported_to")
            if (
                not isinstance(supported_from, str)
                or supported_from not in self.province_by_id
                or not isinstance(supported_to, str)
                or self._node_to_province(supported_to) not in self.province_by_id
            ):
                return self._validated_row(
                    unit,
                    order_id,
                    "substituted",
                    "illegal_support_move_target",
                    self._hold_order(unit, order_id),
                )
            if not self._can_support_province(unit, self._node_to_province(supported_to)):
                return self._validated_row(
                    unit,
                    order_id,
                    "substituted",
                    "illegal_support_move_reach",
                    self._hold_order(unit, order_id),
                )
            effective = {
                "schema_version": self.schema_version,
                "order_id": order_id,
                "unit_ref": unit["unit_id"],
                "power": unit["owner"],
                "type": "SUPPORT_MOVE",
                "payload": {
                    "from": unit["province"],
                    "supported_from": supported_from,
                    "supported_to": supported_to,
                },
            }
            return self._validated_row(unit, order_id, "accepted", None, effective)

        if order_type == "CONVOY":
            army_from = payload.get("army_from")
            army_to = payload.get("army_to")
            convoy_from = payload.get("from")
            if (
                unit["type"] != "F"
                or unit["province"] not in self.convoy_passable_seas
                or not isinstance(army_from, str)
                or not isinstance(army_to, str)
                or not isinstance(convoy_from, str)
            ):
                return self._validated_row(
                    unit, order_id, "substituted", "illegal_convoy", self._hold_order(unit, order_id)
                )
            if convoy_from != unit["province"]:
                return self._validated_row(
                    unit, order_id, "substituted", "illegal_convoy", self._hold_order(unit, order_id)
                )
            if army_from not in self.province_by_id or self._node_to_province(army_to) not in self.province_by_id:
                return self._validated_row(
                    unit,
                    order_id,
                    "substituted",
                    "illegal_convoy_target",
                    self._hold_order(unit, order_id),
                )
            army_to_province = self._node_to_province(army_to)
            if not self._is_convoy_eligible_province(army_from) or not self._is_convoy_eligible_province(
                army_to_province
            ):
                return self._validated_row(
                    unit,
                    order_id,
                    "substituted",
                    "illegal_convoy_target",
                    self._hold_order(unit, order_id),
                )
            effective = {
                "schema_version": self.schema_version,
                "order_id": order_id,
                "unit_ref": unit["unit_id"],
                "power": unit["owner"],
                "type": "CONVOY",
                "payload": {"from": unit["province"], "army_from": army_from, "army_to": army_to_province},
            }
            return self._validated_row(unit, order_id, "accepted", None, effective)

        return self._validated_row(
            unit, order_id, "substituted", "unknown_order_type", self._hold_order(unit, order_id)
        )

    def _validate_retreat_order(
        self, unit: dict[str, Any], submitted: dict[str, Any], order_id: str
    ) -> dict[str, Any]:
        order_type = str(submitted.get("type", "")).upper()
        payload = submitted.get("payload") if isinstance(submitted.get("payload"), dict) else {}
        if order_type != "RETREAT":
            return self._validated_row(unit, order_id, "rejected", "non_retreat_order", None)
        if payload.get("via_convoy", False):
            return self._validated_row(unit, order_id, "rejected", "retreat_by_convoy_illegal", None)
        to_node = payload.get("to")
        if not isinstance(to_node, str):
            return self._validated_row(unit, order_id, "rejected", "malformed_retreat", None)
        if not self._is_legal_move(unit, unit["province"], to_node):
            return self._validated_row(unit, order_id, "rejected", "illegal_retreat", None)
        effective = {
            "schema_version": self.schema_version,
            "order_id": order_id,
            "unit_ref": unit["unit_id"],
            "power": unit["owner"],
            "type": "RETREAT",
            "payload": {"from": unit["province"], "to": to_node},
        }
        return self._validated_row(unit, order_id, "accepted", None, effective)

    def _validated_row(
        self,
        unit: dict[str, Any],
        order_id: str,
        validation: str,
        reason: str | None,
        effective_order: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "order_id": order_id,
            "unit_id": unit["unit_id"],
            "validation": validation,
            "reason": reason,
            "effective_order": effective_order,
        }

    def _validated_adjustment_row(
        self,
        order_id: str,
        power: str,
        validation: str,
        reason: str | None,
        effective_order: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "order_id": order_id,
            "unit_id": f"adjustment::{power}",
            "validation": validation,
            "reason": reason,
            "effective_order": effective_order,
        }

    def _hold_order(self, unit: dict[str, Any], order_id: str) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "order_id": order_id,
            "unit_ref": unit["unit_id"],
            "power": unit["owner"],
            "type": "HOLD",
            "payload": {"at": unit["province"]},
        }

    def _participant_units(self, state_snapshot: dict[str, Any], phase: str) -> list[dict[str, Any]]:
        units = state_snapshot.get("units", [])
        if phase == "retreat":
            selected = [unit for unit in units if self._needs_retreat(unit)]
        else:
            selected = [unit for unit in units if unit.get("status", "active") == "active"]
        return sorted(selected, key=lambda row: row["unit_id"])

    def _needs_retreat(self, unit: dict[str, Any]) -> bool:
        return bool(unit.get("must_retreat")) or unit.get("status") == "dislodged"

    def _extract_resolved_orders(
        self, validated_orders: list[dict[str, Any]], unit_by_id: dict[str, dict[str, Any]]
    ) -> dict[str, _ResolvedOrder]:
        resolved: dict[str, _ResolvedOrder] = {}
        for row in sorted(validated_orders, key=lambda item: item["unit_id"]):
            unit_id = row["unit_id"]
            if unit_id not in unit_by_id:
                continue
            effective = row.get("effective_order")
            if not isinstance(effective, dict):
                continue
            payload = effective.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            to_node = payload.get("to") if isinstance(payload.get("to"), str) else None
            resolved[unit_id] = _ResolvedOrder(
                unit_id=unit_id,
                order_id=str(effective.get("order_id", row["order_id"])),
                owner=unit_by_id[unit_id]["owner"],
                unit_type=unit_by_id[unit_id]["type"],
                order_type=str(effective.get("type", "HOLD")).upper(),
                payload=payload,
                from_province=unit_by_id[unit_id]["province"],
                to_node=to_node,
            )
        return resolved

    def _compute_support_cut_flags(
        self,
        resolved: dict[str, _ResolvedOrder],
        convoy_possible: dict[str, bool],
        excluded_attacker_unit_ids: set[str] | None = None,
    ) -> dict[str, bool]:
        attacks_by_destination = self._build_attacks_by_destination(
            resolved,
            convoy_possible,
            excluded_attacker_unit_ids or set(),
        )
        cut_flags: dict[str, bool] = {}
        for order in resolved.values():
            if order.order_type not in {"SUPPORT_HOLD", "SUPPORT_MOVE"}:
                continue
            supporter_province = order.from_province
            attacks = attacks_by_destination.get(supporter_province, [])
            if order.order_type == "SUPPORT_HOLD":
                exempt = str(order.payload.get("target"))
            else:
                exempt = self._node_to_province(str(order.payload.get("supported_to", "")))
            cut_flags[order.unit_id] = any(
                attack.owner != order.owner and attack.from_province != exempt for attack in attacks
            )
        return cut_flags

    def _compute_support_strength(
        self,
        resolved: dict[str, _ResolvedOrder],
        unit_at_province: dict[str, dict[str, Any]],
        support_cut: dict[str, bool],
        excluded_supporters: set[str],
        convoy_possible: dict[str, bool],
    ) -> tuple[dict[str, int], dict[tuple[str, str], int], list[dict[str, Any]]]:
        hold_support: dict[str, int] = {}
        move_support: dict[tuple[str, str], int] = {}
        outcomes: list[dict[str, Any]] = []

        move_orders = {
            (order.from_province, self._node_to_province(order.to_node or order.from_province))
            for order in resolved.values()
            if self._is_active_move(order, convoy_possible)
        }

        for order in sorted(resolved.values(), key=lambda row: row.unit_id):
            if order.order_type not in {"SUPPORT_HOLD", "SUPPORT_MOVE"}:
                continue
            if order.unit_id in excluded_supporters:
                outcomes.append(
                    {"schema_version": self.schema_version, "unit_id": order.unit_id, "status": "dislodged"}
                )
                continue
            if support_cut.get(order.unit_id, False):
                outcomes.append(
                    {"schema_version": self.schema_version, "unit_id": order.unit_id, "status": "cut"}
                )
                continue

            if order.order_type == "SUPPORT_HOLD":
                target = str(order.payload.get("target"))
                target_unit = unit_at_province.get(target)
                target_order = None if target_unit is None else resolved.get(target_unit["unit_id"])
                if target_unit is None or (
                    target_order is not None and target_order.order_type == "MOVE"
                ):
                    outcomes.append(
                        {"schema_version": self.schema_version, "unit_id": order.unit_id, "status": "invalid"}
                    )
                    continue
                hold_support[target] = hold_support.get(target, 0) + 1
                outcomes.append(
                    {"schema_version": self.schema_version, "unit_id": order.unit_id, "status": "effective"}
                )
                continue

            supported_from = str(order.payload.get("supported_from"))
            supported_to = self._node_to_province(str(order.payload.get("supported_to")))
            supported_unit = unit_at_province.get(supported_from)
            supported_order = resolved.get(supported_unit["unit_id"]) if supported_unit else None
            specified_node = str(order.payload.get("supported_to"))
            coast_mismatch = specified_node in self.coast_by_id and (supported_order is None or supported_order.to_node != specified_node)
            if supported_from == order.from_province or (supported_from, supported_to) not in move_orders or coast_mismatch:
                outcomes.append(
                    {"schema_version": self.schema_version, "unit_id": order.unit_id, "status": "invalid"}
                )
                continue
            move_support[(supported_from, supported_to)] = (
                move_support.get((supported_from, supported_to), 0) + 1
            )
            outcomes.append(
                {"schema_version": self.schema_version, "unit_id": order.unit_id, "status": "effective"}
            )

        return hold_support, move_support, outcomes

    def _compute_convoy_possibility(
        self, resolved: dict[str, _ResolvedOrder], excluded_convoy_fleets: set[str]
    ) -> tuple[dict[str, bool], dict[str, list[list[str]]]]:
        convoy_fleets_by_tuple: dict[tuple[str, str], set[str]] = {}
        for order in resolved.values():
            if order.order_type != "CONVOY" or order.unit_id in excluded_convoy_fleets:
                continue
            army_from = str(order.payload.get("army_from"))
            army_to = self._node_to_province(str(order.payload.get("army_to")))
            convoy_fleets_by_tuple.setdefault((army_from, army_to), set()).add(order.from_province)

        convoy_possible: dict[str, bool] = {}
        convoy_routes: dict[str, list[list[str]]] = {}
        for order in sorted(resolved.values(), key=lambda row: row.unit_id):
            if order.order_type != "MOVE" or order.unit_type != "A" or order.to_node is None:
                continue
            if not self._move_requires_convoy(order):
                continue
            destination = self._node_to_province(order.to_node)
            fleets = convoy_fleets_by_tuple.get((order.from_province, destination), set())
            if not fleets:
                convoy_possible[order.unit_id] = False
                convoy_routes[order.unit_id] = []
                continue
            route = self._find_convoy_route(order.from_province, destination, fleets)
            convoy_possible[order.unit_id] = route is not None
            convoy_routes[order.unit_id] = [route] if route is not None else []
        return convoy_possible, convoy_routes

    def _find_convoy_route(
        self, army_from: str, army_to: str, fleet_provinces: set[str]
    ) -> list[str] | None:
        starts = sorted([fleet for fleet in fleet_provinces if self._fleet_touches_province(fleet, army_from)])
        goals = set([fleet for fleet in fleet_provinces if self._fleet_touches_province(fleet, army_to)])
        if not starts or not goals:
            return None
        queue: deque[tuple[str, list[str]]] = deque((start, [start]) for start in starts)
        visited = set(starts)
        while queue:
            node, path = queue.popleft()
            if node in goals:
                return path
            neighbors = sorted([n for n in self._fleet_adjacency(node) if n in fleet_provinces])
            for neighbor in neighbors:
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))
        return None

    def _build_attacks_by_destination(
        self,
        resolved: dict[str, _ResolvedOrder],
        convoy_possible: dict[str, bool],
        excluded_attacker_unit_ids: set[str],
    ) -> dict[str, list[_ResolvedOrder]]:
        attacks_by_destination: dict[str, list[_ResolvedOrder]] = {}
        for order in resolved.values():
            if order.unit_id in excluded_attacker_unit_ids:
                continue
            if not self._is_active_move(order, convoy_possible):
                continue
            destination = self._node_to_province(order.to_node or order.from_province)
            attacks_by_destination.setdefault(destination, []).append(order)
        return attacks_by_destination

    def _is_active_move(self, order: _ResolvedOrder, convoy_possible: dict[str, bool]) -> bool:
        if order.order_type != "MOVE" or order.to_node is None:
            return False
        if self._move_requires_convoy(order):
            return convoy_possible.get(order.unit_id, False)
        return True

    def _move_requires_convoy(self, order: _ResolvedOrder) -> bool:
        if order.unit_type != "A" or order.to_node is None:
            return False
        if bool(order.payload.get("via_convoy", False)):
            return True
        destination = self._node_to_province(order.to_node)
        return destination not in self.province_by_id[order.from_province].army_adjacency

    def _can_support_province(self, unit: dict[str, Any], destination: str) -> bool:
        nodes = [destination, *self.province_by_id[destination].coasts]
        return any(self._is_legal_move(unit, unit["province"], node) for node in nodes)

    def _is_legal_move(self, unit: dict[str, Any], from_province: str, to_node: str) -> bool:
        destination_province = self._node_to_province(to_node)
        if destination_province not in self.province_by_id:
            return False
        if unit["type"] == "A":
            return destination_province in self.province_by_id[from_province].army_adjacency
        origin_node = unit.get("coast") if unit.get("coast") else from_province
        adjacency = self._fleet_adjacency(origin_node)
        return to_node in adjacency or destination_province in adjacency

    def _is_convoy_eligible_province(self, province_id: str) -> bool:
        province = self.province_by_id.get(province_id)
        return province is not None and province.province_type == "coastal"

    def _fleet_touches_province(self, fleet_node: str, province_id: str) -> bool:
        adjacency = self._fleet_adjacency(fleet_node)
        if province_id in adjacency:
            return True
        province = self.province_by_id[province_id]
        return any(coast_id in adjacency for coast_id in province.coasts)

    def _fleet_adjacency(self, node_id: str) -> set[str]:
        if node_id in self.coast_by_id:
            return set(self.coast_by_id[node_id].fleet_adjacency)
        province = self.province_by_id.get(node_id)
        if province is None:
            return set()
        return set(province.fleet_adjacency)

    def _node_to_province(self, node_id: str) -> str:
        if node_id in self.coast_by_id:
            return self.coast_by_id[node_id].province_id
        return node_id

    def _home_centers_by_power(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for province in self.province_by_id.values():
            if province.home_center_owner is None:
                continue
            result.setdefault(province.home_center_owner, []).append(province.id)
        for power in list(result.keys()):
            result[power] = sorted(result[power])
        return result

    def _can_build(
        self,
        *,
        power: str,
        unit_type: str,
        province_id: str,
        coast: str | None,
        output_units: list[dict[str, Any]],
        owned_centers: list[str],
        home_centers: list[str],
    ) -> bool:
        province = self.province_by_id.get(province_id)
        if province is None:
            return False
        if province_id not in owned_centers:
            return False
        if province_id not in home_centers:
            return False
        if any(unit.get("status") == "active" and unit.get("province") == province_id for unit in output_units):
            return False
        if unit_type == "A":
            return "A" in province.occupiable_by
        if unit_type == "F":
            if "F" not in province.occupiable_by:
                return False
            if province.coasts and (coast is None or coast not in province.coasts):
                return False
            return True
        return False

    def _new_unit_id(self, output_units: list[dict[str, Any]], seed_idx: int) -> str:
        existing = {str(unit.get("unit_id")) for unit in output_units}
        numeric_ids = [
            int(unit_id.removeprefix("unit_"))
            for unit_id in existing
            if unit_id.startswith("unit_") and unit_id.removeprefix("unit_").isdigit()
        ]
        idx = max([*numeric_ids, max(0, seed_idx - 1)]) + 1
        while True:
            candidate = f"unit_{idx:04d}"
            if candidate not in existing:
                return candidate
            idx += 1

    def _phase_id_for_state(self, state: dict[str, Any]) -> str:
        year = int(state.get("year", 1901))
        season = str(state.get("season", "winter"))
        phase = str(state.get("phase", "adjustment"))
        return f"{year}_{season}_{phase}"

    def _auto_disband_selection(
        self, candidates: list[dict[str, Any]], owned_home_centers: list[str], count: int
    ) -> list[str]:
        scored: list[tuple[int, str, str]] = []
        for unit in candidates:
            distance = self._distance_to_owned_home(unit, owned_home_centers)
            scored.append((distance, str(unit.get("province")), str(unit.get("unit_id"))))
        scored.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
        return [unit_id for _, _, unit_id in scored[:count]]

    def _distance_to_owned_home(self, unit: dict[str, Any], owned_home_centers: list[str]) -> int:
        if not owned_home_centers:
            return 10**9
        start = str(unit.get("province"))
        unit_type = str(unit.get("type"))
        graph_neighbors = self._graph_neighbors_for_unit(unit_type, unit)
        queue: deque[tuple[str, int]] = deque([(start, 0)])
        visited = {start}
        target_set = set(owned_home_centers)
        while queue:
            node, dist = queue.popleft()
            if node in target_set:
                return dist
            for nxt in graph_neighbors(node):
                if nxt in visited:
                    continue
                visited.add(nxt)
                queue.append((nxt, dist + 1))
        return 10**9

    def _graph_neighbors_for_unit(self, unit_type: str, unit: dict[str, Any]):
        if unit_type == "A":
            def neighbors(node: str) -> list[str]:
                province = self.province_by_id.get(node)
                return list(province.army_adjacency) if province is not None else []
            return neighbors
        coast_node = unit.get("coast") if isinstance(unit.get("coast"), str) else None

        def neighbors(node: str) -> list[str]:
            source = coast_node if node == str(unit.get("province")) and coast_node else node
            fleet_neighbors = self._fleet_adjacency(source)
            return sorted({self._node_to_province(dest) for dest in fleet_neighbors})

        return neighbors
