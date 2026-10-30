import hashlib
import json

import pytest

from llm_diplomacy.runner.build_rule_proof import verify_reviewed_boundary
from llm_diplomacy.runner.recovery import canonical_hash


def sample(tmp_path):
    decision = "game_001/1908_winter_adjustment/once/france/metternich/submit_orders"
    payload = {"orders": []}
    response = {"content": json.dumps(payload)}
    attempts = [{"kind": "attempt", "outcome": "received", "decision_id": decision,
                 "attempt_id": f"a{i}", "prompt_hash": f"p{i}", "response": response} for i in range(3)]
    rows = [attempts[0], {"kind": "response_validation", "decision_id": decision, "valid": False},
            attempts[1], {"kind": "response_validation", "decision_id": decision, "valid": False}, attempts[2]]
    receipt = {"sha256": "receipt", "source_journal_count": len(rows),
               "from_identity": {"experiment_id": "game"}, "to_identity": {"code_sha256": "package"},
               "boundary": {"decision_id": decision, "attempt_id": "a2", "prompt_hash": "p2",
                            "response_hash": canonical_hash(response), "amend": True}}
    context = {"game_id": "game_001", "year": 1908, "season": "winter", "phase": "adjustment",
               "units": [{"unit_id": p, "owner": "france", "type": "A", "province": p, "status": "active"}
                         for p in ("bre", "par", "mar")],
               "supply_ownership": {p: "france" for p in ("bre", "par", "mar", "bel")}}
    result = {"experiment": "game", "expected_boundary_gate": True,
              "attempted_provider_calls": {"generation": 0, "dispatch": 0},
              "source_journal_prefix_unchanged": True, "ledger_unchanged": True, "budget_unchanged": True,
              "candidate_package_matches_receipt": True, "candidate_package_before": "package",
              "candidate_package_after": "package", "boundary_validation_trace": [
                  {"valid": False}, {"valid": False},
                  {"valid": True, "context_sha256": canonical_hash(context), "payload_sha256": canonical_hash(payload)}]}
    result_path, context_path, tree_path = (tmp_path / x for x in ("result", "context", "tree"))
    result_path.write_text(json.dumps(result), encoding="utf8")
    context_path.write_text(json.dumps(context), encoding="utf8")
    tree_path.write_text(json.dumps({"scan": "windows_extended_length_paths", "difference_count": 0,
        "before_files": 1, "after_files": 1,
        "original_replay_result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest()}), encoding="utf8")
    paths = {"result": result_path, "context": context_path, "tree_proof": tree_path}
    pins = {k: hashlib.sha256(v.read_bytes()).hexdigest() for k, v in paths.items()}
    return receipt, rows, paths, pins


def verify(receipt, rows, paths, pins):
    return verify_reviewed_boundary(receipt=receipt, journal_rows=rows, result_path=paths["result"],
        context_path=paths["context"], tree_proof_path=paths["tree_proof"], expected_hashes=pins)


def test_exact_replay_evidence_remains_non_authorizing(tmp_path):
    receipt, rows, paths, pins = sample(tmp_path)
    proof = verify(receipt, rows, paths, pins)
    assert proof["attempt_id"] == "a2" and proof["valid"] and not proof["adoption_authorized"]


def test_mutated_context_and_old_response_rejected(tmp_path):
    receipt, rows, paths, pins = sample(tmp_path)
    paths["context"].write_text('{}')
    with pytest.raises(ValueError, match="reviewed hash"):
        verify(receipt, rows, paths, pins)
    receipt, rows, paths, pins = sample(tmp_path)
    receipt["boundary"]["attempt_id"] = "a1"
    with pytest.raises(ValueError, match="latest saved response"):
        verify(receipt, rows, paths, pins)


def test_missing_rejections_and_generation_attempt_fail(tmp_path):
    receipt, rows, paths, pins = sample(tmp_path)
    rows[1]["valid"] = True
    with pytest.raises(ValueError, match="preserved rejection"):
        verify(receipt, rows, paths, pins)
    receipt, rows, paths, pins = sample(tmp_path)
    result = json.loads(paths["result"].read_text())
    result["attempted_provider_calls"]["generation"] = 1
    paths["result"].write_text(json.dumps(result))
    pins["result"] = hashlib.sha256(paths["result"].read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="no-provider"):
        verify(receipt, rows, paths, pins)
