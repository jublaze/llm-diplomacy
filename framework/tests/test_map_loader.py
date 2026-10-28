from __future__ import annotations

import json

from llm_diplomacy.map.loader import load_map, load_standard_map


def test_standard_map_loads() -> None:
    loaded = load_standard_map()
    assert loaded.schema_version == "1.0"
    assert loaded.map_id == "standard"
    assert len(loaded.provinces) == 75


def test_supply_center_count_is_34() -> None:
    loaded = load_standard_map()
    supply_centers = [province for province in loaded.provinces if province.supply_center]
    assert len(supply_centers) == 34


def test_serialization_round_trip(tmp_path) -> None:
    loaded = load_standard_map()
    payload = loaded.to_dict()
    roundtrip_path = tmp_path / "roundtrip_standard.json"
    roundtrip_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    loaded_roundtrip = load_map(roundtrip_path)
    assert loaded_roundtrip.to_dict() == payload
