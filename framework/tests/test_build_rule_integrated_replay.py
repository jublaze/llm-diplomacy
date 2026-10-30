import hashlib
import json

import pytest

from llm_diplomacy.agents.baseline_llm_runtime import BaselineLLMAgentRuntime
from llm_diplomacy.agents.contracts import AgentContext
from llm_diplomacy.agents.llm_provider import FakeLLMProvider, LLMResponse
from llm_diplomacy.runner.recovery import ExperimentRecovery, atomic_json, canonical_hash
from test_build_rule_accounting import setup


def test_synthetic_replay_adoption_activation_crosses_saved_boundary_before_next_dispatch(tmp_path, monkeypatch):
    root = tmp_path / "game"
    old, new = {"agents/llm_context.py": "old"}, {"agents/llm_context.py": "new"}
    source = {"experiment_id": "game", "code_sha256": canonical_hash(old)}
    target = {**source, "code_sha256": canonical_hash(new)}
    recovery = ExperimentRecovery(root, identity=source, resume=False, repeat_received_invalid=True)
    recovery.prepare()
    board = {"game_id": "game_001", "year": 1908, "season": "winter", "phase": "adjustment",
             "units": [{"unit_id": p, "owner": "france", "type": "A", "province": p, "status": "active"}
                       for p in ("bre", "par", "mar")],
             "supply_ownership": {p: "france" for p in ("bre", "par", "mar", "bel")}}
    ctx = AgentContext("1.0", "game", "game_001", "1908_winter_adjustment", "metternich", "france", board)
    answer = json.dumps({"schema_version": "1.0", "orders": [], "order_rationale":
                         {"goal_supported": "hold", "why_this_advances_strategy": "no vacant home center"}})
    class SyntheticProvider(FakeLLMProvider):
        calls = 0
        def generate(self, request):
            self.calls += 1
            return LLMResponse("1.0", answer, "fake", {})
    provider = SyntheticProvider()
    runtime = BaselineLLMAgentRuntime(schema_version="1.0", runtime_root=tmp_path / "runtime",
                                      provider=provider, recovery=recovery, halt_on_issue=True)
    old_build, old_validate, old_record = runtime.context_builder.build, runtime._validate_submit_orders_legality, recovery.record_response
    monkeypatch.setattr(runtime.context_builder, "build", lambda context: old_build(context, legacy_adjustment=True))
    monkeypatch.setattr(runtime, "_validate_submit_orders_legality", lambda **kw: old_validate(**{**kw, "legacy_adjustment": True}))
    def stop_after_third(**kwargs):
        result = old_record(**kwargs)
        if provider.calls == 3:
            raise KeyboardInterrupt("saved synthetic response")
        return result
    monkeypatch.setattr(recovery, "record_response", stop_after_third)
    with pytest.raises(KeyboardInterrupt):
        runtime._invoke(method="submit_orders", context=ctx)
    monkeypatch.setattr(runtime.context_builder, "build", old_build)
    monkeypatch.setattr(runtime, "_validate_submit_orders_legality", old_validate)
    rows = recovery._journal_rows()
    attempts = [row for row in rows if row.get("kind") == "attempt" and row.get("outcome") == "received"]
    assert len(attempts) == 3
    decision, saved, prompt = attempts[-1]["decision_id"], attempts[-1]["attempt_id"], attempts[-1]["prompt_hash"]
    recovery.checkpoint(cursor={"game_id": "game_001", "phase_id": "1908_winter_adjustment", "unit": "phase_started"}, state={}, applied=[])
    recovery.halt(reason="synthetic")
    (root / "recovery/initial_snapshot").mkdir()
    atomic_json(root / "recovery/snapshot_inventory.json", {"version": 1, "inventory": [], "sha256": canonical_hash([])})
    (root / "metrics/budget").mkdir(parents=True)
    (root / "metrics/provider_attempts.jsonl").write_bytes(b'{"kind":"request","old":true}\n')
    (root / "metrics/budget/budget_state.json").write_text(json.dumps(setup()["baseline_budget"]), encoding="utf8")
    target_recovery = ExperimentRecovery(root, identity=target, resume=True, repeat_received_invalid=True)
    target_recovery.authorize_build_rule_replay(source_code_manifest=old, target_code_manifest=new,
        decision_id=decision, attempt_id=saved, prompt_hash=prompt, amend=True)
    rows = target_recovery._journal_rows()
    transition = next(row for row in rows if row["kind"] == "build_rule_replay_transition_v1")
    context = board
    trace = [{"valid": False}, {"valid": False}, {"valid": True, "context_sha256": canonical_hash(context),
             "payload_sha256": canonical_hash(json.loads(answer))}]
    result = {"experiment": "game", "expected_boundary_gate": True, "attempted_provider_calls": {"generation": 0, "dispatch": 0},
              "source_journal_prefix_unchanged": True, "ledger_unchanged": True, "budget_unchanged": True,
              "candidate_package_matches_receipt": True, "candidate_package_before": target["code_sha256"],
              "candidate_package_after": target["code_sha256"], "boundary_validation_trace": trace}
    evidence_root = root / "recovery/build-rule-evidence"; evidence_root.mkdir()
    (evidence_root / "result.json").write_text(json.dumps(result), encoding="utf8")
    (evidence_root / "context.json").write_text(json.dumps(context), encoding="utf8")
    result_sha = hashlib.sha256((evidence_root / "result.json").read_bytes()).hexdigest()
    (evidence_root / "tree.json").write_text(json.dumps({"scan": "windows_extended_length_paths", "difference_count": 0,
        "before_files": 1, "after_files": 1, "original_replay_result_sha256": result_sha}), encoding="utf8")
    evidence = {key: {"name": file, "sha256": hashlib.sha256((evidence_root / file).read_bytes()).hexdigest()}
                for key, file in (("result", "result.json"), ("context", "context.json"), ("tree_proof", "tree.json"))}
    target_recovery.authorize_build_rule_adoption(evidence=evidence)
    target_recovery.authorize_build_rule_activation()
    target_recovery.prepare()
    runtime.recovery = target_recovery
    monkeypatch.setattr(provider, "generate", lambda request: pytest.fail("provider generation forbidden"))
    output, _ = runtime._invoke(method="submit_orders", context=ctx)
    assert output["orders"] == []
    assert sum(row.get("kind") == "build_rule_boundary_validation_v1" for row in target_recovery._journal_rows()) == 1
    next_ctx = AgentContext("1.0", "game", "game_001", "1909_spring_movement", "metternich", "france",
        {**board, "year": 1909, "season": "spring", "phase": "movement"})
    def deny_dispatch(**kwargs):
        raise RuntimeError("first new dispatch intercepted")
    monkeypatch.setattr(target_recovery, "record_prepared", deny_dispatch)
    with pytest.raises(RuntimeError, match="first new dispatch intercepted"):
        runtime._invoke(method="submit_orders", context=next_ctx)
