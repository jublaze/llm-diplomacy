import copy
import json

import pytest

from llm_diplomacy.runner.recovery import ExperimentRecovery, atomic_json, canonical_hash


DECISION = "game_001/1908_winter_adjustment/once/france/metternich/submit_orders"


def fixture(tmp_path):
    root = tmp_path / "game"
    old = {"agents/llm_context.py": "old"}
    new = {"agents/llm_context.py": "new"}
    source = {"experiment_id": "game", "code_sha256": canonical_hash(old), "config": "unchanged"}
    target = {**source, "code_sha256": canonical_hash(new)}
    recovery = ExperimentRecovery(root, identity=source, resume=False)
    recovery.prepare()
    recovery.record_dispatch(decision_id=DECISION, attempt_id="saved", prompt_hash="exact", provider={})
    recovery.record_response(decision_id=DECISION, attempt_id="saved", prompt_hash="exact", provider={},
                             response={"content": '{"orders": []}'}, usage=None, error_category=None,
                             outcome="received")
    recovery.checkpoint(cursor={"game_id": "game_001", "phase_id": "1908_winter_adjustment", "unit": "phase_started"},
                        state={}, applied=[])
    recovery.halt(reason="synthetic")
    (root / "recovery/initial_snapshot").mkdir()
    atomic_json(root / "recovery/snapshot_inventory.json", {"version": 1, "inventory": [], "sha256": canonical_hash([])})
    (root / "metrics/budget").mkdir(parents=True)
    (root / "metrics/provider_attempts.jsonl").write_text("ledger\n", encoding="utf-8")
    (root / "metrics/budget/budget_state.json").write_text("budget\n", encoding="utf-8")
    return ExperimentRecovery(root, identity=target, resume=True), old, new


def authorize(recovery, old, new):
    recovery.authorize_build_rule_replay(source_code_manifest=old, target_code_manifest=new,
        decision_id=DECISION, attempt_id="saved", prompt_hash="exact", amend=True)


def test_replay_receipt_is_idempotent_and_installs_selector_on_normal_prepare(tmp_path):
    recovery, old, new = fixture(tmp_path)
    before_checkpoint = recovery.checkpoint_path.read_bytes()
    before_ledger = (recovery.root / "metrics/provider_attempts.jsonl").read_bytes()
    with pytest.raises(ValueError, match="identity"):
        recovery.prepare()
    authorize(recovery, old, new)
    after = recovery.journal_path.read_bytes()
    authorize(recovery, old, new)
    assert recovery.journal_path.read_bytes() == after
    recovery.prepare()
    assert recovery.build_rule_replay_selector.select(DECISION).legacy_prompt
    with pytest.raises(ValueError, match="activation"):
        recovery.build_rule_replay_selector.select("new-decision")
    assert recovery.checkpoint_path.read_bytes() == before_checkpoint
    assert (recovery.root / "metrics/provider_attempts.jsonl").read_bytes() == before_ledger


@pytest.mark.parametrize("changed", ["budget", "ledger", "snapshot"])
def test_changed_bound_evidence_blocks_normal_prepare(tmp_path, changed):
    recovery, old, new = fixture(tmp_path)
    authorize(recovery, old, new)
    paths = {"budget": recovery.root / "metrics/budget/budget_state.json",
             "ledger": recovery.root / "metrics/provider_attempts.jsonl",
             "snapshot": recovery.path / "snapshot_inventory.json"}
    paths[changed].write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="accounting changed"):
        recovery.prepare()


def test_wrong_package_and_changed_configuration_are_refused_before_append(tmp_path):
    recovery, old, new = fixture(tmp_path)
    before = recovery.journal_path.read_bytes()
    with pytest.raises(ValueError, match="manifest"):
        authorize(recovery, old, {"agents/llm_context.py": "tampered"})
    assert recovery.journal_path.read_bytes() == before
    recovery.identity["config"] = "changed"
    with pytest.raises(ValueError, match="configuration"):
        authorize(recovery, old, new)
    assert recovery.journal_path.read_bytes() == before


def test_newer_response_and_conflicting_receipt_are_rejected(tmp_path):
    recovery, old, new = fixture(tmp_path)
    recovery.record_dispatch(decision_id=DECISION, attempt_id="newer", prompt_hash="next", provider={})
    recovery.record_response(decision_id=DECISION, attempt_id="newer", prompt_hash="next", provider={},
                             response={"content": "{}"}, usage=None, error_category=None, outcome="received")
    before = recovery.journal_path.read_bytes()
    with pytest.raises(ValueError, match="latest"):
        authorize(recovery, old, new)
    assert recovery.journal_path.read_bytes() == before


def test_retry_after_receipt_append_before_head_completes_without_duplicate(tmp_path, monkeypatch):
    import llm_diplomacy.runner.recovery as module
    recovery, old, new = fixture(tmp_path)
    original = module.atomic_json
    def interrupt_head(path, value):
        if path == recovery.journal_head_path:
            raise OSError("injected head interruption")
        return original(path, value)
    monkeypatch.setattr(module, "atomic_json", interrupt_head)
    with pytest.raises(OSError, match="interruption"):
        authorize(recovery, old, new)
    monkeypatch.setattr(module, "atomic_json", original)
    rows = recovery._journal_rows()
    assert sum(r["kind"] == "build_rule_replay_transition_v1" for r in rows) == 1
    authorize(recovery, old, new)
    recovery.prepare()
    assert sum(r["kind"] == "build_rule_replay_transition_v1" for r in recovery._journal_rows()) == 1
    assert json.loads(recovery.journal_head_path.read_bytes())["sha256"] == recovery._journal_rows()[-1]["sha256"]


