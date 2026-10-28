from __future__ import annotations

import json

import pytest

from llm_diplomacy.map.loader import load_map, load_standard_map
from llm_diplomacy.map.validator import MapValidationError


def _mutated_payload():
    return load_standard_map().to_dict()


def test_map_validator_rejects_duplicate_abbreviation(tmp_path) -> None:
    payload = _mutated_payload()
    payload["provinces"][1]["abbreviation"] = payload["provinces"][0]["abbreviation"]
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MapValidationError):
        load_map(p)


def test_map_validator_rejects_unknown_coast_reference(tmp_path) -> None:
    payload = _mutated_payload()
    payload["provinces"][0]["coasts"] = ["missing_coast"]
    p = tmp_path / "bad2.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MapValidationError):
        load_map(p)


def test_map_validator_rejects_non_sea_convoy_passable(tmp_path) -> None:
    payload = _mutated_payload()
    payload["convoy_passable_seas"] = ["par"]
    p = tmp_path / "bad3.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MapValidationError):
        load_map(p)


def test_map_validator_rejects_missing_required_top_level_field(tmp_path) -> None:
    payload = _mutated_payload()
    payload.pop("map_id")
    p = tmp_path / "bad4.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MapValidationError):
        load_map(p)


def test_map_validator_rejects_coast_with_inland_parent(tmp_path) -> None:
    payload = _mutated_payload()
    payload["coasts"][0]["province_id"] = "par"
    p = tmp_path / "bad5.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MapValidationError):
        load_map(p)
