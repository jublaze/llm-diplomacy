from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Coast:
    schema_version: str
    id: str
    province_id: str
    fleet_adjacency: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "province_id": self.province_id,
            "fleet_adjacency": list(self.fleet_adjacency),
        }


@dataclass(frozen=True)
class Province:
    schema_version: str
    id: str
    display_name: str
    abbreviation: str
    province_type: str
    inland: bool
    coastal: bool
    sea: bool
    supply_center: bool
    home_center_owner: str | None
    occupiable_by: tuple[str, ...]
    coasts: tuple[str, ...]
    army_adjacency: tuple[str, ...]
    fleet_adjacency: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "display_name": self.display_name,
            "abbreviation": self.abbreviation,
            "province_type": self.province_type,
            "inland": self.inland,
            "coastal": self.coastal,
            "sea": self.sea,
            "supply_center": self.supply_center,
            "home_center_owner": self.home_center_owner,
            "occupiable_by": list(self.occupiable_by),
            "coasts": list(self.coasts),
            "army_adjacency": list(self.army_adjacency),
            "fleet_adjacency": list(self.fleet_adjacency),
        }


@dataclass(frozen=True)
class MapDefinition:
    schema_version: str
    map_id: str
    version: int
    powers: tuple[str, ...]
    provinces: tuple[Province, ...]
    coasts: tuple[Coast, ...]
    convoy_passable_seas: tuple[str, ...]
    initial_state: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "map_id": self.map_id,
            "version": self.version,
            "powers": list(self.powers),
            "provinces": [province.to_dict() for province in self.provinces],
            "coasts": [coast.to_dict() for coast in self.coasts],
            "convoy_passable_seas": list(self.convoy_passable_seas),
            "initial_state": self.initial_state,
        }
