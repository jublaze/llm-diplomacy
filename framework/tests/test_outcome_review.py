from __future__ import annotations

import json

from llm_diplomacy.agents.baseline_llm_runtime import BaselineLLMAgentRuntime
from llm_diplomacy.agents.contracts import AgentContext
from llm_diplomacy.agents.llm_provider import FakeLLMProvider


def _context() -> AgentContext:
    return AgentContext(
        schema_version="1.0", experiment_id="exp", game_id="g_review", phase_id="1901_spring_movement",
        agent_id="agent_england", country_assignment="england",
        visible_state={"game_id": "g_review", "year": 1901, "season": "spring", "phase": "movement", "units": [], "supply_ownership": {}, "latest_public_resolution": {"phase": "movement", "public_final_orders": []}},
    )


def test_outcome_review_is_required_in_prompt_bounded_and_preserved_in_artifact(tmp_path) -> None:
    review = " ".join(f"word{i}" for i in range(230))
    provider = FakeLLMProvider(responses_by_method={"reflect": json.dumps({
        "schema_version": "reflection_compact_v3_campaign_memory", "phase_summary": "No change.", "strategic_position": "Stable.",
        "objective_update": {"current_objective": "Test a route.", "why_it_matters": "It may create expansion."},
        "relationship_updates": [], "action_lessons": [], "memory_candidates": [], "belief_summary": "No new belief.", "outcome_review": review, "campaign_plan": "Test a conditional route.", "commitment_memory": "No decision-relevant commitments yet.", "winter_assessment": None,
    })})
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime", provider=provider)
    runtime.register_agent(agent_id="agent_england")
    context = _context()
    context.visible_state["latest_public_resolution"]["phase_id"] = "1901_spring_movement"
    result = runtime.reflect(context)
    assert len(result.reflection["outcome_review"].split()) <= 200
    output = json.loads((tmp_path / "runtime" / "agent_england" / "runtime" / "phase_outputs" / "1901_spring_movement_reflect.json").read_text(encoding="utf-8"))
    assert output["result"]["reflection"]["outcome_review"] == result.reflection["outcome_review"]
    assert "Review the immediately preceding adjudication." in output["llm_meta"]["prompt"]
    assert output["llm_meta"]["outcome_review_target_phase_id"] == "1901_spring_movement"


def test_legacy_reflection_without_outcome_review_normalizes_without_persisting_history(tmp_path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime", provider=FakeLLMProvider())
    legacy = runtime._normalize_reflection_candidate({"schema_version": "reflection_compact_v1", "belief_summary": "Legacy reading."})
    assert legacy["outcome_review"] == ""
    context = _context()
    built = runtime.context_builder.build(context)
    prompt = runtime.context_builder.build_prompt(method="negotiate_tick", built_context=built)
    assert "Outcome review exceeded" not in prompt
    assert "# Current Game Memory" in prompt


def test_fresh_v1_or_blank_v2_reflection_falls_back_with_clear_semantic_reason(tmp_path) -> None:
    base = {
        "phase_summary": "No change.", "strategic_position": "Stable.",
        "objective_update": {"current_objective": "Test a route.", "why_it_matters": "It may create expansion."},
        "relationship_updates": [], "action_lessons": [], "memory_candidates": [], "belief_summary": "No new belief.",
    }
    for name, payload, reason in (
        ("v1", {**base, "schema_version": "reflection_compact_v1"}, "fresh reflection must use schema_version"),
        ("blank", {**base, "schema_version": "reflection_compact_v2_outcome_review", "outcome_review": ""}, "fresh reflection requires a non-empty outcome_review"),
    ):
        runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / name, provider=FakeLLMProvider(responses_by_method={"reflect": json.dumps(payload)}))
        runtime.register_agent(agent_id="agent_england")
        result = runtime.reflect(_context())
        assert result.explanation["fallback_applied"] is True
        assert result.explanation["reason"].startswith(reason)