def test_boundary_rejects_nonempty_saved_orders(tmp_path):
    recovery, old, new = fixture(tmp_path)
    rows = recovery._journal_rows()
    # A separate exact received response can be selected only if it is actually empty.
    recovery.record_dispatch(decision_id=DECISION, attempt_id="newer", prompt_hash="next", provider={})
    recovery.record_response(decision_id=DECISION, attempt_id="newer", prompt_hash="next", provider={},
                             response={"content": '{"orders": [{"type": "BUILD"}]}'},
                             usage=None, error_category=None, outcome="received")
    before = recovery.journal_path.read_bytes()
    with pytest.raises(ValueError, match="empty order"):
        recovery.authorize_build_rule_replay(source_code_manifest=old, target_code_manifest=new,
            decision_id=DECISION, attempt_id="newer", prompt_hash="next", amend=True)
    assert recovery.journal_path.read_bytes() == before


def test_boundary_validation_is_not_accepted_without_adoption(tmp_path):
    recovery, old, new = fixture(tmp_path)
    authorize(recovery, old, new)
    recovery.record_validation(decision_id=DECISION, attempt_id="saved", prompt_hash="exact", valid=True)
    with pytest.raises(ValueError, match="before adoption"):
        recovery.prepare()


def test_wrong_experiment_and_unapproved_path_fail_before_append(tmp_path):
    recovery, old, new = fixture(tmp_path)
    before = recovery.journal_path.read_bytes()
    recovery.identity["experiment_id"] = "other"
    with pytest.raises(ValueError, match="experiment identity"):
        authorize(recovery, old, new)
    recovery.identity["experiment_id"] = "game"
    wrong = {"agents/llm_context.py": "new", "rules/engine.py": "added"}
    recovery.identity["code_sha256"] = canonical_hash(wrong)
    with pytest.raises(ValueError, match="unapproved code path"):
        authorize(recovery, old, wrong)
    assert recovery.journal_path.read_bytes() == before


def test_real_working_tree_replay_checkpoint_and_restart(tmp_path):
    recovery, old, new = fixture(tmp_path)
    source_checkpoint = recovery.checkpoint_path.read_bytes()
    authorize(recovery, old, new)
    recovery.prepare_working_tree()
    assert (recovery.path / "build-rule-source-checkpoint.json").read_bytes() == source_checkpoint
    recovery.prepare()
    cursor = {"game_id": "game_001", "phase_id": "1908_winter_adjustment", "unit": "phase_completed"}
    recovery.progress(cursor=cursor, state={"replay": "synthetic"})
    reopened = ExperimentRecovery(recovery.root, identity=recovery.identity, resume=True)
    assert reopened.prepare()["cursor"] == cursor
    assert reopened.build_rule_replay_selector is not None


def test_original_checkpoint_backup_tampering_blocks_restart(tmp_path):
    recovery, old, new = fixture(tmp_path)
    authorize(recovery, old, new)
    recovery.prepare_working_tree()
    (recovery.path / "build-rule-source-checkpoint.json").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="checkpoint backup changed"):
        recovery.prepare()


def test_drifted_checkpoint_is_rejected_even_with_valid_checksum(tmp_path):
    recovery, old, new = fixture(tmp_path)
    authorize(recovery, old, new)
    recovery.prepare_working_tree()
    checkpoint = json.loads(recovery.checkpoint_path.read_bytes())
    checkpoint["cursor"]["phase_id"] = "1925_winter_adjustment"
    body = {key: value for key, value in checkpoint.items() if key != "sha256"}
    atomic_json(recovery.checkpoint_path, {**body, "sha256": canonical_hash(body)})
    with pytest.raises(ValueError, match="differs from its durable receipt"):
        recovery.prepare()


def test_replay_rejects_phase_outside_historical_journal(tmp_path):
    recovery, old, new = fixture(tmp_path)
    authorize(recovery, old, new)
    recovery.prepare_working_tree()
    before = recovery.checkpoint_path.read_bytes()
    with pytest.raises(ValueError, match="outside historical phases"):
        recovery.progress(cursor={"game_id": "game_001", "phase_id": "2099_winter_adjustment", "unit": "phase_started"},
                          state={})
    assert recovery.checkpoint_path.read_bytes() == before


def test_interrupted_replay_checkpoint_keeps_previous_valid_checkpoint(tmp_path, monkeypatch):
    import llm_diplomacy.runner.recovery as module
    recovery, old, new = fixture(tmp_path)
    authorize(recovery, old, new)
    recovery.prepare_working_tree()
    before = recovery.checkpoint_path.read_bytes()
    original = module.atomic_json
    def interrupted(path, value):
        if path == recovery.checkpoint_path:
            raise OSError("checkpoint interrupted")
        return original(path, value)
    monkeypatch.setattr(module, "atomic_json", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        recovery.progress(cursor={"game_id": "game_001", "phase_id": "1908_winter_adjustment", "unit": "phase_completed"},
                          state={"replay": True})
    monkeypatch.setattr(module, "atomic_json", original)
    assert recovery.checkpoint_path.read_bytes() == before
    reopened = ExperimentRecovery(recovery.root, identity=recovery.identity, resume=True)
    assert reopened.prepare()["cursor"]["unit"] == "phase_started"


def test_cooperative_halt_during_replay_is_bound_and_restartable(tmp_path):
    recovery, old, new = fixture(tmp_path)
    authorize(recovery, old, new)
    recovery.prepare_working_tree()
    recovery.halt(reason="safe_halt:test", decision_id=DECISION)
    reopened = ExperimentRecovery(recovery.root, identity=recovery.identity, resume=True)
    assert reopened.prepare()["halt_reason"] == "safe_halt:test"
