import hashlib
import json

import pytest

from test_build_replay_receipt import fixture, authorize, DECISION
from llm_diplomacy.runner.recovery import canonical_hash


def evidence(recovery):
    rows = recovery._journal_rows()
    transition = next(row for row in rows if row["kind"] == "build_rule_replay_transition_v1")
    answer = next(row for row in rows if row.get("kind") == "attempt" and row.get("attempt_id") == "saved")
    context = {"game_id": "game_001", "year": 1908, "season": "winter", "phase": "adjustment",
               "units": [{"unit_id": p, "owner": "france", "type": "A", "province": p, "status": "active"}
                         for p in ("bre", "par", "mar")],
               "supply_ownership": {p: "france" for p in ("bre", "par", "mar", "bel")}}
    result = {"experiment": "game", "expected_boundary_gate": True,
        "attempted_provider_calls": {"generation": 0, "dispatch": 0}, "source_journal_prefix_unchanged": True,
        "ledger_unchanged": True, "budget_unchanged": True, "candidate_package_matches_receipt": True,
        "candidate_package_before": transition["to_identity"]["code_sha256"],
        "candidate_package_after": transition["to_identity"]["code_sha256"],
        "boundary_validation_trace": [{"valid": True, "context_sha256": canonical_hash(context),
                                       "payload_sha256": canonical_hash(json.loads(answer["response"]["content"]))}]}
    root = recovery.path / "build-rule-evidence"
    root.mkdir()
    (root / "result.json").write_text(json.dumps(result), encoding="utf8")
    (root / "context.json").write_text(json.dumps(context), encoding="utf8")
    result_sha = hashlib.sha256((root / "result.json").read_bytes()).hexdigest()
    (root / "tree.json").write_text(json.dumps({"scan": "windows_extended_length_paths", "difference_count": 0,
        "before_files": 1, "after_files": 1, "original_replay_result_sha256": result_sha}), encoding="utf8")
    return {key: {"name": name, "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest()}
            for key, name in (("result", "result.json"), ("context", "context.json"), ("tree_proof", "tree.json"))}


def test_adoption_appends_once_and_revalidates_evidence_without_activation(tmp_path):
    recovery, old, new = fixture(tmp_path)
    authorize(recovery, old, new)
    data = evidence(recovery)
    old_ledger = (recovery.root / "metrics/provider_attempts.jsonl").read_bytes()
    recovery.authorize_build_rule_adoption(evidence=data)
    before = recovery.journal_path.read_bytes()
    recovery.authorize_build_rule_adoption(evidence=data)
    assert recovery.journal_path.read_bytes() == before
    assert sum(row.get("kind") == "build_rule_boundary_adoption_v1" for row in recovery._journal_rows()) == 1
    recovery.prepare()
    with pytest.raises(ValueError, match="activation"):
        recovery.build_rule_replay_selector.select("future")
    assert (recovery.root / "metrics/provider_attempts.jsonl").read_bytes() == old_ledger
    (recovery.path / "build-rule-evidence/context.json").write_text('{}')
    with pytest.raises(ValueError, match="reviewed hash"):
        recovery.prepare()


def test_wrong_evidence_paths_refused_before_append(tmp_path):
    recovery, old, new = fixture(tmp_path)
    authorize(recovery, old, new)
    data = evidence(recovery)
    data["context"]["name"] = "../outside"
    before = recovery.journal_path.read_bytes()
    with pytest.raises(ValueError, match="path"):
        recovery.authorize_build_rule_adoption(evidence=data)
    assert recovery.journal_path.read_bytes() == before
