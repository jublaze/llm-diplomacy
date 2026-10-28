from pathlib import Path
import runpy
import json

import pytest

from llm_diplomacy.agents.baseline_llm_runtime import BaselineLLMAgentRuntime
from llm_diplomacy.agents.llm_context import PromptContextBuilder
from llm_diplomacy.agents.llm_provider import FakeLLMProvider
from llm_diplomacy.runner.recovery import ExperimentRecovery, canonical_hash
import llm_diplomacy.runner.recovery as recovery_module
from test_baseline_llm_runtime import _ctx, _compact_negotiate_payload


def reflection(count):
    return {
        "schema_version": "reflection_compact_v3_campaign_memory", "phase_summary": "Border stable.",
        "strategic_position": "Defend centers.", "objective_update": {"current_objective": "Expand carefully.", "why_it_matters": "Win."},
        "outcome_review": "Orders preserve a route to growth.", "campaign_plan": "Expand when supported.",
        "commitment_memory": "No confirmed commitments.", "winter_assessment": None,
        "belief_summary": "Await orders.", "belief_updates": [], "action_lessons": [], "memory_candidates": [],
        "relationship_updates": [{"agent_id": f"opponent_{i}", "status": "conditional", "evidence": f"Observed order {i}.",
                                  "value_to_my_objectives": "Possible coordination.", "risk_or_limit": "Unverified.",
                                  "next_use": "Check the next order."} for i in range(count)],
    }


@pytest.mark.parametrize("count", [5, 6])
def test_reflection_preserves_every_opponent_through_memory(tmp_path, count):
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime", provider=FakeLLMProvider())
    context = _ctx(tmp_path)
    payload = reflection(count)
    assert runtime._reflection_normalization_issue(payload) is None
    normalized = runtime._normalize_reflection_candidate(payload)
    assert runtime._validate_payload(method="reflect", payload=normalized, context=context) == (True, "")
    coerced = runtime._coerce_reflection_payload(normalized)
    assert coerced["relationship_updates"] == payload["relationship_updates"]
    runtime.register_agent(agent_id=context.agent_id)
    runtime._update_game_memory_structured(context, coerced)
    memory = json.loads(runtime._game_memory_json_path(context).read_text(encoding="utf-8"))
    assert memory["relationship_updates"] == payload["relationship_updates"]


def test_seventh_relationship_is_rejected_before_strict_compaction(tmp_path):
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path, provider=FakeLLMProvider())
    assert "dropped or trimmed" in runtime._reflection_normalization_issue(reflection(7))
    valid, issue = runtime._validate_payload(method="reflect", payload=reflection(7), context=_ctx(tmp_path))
    assert not valid and "6" in issue
    assert len(runtime._coerce_reflection_payload(reflection(7))["relationship_updates"]) == 6


@pytest.mark.parametrize("method", ["reflect", "negotiate_tick"])
def test_prompt_schema_and_historical_format_are_separate(tmp_path, method):
    builder = PromptContextBuilder(folio_root=tmp_path)
    built = builder.build(_ctx(tmp_path))
    current = builder.build_prompt(method=method, built_context=built)
    historical = builder.build_prompt(method=method, built_context=built, legacy_relationship_limit=True)
    assert builder._method_schema(method)["properties"]["relationship_updates"]["maxItems"] == 6
    assert '"maxItems": 6' in current and '"maxItems": 6' not in historical
    if method == "reflect":
        assert "maximum 6 entries" in current and "maximum 6 entries" not in historical
    else:
        assert "relationship_updates: maximum 6" in current and "relationship_updates: maximum 3" in historical


def halted(tmp_path):
    before = {"code_sha256": "old", "configuration_sha256": "same"}
    old = ExperimentRecovery(tmp_path, identity=before, resume=False)
    old.prepare()
    old.prepare_working_tree()
    (old.working_root / "initial.json").write_text("{}")
    old.save_initial_snapshot()
    old.record_dispatch(decision_id="game/phase/once/russia/agent/reflect", prompt_hash="old-prompt", attempt_id="saved", provider={})
    old.record_response(decision_id="game/phase/once/russia/agent/reflect", prompt_hash="old-prompt", attempt_id="saved",
                        provider={}, response={"content": json.dumps(reflection(5))}, usage={}, error_category=None, outcome="received")
    old.record_validation(decision_id="game/phase/once/russia/agent/reflect", prompt_hash="old-prompt", attempt_id="saved", valid=False, issue="relationship limit")
    old.halt(reason="invalid_output")
    return old, {**before, "code_sha256": "new"}


