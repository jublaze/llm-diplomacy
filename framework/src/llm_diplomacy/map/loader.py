from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model import Coast, MapDefinition, Province
from .validator import validate_map_payload


def load_standard_map() -> MapDefinition:
    map_path = Path(__file__).resolve().parents[3] / "maps" / "standard.json"
    return load_map(map_path)


def load_map(path: str | Path) -> MapDefinition:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_map_payload(raw)
    return _to_map_definition(raw)


def _to_map_definition(raw: dict[str, Any]) -> MapDefinition:
    provinces = tuple(
        Province(
            schema_version=province["schema_version"],
            id=province["id"],
            display_name=province["display_name"],
            abbreviation=province["abbreviation"],
            province_type=province["province_type"],
            inland=province["inland"],
            coastal=province["coastal"],
            sea=province["sea"],
            supply_center=province["supply_center"],
            home_center_owner=province["home_center_owner"],
            occupiable_by=tuple(province["occupiable_by"]),
            coasts=tuple(province["coasts"]),
            army_adjacency=tuple(province["army_adjacency"]),
            fleet_adjacency=tuple(province["fleet_adjacency"]),
        )
        for province in raw["provinces"]
    )
    coasts = tuple(
        Coast(
            schema_version=coast["schema_version"],
            id=coast["id"],
            province_id=coast["province_id"],
            fleet_adjacency=tuple(coast["fleet_adjacency"]),
        )
        for coast in raw["coasts"]
    )
    return MapDefinition(
        schema_version=raw["schema_version"],
        map_id=raw["map_id"],
        version=raw["version"],
        powers=tuple(raw["powers"]),
        provinces=provinces,
        coasts=coasts,
        convoy_passable_seas=tuple(raw["convoy_passable_seas"]),
        initial_state=raw["initial_state"],
    )
