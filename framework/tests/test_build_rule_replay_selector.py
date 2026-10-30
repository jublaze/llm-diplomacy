import copy
import json

import pytest

from llm_diplomacy.agents.contracts import AgentContext
from llm_diplomacy.agents.llm_context import PromptContextBuilder
from llm_diplomacy.agents.baseline_llm_runtime import BaselineLLMAgentRuntime
from llm_diplomacy.runner.build_rule_replay_selector import BuildRuleReplaySelector
from llm_diplomacy.runner.recovery import ExperimentRecovery
from llm_diplomacy.agents.llm_provider import FakeLLMProvider, LLMResponse


BOUNDARY = "game_001/1908_winter_adjustment/once/france/metternich/submit_orders"
LUNA = "game_001/1904_winter_negotiation/tick-3/france/metternich/negotiate_tick"


def selector(*, amend=True):
    return BuildRuleReplaySelector(historical_attempts={BOUNDARY: (("old", "p0"), ("latest", "p1")),
                                                          LUNA: (("luna", "pl"),)},
        boundary_decision=BOUNDARY, boundary_attempt="latest", boundary_prompt="p1",
        amend_boundary=amend)


def test_historical_and_boundary_selection_require_exact_saved_response():
    s = selector()
    old = s.select(LUNA)
    assert old.legacy_prompt and old.legacy_validation and not old.amended_boundary_validation
    assert s.select(BOUNDARY).legacy_prompt
    saved = {"decision_id": BOUNDARY, "attempt_id": "latest", "prompt_hash": "p1", "outcome": "received"}
    for bad in (None, saved, {**saved, "prompt_hash": "other"},
                {**saved, "outcome": "unknown"}):
        with pytest.raises(ValueError):
            s.select_attempt(decision_id=BOUNDARY, prompt_hash="p1", cached=bad)
    first = {**saved, "attempt_id": "old", "prompt_hash": "p0"}
    prior = s.select_attempt(decision_id=BOUNDARY, prompt_hash="p0", cached=first)
    assert prior.legacy_validation and not prior.boundary
    s.advance_historical(decision_id=BOUNDARY, selection=prior)
    boundary = s.select_attempt(decision_id=BOUNDARY, prompt_hash="p1", cached=saved)
    assert boundary.legacy_prompt and not boundary.legacy_validation and boundary.amended_boundary_validation
    with pytest.raises(ValueError, match="durable"):
        s.select("future")


def test_stale_or_mismatched_boundary_is_rejected():
    with pytest.raises(ValueError, match="latest"):
        BuildRuleReplaySelector(historical_attempts={BOUNDARY: (("old", "p0"), ("latest", "p1"))},
            boundary_decision=BOUNDARY, boundary_attempt="old", boundary_prompt="p0", amend_boundary=True)
    with pytest.raises(ValueError, match="order"):
        BuildRuleReplaySelector(historical_attempts={LUNA: (("luna", "pl"),)},
            boundary_decision=LUNA, boundary_attempt="luna", boundary_prompt="pl", amend_boundary=True)


def test_old_prompt_context_and_new_boundary_validation_are_separate(tmp_path):
    builder = PromptContextBuilder(folio_root=tmp_path)
    state = {"year": 1908, "season": "winter", "phase": "adjustment", "units": [
        {"unit_id": p, "owner": "france", "type": "A", "province": p, "status": "active"}
        for p in ("bre", "par", "mar")],
        "supply_ownership": {p: "france" for p in ("bre", "par", "mar", "bel")}}
    ctx = AgentContext("1.0", "synthetic", "game_001", "1908_winter_adjustment", "metternich", "france", state)
    historical = builder.build(ctx, legacy_adjustment=True)
    assert historical == builder.build(copy.deepcopy(ctx), legacy_adjustment=True)
    modern = builder.build(ctx)
    runtime = object.__new__(BaselineLLMAgentRuntime)
    assert runtime._validate_submit_orders_legality(context=ctx, built_context=historical,
        orders=[], legacy_adjustment=True) == "adjustment requires exactly 1 BUILD orders"
    assert runtime._validate_submit_orders_legality(context=ctx, built_context=modern, orders=[]) is None


