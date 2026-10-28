from __future__ import annotations

import json

import pytest

from llm_diplomacy.runner.recovery import ExperimentRecovery, RecoveryLock, canonical_hash


def test_fresh_checkpoint_resume_identity_and_corruption_are_fail_closed(tmp_path):
    root = tmp_path / "experiment"
    identity = {"experiment_id": "case-a", "config_hash": "abc", "code_hash": "v1"}
    fresh = ExperimentRecovery(root, identity=identity, resume=False)
    assert fresh.prepare() is None
    resumed = ExperimentRecovery(root, identity=identity, resume=True)
    checkpoint = resumed.prepare()
    assert checkpoint["cursor"] == {"game_index": 1, "unit": "game_start"}
    with pytest.raises(FileExistsError):
        ExperimentRecovery(root, identity=identity, resume=False).prepare()
    with pytest.raises(ValueError, match="identity"):
        ExperimentRecovery(root, identity={**identity, "code_hash": "v2"}, resume=True).prepare()
    resumed.checkpoint_path.write_text('{"cursor":null}', encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt"):
        ExperimentRecovery(root, identity=identity, resume=True).prepare()


def test_journal_reuses_only_durable_received_response(tmp_path):
    recovery = ExperimentRecovery(tmp_path, identity={"id": "a"}, resume=False)
    recovery.prepare()
    recovery.record_response(
        decision_id="g1/p/a/orders", prompt_hash="h1", attempt_id="attempt-1",
        provider={"provider_name": "mock"}, response={"content": "{}", "model": "mock", "raw": {}},
        usage={"input_tokens": 1}, error_category=None, outcome="received",
    )
    recovery.record_response(
        decision_id="g1/p/b/orders", prompt_hash="h2", attempt_id="attempt-2",
        provider={"provider_name": "mock"}, response=None, usage=None,
        error_category="provider_timeout", outcome="unknown",
    )
    assert recovery.cached_response("g1/p/a/orders", "h1")["attempt_id"] == "attempt-1"
    assert recovery.cached_response("g1/p/a/orders", "other") is None
    assert recovery.cached_response("g1/p/b/orders", "h2") is None
    assert "api_key" not in recovery.journal_path.read_text(encoding="utf-8")


def test_exclusive_recovery_lock_rejects_concurrent_writer(tmp_path):
    path = tmp_path / "writer.lock"
    with RecoveryLock(path):
        with pytest.raises(RuntimeError, match="active recovery writer"):
            with RecoveryLock(path):
                pass


def test_checkpoint_checksum_covers_cursor_and_state(tmp_path):
    recovery = ExperimentRecovery(tmp_path, identity={"id": "a"}, resume=False)
    recovery.prepare()
    recovery.checkpoint(cursor={"unit": "phase"}, state={"year": 1901}, applied=[])
    checkpoint = json.loads(recovery.checkpoint_path.read_text(encoding="utf-8"))
    body = {key: value for key, value in checkpoint.items() if key != "sha256"}
    assert checkpoint["sha256"] == canonical_hash(body)


def test_correction_status_suppresses_only_the_bound_parent_rejection(tmp_path):
    recovery = ExperimentRecovery(tmp_path / "experiment", identity={"id": "correct"}, resume=False)
    recovery.prepare()
    decision, base_hash, correction_hash = "g1/p/once/france/actor/submit_orders", "base", "correction"
    parent_response = {"content": '{"orders":[]}', "model": "mock", "raw": {}}
    recovery.record_response(decision_id=decision, prompt_hash=base_hash, attempt_id="parent",
                             provider={}, response=parent_response, usage=None,
                             error_category=None, outcome="received")
    recovery.record_validation(decision_id=decision, prompt_hash=base_hash, attempt_id="parent",
                               valid=False, issue="rejected answer")
    feedback = {"engine_errors": [{"reason": "missing_order"}], "legal_options": ["HOLD"]}
    intent = recovery.prepare_order_correction(decision_id=decision, base_prompt_hash=base_hash,
                                               feedback=feedback, correction_prompt_hash=correction_hash)
    assert intent["parent_validation_hash"]
    assert recovery.status()["rejected_responses"]
    recovery.record_response(decision_id=decision, prompt_hash=correction_hash, attempt_id="corrected",
                             provider={}, response={"content": '{"orders":["HOLD"]}'}, usage=None,
                             error_category=None, outcome="received")
    recovery.record_validation(decision_id=decision, prompt_hash=correction_hash, attempt_id="corrected",
                               valid=True, issue=None)
    assert recovery.status()["rejected_responses"] == []

    recovery.record_validation(decision_id=decision, prompt_hash=base_hash, attempt_id="parent",
                               valid=False, issue="new rejection after correction")
    assert recovery.status()["rejected_responses"] == [
        {"decision_id": decision, "prompt_hash": base_hash, "attempt_id": "parent",
         "issue": "new rejection after correction"}
    ]


def test_reflection_correction_intent_binds_method_and_parent_issue(tmp_path):
    recovery = ExperimentRecovery(tmp_path / "experiment", identity={"id": "reflect-correct"}, resume=False)
    recovery.prepare()
    decision = "g1/p/once/russia/agent/reflect"
    parent_response = {"content": '{"schema_version":"reflection_campaign_v1"}', "model": "mock", "raw": {}}
    recovery.record_response(decision_id=decision, prompt_hash="base", attempt_id="parent", provider={},
                             response=parent_response, usage={"total_tokens": 4},
                             error_category=None, outcome="received")
    recovery.record_validation(decision_id=decision, prompt_hash="base", attempt_id="parent",
                               valid=False, issue="wrong reflection schema")
    feedback = {"method": "reflect", "validator_issue": "wrong reflection schema",
                "rejected_answer": parent_response["content"], "required_schema": "reflection_compact_v3_campaign_memory"}
    intent = recovery.prepare_order_correction(decision_id=decision, base_prompt_hash="base", feedback=feedback,
        correction_prompt_hash="correction", method="reflect")
    assert intent["method"] == "reflect"
    recovery._validate_journal_semantics(recovery._journal_rows())
    before = recovery.journal_path.read_bytes()
    with pytest.raises(ValueError, match="method must match"):
        recovery.prepare_order_correction(decision_id=decision, base_prompt_hash="base", feedback=feedback,
            correction_prompt_hash="another", method="submit_orders")
    assert recovery.journal_path.read_bytes() == before


def test_negotiation_correction_intent_binds_method_and_parent_issue(tmp_path):
    recovery = ExperimentRecovery(tmp_path / "experiment", identity={"id": "negotiate-correct"}, resume=False)
    recovery.prepare()
    decision = "g1/p/tick-3/austria/agent/negotiate_tick"
    parent_response = {"content": '{"private_messages":[{"recipientient":"england"}]}', "model": "mock", "raw": {}}
    recovery.record_response(decision_id=decision, prompt_hash="base", attempt_id="parent", provider={},
                             response=parent_response, usage={"total_tokens": 4},
                             error_category=None, outcome="received")
    issue = "private_messages.recipient must be non-empty string"
    recovery.record_validation(decision_id=decision, prompt_hash="base", attempt_id="parent",
                               valid=False, issue=issue)
    feedback = {"method": "negotiate_tick", "validator_issue": issue,
                "rejected_answer": parent_response["content"],
                "required_fields": ["private_messages", "global_messages", "relationship_updates",
                                    "withheld_information"]}
    intent = recovery.prepare_order_correction(decision_id=decision, base_prompt_hash="base", feedback=feedback,
        correction_prompt_hash="corrected", method="negotiate_tick")
    assert intent["method"] == "negotiate_tick"
    recovery._validate_journal_semantics(recovery._journal_rows())
    before = recovery.journal_path.read_bytes()
    with pytest.raises(ValueError, match="method must match"):
        recovery.prepare_order_correction(decision_id=decision, base_prompt_hash="base", feedback=feedback,
            correction_prompt_hash="other", method="reflect")
    assert recovery.journal_path.read_bytes() == before


def test_safe_halt_marker_is_durable_and_blocks_dispatch(tmp_path):
    recovery = ExperimentRecovery(tmp_path, identity={"id": "halt"}, resume=False)
    recovery.prepare()
    recovery.request_safe_halt("operator noticed an issue")
    assert recovery.status()["safe_halt_requested"] is True
    with pytest.raises(RuntimeError, match="Safe halt requested"):
        recovery.check_safe_halt()
    assert recovery.status()["halt_required"] is True
    recovery.clear_safe_halt()
    assert recovery.status()["safe_halt_requested"] is False


def test_retry_invalid_requires_durable_rejection(tmp_path):
    recovery = ExperimentRecovery(tmp_path, identity={"id": "retry"}, resume=False)
    recovery.prepare()
    recovery.record_response(decision_id="d", prompt_hash="h", attempt_id="a", provider={},
                            response={"content": "{}"}, usage=None, error_category=None,
                            outcome="received")
    with pytest.raises(ValueError, match="durably rejected"):
        recovery.retry_cached_response(decision_id="d", prompt_hash="h", attempt_id="a",
                                       choice="retry_after_review")


def test_partial_tree_publish_finishes_using_pre_and_post_hashes(tmp_path):
    recovery = ExperimentRecovery(tmp_path / "experiment", identity={"id": "a"}, resume=False)
    recovery.prepare()
    recovery.prepare_working_tree()
    stage = recovery.working_root / "experiments" / "case"
    canonical = tmp_path / "experiment" / "published"
    (stage / "games" / "game_001").mkdir(parents=True)
    (stage / "games" / "game_001" / "board.json").write_text('{"year":1901}', encoding="utf-8")
    (stage / "logs").mkdir()
    (stage / "logs" / "events.jsonl").write_text('{"event":"done"}\n', encoding="utf-8")
    recovery.commit_tree(staged_root=stage, canonical_root=canonical,
                         cursor={"unit": "game_completed"}, state={"year": 1901}, applied=[],
                         stop_after_files=1)
    assert (canonical / "games" / "game_001" / "board.json").exists()
    assert not (canonical / "logs" / "events.jsonl").exists()
    recovery.finish_pending_commit(canonical_root=canonical)
    assert (canonical / "logs" / "events.jsonl").read_text(encoding="utf-8") == '{"event":"done"}\n'
    assert not (recovery.path / "commit_manifest.json").exists()


def test_snapshot_inventory_and_valid_json_journal_truncation_fail_before_stage_move(tmp_path):
    root = tmp_path / "experiment"
    recovery = ExperimentRecovery(root, identity={"id": "tamper"}, resume=False)
    recovery.prepare()
    recovery.prepare_working_tree()
    source = recovery.working_root / "agents" / "cassandra" / "memory.md"
    source.parent.mkdir(parents=True)
    source.write_text("original", encoding="utf-8")
    recovery.save_initial_snapshot()
    recovery.record_dispatch(decision_id="g/p/o/FR/agent/submit_orders", prompt_hash="h",
                             attempt_id="a", provider={})
    staged = recovery.working_root / "stage-sentinel.txt"
    staged.write_text("preserve", encoding="utf-8")
    snapshot_file = recovery.path / "initial_snapshot" / "agents" / "cassandra" / "memory.md"
    snapshot_file.write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="snapshot inventory"):
        ExperimentRecovery(root, identity={"id": "tamper"}, resume=True).prepare_working_tree()
    assert staged.exists()

    # Restore the snapshot, then truncate the durable journal at a complete JSON
    # line. The independent head marker must detect the lost committed tail.
    snapshot_file.write_text("original", encoding="utf-8")
    lines = recovery.journal_path.read_bytes().splitlines(keepends=True)
    recovery.journal_path.write_bytes(b"".join(lines[:-1]))
    with pytest.raises(ValueError, match="truncated"):
        ExperimentRecovery(root, identity={"id": "tamper"}, resume=True).prepare_working_tree()
    assert staged.exists()