def test_explicit_code_upgrade_preserves_history_and_requires_exact_target(tmp_path):
    old, identity = halted(tmp_path)
    recovery = ExperimentRecovery(tmp_path, identity=identity, resume=True)
    with pytest.raises(ValueError, match="identity"):
        recovery.prepare()
    prefix = old.journal_path.read_bytes()
    checkpoint = old.checkpoint_path.read_bytes()
    with pytest.raises(ValueError, match="approved target"):
        recovery.approve_relationship_limit_six(expected_from=canonical_hash(old.identity), expected_to="wrong")
    assert old.journal_path.read_bytes() == prefix
    recovery.approve_relationship_limit_six(expected_from=canonical_hash(old.identity), expected_to=canonical_hash(identity))
    assert old.journal_path.read_bytes().startswith(prefix)
    assert old.checkpoint_path.read_bytes() == checkpoint
    assert recovery.legacy_prompt_hash("game/phase/once/russia/agent/reflect") == "old-prompt"
    assert recovery.legacy_prompt_hash("next/decision") is None
    assert recovery.status()["rejected_responses"] == []
    assert recovery.status()["halt_required"] is True
    recovery.prepare()
    assert json.loads(recovery.cached_response("game/phase/once/russia/agent/reflect", "old-prompt")["response"]["content"])["relationship_updates"] == reflection(5)["relationship_updates"]
    changed = ExperimentRecovery(tmp_path, identity={**identity, "code_sha256": "unapproved"}, resume=True)
    with pytest.raises(ValueError, match="approval"):
        changed.prepare()


def test_upgrade_rejects_configuration_drift_without_writes(tmp_path):
    old, identity = halted(tmp_path)
    identity["configuration_sha256"] = "changed"
    recovery = ExperimentRecovery(tmp_path, identity=identity, resume=True)
    prefix = old.journal_path.read_bytes()
    with pytest.raises(ValueError, match="code changes only"):
        recovery.approve_relationship_limit_six(expected_from=canonical_hash(old.identity), expected_to=canonical_hash(identity))
    assert old.journal_path.read_bytes() == prefix


@pytest.mark.parametrize("count", [5, 6, 7])
def test_strict_negotiation_capacity(tmp_path, count):
    updates = [{"agent_id": f"opponent_{i}", "status": "conditional", "next_use": "Verify next order."}
               for i in range(count)]
    response = _compact_negotiate_payload(relationship_updates=updates)
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path,
                                     provider=FakeLLMProvider(responses_by_method={"negotiate_tick": json.dumps(response)}),
                                     halt_on_issue=True)
    runtime.register_agent(agent_id="agent_llm")
    if count > 6:
        with pytest.raises(RuntimeError, match="Recovery required before fallback"):
            runtime._invoke(method="negotiate_tick", context=_ctx(tmp_path))
    else:
        parsed, _ = runtime._invoke(method="negotiate_tick", context=_ctx(tmp_path))
        assert len(parsed["relationship_updates"]) == count


def test_legacy_relationship_lists_share_six_entry_capacity(tmp_path):
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path, provider=FakeLLMProvider())
    payload = reflection(0)
    payload["relationships_that_created_value"] = [{"agent_id": f"ally_{i}"} for i in range(3)]
    payload["relationships_that_stagnated"] = [{"agent_id": f"other_{i}"} for i in range(3)]
    assert runtime._reflection_normalization_issue(payload) is None
    assert len(runtime._normalize_reflection_candidate(payload)["relationship_updates"]) == 6
    payload = reflection(0)
    payload["relationships_that_created_value"] = [{"agent_id": f"ally_{i}"} for i in range(7)]
    assert "six-entry" in runtime._reflection_normalization_issue(payload)


