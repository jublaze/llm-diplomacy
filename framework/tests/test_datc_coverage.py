import copy
import hashlib
import json
from pathlib import Path

import pytest

from llm_diplomacy.compliance.datc import check_datc_coverage, ingest_datc_fixtures
from llm_diplomacy.rules.diplomacy import DiplomacyRuleEngine

CORPUS = Path(__file__).parent / "fixtures/datc/upstream"
CASES = ingest_datc_fixtures(CORPUS)


def test_pinned_source_and_official_coverage():
    assert hashlib.sha256((CORPUS / "datc.json").read_bytes()).hexdigest() == "6c2300a4550a9ae21e824f9b5f0e7e039cf6802bbe07efc0ac3148a9eb43e711"
    manifest = json.loads((CORPUS / "coverage.json").read_text())
    assert len({row["official_reference"] for row in manifest["cases"].values()}) == 164
    assert all(case.expected or case.unsupported_reason for case in CASES)


def test_coverage_accounts_for_adapter_limitation_separately(tmp_path):
    manifest = json.loads((CORPUS / "coverage.json").read_text(encoding="utf-8"))
    report = check_datc_coverage(DiplomacyRuleEngine(), CASES, CORPUS / "coverage.json")
    assert report == {
        "total": 172,
        "passed": 155,
        "policy_deviations": 9,
        "adapter_limitations": 1,
        "historical_alternatives": 7,
        "unexplained": [],
    }
    ordinary_g11 = manifest["cases"]["t6g11_a_convoy_to_an_adjacent_place_with_a_paradox"]
    explicit_g11 = manifest["cases"]["t6g11_variant_an_explicit_convoy_to_an_adjacent_place_with_a_paradox"]
    assert ordinary_g11["status"] == "adapter_limitation"
    assert explicit_g11["status"] == "pass"
    assert "DIPLOMACY_RULE_SPEC" not in ordinary_g11["reason"]
    assert ordinary_g11["result_sha256"] == "62516dfb828388739159ff73925e738e84c349fae015a8a3b2a3306c9616c2a8"

    ordinary_g11["result_sha256"] = "unexpected"
    changed = tmp_path / "coverage.json"
    changed.write_text(json.dumps(manifest), encoding="utf-8")
    try:
        assert check_datc_coverage(DiplomacyRuleEngine(), CASES, changed)["unexplained"]
    finally:
        changed.unlink(missing_ok=True)


@pytest.mark.parametrize("case", [c for c in CASES if not c.unsupported_reason], ids=lambda c: c.case_id)
def test_corpus_occupancy_and_order_permutation(case):
    engine = DiplomacyRuleEngine()
    original = engine.resolve_phase(case.initial_state, engine.validate_orders(case.initial_state, case.orders))
    state = copy.deepcopy(case.initial_state)
    state["units"].reverse()
    reversed_result = engine.resolve_phase(state, engine.validate_orders(state, list(reversed(case.orders))))
    for result in (original, reversed_result):
        active = [u["province"] for u in result["output_state"]["units"] if u.get("status") == "active"]
        assert len(active) == len(set(active))
        result["output_state"]["units"].sort(key=lambda u: u["unit_id"])
    assert original == reversed_result
    names = [u["unit_id"] for u in state["units"]]
    rename = {name: f"renamed_{len(names)-index:03d}" for index, name in enumerate(sorted(names))}
    def replace(value, mapping):
        if isinstance(value, str):
            for prefix in ("missing_", "auto_hold_"):
                if value.startswith(prefix) and value[len(prefix):] in mapping:
                    return prefix + mapping[value[len(prefix):]]
            return mapping.get(value, value)
        if isinstance(value, dict):
            return {key: replace(item, mapping) for key, item in value.items()}
        if isinstance(value, list):
            rows = [replace(item, mapping) for item in value]
            return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True)) if all(isinstance(row, dict) for row in rows) else rows
        return value
    renamed_state = replace(state, rename)
    renamed_orders = replace(case.orders, rename)
    renamed = engine.resolve_phase(renamed_state, engine.validate_orders(renamed_state, renamed_orders))
    assert replace(renamed, {value: key for key, value in rename.items()}) == replace(original, {})


def test_coverage_rejects_missing_case_and_changed_policy(tmp_path):
    manifest = json.loads((CORPUS / "coverage.json").read_text())
    policy = next(row for row in manifest["cases"].values() if row["status"] == "policy_deviation")
    policy["result_sha256"] = "changed"
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps(manifest))
    assert check_datc_coverage(DiplomacyRuleEngine(), CASES, path)["unexplained"]
    manifest["cases"].pop(next(iter(manifest["cases"])))
    path.write_text(json.dumps(manifest))
    assert check_datc_coverage(DiplomacyRuleEngine(), CASES, path)["unexplained"]


@pytest.mark.parametrize(("prefix", "convoys", "moved"), [
    ("t6g11_variant", {"swe": False}, {"nth"}),
    ("t6g11_a_", {"swe": False}, {"nth"}),
    ("t6f16_", {"bre": True}, {"bel"}),
    ("t6f17_", {"bre": True}, {"bel", "bre"}),
    ("t6f18_", {"lon": True}, {"lon", "ska"}),
    ("t6f22_", {"bre": True, "nwy": True}, set()),
    ("t6f23_", {"bre": False, "nwy": False}, set()),
])
def test_per_army_convoy_freeze_and_cycle_fallback(prefix, convoys, moved):
    case = next(c for c in CASES if c.case_id.startswith(prefix))
    engine = DiplomacyRuleEngine()
    result = engine.resolve_phase(case.initial_state, engine.validate_orders(case.initial_state, case.orders))
    origins = {u["unit_id"]: u["province"] for u in case.initial_state["units"]}
    assert {origins[r["army_unit_id"]]: r["status"] == "possible" for r in result["convoy_outcomes"]} == convoys
    assert {r["from"] for r in result["movement_outcomes"] if r["result"] == "moved"} == moved


def test_paradox_fallback_preserves_independent_convoy():
    case = next(c for c in CASES if c.case_id.startswith("t6f23_"))
    state, orders = copy.deepcopy(case.initial_state), copy.deepcopy(case.orders)
    state["units"].extend([
        {"unit_id": "independent_army", "owner": "austria", "type": "A", "province": "nap", "status": "active"},
        {"unit_id": "independent_fleet", "owner": "austria", "type": "F", "province": "ion", "status": "active"},
    ])
    orders.extend([
        {"schema_version": "1.0", "order_id": "independent_move", "unit_ref": "independent_army", "power": "austria", "type": "MOVE", "payload": {"from": "nap", "to": "tun", "via_convoy": True}},
        {"schema_version": "1.0", "order_id": "independent_convoy", "unit_ref": "independent_fleet", "power": "austria", "type": "CONVOY", "payload": {"from": "ion", "army_from": "nap", "army_to": "tun"}},
    ])
    engine = DiplomacyRuleEngine()
    result = engine.resolve_phase(state, engine.validate_orders(state, orders))
    assert {r["unit_id"] for r in result["movement_outcomes"] if r["result"] == "moved"} == {"independent_army"}
    assert {r["army_unit_id"] for r in result["convoy_outcomes"] if r["status"] == "possible"} == {"independent_army"}
