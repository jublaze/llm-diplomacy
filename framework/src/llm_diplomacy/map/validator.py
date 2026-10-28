from __future__ import annotations

from collections import Counter
from typing import Any


class MapValidationError(ValueError):
    pass


def validate_map_payload(payload: dict[str, Any]) -> None:
    required_top_level = (
        "schema_version",
        "map_id",
        "version",
        "powers",
        "provinces",
        "coasts",
        "convoy_passable_seas",
        "initial_state",
    )
    missing_top = [field for field in required_top_level if field not in payload]
    if missing_top:
        raise MapValidationError(f"Missing top-level map fields: {missing_top}")

    provinces = payload["provinces"]
    coasts = payload["coasts"]
    if not isinstance(provinces, list) or not isinstance(coasts, list):
        raise MapValidationError("'provinces' and 'coasts' must be lists.")

    province_ids = [row["id"] for row in provinces]
    coast_ids = [row["id"] for row in coasts]
    all_ids = set(province_ids) | set(coast_ids)

    _ensure_unique("province ids", province_ids)
    _ensure_unique("coast ids", coast_ids)

    abbreviations = [row["abbreviation"] for row in provinces]
    _ensure_unique("province abbreviations", abbreviations)

    coast_by_id = {row["id"]: row for row in coasts}
    province_by_id = {row["id"]: row for row in provinces}

    for province in provinces:
        _validate_province_references(province, province_by_id, coast_by_id, all_ids)
        _validate_province_type_contracts(province, province_by_id, coast_by_id)

    for coast in coasts:
        _validate_coast_references(coast, province_by_id, coast_by_id, all_ids)

    for province in provinces:
        owner = province["home_center_owner"]
        if owner is not None and not province["supply_center"]:
            raise MapValidationError(
                f"Home center must be a supply center: province={province['id']}"
            )

    for sea_id in payload["convoy_passable_seas"]:
        province = province_by_id.get(sea_id)
        if province is None:
            raise MapValidationError(f"Unknown convoy-passable sea: {sea_id}")
        if province["province_type"] != "sea":
            raise MapValidationError(
                f"Convoy-passable entry must be sea province: {sea_id}"
            )


def _ensure_unique(name: str, values: list[str]) -> None:
    duplicates = [value for value, count in Counter(values).items() if count > 1]
    if duplicates:
        raise MapValidationError(f"Duplicate {name}: {sorted(duplicates)}")


def _validate_province_references(
    province: dict[str, Any],
    province_by_id: dict[str, dict[str, Any]],
    coast_by_id: dict[str, dict[str, Any]],
    all_ids: set[str],
) -> None:
    pid = province["id"]
    for army_dest in province["army_adjacency"]:
        if army_dest not in province_by_id:
            raise MapValidationError(
                f"Army adjacency must reference province ids only: {pid} -> {army_dest}"
            )
    for fleet_dest in province["fleet_adjacency"]:
        if fleet_dest not in all_ids:
            raise MapValidationError(f"Unknown fleet adjacency target: {pid} -> {fleet_dest}")
    for coast_id in province["coasts"]:
        coast = coast_by_id.get(coast_id)
        if coast is None:
            raise MapValidationError(f"Province references unknown coast: {pid} -> {coast_id}")
        if coast["province_id"] != pid:
            raise MapValidationError(
                f"Coast parent mismatch: coast={coast_id} province={pid} parent={coast['province_id']}"
            )


def _validate_province_type_contracts(
    province: dict[str, Any],
    province_by_id: dict[str, dict[str, Any]],
    coast_by_id: dict[str, dict[str, Any]],
) -> None:
    pid = province["id"]
    ptype = province["province_type"]
    occupiable_by = tuple(province["occupiable_by"])
    army_targets = province["army_adjacency"]
    fleet_targets = province["fleet_adjacency"]

    if ptype == "inland":
        if occupiable_by != ("A",):
            raise MapValidationError(f"Inland province must be occupiable_by ['A']: {pid}")
        if fleet_targets:
            raise MapValidationError(f"Inland province cannot have fleet adjacency: {pid}")
    elif ptype == "sea":
        if occupiable_by != ("F",):
            raise MapValidationError(f"Sea province must be occupiable_by ['F']: {pid}")
        if army_targets:
            raise MapValidationError(f"Sea province cannot have army adjacency: {pid}")
    elif ptype == "coastal":
        if occupiable_by != ("A", "F"):
            raise MapValidationError(
                f"Coastal province must be occupiable_by ['A', 'F']: {pid}"
            )
    else:
        raise MapValidationError(f"Unknown province_type: {ptype} ({pid})")

    for dest in army_targets:
        if province_by_id[dest]["province_type"] == "sea":
            raise MapValidationError(f"Army adjacency references sea province: {pid} -> {dest}")

    for dest in fleet_targets:
        if dest in coast_by_id:
            continue
        if province_by_id[dest]["province_type"] == "inland":
            raise MapValidationError(
                f"Fleet adjacency cannot reference inland province: {pid} -> {dest}"
            )


def _validate_coast_references(
    coast: dict[str, Any],
    province_by_id: dict[str, dict[str, Any]],
    coast_by_id: dict[str, dict[str, Any]],
    all_ids: set[str],
) -> None:
    coast_id = coast["id"]
    province_id = coast["province_id"]
    province = province_by_id.get(province_id)
    if province is None:
        raise MapValidationError(f"Coast references unknown province: {coast_id} -> {province_id}")
    if province["province_type"] != "coastal":
        raise MapValidationError(
            f"Coast parent must be coastal province: {coast_id} -> {province_id}"
        )
    for dest in coast["fleet_adjacency"]:
        if dest not in all_ids:
            raise MapValidationError(f"Unknown coast fleet adjacency target: {coast_id} -> {dest}")
        if dest in coast_by_id:
            continue
        if province_by_id[dest]["province_type"] == "inland":
            raise MapValidationError(
                f"Coast fleet adjacency cannot reference inland province: {coast_id} -> {dest}"
            )
