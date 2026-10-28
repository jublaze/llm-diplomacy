from __future__ import annotations

import json
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DatcCase:
    schema_version: str
    case_id: str
    title: str
    initial_state: dict[str, Any]
    orders: list[dict[str, Any]]
    expected: dict[str, Any]
    source: Path
    unsupported_reason: str | None = None


@dataclass(frozen=True)
class DatcRunFailure:
    case_id: str
    source: Path
    detail: str


@dataclass(frozen=True)
class DatcRunReport:
    total: int
    passed: int
    failed: int
    failures: list[DatcRunFailure]


def check_datc_coverage(engine: Any, cases: list[DatcCase], manifest_path: str | Path) -> dict[str, Any]:
    """Check the entire selected corpus, including exact declared variant outcomes."""
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    entries = manifest["cases"]
    result: dict[str, Any] = {"total": len(cases), "passed": 0, "policy_deviations": 0,
                              "adapter_limitations": 0,
                              "historical_alternatives": 0, "unexplained": []}
    if not cases or set(entries) != {case.case_id for case in cases}:
        result["unexplained"].append("coverage manifest case IDs differ from corpus")
        return result
    for case in cases:
        entry = entries[case.case_id]
        if case.unsupported_reason:
            if entry.get("status") != "historical_alternative" or entry.get("reason") != case.unsupported_reason:
                result["unexplained"].append(case.case_id + ": undeclared exclusion")
            else:
                result["historical_alternatives"] += 1
            continue
        validated = engine.validate_orders(case.initial_state, case.orders)
        resolved = engine.resolve_phase(case.initial_state, validated)
        mismatch = _first_mismatch(case.expected, validated, resolved)
        active = [u["province"] for u in resolved["output_state"]["units"] if u.get("status") == "active"]
        if len(active) != len(set(active)):
            result["unexplained"].append(case.case_id + ": duplicate active occupancy")
        if entry.get("status") == "pass" and mismatch is None:
            result["passed"] += 1
        elif entry.get("status") in {"policy_deviation", "adapter_limitation"} and entry.get("reason") and mismatch:
            digest = hashlib.sha256(json.dumps(resolved, sort_keys=True).encode()).hexdigest()
            if digest == entry.get("result_sha256") and mismatch == entry.get("mismatch"):
                category = "policy_deviations" if entry["status"] == "policy_deviation" else "adapter_limitations"
                result[category] += 1
            else:
                result["unexplained"].append(case.case_id + ": declared non-pass outcome changed")
        else:
            result["unexplained"].append(case.case_id + ": " + (mismatch or "unexpected status"))
    return result


def discover_datc_fixture_dirs(
    committed_dir: str | Path, external_env_var: str = "LLM_DIPLOMACY_DATC_DIR"
) -> tuple[Path, Path | None]:
    committed = Path(committed_dir)
    external_raw = os.environ.get(external_env_var, "").strip()
    if not external_raw:
        return committed, None
    external = Path(external_raw)
    if not external.is_dir():
        raise FileNotFoundError(f"Configured DATC fixture directory does not exist: {external}")
    return committed, external


def ingest_datc_fixtures(fixtures_dir: str | Path) -> list[DatcCase]:
    root = Path(fixtures_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"DATC fixture directory does not exist: {root}")
    out: list[DatcCase] = []
    paths = sorted(root.glob("*.json"))
    if not paths:
        raise ValueError(f"DATC fixture directory contains no JSON cases: {root}")
    upstream = root / "datc.json"
    if upstream in paths:
        return _ingest_upstream_datc(upstream)
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"DATC fixture is unreadable or invalid JSON: {path}: {exc}") from exc
        out.append(_to_case(path, payload))
    case_ids = [case.case_id for case in out]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError(f"DATC fixture directory has duplicate case_id values: {root}")
    return out