@pytest.mark.parametrize("blocked", ["unknown", "skipped", "pending_commit"])
def test_preparation_refuses_unresolved_or_discarded_evidence(tmp_path, blocked):
    old, identity = halted(tmp_path)
    if blocked == "unknown":
        old.record_prepared(decision_id="other/decision", prompt_hash="h", attempt_id="unknown", provider={})
    elif blocked == "skipped":
        old.retry_cached_response(decision_id="game/phase/once/russia/agent/reflect", prompt_hash="old-prompt",
                                  attempt_id="saved", choice="retry_after_review")
    else:
        (old.path / "commit_manifest.json").write_text("{}")
    prefix = old.journal_path.read_bytes()
    recovery = ExperimentRecovery(tmp_path, identity=identity, resume=True)
    with pytest.raises(ValueError):
        recovery.approve_relationship_limit_six(expected_from=canonical_hash(old.identity), expected_to=canonical_hash(identity))
    assert old.journal_path.read_bytes() == prefix


def test_approved_decision_cannot_reissue_if_cache_was_explicitly_skipped(tmp_path):
    old, identity = halted(tmp_path)
    recovery = ExperimentRecovery(tmp_path, identity=identity, resume=True)
    recovery.approve_relationship_limit_six(expected_from=canonical_hash(old.identity), expected_to=canonical_hash(identity))
    recovery.retry_cached_response(decision_id="game/phase/once/russia/agent/reflect", prompt_hash="old-prompt",
                                   attempt_id="saved", choice="retry_after_review")
    with pytest.raises(ValueError, match="refusing another request"):
        recovery.legacy_prompt_hash("game/phase/once/russia/agent/reflect")


def test_free_text_migration_blocks_new_dispatch_until_adoption_is_accepted(tmp_path, monkeypatch):
    recovery = ExperimentRecovery(tmp_path, identity={"code_sha256": "current"}, resume=True)
    adoption = {
        "decision_id": "game_001/1903_fall_retreat/once/germany/shaver/reflect",
        "attempt_id": "db7b6fab-7972-4554-9768-5db6d5ebb035",
        "prompt_hash": "b81704cd87c4973624edabf849d44db08e3bb2ea24448a0c906cabf60c897e9e",
    }
    event = {"kind": "free_text_reflection_migration", "sequence": 0, "adoption": adoption}
    rows = [event]
    monkeypatch.setattr(recovery, "_journal_rows", lambda: rows)
    monkeypatch.setattr(recovery, "_validate_free_text_reflection_migration", lambda *args, **kwargs: event)
    with pytest.raises(ValueError, match="must validate before any new provider dispatch"):
        recovery.assert_migration_replay_complete_before_dispatch()
    rows.append({"kind": "response_validation", **adoption, "valid": True})
    recovery.assert_migration_replay_complete_before_dispatch()


def test_migration_prompt_map_keeps_base_hash_and_correction_intent_hash():
    decision = "game/phase/once/germany/agent/reflect"
    rows = [
        {"kind": "dispatch_prepared", "decision_id": decision, "prompt_hash": "base"},
        {"kind": "attempt", "decision_id": decision, "prompt_hash": "base"},
        {"kind": "order_correction_intent", "decision_id": decision,
         "base_prompt_hash": "base", "correction_prompt_hash": "corrected"},
        {"kind": "dispatch_prepared", "decision_id": decision, "prompt_hash": "corrected"},
    ]
    assert ExperimentRecovery._historical_prompt_hashes(rows) == {decision: "base"}