def test_same_decision_replays_two_rejections_then_latest_unvalidated_response(tmp_path):
    attempts = (("one", "hash1"), ("two", "hash2"), ("three", "hash3"))
    s = BuildRuleReplaySelector(historical_attempts={BOUNDARY: attempts}, boundary_decision=BOUNDARY,
        boundary_attempt="three", boundary_prompt="hash3", amend_boundary=True)
    rows = [{"kind": "attempt", "decision_id": BOUNDARY, "attempt_id": attempt,
             "prompt_hash": prompt, "outcome": "received", "response": {"content": '{"orders": []}'}}
            for attempt, prompt in attempts]
    class SavedJournal:
        def _journal_rows(self):
            return rows
    state = {"year": 1908, "season": "winter", "phase": "adjustment", "units": [
        {"unit_id": p, "owner": "france", "type": "A", "province": p, "status": "active"}
        for p in ("bre", "par", "mar")],
        "supply_ownership": {p: "france" for p in ("bre", "par", "mar", "bel")}}
    ctx = AgentContext("1.0", "synthetic", "game_001", "1908_winter_adjustment", "metternich", "france", state)
    builder = PromptContextBuilder(folio_root=tmp_path)
    runtime = object.__new__(BaselineLLMAgentRuntime)
    before = copy.deepcopy(rows)
    for index, (attempt, prompt) in enumerate(attempts):
        assert s.select(BOUNDARY).legacy_prompt
        cached = s.cached_response(SavedJournal(), decision_id=BOUNDARY, prompt_hash=prompt)
        selection = s.select_attempt(decision_id=BOUNDARY, prompt_hash=prompt, cached=cached)
        assert cached["attempt_id"] == attempt
        built = builder.build(ctx, legacy_adjustment=not selection.amended_boundary_validation)
        issue = runtime._validate_submit_orders_legality(context=ctx, built_context=built,
            orders=[], legacy_adjustment=selection.legacy_validation)
        if index < 2:
            assert issue == "adjustment requires exactly 1 BUILD orders"
            s.advance_historical(decision_id=BOUNDARY, selection=selection)
        else:
            assert selection.boundary and selection.amended_boundary_validation and issue is None
            with pytest.raises(ValueError, match="adoption"):
                s.advance_historical(decision_id=BOUNDARY, selection=selection)
    assert rows == before
    with pytest.raises(ValueError, match="activation"):
        s.select("future")


def test_runtime_replays_two_corrections_to_saved_boundary_without_provider(tmp_path, monkeypatch):
    state = {"year": 1908, "season": "winter", "phase": "adjustment", "units": [
        {"unit_id": p, "owner": "france", "type": "A", "province": p, "status": "active"}
        for p in ("bre", "par", "mar")],
        "supply_ownership": {p: "france" for p in ("bre", "par", "mar", "bel")}}
    ctx = AgentContext("1.0", "synthetic", "game_001", "1908_winter_adjustment", "metternich", "france", state)
    response = json.dumps({"schema_version": "1.0", "orders": [], "order_rationale":
        {"goal_supported": "hold", "why_this_advances_strategy": "no vacant home center"}})
    class SyntheticProvider(FakeLLMProvider):
        calls = 0
        def generate(self, request):
            self.calls += 1
            return LLMResponse("1.0", response, "fake", {})
    recovery = ExperimentRecovery(tmp_path / "experiment", identity={"test": "replay"}, resume=False,
                                  repeat_received_invalid=True)
    recovery.prepare()
    provider = SyntheticProvider()
    runtime = BaselineLLMAgentRuntime(schema_version="1.0", runtime_root=tmp_path / "runtime",
                                      provider=provider, recovery=recovery, halt_on_issue=True)
    original_build = runtime.context_builder.build
    original_validate = runtime._validate_submit_orders_legality
    monkeypatch.setattr(runtime.context_builder, "build", lambda context: original_build(context, legacy_adjustment=True))
    monkeypatch.setattr(runtime, "_validate_submit_orders_legality", lambda **kw:
                        original_validate(**{**kw, "legacy_adjustment": True}))
    original_record = recovery.record_response
    def interrupt_third(**kw):
        result = original_record(**kw)
        if provider.calls == 3:
            raise KeyboardInterrupt("synthetic saved response")
        return result
    monkeypatch.setattr(recovery, "record_response", interrupt_third)
    with pytest.raises(KeyboardInterrupt):
        runtime._invoke(method="submit_orders", context=ctx)
    rows = recovery._journal_rows()
    attempts = [r for r in rows if r.get("kind") == "attempt" and r.get("outcome") == "received"]
    validations = [r for r in rows if r.get("kind") == "response_validation"]
    assert len(attempts) == 3 and len(validations) == 2 and all(not r["valid"] for r in validations)
    before = recovery.journal_path.read_bytes()
    monkeypatch.setattr(runtime.context_builder, "build", original_build)
    monkeypatch.setattr(runtime, "_validate_submit_orders_legality", original_validate)
    monkeypatch.setattr(recovery, "record_response", original_record)
    monkeypatch.setattr(provider, "generate", lambda request: pytest.fail("provider called during replay"))
    pairs = tuple((r["attempt_id"], r["prompt_hash"]) for r in attempts)
    recovery.build_rule_replay_selector = BuildRuleReplaySelector(
        historical_attempts={BOUNDARY: pairs}, boundary_decision=BOUNDARY,
        boundary_attempt=pairs[-1][0], boundary_prompt=pairs[-1][1], amend_boundary=True)
    with pytest.raises(RuntimeError, match="durable adoption"):
        runtime._invoke(method="submit_orders", context=ctx)
    assert recovery.journal_path.read_bytes() == before