def run_datc_cases(engine: Any, cases: list[DatcCase]) -> DatcRunReport:
    if not cases:
        raise ValueError("DATC run requires at least one case")
    failures: list[DatcRunFailure] = []
    passed = 0
    for case in cases:
        if case.unsupported_reason:
            failures.append(DatcRunFailure(case.case_id, case.source, f"unsupported: {case.unsupported_reason}"))
            continue
        validated = engine.validate_orders(case.initial_state, case.orders)
        resolved = engine.resolve_phase(case.initial_state, validated)
        mismatch = _first_mismatch(case.expected, validated, resolved)
        if mismatch is None:
            passed += 1
            continue
        failures.append(
            DatcRunFailure(
                case_id=case.case_id,
                source=case.source,
                detail=mismatch,
            )
        )
    return DatcRunReport(total=len(cases), passed=passed, failed=len(failures), failures=failures)


def _to_case(path: Path, payload: dict[str, Any]) -> DatcCase:
    if not isinstance(payload, dict):
        raise ValueError(f"DATC fixture must be a JSON object: {path}")
    schema_version = payload.get("schema_version")
    case_id = payload.get("case_id")
    title = payload.get("title")
    initial_state = payload.get("initial_state")
    orders = payload.get("orders")
    expected = payload.get("expected")
    if schema_version != "1.0":
        raise ValueError(f"DATC fixture schema_version must be '1.0': {path}")
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError(f"DATC fixture case_id must be a non-empty string: {path}")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"DATC fixture title must be a non-empty string: {path}")
    if not isinstance(initial_state, dict):
        raise ValueError(f"DATC fixture invalid initial_state object: {path}")
    if not isinstance(orders, list) or not orders or any(not isinstance(row, dict) for row in orders):
        raise ValueError(f"DATC fixture orders must be a non-empty list of objects: {path}")
    if not isinstance(expected, dict):
        raise ValueError(f"DATC fixture invalid expected object: {path}")
    supported = {
        "validated_orders",
        "movement_outcomes",
        "support_outcomes",
        "convoy_outcomes",
        "retreat_outcomes",
        "adjustment_outcomes",
        "output_state",
    }
    unknown = set(expected) - supported
    if unknown:
        raise ValueError(f"DATC fixture has unsupported expected keys {sorted(unknown)}: {path}")
    if not expected:
        raise ValueError(f"DATC fixture expected must assert at least one outcome: {path}")
    for key, value in expected.items():
        if key == "output_state":
            if not isinstance(value, dict) or not value:
                raise ValueError(f"DATC fixture expected.output_state must be a non-empty object: {path}")
        elif not isinstance(value, dict) or not value or any(
            not isinstance(row_id, str) or not row_id.strip() or outcome is None
            for row_id, outcome in value.items()
        ):
            raise ValueError(f"DATC fixture expected.{key} must map ids to outcomes: {path}")
    return DatcCase(
        schema_version=schema_version,
        case_id=case_id,
        title=title,
        initial_state=initial_state,
        orders=orders,
        expected=expected,
        source=path,
    )


_POWER_NAMES = {
    "AUS": "austria", "ENG": "england", "FRA": "france", "GER": "germany",
    "ITA": "italy", "RUS": "russia", "TUR": "turkey",
}
_UNIT_ORDER_RE = re.compile(r"^(?P<unit>[AF])\s+(?P<origin>[a-z]{3}(?:\([a-z]{2}\))?)(?:\s+(?P<command>.*))?$", re.I)