def synthetic_migration_validator(tmp_path, monkeypatch):
    decision = "game_001/1903_fall_retreat/once/germany/shaver/reflect"
    attempt_id = "db7b6fab-7972-4554-9768-5db6d5ebb035"
    prompt_hash = "b81704cd87c4973624edabf849d44db08e3bb2ea24448a0c906cabf60c897e9e"
    response = {"content": "synthetic response; never accepted by this negative-test fixture"}
    source_manifest = {"runner/recovery.py": "old"}
    target_manifest = {"runner/recovery.py": "new"}
    before = {"code_sha256": canonical_hash(source_manifest), "configuration_sha256": "same"}
    after = {"code_sha256": canonical_hash(target_manifest), "configuration_sha256": "same"}
    attempt = {"kind": "attempt", "attempt_id": attempt_id, "decision_id": decision,
               "prompt_hash": prompt_hash, "outcome": "received", "response": response,
               "sha256": "journal-head"}
    checkpoint = {"identity": before, "journal_anchor": {"count": 0, "head": None}}
    checkpoint["sha256"] = canonical_hash(checkpoint)
    approval = {
        "kind": "free_text_reflection_migration", "sequence": 1, "previous_hash": "journal-head",
        "source_journal_count": 1, "source_journal_head": "journal-head",
        "from_identity": before, "to_identity": after, "source_checkpoint": checkpoint,
        "source_snapshot_inventory_sha256": "snapshot-hash",
        "source_code_manifest": source_manifest, "target_code_manifest": target_manifest,
        "changed_code_paths": ["runner/recovery.py"],
        "source_response_hashes": {attempt_id: canonical_hash(response)},
        "source_prompt_hashes": {attempt_id: prompt_hash}, "preserved_skipped_attempts": [],
        "source_validation_hashes": [], "historical_prompt_hashes": {decision: prompt_hash},
        "adoption": {"decision_id": decision, "attempt_id": attempt_id, "prompt_hash": prompt_hash,
                     "response_hash": canonical_hash(response), "content_sha256": "synthetic",
                     "protocol": "free_text_reflection_v1"},
    }
    recovery = ExperimentRecovery(tmp_path / "luna1912-1018-r2", identity=after, resume=True)
    rows = [attempt, approval]
    monkeypatch.setattr(recovery, "_journal_rows", lambda: rows)
    monkeypatch.setattr(recovery, "_validate_negotiation_correction_code_upgrade",
                        lambda **kwargs: {"to_identity": before, "target_code_manifest": source_manifest})
    monkeypatch.setattr(recovery_module, "_file_hash", lambda _: "snapshot-hash")
    return recovery, rows, attempt, approval, before, after


@pytest.mark.parametrize("tamper, message", [
    ("receipt_anchor", "journal anchor"),
    ("attempt_id", "saved request history"),
    ("response", "saved request history"),
    ("prompt_map", "historical prompt map"),
    ("configuration", "configuration or execution data"),
])
def test_free_text_migration_validator_rejects_tampered_bindings(tmp_path, monkeypatch, tamper, message):
    recovery, rows, attempt, approval, before, after = synthetic_migration_validator(tmp_path, monkeypatch)
    if tamper == "receipt_anchor":
        approval["source_journal_head"] = "different-head"
    elif tamper == "attempt_id":
        attempt["attempt_id"] = "different-attempt"
    elif tamper == "response":
        attempt["response"] = {"content": "changed"}
    elif tamper == "prompt_map":
        approval["historical_prompt_hashes"] = {"other/decision": "wrong"}
    else:
        before["configuration_sha256"] = "changed"
    with pytest.raises(ValueError, match=message):
        recovery._validate_free_text_reflection_migration()


@pytest.mark.parametrize("partial", ["empty", "one_file", "corrupt", "verified"])
def test_predecessor_backup_retry_requires_complete_verified_set(tmp_path, partial):
    old, identity = halted(tmp_path)
    recovery = ExperimentRecovery(tmp_path, identity=identity, resume=True)
    ensure_backup = runpy.run_path(str(Path(__file__).parents[1] / "scripts/prepare_relationship_resume.py"))["ensure_backup"]
    backup = old.path / "before_relationship_six"
    backup.mkdir()
    if partial in {"one_file", "corrupt"}:
        (backup / "checkpoint.json").write_bytes(old.checkpoint_path.read_bytes() if partial == "one_file" else b"changed")
    args = {"expected_before": canonical_hash(old.identity), "expected_after": canonical_hash(identity)}
    prefix = old.journal_path.read_bytes()
    if partial == "corrupt":
        with pytest.raises(ValueError, match="differs from source"):
            ensure_backup(recovery, **args)
        assert not (backup / "manifest.json").exists()
    else:
        ensure_backup(recovery, **args)
        manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
        assert len(manifest["files"]) == 4
        assert all((backup / name).read_bytes() == (old.path / name).read_bytes() for name in manifest["files"])
        if partial == "verified":
            (backup / "journal.jsonl").write_bytes(b"changed")
            with pytest.raises(ValueError, match="incomplete or changed"):
                ensure_backup(recovery, **args)
        else:
            ensure_backup(recovery, **args)
    assert old.journal_path.read_bytes() == prefix
