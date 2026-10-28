from __future__ import annotations

from llm_diplomacy.map.loader import load_standard_map


def test_all_adjacency_references_resolve() -> None:
    loaded = load_standard_map()
    province_ids = {province.id for province in loaded.provinces}
    coast_ids = {coast.id for coast in loaded.coasts}
    all_ids = province_ids | coast_ids

    for province in loaded.provinces:
        assert all(dest in province_ids for dest in province.army_adjacency)
        assert all(dest in all_ids for dest in province.fleet_adjacency)
        assert all(coast in coast_ids for coast in province.coasts)

    for coast in loaded.coasts:
        assert coast.province_id in province_ids
        assert all(dest in all_ids for dest in coast.fleet_adjacency)


def test_split_coast_provinces_validate() -> None:
    loaded = load_standard_map()
    split_lookup = {province.id: set(province.coasts) for province in loaded.provinces if province.coasts}
    assert split_lookup == {
        "bul": {"bul_ec", "bul_sc"},
        "spa": {"spa_nc", "spa_sc"},
        "stp": {"stp_nc", "stp_sc"},
    }


def test_denmark_kiel_constantinople_connectors_validate() -> None:
    loaded = load_standard_map()
    province_by_id = {province.id: province for province in loaded.provinces}

    den = province_by_id["den"]
    kie = province_by_id["kie"]
    con = province_by_id["con"]

    assert {"kie", "swe"} <= set(den.army_adjacency)
    assert {"bal", "hel", "nth", "ska", "swe", "kie"} <= set(den.fleet_adjacency)

    assert {"ber", "mun", "ruh"} <= set(kie.army_adjacency)
    assert {"bal", "den", "hel", "hol"} <= set(kie.fleet_adjacency)

    assert {"ank", "smy", "bul"} <= set(con.army_adjacency)
    assert {"aeg", "bla", "ank", "smy", "bul_ec", "bul_sc"} <= set(con.fleet_adjacency)


def test_no_orphan_provinces() -> None:
    loaded = load_standard_map()
    for province in loaded.provinces:
        has_edge = bool(province.army_adjacency or province.fleet_adjacency or province.coasts)
        assert has_edge, f"orphan province: {province.id}"
