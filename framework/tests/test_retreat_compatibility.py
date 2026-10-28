from __future__ import annotations

import json

import pytest

from llm_diplomacy.runner.recovery import ExperimentRecovery, canonical_hash


def _approved_chain(tmp_path, *, retreat_receipt=True):
    root = tmp_path / "luna1912-1018-r2"
    base_manifest = {"runner/scripted_experiment_runner.py": "old-runner",
                     "runner/recovery.py": "old-recovery", "agents/baseline.py": "unchanged"}
    relation_manifest = {**base_manifest, "runner/scripted_experiment_runner.py": "six-runner"}
    target_manifest = {"runner/scripted_experiment_runner.py": "new-runner",
                       "runner/recovery.py": "new-recovery", "agents/baseline.py": "unchanged"}
    before = {"experiment_id": root.name, "schema_version": "1.0",
              "configuration_sha256": "cfg", "routes_sha256": "routes", "execution_data_sha256": "data",
              "code_sha256": canonical_hash(base_manifest)}
    relationship_identity = {**before, "code_sha256": canonical_hash(relation_manifest)}
    target_identity = {**before, "code_sha256": canonical_hash(target_manifest)}

    initial = ExperimentRecovery(root, identity=before, resume=False)
    initial.prepare()
    initial.prepare_working_tree()
    (initial.working_root / "snapshot.txt").write_text("stable", encoding="utf-8")
    initial.save_initial_snapshot()
    initial.record_response(decision_id="game_001/1901_spring_movement/once/france/a/submit_orders",
                            prompt_hash="old-prompt", attempt_id="old-response", provider={},
                            response={"content": '{"orders":[]}'}, usage=None, error_category=None,
                            outcome="received")
    initial.halt(reason="invalid_output")

    relationship = ExperimentRecovery(root, identity=relationship_identity, resume=True)
    relationship.approve_relationship_limit_six(expected_from=canonical_hash(before),
                                                expected_to=canonical_hash(relationship_identity))
    relationship.checkpoint(cursor={"unit": "phase_started", "game_id": "game_001",
                                    "phase_id": "1902_spring_retreat"},
                            state={"year": 1902, "season": "spring", "phase": "retreat"}, applied=[])
    relationship.halt(reason="orders_rejected_or_substituted")
    for index, power in enumerate(("austria", "england", "france", "germany", "italy", "russia", "turkey")):
        decision_id = f"game_001/1902_spring_retreat/once/{power}/agent/submit_orders"
        attempt_id = f"retreat-{index}"
        relationship.record_response(decision_id=decision_id, prompt_hash=f"prompt-{index}",
                                     attempt_id=attempt_id, provider={},
                                     response={"content": '{"orders":[]}'}, usage=None,
                                     error_category=None, outcome="received")
        relationship.record_validation(decision_id=decision_id, prompt_hash=f"prompt-{index}",
                                       attempt_id=attempt_id, valid=True)
        relationship.record_validation(decision_id=decision_id, prompt_hash=f"prompt-{index}",
                                       attempt_id=attempt_id, valid=False, issue="old global rejection")

    if not retreat_receipt:
        return root, relationship_identity, relation_manifest, {"runner/scripted_experiment_runner.py": "six-runner",
                                                                "agents/baseline.py": "unchanged"}

    target = ExperimentRecovery(root, identity=target_identity, resume=True)
    target.approve_retreat_missing_order_upgrade(
        expected_from=canonical_hash(relationship_identity), expected_to=canonical_hash(target_identity),
        source_code_manifest=relation_manifest, target_code_manifest=target_manifest)
    return root, target_identity, relation_manifest, target_manifest


def test_relationship_only_checkpoint_still_resumes_before_retreat_receipt(tmp_path):
    root, identity, _, _ = _approved_chain(tmp_path, retreat_receipt=False)
    checkpoint = ExperimentRecovery(root, identity=identity, resume=True).prepare()
    assert checkpoint["identity"] == identity


def test_retreat_compatibility_approval_is_idempotent(tmp_path):
    root, identity, source_manifest, target_manifest = _approved_chain(tmp_path)
    recovery = ExperimentRecovery(root, identity=identity, resume=True)
    before = sum(row.get("kind") == "retreat_missing_order_upgrade" for row in recovery._journal_rows())
    recovery.approve_retreat_missing_order_upgrade(
        expected_from=canonical_hash(recovery._relationship_upgrade()["to_identity"]),
        expected_to=canonical_hash(identity), source_code_manifest=source_manifest,
        target_code_manifest=target_manifest)
    after = sum(row.get("kind") == "retreat_missing_order_upgrade" for row in recovery._journal_rows())
    assert before == after == 1