def test_zero_dispatch_crash_before_snapshot_bootstraps_without_trusting_partial_stage(tmp_path):
    root = tmp_path / "experiment"
    recovery = ExperimentRecovery(root, identity={"id": "bootstrap"}, resume=False)
    recovery.prepare()
    recovery.prepare_working_tree()
    partial = recovery.working_root / "prepared-agent.txt"
    partial.write_text("partial", encoding="utf-8")
    resumed = ExperimentRecovery(root, identity={"id": "bootstrap"}, resume=True)
    resumed.prepare_working_tree()
    assert resumed.needs_bootstrap
    assert not (resumed.working_root / "prepared-agent.txt").exists()
    assert list((root / "recovery" / "partials").glob("stage-*"))


def test_crash_between_snapshot_rename_and_inventory_recovers_as_zero_dispatch_bootstrap(tmp_path):
    root = tmp_path / "experiment"
    recovery = ExperimentRecovery(root, identity={"id": "snapshot-gap"}, resume=False)
    recovery.prepare()
    recovery.prepare_working_tree()
    (recovery.working_root / "folio.json").write_text("{}", encoding="utf-8")
    recovery.save_initial_snapshot()
    (recovery.path / "snapshot_inventory.json").unlink()
    resumed = ExperimentRecovery(root, identity={"id": "snapshot-gap"}, resume=True)
    resumed.prepare_working_tree()
    assert resumed.needs_bootstrap
    assert list((root / "recovery" / "partials").glob("initial_snapshot-*"))


def test_first_created_journal_row_without_head_can_finish_zero_dispatch_bootstrap(tmp_path):
    root = tmp_path / "experiment"
    recovery = ExperimentRecovery(root, identity={"id": "first-row"}, resume=False)
    recovery.prepare()
    recovery.checkpoint_path.unlink()
    recovery.journal_head_path.unlink()
    resumed = ExperimentRecovery(root, identity={"id": "first-row"}, resume=True)
    checkpoint = resumed.prepare()
    assert checkpoint["identity"] == {"id": "first-row"}
    assert resumed.needs_bootstrap
    assert resumed.journal_head_path.is_file()
