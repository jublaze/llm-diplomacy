from __future__ import annotations

from llm_diplomacy.map.loader import load_standard_map


EXPECTED_PROVINCES = {
    "adr", "aeg", "alb", "ank", "apu", "arm", "bal", "bar", "bel", "ber", "bla", "boh", "bot", "bre",
    "bud", "bul", "bur", "cly", "con", "den", "eas", "edi", "eng", "fin", "gal", "gas", "gre",
    "hel", "hol", "ion", "iri", "kie", "lvn", "lon", "lvp", "lyo", "mao", "mar", "mos", "mun",
    "naf", "nao", "nap", "nth", "nwy", "nwg", "par", "pic", "pie", "por", "pru", "rom", "ruh",
    "rum", "ser", "sev", "sil", "ska", "smy", "spa", "stp", "swe", "syr", "tri", "tun", "tus",
    "tyr", "tys", "ukr", "ven", "vie", "wal", "war", "wes", "yor",
}

EXPECTED_SUPPLY_CENTERS = {
    "ank", "bel", "ber", "bre", "bud", "bul", "con", "den", "edi", "gre", "hol", "kie", "lon", "lvp", "mar",
    "mos", "mun", "nap", "nwy", "par", "por", "rom", "rum", "ser", "sev", "smy", "spa", "stp", "swe", "tri",
    "tun", "ven", "vie", "war",
}

EXPECTED_HOME_CENTERS = {
    "austria": {"vie", "bud", "tri"},
    "england": {"lon", "edi", "lvp"},
    "france": {"par", "bre", "mar"},
    "germany": {"ber", "kie", "mun"},
    "italy": {"rom", "ven", "nap"},
    "russia": {"mos", "war", "sev", "stp"},
    "turkey": {"ank", "con", "smy"},
}


def test_map_audit_province_catalog_matches_spec() -> None:
    loaded = load_standard_map()
    actual = {province.id for province in loaded.provinces}
    assert actual == EXPECTED_PROVINCES


def test_map_audit_supply_centers_match_spec() -> None:
    loaded = load_standard_map()
    actual = {province.id for province in loaded.provinces if province.supply_center}
    assert actual == EXPECTED_SUPPLY_CENTERS


def test_map_audit_home_centers_match_spec() -> None:
    loaded = load_standard_map()
    actual: dict[str, set[str]] = {}
    for province in loaded.provinces:
        if province.home_center_owner:
            actual.setdefault(province.home_center_owner, set()).add(province.id)
    assert actual == EXPECTED_HOME_CENTERS


def test_map_audit_connector_regions_have_expected_dual_access() -> None:
    loaded = load_standard_map()
    by_id = {province.id: province for province in loaded.provinces}
    den = by_id["den"]
    kie = by_id["kie"]
    con = by_id["con"]

    assert "swe" in den.army_adjacency and "swe" in den.fleet_adjacency
    assert "hol" in kie.fleet_adjacency and "ruh" in kie.army_adjacency
    assert "ank" in con.army_adjacency and "ank" in con.fleet_adjacency