def _ingest_upstream_datc(path: Path) -> list[DatcCase]:
    """Convert the linked DATCv3 JSON case index into executable project cases."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"DATC source corpus is unreadable or invalid JSON: {path}: {exc}") from exc
    supplements_path = path.with_name("supplements.json")
    supplements = json.loads(supplements_path.read_text()) if supplements_path.exists() else {}
    source_cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(source_cases, list) or not source_cases:
        raise ValueError(f"DATC source corpus must contain a non-empty cases list: {path}")
    from llm_diplomacy.rules.diplomacy import DiplomacyRuleEngine

    engine = DiplomacyRuleEngine()
    additional_path = path.with_name("additional_cases.json")
    if additional_path.exists():
        source_cases = [*source_cases, *json.loads(additional_path.read_text())]
    converted: list[DatcCase] = []
    seen: dict[str, int] = {}
    for raw in source_cases:
        if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
            raise ValueError(f"DATC source corpus contains a malformed case: {path}")
        source_name = raw["name"]
        raw = {**raw, **supplements.get(source_name, {})}
        seen[source_name] = seen.get(source_name, 0) + 1
        edition = str(raw.get("edition", ""))
        case_id = source_name + (f"_{edition}" if edition else "")
        if case_id in {case.case_id for case in converted}:
            case_id = f"{case_id}_{seen[source_name]}"
        unsupported = _external_case_unsupported_reason(raw)
        try:
            setup_mismatches = {}
            if raw.get("phase") == "Retreat":
                previous = raw.get("preceding_main_phase")
                if not isinstance(previous, dict):
                    raise ValueError("retreat case omits preceding_main_phase")
                main_case = {**raw, "phase": "Main", "orders": previous.get("orders")}
                state, main_orders, setup_assertions = _convert_external_case(main_case, engine)
                main_validated = engine.validate_orders(state, main_orders)
                main_result = engine.resolve_phase(state, main_validated)
                state = main_result["output_state"]
                state["phase"] = "retreat"
                _, retreat_orders, assertions = _convert_external_case({**raw, "phase": "Main"}, engine)
                assertion_by_order = {row["order_id"]: row for row in assertions}
                for order in retreat_orders:
                    province = order["payload"].get("from", order["payload"].get("at"))
                    matches = [unit for unit in state["units"] if unit["province"] == province and unit["owner"] == order["power"]]
                    if len(matches) != 1:
                        raise ValueError(f"retreat order has ambiguous/missing unit at {province}")
                    unit = matches[0]
                    order["unit_ref"] = unit["unit_id"]
                    if order["type"] == "MOVE":
                        order["type"] = "RETREAT"
                        order["payload"] = {"from": province, "to": order["payload"]["to"]}
                    assertion = assertion_by_order.get(order["order_id"])
                    if assertion:
                        assertion["unit_id"] = unit["unit_id"]
                        assertion["order_type"] = order["type"]
                orders = retreat_orders
                setup_mismatches = _check_setup_assertions(setup_assertions, main_result)
            else:
                state, orders, assertions = _convert_external_case(raw, engine)
        except (KeyError, TypeError, ValueError) as exc:
            state, orders, assertions = {}, [], []
            setup_mismatches = {}
            unsupported = unsupported or f"conversion failed: {exc}"
        expected = {"datc_orders": assertions} if assertions else {}
        if setup_mismatches:
            expected["setup_mismatches"] = setup_mismatches
        converted.append(
            DatcCase(
                schema_version="1.0",
                case_id=case_id,
                title=source_name,
                initial_state=state,
                orders=orders,
                expected=expected,
                source=path,
                unsupported_reason=unsupported or (None if assertions else "source case has no executable expectations"),
            )
        )
    return converted


def _external_case_unsupported_reason(case: dict[str, Any]) -> str | None:
    if case.get("edition") in {"1971", "1982"}:
        return f"source edition {case['edition']} is a historical alternative, not the selected corpus expectation"
    return None


def _check_setup_assertions(assertions: list[dict[str, Any]], result: dict[str, Any]) -> dict[str, Any]:
    mismatch = _first_mismatch({"datc_orders": assertions}, {"validated_orders": result["validated_orders"]}, result)
    return {"preceding_phase": mismatch} if mismatch else {}


def _convert_external_case(case: dict[str, Any], engine: Any) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    phase = case.get("phase")
    if phase not in {"Main", "Build"}:
        raise ValueError(f"unsupported source phase {phase!r}")
    state = engine.initial_state({})
    state["units"] = []
    state["phase"] = "movement" if phase == "Main" else "adjustment"
    state["season"] = "spring" if phase == "Main" else "winter"
    raw_units = case.get("starting_state")
    if raw_units is not None:
        if not isinstance(raw_units, list):
            raise ValueError("starting_state must be a list")
        state["units"] = [_external_unit(text, i) for i, text in enumerate(raw_units, start=1)]
    elif phase == "Main":
        state["units"] = _external_units_from_orders(case.get("orders"))
    if isinstance(case.get("occupiers"), dict):
        state["supply_ownership"] = {
            str(province): _POWER_NAMES[str(power)] for province, power in case["occupiers"].items()
        }
    orders_by_text = case.get("orders")
    if not isinstance(orders_by_text, (dict, list)):
        raise ValueError("orders must be an object")
    orders: list[dict[str, Any]] = []
    assertions: list[dict[str, Any]] = []
    unit_by_province = {unit["province"]: unit for unit in state["units"]}
    for index, raw_disband in enumerate(case.get("civil_disorder", []), start=1):
        match = re.fullmatch(r"\s*[A-Z]{3}:\s*[AF]\s+([a-z]{3}(?:\([a-z]{2}\))?)\s*", str(raw_disband), re.I)
        if match is None:
            raise ValueError(f"unrecognized civil-disorder unit {raw_disband!r}")
        province, _ = _external_location(match.group(1))
        unit = unit_by_province.get(province)
        if unit is None:
            raise ValueError(f"civil-disorder unit missing from starting_state: {raw_disband!r}")
        assertions.append({"order_id": f"{case.get('name')}_civil_{index:03d}", "unit_id": unit["unit_id"], "order_type": "AUTO_DISBAND", "succeeds": True})
    for index, (raw_order, expected_result) in enumerate(orders_by_text.items() if isinstance(orders_by_text, dict) else orders_by_text, start=1):
        if expected_result is not None and expected_result not in {"Succeeds", "Fails"}:
            raise ValueError(f"unsupported expected result {expected_result!r}")
        power_and_order = str(raw_order).split(":", 1)
        if len(power_and_order) != 2 or power_and_order[0].strip() not in _POWER_NAMES:
            raise ValueError(f"malformed order {raw_order!r}")
        power = _POWER_NAMES[power_and_order[0].strip()]
        unit_match = _UNIT_ORDER_RE.fullmatch(power_and_order[1].strip())
        if unit_match is None:
            raise ValueError(f"unrecognized order syntax {raw_order!r}")
        unit_type = unit_match.group("unit").upper()
        origin, origin_coast = _external_location(unit_match.group("origin"))
        command = (unit_match.group("command") or "").strip()
        unit = unit_by_province.get(origin)
        if unit is None and not (phase == "Build" and command.lower() in {"build", "disband"}):
            raise ValueError(f"no starting unit at {origin} for {raw_order!r}")
        order_id = f"{case.get('name')}_{index:03d}"
        order_type, order_payload, extra = _external_order(command, unit_type, origin, origin_coast, engine)
        order = {
            "schema_version": "1.0", "order_id": order_id, "unit_ref": unit["unit_id"] if unit else f"source_{order_id}",
            "power": power, "type": order_type, "payload": order_payload,
        }
        if order_type == "DISBAND":
            order["payload"]["unit_ref"] = unit["unit_id"] if unit else f"source_{order_id}"
        order.update(extra)
        orders.append(order)
        if expected_result is None:
            continue
        assertions.append({
            "order_id": order_id, "unit_id": unit["unit_id"] if unit else f"source_{order_id}", "order_type": order_type,
            "succeeds": expected_result == "Succeeds",
            **({"army_unit_id": unit_by_province.get(order_payload.get("army_from"), {}).get("unit_id")} if order_type == "CONVOY" else {}),
        })
    for order in orders:
        if order["type"] == "MOVE" and (unit := unit_by_province.get(order["payload"]["from"])) and unit["type"] == "A":
            matching_own_convoy = any(
                fleet["type"] == "CONVOY" and fleet["power"] == order["power"]
                and fleet["payload"]["army_from"] == order["payload"]["from"]
                and fleet["payload"]["army_to"] == order["payload"]["to"]
                and _potential_convoy_includes(engine, state["units"], order["payload"]["from"], order["payload"]["to"], fleet["payload"]["from"])
                for fleet in orders
            )
            if matching_own_convoy:
                order["payload"]["via_convoy"] = True
    if not assertions and not (phase == "Build" and not orders_by_text):
        raise ValueError("case has no asserted orders")
    return state, orders, assertions


def _potential_convoy_includes(engine, units, origin, destination, required):
    fleets = {u["province"] for u in units if u["type"] == "F" and u["province"] in engine.convoy_passable_seas}
    def visit(node, path):
        if engine._fleet_touches_province(node, destination):
            return required in path
        return any(visit(n, (*path, n)) for n in engine._fleet_adjacency(node) & fleets if n not in path)
    return any(visit(start, (start,)) for start in fleets if engine._fleet_touches_province(start, origin))


def _external_units_from_orders(orders: Any) -> list[dict[str, Any]]:
    if not isinstance(orders, dict):
        raise ValueError("orders must be an object")
    units: dict[str, tuple[str, str, str | None]] = {}
    references: list[tuple[str, str, str | None, str]] = []
    for raw_order in orders:
        parts = str(raw_order).split(":", 1)
        if len(parts) != 2 or parts[0].strip() not in _POWER_NAMES:
            continue
        match = _UNIT_ORDER_RE.fullmatch(parts[1].strip())
        if match is None:
            continue
        unit_type = match.group("unit").upper()
        origin, coast = _external_location(match.group("origin"))
        power = _POWER_NAMES[parts[0].strip()]
        command = match.group("command") or ""
        if command.strip().lower() not in {"build"}:
            units[origin] = (power, unit_type, coast)
        support = re.search(r"supports\s+[AF]\s+([a-z]{3}(?:\([a-z]{2}\))?)", command, re.I)
        convoy = re.search(r"convoys\s+A\s+([a-z]{3}(?:\([a-z]{2}\))?)", command, re.I)
        reference = support or convoy
        if reference:
            ref_province, ref_coast = _external_location(reference.group(1))
            ref_type = "A" if convoy else re.search(r"supports\s+([AF])", command, re.I).group(1).upper()
            references.append((ref_province, ref_type, ref_coast, power))
    for province, unit_type, coast, power in references:
        units.setdefault(province, (power, unit_type, coast))
    return [
        {
            "schema_version": "1.0", "unit_id": f"datc_{index:03d}_{province}", "owner": power,
            "type": unit_type, "province": province, "coast": _external_coast_id(province, coast),
            "status": "active", "must_retreat": False, "dislodged_from": None,
        }
        for index, (province, (power, unit_type, coast)) in enumerate(sorted(units.items()), start=1)
    ]


def _external_unit(text: str, index: int) -> dict[str, Any]:
    match = re.fullmatch(r"\s*([A-Z]{3}):\s*([AF])\s+([a-z]{3}(?:\([a-z]{2}\))?)\s*", text, re.I)
    if match is None or match.group(1).upper() not in _POWER_NAMES:
        raise ValueError(f"unrecognized starting unit {text!r}")
    province, coast = _external_location(match.group(3))
    owner = _POWER_NAMES[match.group(1).upper()]
    return {
        "schema_version": "1.0", "unit_id": f"datc_{index:03d}_{province}", "owner": owner,
        "type": match.group(2).upper(), "province": province,
        "coast": _external_coast_id(province, coast), "status": "active",
        "must_retreat": False, "dislodged_from": None,
    }


def _external_location(text: str) -> tuple[str, str | None]:
    match = re.fullmatch(r"([a-z]{3})(?:\(([a-z]{2})\))?", text, re.I)
    if match is None:
        raise ValueError(f"unrecognized province {text!r}")
    return match.group(1).lower(), match.group(2).lower() if match.group(2) else None


def _external_coast_id(province: str, coast: str | None) -> str | None:
    return f"{province}_{coast}" if coast else None


def _external_order(command: str, unit_type: str, origin: str, origin_coast: str | None, engine: Any) -> tuple[str, dict[str, Any], dict[str, Any]]:
    lowered = command.lower()
    if lowered in {"hold", "holds"}:
        return "HOLD", {"from": origin}, {}
    if lowered.startswith("supports "):
        target = re.fullmatch(r"supports\s+([AF])\s+([a-z]{3}(?:\([a-z]{2}\))?)(?:\s*->\s*([a-z]{3}(?:\([a-z]{2}\))?))?", command, re.I)
        if target is None:
            raise ValueError(f"unrecognized support command {command!r}")
        supported_from, _ = _external_location(target.group(2))
        if target.group(3) is None:
            return "SUPPORT_HOLD", {"from": origin, "target": supported_from}, {}
        supported_to, coast = _external_location(target.group(3))
        return "SUPPORT_MOVE", {
            "from": origin, "supported_from": supported_from,
            "supported_to": _external_coast_id(supported_to, coast) or supported_to,
        }, {}
    if lowered.startswith("convoys "):
        convoy = re.fullmatch(r"convoys\s+(?:A\s+)?([a-z]{3}(?:\([a-z]{2}\))?)\s*->\s*([a-z]{3}(?:\([a-z]{2}\))?)", command, re.I)
        if convoy is None:
            raise ValueError(f"unrecognized convoy command {command!r}")
        army_from, _ = _external_location(convoy.group(1))
        army_to, coast = _external_location(convoy.group(2))
        return "CONVOY", {
            "from": origin, "army_from": army_from,
            "army_to": _external_coast_id(army_to, coast) or army_to,
        }, {}
    if lowered in {"build", "disband"}:
        if lowered == "build":
            return "BUILD", {"unit_type": unit_type, "province": origin, "coast": _external_coast_id(origin, origin_coast)}, {}
        return "DISBAND", {"unit_ref": ""}, {}
    move = re.fullmatch(r"(?:->\s*)?([a-z]{3}(?:\([a-z]{2}\))?)(?:\s+via\s+convoy)?", command, re.I)
    if move is None:
        raise ValueError(f"unrecognized order command {command!r}")
    destination, coast = _external_location(move.group(1))
    via_convoy = "via convoy" in lowered
    payload: dict[str, Any] = {"from": origin, "to": _external_coast_id(destination, coast) or destination}
    if unit_type == "A":
        # DATC's notation implies a convoy for a non-adjacent army move; the
        # engine's internal order schema requires that intent explicitly.
        origin_province = engine.province_by_id.get(origin)
        requires_convoy = origin_province is None or destination not in origin_province.army_adjacency
        if via_convoy or requires_convoy:
            payload["via_convoy"] = True
    return "MOVE", payload, {}


def _first_mismatch(
    expected: dict[str, Any], validated: dict[str, Any], resolved: dict[str, Any]
) -> str | None:
    setup_mismatches = expected.get("setup_mismatches")
    if setup_mismatches:
        return f"preceding movement setup mismatch: {setup_mismatches}"
    external_orders = expected.get("datc_orders")
    if isinstance(external_orders, list):
        validated_by_unit = {
            str(row.get("unit_id")): row for row in validated.get("validated_orders", []) if isinstance(row, dict)
        }
        movement_by_unit = {str(row.get("unit_id")): row for row in resolved.get("movement_outcomes", [])}
        support_by_unit = {str(row.get("unit_id")): row for row in resolved.get("support_outcomes", [])}
        retreat_by_unit = {str(row.get("unit_id")): row for row in resolved.get("retreat_outcomes", [])}
        adjustment_by_order = {str(row.get("order_id")): row for row in resolved.get("adjustment_outcomes", [])}
        convoy_status = {
            str(row.get("army_unit_id")): row.get("status") for row in resolved.get("convoy_outcomes", [])
        }
        final_units = {str(row.get("unit_id")): row for row in resolved.get("output_state", {}).get("units", [])}
        for assertion in external_orders:
            unit_id = assertion["unit_id"]
            order_type = assertion["order_type"]
            order_id = assertion["order_id"]
            if order_type == "MOVE":
                success = movement_by_unit.get(unit_id, {}).get("result") == "moved"
            elif order_type in {"SUPPORT_HOLD", "SUPPORT_MOVE"}:
                success = support_by_unit.get(unit_id, {}).get("status") == "effective"
            elif order_type == "CONVOY":
                success = (validated_by_unit.get(unit_id, {}).get("effective_order", {}).get("type") == "CONVOY"
                           and movement_by_unit.get(unit_id, {}).get("result") == "held"
                           and convoy_status.get(assertion.get("army_unit_id")) == "possible")
            elif order_type == "HOLD":
                success = movement_by_unit.get(unit_id, {}).get("result") == "held"
            elif order_type == "RETREAT":
                success = retreat_by_unit.get(unit_id, {}).get("result") == "retreated"
            elif order_type == "BUILD":
                success = adjustment_by_order.get(order_id, {}).get("result") == "built"
            elif order_type == "DISBAND":
                success = adjustment_by_order.get(order_id, {}).get("result") == "disbanded"
            elif order_type == "AUTO_DISBAND":
                success = final_units.get(unit_id, {}).get("status") == "destroyed"
            else:
                return f"unsupported order type in DATC assertion: {order_type}"
            if success != assertion["succeeds"]:
                return (
                    f"{order_id} expected={'succeeds' if assertion['succeeds'] else 'fails'} "
                    f"actual={'succeeds' if success else 'fails'}"
                )

    expected_validation = expected.get("validated_orders")
    if isinstance(expected_validation, dict):
        actual_validation = {
            row["unit_id"]: row["validation"] for row in validated.get("validated_orders", [])
        }
        for unit_id, status in expected_validation.items():
            if actual_validation.get(unit_id) != status:
                return (
                    f"validated_orders[{unit_id}] expected={status} "
                    f"actual={actual_validation.get(unit_id)}"
                )

    for key, id_field in (
        ("movement_outcomes", "unit_id"),
        ("support_outcomes", "unit_id"),
        ("convoy_outcomes", "army_unit_id"),
        ("retreat_outcomes", "unit_id"),
        ("adjustment_outcomes", "power"),
    ):
        exp_map = expected.get(key)
        if isinstance(exp_map, dict):
            actual_rows = resolved.get(key, [])
            if not isinstance(actual_rows, list):
                return f"{key} expected list in resolved payload"
            actual_map: dict[str, Any] = {}
            for row in actual_rows:
                if not isinstance(row, dict):
                    continue
                row_id = row.get(id_field)
                if isinstance(row_id, str) and "result" in row:
                    actual_map[row_id] = row.get("result")
                elif isinstance(row_id, str) and "status" in row:
                    actual_map[row_id] = row.get("status")
            for row_id, exp_value in exp_map.items():
                if actual_map.get(row_id) != exp_value:
                    return f"{key}[{row_id}] expected={exp_value} actual={actual_map.get(row_id)}"

    expected_state = expected.get("output_state")
    if isinstance(expected_state, dict):
        actual_state = resolved.get("output_state")
        if not isinstance(actual_state, dict):
            return "output_state expected object in resolved payload"
        if expected_state != actual_state:
            return "output_state mismatch"
    return None