def test_retreat_compatibility_chains_after_relationship_approval_and_preserves_false_rows(tmp_path):
    root, identity, _, _ = _approved_chain(tmp_path)
    recovery = ExperimentRecovery(root, identity=identity, resume=True)
    checkpoint = recovery.prepare()
    assert checkpoint["identity"] != identity  # preserve the source checkpoint byte identity
    assert recovery.status()["rejected_responses"] == []
    received_before = [row for row in recovery._journal_rows()
                       if row.get("kind") == "attempt" and row.get("outcome") == "received"]
    assert len(received_before) == 8
    assert sum(row.get("kind") == "response_validation" and row.get("valid") is False
               for row in recovery._journal_rows()) == 7


def test_status_shows_new_failure_for_same_approved_retreat_attempt(tmp_path):
    root, identity, _, _ = _approved_chain(tmp_path)
    recovery = ExperimentRecovery(root, identity=identity, resume=True)
    attempt = next(row for row in recovery._journal_rows() if row.get("kind") == "attempt"
                   and row.get("decision_id", "").startswith("game_001/1902_spring_retreat/"))
    recovery.record_validation(decision_id=attempt["decision_id"], prompt_hash=attempt["prompt_hash"],
                               attempt_id=attempt["attempt_id"], valid=False, issue="NEW_REAL_FAILURE")
    rejected = recovery.status()["rejected_responses"]
    assert len(rejected) == 1
    assert rejected[0]["attempt_id"] == attempt["attempt_id"]
    assert rejected[0]["issue"] == "NEW_REAL_FAILURE"


def test_retreat_compatibility_survives_later_checkpoint_and_inflight_request(tmp_path):
    root, identity, _, _ = _approved_chain(tmp_path)
    recovery = ExperimentRecovery(root, identity=identity, resume=True)
    recovery.prepare()
    recovery.checkpoint(cursor={"unit": "phase_completed", "game_id": "game_001",
                               "phase_id": "1902_spring_retreat"}, state={}, applied=[])
    recovery.record_prepared(decision_id="game_001/1902_fall_movement/once/england/a/submit_orders",
                            prompt_hash="next-prompt", attempt_id="next-attempt", provider={})
    assert recovery.prepare()["identity"] == identity
    assert recovery.status()["in_flight"][0]["attempt_id"] == "next-attempt"


def test_retreat_compatibility_rejects_unapproved_package_changes(tmp_path):
    from scripts.prepare_retreat_resume import validate_package_change

    baseline = {"runner/scripted_experiment_runner.py": "a", "runner/recovery.py": "b",
                "agents/unchanged.py": "c"}
    current = {**baseline, "runner/scripted_experiment_runner.py": "d",
               "runner/recovery.py": "e"}
    assert validate_package_change(baseline, current, canonical_hash(current)) == [
        "runner/recovery.py", "runner/scripted_experiment_runner.py"]
    with pytest.raises(ValueError, match="allowlist"):
        validate_package_change(baseline, {**current, "agents/unchanged.py": "tampered"},
                                canonical_hash({**current, "agents/unchanged.py": "tampered"}))
    with pytest.raises(ValueError, match="file set"):
        validate_package_change(baseline, {**current, "new.py": "added"},
                                canonical_hash({**current, "new.py": "added"}))


def test_retreat_compatibility_rejects_saved_response_tamper(tmp_path):
    root, identity, _, _ = _approved_chain(tmp_path)
    recovery = ExperimentRecovery(root, identity=identity, resume=True)
    rows = recovery._journal_rows()
    response = next(row for row in rows if row.get("kind") == "attempt"
                    and row.get("decision_id", "").startswith("game_001/1902_spring_retreat/"))
    response["response"]["content"] = '{"orders":["changed"]}'
    lines = []
    previous = None
    for sequence, row in enumerate(rows):
        row = {key: value for key, value in row.items() if key not in {"sha256", "sequence", "previous_hash"}}
        body = {**row, "sequence": sequence, "previous_hash": previous}
        digest = canonical_hash(body)
        lines.append({**body, "sha256": digest})
        previous = digest
    recovery.journal_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in lines),
                                    encoding="utf-8")
    recovery.journal_head_path.write_text(json.dumps(
        {"sequence": lines[-1]["sequence"], "sha256": lines[-1]["sha256"]}), encoding="utf-8")
    with pytest.raises(ValueError):
        ExperimentRecovery(root, identity=identity, resume=True).prepare()

