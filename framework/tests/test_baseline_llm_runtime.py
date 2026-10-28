from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_diplomacy.agents.baseline_llm_runtime import BaselineLLMAgentRuntime
from llm_diplomacy.agents.contracts import AgentContext
from llm_diplomacy.agents.llm_provider import FakeLLMProvider, LLMRequest, LLMResponse


@pytest.fixture(autouse=True)
def _upgrade_legacy_fake_reflections(monkeypatch):
    """Keep pre-v2 fixture content focused on its original behavior, not the new schema gate."""
    original_generate = FakeLLMProvider.generate

    def generate(provider, request):
        response = original_generate(provider, request)
        if request.method != "reflect":
            return response
        try:
            payload = json.loads(response.content)
        except json.JSONDecodeError:
            return response
        if not isinstance(payload, dict) or str(payload.get("schema_version", "")) not in {"1.0", "reflection_compact_v1", "reflection_compact_v2_outcome_review"}:
            return response
        payload["schema_version"] = "reflection_compact_v3_campaign_memory"
        payload.setdefault("outcome_review", "The published order record and adjudication are the evidence for the next action.")
        payload.setdefault("campaign_plan", "Maintain a conditional route while revising it against public adjudication.")
        payload.setdefault("commitment_memory", "No decision-relevant commitments yet.")
        payload.setdefault("winter_assessment", None)
        return LLMResponse(schema_version=response.schema_version, content=json.dumps(payload), model=response.model, raw=response.raw)

    monkeypatch.setattr(FakeLLMProvider, "generate", generate)
from llm_diplomacy.compliance.artifacts import ArtifactValidator
from llm_diplomacy.config.loader import load_experiment_config
from llm_diplomacy.runner import ScriptedExperimentRunner


def _ctx(tmp_path: Path) -> AgentContext:
    return AgentContext(
        schema_version="1.0",
        experiment_id="exp",
        game_id="g1",
        phase_id="1901_spring_movement",
        agent_id="agent_llm",
        country_assignment="france",
        visible_state={
                                "schema_version": "reflection_compact_v2_outcome_review",
            "game_id": "g1",
            "year": 1901,
            "season": "spring",
            "phase": "movement",
            "units": [
                {"unit_id": "u_fra_a_par", "owner": "france", "type": "A", "province": "par", "status": "active"},
                {"unit_id": "u_fra_f_bre", "owner": "france", "type": "F", "province": "bre", "status": "active"},
            ],
            "private_messages": [
                {"to": {"type": "private", "to_agent_id": "agent_llm"}, "body": "for llm"},
                {"to": {"type": "private", "to_agent_id": "other_agent"}, "body": "secret"},
            ],
            "global_messages": [],
            "power_to_agent": {"france": "agent_llm", "england": "agent_eng"},
        },
    )


def _compact_negotiate_payload(
    *,
    private_messages: list[dict[str, object]] | None = None,
    global_messages: list[dict[str, object]] | None = None,
    relationship_updates: list[dict[str, object]] | None = None,
    withheld_information: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "private_messages": private_messages or [],
        "global_messages": global_messages or [],
        "relationship_updates": relationship_updates or [],
        "withheld_information": withheld_information or [],
    }


def _adjustment_ctx(*, delta: int = 0) -> AgentContext:
    units = [
        {"unit_id": "u_fra_a_par", "owner": "france", "type": "A", "province": "par", "status": "active"},
        {"unit_id": "u_fra_f_bre", "owner": "france", "type": "F", "province": "bre", "status": "active"},
    ]
    supply_ownership = {"par": "france", "bre": "france"}
    if delta > 0:
        supply_ownership["mar"] = "france"
    if delta < 0:
        supply_ownership = {"par": "france"}
    return AgentContext(
        schema_version="1.0",
        experiment_id="exp",
        game_id="g1",
        phase_id="1901_winter_adjustment",
        agent_id="agent_llm",
        country_assignment="france",
        visible_state={
                                "schema_version": "reflection_compact_v2_outcome_review",
            "game_id": "g1",
            "year": 1901,
            "season": "winter",
            "phase": "adjustment",
            "units": units,
            "supply_ownership": supply_ownership,
            "private_messages": [],
            "global_messages": [],
            "power_to_agent": {"france": "agent_llm", "england": "agent_eng"},
        },
    )


def test_valid_negotiate_output_parses(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(
            responses_by_method={
                "negotiate_tick": json.dumps(
                    {
                        "done": False,
                        "communication_plan": {
                            "goal": "Open channel with England",
                            "target_players": ["england"],
                            "channel_choices": ["private:england"],
                        },
                        "relationship_analysis": [
                            {
                                "agent_id": "england",
                                "current_relationship": "cautious",
                                "what_they_may_want": "avoid border conflict",
                                "what_i_want_from_them": "non-aggression",
                                "how_this_serves_my_objectives": "frees units to pressure another front",
                                "risk_to_my_objectives": "could mask a surprise attack",
                                "specific_evidence": "recent message discussed border caution",
                                "next_strategic_use_of_relationship": "request province-specific border terms",
                                "next_conversation_move": "ask for concrete move commitments",
                            }
                        ],
                        "messages": [{"to": "england", "body": "hello"}],
                        "explanation": {"summary": "opened"},
                    }
                )
            }
        ),
    )
    runtime.register_agent(agent_id="agent_llm")
    tick = runtime.negotiate_tick(_ctx(tmp_path))
    assert tick.ok is True
    assert len(tick.messages) == 1
    assert tick.messages[0]["to"]["to_agent_id"] == "agent_eng"


def test_strict_invalid_order_gets_one_private_corrective_request(tmp_path: Path) -> None:
    from llm_diplomacy.runner.recovery import ExperimentRecovery

    original = {
        "orders": [{"unit_ref": "u_fra_f_bre", "type": "SUPPORT_MOVE",
                    "payload": {"supported_from": "par", "supported_to": "mos"}}],
        "order_rationale": {"goal_supported": "support", "why_this_advances_strategy": "support Paris"},
    }
    corrected = {
        "orders": [
            {"unit_ref": "u_fra_a_par", "type": "HOLD", "payload": {"at": "par"}},
            {"unit_ref": "u_fra_f_bre", "type": "HOLD", "payload": {"at": "bre"}},
        ],
        "order_rationale": {"goal_supported": "hold", "why_this_advances_strategy": "preserve position"},
        "passivity_review": {"justified": True, "reason": "Holding is intentional."},
    }
    original_text = json.dumps(original)

    class SequenceProvider:
        metadata = {"provider_name": "offline-test"}

        def __init__(self):
            self.requests = []

        def generate(self, request):
            self.requests.append(request)
            payload = original if len(self.requests) == 1 else corrected
            usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12,
                     "estimated_cost_usd": 0.25}
            return LLMResponse("1.0", json.dumps(payload), "offline-test",
                               {"provider": "fake", "usage": usage})

    provider = SequenceProvider()
    recovery = ExperimentRecovery(tmp_path / "recovery", identity={"id": "correction-test"}, resume=False)
    recovery.prepare()
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime",
                                      provider=provider, halt_on_issue=True, recovery=recovery)
    result = runtime.submit_orders(_ctx(tmp_path))

    assert len(provider.requests) == 2
    assert provider.requests[0].prompt in provider.requests[1].prompt
    assert "ORDER CORRECTION REQUIRED" in provider.requests[1].prompt
    assert "illegal order payload" in provider.requests[1].prompt
    feedback = json.loads(provider.requests[1].prompt.rsplit("\n", 1)[-1])
    assert feedback["rejected_answer"] == original_text
    assert len(result.orders) == 2
    assert result.usage == {"calls": 2, "prompt_tokens": 20, "completion_tokens": 4,
                            "tokens": 24, "estimated_cost": 0.5, "usage_available": True}
    rows = recovery._journal_rows()
    intent = next(row for row in rows if row.get("kind") == "order_correction_intent")
    received = [row for row in rows if row.get("kind") == "attempt" and row.get("outcome") == "received"]
    assert len(received) == 2
    assert intent["parent_attempt_id"] == received[0]["attempt_id"]
    assert intent["correction_prompt_hash"] == received[1]["prompt_hash"]
    assert received[0]["prompt_hash"] != received[1]["prompt_hash"]
    assert "include every required unit" in provider.requests[1].prompt
    parent_validation = next(row for row in rows if row.get("kind") == "response_validation"
                             and row.get("attempt_id") == received[0]["attempt_id"]
                             and row.get("valid") is False)
    assert parent_validation["issue"] == "engine precheck rejected one or more orders"

    class NoCallProvider:
        metadata = {"provider_name": "offline-replay-guard"}

        def generate(self, request):
            raise AssertionError("cached movement correction must not call the provider")

    resumed = ExperimentRecovery(tmp_path / "recovery", identity={"id": "correction-test"}, resume=True)
    resumed.prepare()
    replay = BaselineLLMAgentRuntime(
        reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime",
        provider=NoCallProvider(), halt_on_issue=True, recovery=resumed,
    )
    replayed = replay.submit_orders(_ctx(tmp_path))
    assert len(replayed.orders) == 2
    replayed_intent = next(row for row in resumed._journal_rows() if row.get("kind") == "order_correction_intent")
    assert replayed_intent == intent


def test_strict_wrong_negotiation_schema_gets_one_corrective_request(tmp_path: Path) -> None:
    from llm_diplomacy.runner.recovery import ExperimentRecovery

    invalid = {
        "private_messages": [{"recipientient": "england", "body": "Can we coordinate?", "purpose": "border_probe"}],
        "global_messages": [], "relationship_updates": [], "withheld_information": [],
    }
    corrected = {
        "private_messages": [{"recipient": "england", "body": "Can we coordinate?", "purpose": "border_probe"}],
        "global_messages": [], "relationship_updates": [], "withheld_information": [],
    }

    class SequenceProvider:
        metadata = {"provider_name": "offline-test"}

        def __init__(self):
            self.requests = []

        def generate(self, request):
            self.requests.append(request)
            payload = invalid if len(self.requests) == 1 else corrected
            usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12,
                     "estimated_cost_usd": 0.25}
            return LLMResponse("1.0", json.dumps(payload), "offline-test",
                               {"provider": "fake", "usage": usage})

    provider = SequenceProvider()
    recovery = ExperimentRecovery(tmp_path / "recovery", identity={"id": "negotiation-correction"}, resume=False)
    recovery.prepare()
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime",
        provider=provider, halt_on_issue=True, recovery=recovery)
    runtime.register_agent(agent_id="agent_llm")

    result = runtime.negotiate_tick(_ctx(tmp_path))

    assert result.ok and len(result.messages) == 1
    assert result.messages[0]["to"] == {"type": "private", "to_agent_id": "agent_eng"}
    assert len(provider.requests) == 2
    assert provider.requests[0].prompt in provider.requests[1].prompt
    assert "NEGOTIATION CORRECTION REQUIRED" in provider.requests[1].prompt
    assert "private_messages.recipient must be non-empty string" in provider.requests[1].prompt
    assert "recipientient" in provider.requests[1].prompt
    assert provider.requests[1].prompt.count('"recipient": "england"') >= 1
    feedback = json.loads(provider.requests[1].prompt.rsplit("\n", 1)[-1])
    assert feedback["rejected_answer"] == json.dumps(invalid)
    assert feedback["validator_issue"] == "private_messages.recipient must be non-empty string"
    rows = recovery._journal_rows()
    intent = next(row for row in rows if row.get("kind") == "order_correction_intent")
    received = [row for row in rows if row.get("kind") == "attempt" and row.get("outcome") == "received"]
    validations = [row for row in rows if row.get("kind") == "response_validation"]
    assert intent["method"] == "negotiate_tick"
    assert len(received) == 2 and received[0]["prompt_hash"] != received[1]["prompt_hash"]
    assert [row["valid"] for row in validations] == [False, True]
    assert result.usage["calls"] == 2


def test_strict_negotiation_corrects_message_that_would_be_dropped(tmp_path: Path) -> None:
    from llm_diplomacy.runner.recovery import ExperimentRecovery

    rejected = {"private_messages": [{"recipient": "unknown-power", "body": "coordinate", "purpose": "offer"}],
                "global_messages": [], "relationship_updates": [], "withheld_information": []}
    corrected = {"private_messages": [], "global_messages": [],
                 "relationship_updates": [], "withheld_information": []}

    class SequenceProvider:
        metadata = {"provider_name": "offline-test"}

        def __init__(self):
            self.requests = []

        def generate(self, request):
            self.requests.append(request)
            answer = rejected if len(self.requests) == 1 else corrected
            return LLMResponse("1.0", json.dumps(answer), "offline-test", {"provider": "fake"})

    provider = SequenceProvider()
    recovery = ExperimentRecovery(tmp_path / "recovery", identity={"id": "negotiation-loss"}, resume=False)
    recovery.prepare()
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime",
        provider=provider, halt_on_issue=True, recovery=recovery)
    runtime.register_agent(agent_id="agent_llm")

    result = runtime.negotiate_tick(_ctx(tmp_path))

    assert result.ok and result.messages == []
    assert len(provider.requests) == 2
    feedback = json.loads(provider.requests[1].prompt.rsplit("\n", 1)[-1])
    assert feedback["rejected_answer"] == json.dumps(rejected)
    assert "normalization would drop" in feedback["validator_issue"]
    assert "unknown-power" in feedback["validator_issue"]
    assert len([row for row in recovery._journal_rows()
                if row.get("kind") == "response_validation"]) == 2


def test_strict_negotiation_corrects_lossy_legacy_relationship_value(tmp_path: Path) -> None:
    from llm_diplomacy.runner.recovery import ExperimentRecovery

    rejected = {"messages": [{"to": "private:england", "body": "hello"}],
        "relationship_analysis": [
            {"agent_id": "england", "current_relationship": "conditional",
             "next_strategic_use_of_relationship": "verify support"},
            {"agent_id": "france", "current_relationship": {"meaning": "LOST_FACT"},
             "next_strategic_use_of_relationship": "verify the border"},
        ]}
    corrected = {"messages": [{"to": "private:england", "body": "hello"}],
        "relationship_analysis": [
            {"agent_id": "england", "current_relationship": "conditional",
             "next_strategic_use_of_relationship": "verify support"},
            {"agent_id": "france", "current_relationship": "uncertain",
             "next_strategic_use_of_relationship": "verify the border"},
        ]}

    class SequenceProvider:
        metadata = {"provider_name": "offline-test"}

        def __init__(self):
            self.requests = []

        def generate(self, request):
            self.requests.append(request)
            answer = rejected if len(self.requests) == 1 else corrected
            return LLMResponse("1.0", json.dumps(answer), "offline-test", {"provider": "fake"})

    provider = SequenceProvider()
    recovery = ExperimentRecovery(tmp_path / "recovery", identity={"id": "legacy-relationship-loss"}, resume=False)
    recovery.prepare()
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime",
        provider=provider, halt_on_issue=True, recovery=recovery)
    runtime.register_agent(agent_id="agent_llm")

    result = runtime.negotiate_tick(_ctx(tmp_path))

    assert result.ok and len(result.messages) == 1
    assert len(provider.requests) == 2
    feedback = json.loads(provider.requests[1].prompt.rsplit("\n", 1)[-1])
    assert feedback["rejected_answer"] == json.dumps(rejected)
    assert "current_relationship must be a string" in feedback["validator_issue"]
    assert "LOST_FACT" in feedback["validator_issue"] or "LOST_FACT" in feedback["rejected_answer"]


def test_strict_negotiation_does_not_accept_normalizer_filled_empty_object(tmp_path: Path) -> None:
    from llm_diplomacy.runner.recovery import ExperimentRecovery

    class EmptyProvider:
        metadata = {"provider_name": "offline-test"}

        def __init__(self):
            self.requests = []

        def generate(self, request):
            self.requests.append(request)
            return LLMResponse("1.0", "{}", "offline-test", {"provider": "fake"})

    provider = EmptyProvider()
    recovery = ExperimentRecovery(tmp_path / "recovery", identity={"id": "empty-negotiation"}, resume=False)
    recovery.prepare()
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime",
        provider=provider, halt_on_issue=True, recovery=recovery)
    runtime.register_agent(agent_id="agent_llm")

    with pytest.raises(RuntimeError, match="invalid_output"):
        runtime.negotiate_tick(_ctx(tmp_path))

    assert len(provider.requests) == 2
    assert "missing the required compact fields" in provider.requests[1].prompt
    assert not list((tmp_path / "runtime/agent_llm/runtime/phase_outputs").glob("*.json"))


def test_strict_empty_movement_orders_get_one_missing_unit_correction(tmp_path: Path) -> None:
    from llm_diplomacy.runner.recovery import ExperimentRecovery

    class SequenceProvider:
        metadata = {"provider_name": "offline-test"}

        def __init__(self):
            self.requests = []

        def generate(self, request):
            self.requests.append(request)
            payload = {"orders": [], "order_rationale": {"goal_supported": "No order",
                        "why_this_advances_strategy": "No movement supplied."}} if len(self.requests) == 1 else {
                "orders": [
                    {"unit_ref": "u_fra_a_par", "type": "HOLD", "payload": {"at": "par"}},
                    {"unit_ref": "u_fra_f_bre", "type": "HOLD", "payload": {"at": "bre"}},
                ],
                "order_rationale": {"goal_supported": "Hold", "why_this_advances_strategy": "Preserve positions."},
                "passivity_review": {"justified": True, "reason": "A deliberate defensive hold."},
            }
            return LLMResponse("1.0", json.dumps(payload), "offline-test", {"provider": "fake"})

    recovery = ExperimentRecovery(tmp_path / "recovery", identity={"id": "empty-correction"}, resume=False)
    recovery.prepare()
    provider = SequenceProvider()
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime",
                                      provider=provider, halt_on_issue=True, recovery=recovery)
    result = runtime.submit_orders(_ctx(tmp_path))
    assert len(provider.requests) == 2
    assert len(result.orders) == 2
    correction = json.loads(provider.requests[1].prompt.rsplit("\n", 1)[-1])
    assert [row["reason"] for row in correction["engine_errors"]] == ["missing_order", "missing_order"]
    assert "include every required unit" in provider.requests[1].prompt


def test_strict_wrong_reflection_schema_gets_one_corrective_request(tmp_path: Path) -> None:
    from llm_diplomacy.runner.recovery import ExperimentRecovery

    invalid = {"schema_version": "reflection_campaign_v1", "belief_summary": "The published order record is evidence."}
    corrected = {
        "schema_version": "reflection_compact_v3_campaign_memory",
        "outcome_review": "The published order record and adjudication are the evidence for the next action.",
        "campaign_plan": "Maintain a conditional route and revise it against public evidence.",
        "commitment_memory": "No decision-relevant commitments yet.",
        "objective_observations": ["The public board confirms the result."], "belief_updates": [],
        "strategic_intent": {"short_term": "Verify the next position", "medium_term": "Preserve options",
                             "risk_notes": []},
        "memory_update": "The published order record is evidence for future decisions.",
    }

    class SequenceProvider:
        metadata = {"provider_name": "offline-test"}

        def __init__(self):
            self.requests = []

        def generate(self, request):
            self.requests.append(request)
            content = invalid if len(self.requests) == 1 else corrected
            usage = {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14,
                     "estimated_cost_usd": .02}
            return LLMResponse("1.0", json.dumps(content), "offline-test",
                               {"provider": "fake", "usage": usage})

    recovery = ExperimentRecovery(tmp_path / "recovery", identity={"id": "reflection-correction"}, resume=False)
    recovery.prepare()
    provider = SequenceProvider()
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime",
        provider=provider, halt_on_issue=True, recovery=recovery)
    runtime.register_agent(agent_id="agent_llm")
    runtime.context_builder.build(_ctx(tmp_path))
    memory_json = tmp_path / "runtime/agent_llm/folio/game_memory/g1.json"
    memory_markdown = tmp_path / "runtime/agent_llm/folio/game_memory/g1.md"
    before_memory = memory_json.read_bytes() if memory_json.exists() else None
    before_markdown = memory_markdown.read_bytes() if memory_markdown.exists() else None
    result = runtime.reflect(_ctx(tmp_path))

    assert result.ok and result.reflection["schema_version"] == "reflection_compact_v3_campaign_memory"
    assert result.reflection["memory_update"] == corrected["memory_update"]
    assert len(provider.requests) == 2
    assert provider.requests[0].prompt in provider.requests[1].prompt
    assert "REFLECTION CORRECTION REQUIRED" in provider.requests[1].prompt
    assert "fresh reflection must use schema_version reflection_compact_v3_campaign_memory" in provider.requests[1].prompt
    assert invalid["schema_version"] in provider.requests[1].prompt
    rows = recovery._journal_rows()
    intent = next(row for row in rows if row.get("kind") == "order_correction_intent")
    assert intent["method"] == "reflect"
    assert len([row for row in rows if row.get("kind") == "attempt" and row.get("outcome") == "received"]) == 2
    assert result.usage["calls"] == 2
    completed_memory = memory_json.read_bytes()
    completed_markdown = memory_markdown.read_bytes()
    if before_memory is None:
        memory_json.unlink()
    else:
        memory_json.write_bytes(before_memory)
    if before_markdown is None:
        memory_markdown.unlink(missing_ok=True)
    else:
        memory_markdown.write_bytes(before_markdown)
    replay = runtime.reflect(_ctx(tmp_path))
    assert replay.reflection["memory_update"] == corrected["memory_update"]
    assert len(provider.requests) == 2
    assert memory_json.read_bytes() == completed_memory
    assert memory_markdown.read_bytes() == completed_markdown


def test_strict_reflection_semantics_check_normalized_memory_candidate_before_effects(tmp_path: Path) -> None:
    from dataclasses import replace
    from llm_diplomacy.runner.recovery import ExperimentRecovery

    invalid = {
        "schema_version": "reflection_compact_v3_campaign_memory",
        "phase_summary": "The public board is stable.",
        "strategic_position": "Wait for confirmed movement.",
        "outcome_review": "No notable outcome.",
        "campaign_plan": "Maintain a conditional route.",
        "commitment_memory": "No outstanding commitments.",
        "memory_candidates": ["We trusted France without any message evidence."],
        "belief_updates": [], "relationship_updates": [], "action_lessons": [],
    }
    corrected = {
        "schema_version": "reflection_compact_v3_campaign_memory",
        "phase_summary": "The public board is stable.",
        "strategic_position": "Wait for confirmed movement.",
        "outcome_review": "No notable outcome.",
        "campaign_plan": "Maintain a conditional route.",
        "commitment_memory": "No outstanding commitments.",
        "memory_update": "The public board provides no message evidence about trust.",
        "belief_updates": [], "relationship_updates": [], "action_lessons": [],
        "memory_candidates": [],
    }

    class SequenceProvider:
        metadata = {"provider_name": "offline-test"}

        def __init__(self):
            self.requests = []

        def generate(self, request):
            self.requests.append(request)
            payload = invalid if len(self.requests) == 1 else corrected
            return LLMResponse("1.0", json.dumps(payload), "offline-test", {"provider": "fake"})

    recovery = ExperimentRecovery(tmp_path / "recovery", identity={"id": "normalized-reflection-semantics"}, resume=False)
    recovery.prepare()
    provider = SequenceProvider()
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime",
        provider=provider, halt_on_issue=True, recovery=recovery)
    runtime.register_agent(agent_id="agent_llm")
    context = _ctx(tmp_path)
    context = replace(context, visible_state={**context.visible_state, "private_messages": [], "global_messages": []})
    runtime.context_builder.build(context)
    memory_json = tmp_path / "runtime/agent_llm/folio/game_memory/g1.json"
    memory_markdown = tmp_path / "runtime/agent_llm/folio/game_memory/g1.md"
    before_memory = memory_json.read_bytes() if memory_json.exists() else None
    before_markdown = memory_markdown.read_bytes() if memory_markdown.exists() else None

    result = runtime.reflect(context)

    assert result.ok
    assert result.reflection["memory_update"] == corrected["memory_update"]
    assert len(provider.requests) == 2
    assert "claims trust/commitment/cooperation without message evidence" in provider.requests[1].prompt
    rows = recovery._journal_rows()
    validations = [row for row in rows if row.get("kind") == "response_validation"]
    assert [row["valid"] for row in validations] == [False, True]
    assert validations[0]["issue"] == "claims trust/commitment/cooperation without message evidence"
    assert (memory_json.read_bytes() if memory_json.exists() else None) != before_memory
    assert (memory_markdown.read_bytes() if memory_markdown.exists() else None) != before_markdown


def test_cached_reflection_receipt_is_validated_after_crash_before_parse(tmp_path: Path, monkeypatch) -> None:
    from llm_diplomacy.runner.recovery import ExperimentRecovery

    payload = {
        "schema_version": "reflection_compact_v3_campaign_memory",
        "phase_summary": "The public board is stable.",
        "strategic_position": "Wait for confirmed movement.",
        "outcome_review": "No notable outcome.",
        "campaign_plan": "Maintain a conditional route.",
        "commitment_memory": "No outstanding commitments.",
        "memory_update": "The public board provides no new evidence.",
        "belief_updates": [], "relationship_updates": [], "action_lessons": [], "memory_candidates": [],
    }

    class OnceProvider:
        metadata = {"provider_name": "offline-test"}

        def __init__(self):
            self.calls = 0

        def generate(self, request):
            self.calls += 1
            return LLMResponse("1.0", json.dumps(payload), "offline-test", {"provider": "fake"})

    recovery = ExperimentRecovery(tmp_path / "recovery", identity={"id": "receipt-before-parse"}, resume=False)
    recovery.prepare()
    provider = OnceProvider()
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime",
        provider=provider, halt_on_issue=True, recovery=recovery)
    runtime.register_agent(agent_id="agent_llm")
    runtime.context_builder.build(_ctx(tmp_path))
    memory_json = tmp_path / "runtime/agent_llm/folio/game_memory/g1.json"
    memory_markdown = tmp_path / "runtime/agent_llm/folio/game_memory/g1.md"
    before_memory = memory_json.read_bytes() if memory_json.exists() else None
    before_markdown = memory_markdown.read_bytes() if memory_markdown.exists() else None
    original_record_response = recovery.record_response

    def crash_after_receipt(**kwargs):
        original_record_response(**kwargs)
        raise RuntimeError("simulated crash after received response was journaled")

    monkeypatch.setattr(recovery, "record_response", crash_after_receipt)
    with pytest.raises(RuntimeError, match="simulated crash after received response"):
        runtime.reflect(_ctx(tmp_path))
    assert provider.calls == 1
    assert not [row for row in recovery._journal_rows() if row.get("kind") == "response_validation"]
    monkeypatch.setattr(recovery, "record_response", original_record_response)

    result = runtime.reflect(_ctx(tmp_path))

    assert result.ok
    assert provider.calls == 1
    validations = [row for row in recovery._journal_rows() if row.get("kind") == "response_validation"]
    assert len(validations) == 1 and validations[0]["valid"] is True
    assert recovery.status()["rejected_responses"] == []
    journal_after_validation = recovery.journal_path.read_bytes()
    if before_memory is None:
        memory_json.unlink(missing_ok=True)
    else:
        memory_json.write_bytes(before_memory)
    if before_markdown is None:
        memory_markdown.unlink(missing_ok=True)
    else:
        memory_markdown.write_bytes(before_markdown)
    runtime.reflect(_ctx(tmp_path))
    assert provider.calls == 1
    assert recovery.journal_path.read_bytes() == journal_after_validation


@pytest.mark.parametrize("bad_correction", ["wrong_schema", "invalid_semantics"])
def test_strict_invalid_corrected_reflection_halts_before_memory_write(tmp_path: Path, bad_correction: str) -> None:
    from llm_diplomacy.runner.recovery import ExperimentRecovery

    class InvalidTwiceProvider:
        metadata = {"provider_name": "offline-test"}

        def __init__(self):
            self.requests = []

        def generate(self, request):
            self.requests.append(request)
            if len(self.requests) == 1 or bad_correction == "wrong_schema":
                payload = {"schema_version": "reflection_campaign_v1", "belief_summary": "evidence"}
            else:
                payload = {
                    "schema_version": "reflection_compact_v3_campaign_memory",
                    "phase_summary": "The public record is stable.", "strategic_position": "The position is stable.",
                    "objective_update": {"current_objective": "Verify support.", "why_it_matters": "Avoid risk."},
                    "relationship_updates": [{"agent_id": "italy", "status": "trust", "evidence": "short",
                        "value_to_my_objectives": "low", "risk_or_limit": "", "next_use": ""}],
                    "action_lessons": [], "memory_candidates": [], "belief_summary": "No new evidence.",
                    "outcome_review": "The published order record and adjudication are evidence for the next action.",
                    "campaign_plan": "Maintain a conditional route.",
                    "commitment_memory": "No decision-relevant commitments yet.",
                    "memory_update": "The public board shows no change.",
                }
            return LLMResponse("1.0", json.dumps(payload), "offline-test", {"provider": "fake"})

    recovery = ExperimentRecovery(tmp_path / "recovery", identity={"id": "reflection-correction-fails"}, resume=False)
    recovery.prepare()
    provider = InvalidTwiceProvider()
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime",
        provider=provider, halt_on_issue=True, recovery=recovery)
    runtime.register_agent(agent_id="agent_llm")
    runtime.context_builder.build(_ctx(tmp_path))
    memory_json = tmp_path / "runtime/agent_llm/folio/game_memory/g1.json"
    before_memory = memory_json.read_bytes() if memory_json.exists() else None
    memory_markdown = tmp_path / "runtime/agent_llm/folio/game_memory/g1.md"
    before_markdown = memory_markdown.read_bytes() if memory_markdown.exists() else None
    with pytest.raises(RuntimeError, match="Recovery required before fallback: reflect invalid_output"):
        runtime.reflect(_ctx(tmp_path))

    assert len(provider.requests) == 2
    rows = recovery._journal_rows()
    assert len([row for row in rows if row.get("kind") == "dispatch"]) == 2
    assert len([row for row in rows if row.get("kind") == "order_correction_intent"]) == 1
    assert len([row for row in rows if row.get("kind") == "response_validation" and row.get("valid") is False]) == 2
    assert (memory_json.read_bytes() if memory_json.exists() else None) == before_memory
    assert (memory_markdown.read_bytes() if memory_markdown.exists() else None) == before_markdown


def test_negotiate_private_prefix_target_normalizes_to_agent_id(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(
            responses_by_method={
                "negotiate_tick": json.dumps(
                    {
                        "done": False,
                        "communication_plan": {
                            "goal": "Targeted outreach",
                            "target_players": ["england"],
                            "channel_choices": ["private:england"],
                        },
                        "relationship_analysis": [
                            {
                                "agent_id": "england",
                                "current_relationship": "new",
                                "what_they_may_want": "information",
                                "what_i_want_from_them": "coordination",
                                "how_this_serves_my_objectives": "supports defensive planning",
                                "risk_to_my_objectives": "vague commitments",
                                "specific_evidence": "initial private outreach received",
                                "next_strategic_use_of_relationship": "test with concrete question",
                                "next_conversation_move": "ask for specific destination",
                            }
                        ],
                        "messages": [{"to": "private:england", "body": "hello"}],
                    }
                )
            }
        ),
    )
    runtime.register_agent(agent_id="agent_llm")
    tick = runtime.negotiate_tick(_ctx(tmp_path))
    assert tick.ok is True
    assert len(tick.messages) == 1
    assert tick.messages[0]["to"]["to_agent_id"] == "agent_eng"


def test_negotiate_missing_strategic_relationship_fields_falls_back(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(
            responses_by_method={
                "negotiate_tick": json.dumps(
                    {
                        "done": False,
                        "communication_plan": {
                            "goal": "Open channel with England",
                            "target_players": ["england"],
                            "channel_choices": ["private:england"],
                        },
                        "relationship_analysis": [
                            {
                                "agent_id": "england",
                                "current_relationship": "new",
                                "what_they_may_want": "info",
                                "what_i_want_from_them": "support",
                                "how_this_serves_my_objectives": "",
                                "risk_to_my_objectives": "",
                                "specific_evidence": "",
                                "next_strategic_use_of_relationship": "",
                                "next_conversation_move": "ask question",
                            }
                        ],
                        "messages": [{"to": "england", "body": "hello"}],
                    }
                )
            }
        ),
    )
    runtime.register_agent(agent_id="agent_llm")
    tick = runtime.negotiate_tick(_ctx(tmp_path))
    assert tick.ok is True
    assert tick.messages == []
    assert bool((tick.explanation or {}).get("fallback_applied")) is True


def test_malformed_negotiate_fallback(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(responses_by_method={"negotiate_tick": "not json"}),
    )
    runtime.register_agent(agent_id="agent_llm")
    tick = runtime.negotiate_tick(_ctx(tmp_path))
    assert tick.ok is True
    assert tick.messages == []
    assert bool((tick.explanation or {}).get("fallback_applied")) is True


def test_valid_order_output_parses(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(
            responses_by_method={
                "submit_orders": json.dumps(
                    {
                        "orders": [
                            {"unit_ref": "u_fra_a_par", "type": "HOLD", "payload": {"at": "par"}},
                        ],
                        "order_rationale": {
                            "goal_supported": "No immediate safe attack",
                            "why_this_advances_strategy": "Preserves position",
                        },
                        "passivity_review": {"justified": True, "reason": "No safe supported move available"},
                        "explanation": {"summary": "hold"},
                    }
                )
            }
        ),
    )
    runtime.register_agent(agent_id="agent_llm")
    orders = runtime.submit_orders(_ctx(tmp_path))
    assert orders.ok is True
    assert len(orders.orders) == 1
    assert orders.orders[0]["type"] == "HOLD"


def test_malformed_order_fallback_holds_all_units(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(responses_by_method={"submit_orders": "[]"}),
    )
    runtime.register_agent(agent_id="agent_llm")
    orders = runtime.submit_orders(_ctx(tmp_path))
    assert len(orders.orders) == 2
    assert all(row["type"] == "HOLD" for row in orders.orders)


def test_all_hold_without_passivity_review_falls_back(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(
            responses_by_method={
                "submit_orders": json.dumps(
                    {
                        "orders": [
                            {"unit_ref": "u_fra_a_par", "type": "HOLD", "payload": {"at": "par"}},
                            {"unit_ref": "u_fra_f_bre", "type": "HOLD", "payload": {"at": "bre"}},
                        ],
                        "order_rationale": {
                            "goal_supported": "stay safe",
                            "why_this_advances_strategy": "temporary caution",
                        },
                    }
                )
            }
        ),
    )
    runtime.register_agent(agent_id="agent_llm")
    orders = runtime.submit_orders(_ctx(tmp_path))
    assert len(orders.orders) == 2
    assert all(row["type"] == "HOLD" for row in orders.orders)
    assert bool((orders.explanation or {}).get("fallback_applied")) is True


def test_malformed_real_provider_like_output_triggers_fallback(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(
            responses_by_method={
                "submit_orders": "Sure, here's what I would do:\n1) HOLD PAR\n2) HOLD BRE"
            }
        ),
    )
    runtime.register_agent(agent_id="agent_llm")
    orders = runtime.submit_orders(_ctx(tmp_path))
    assert len(orders.orders) == 2
    assert all(row["type"] == "HOLD" for row in orders.orders)
    assert bool((orders.explanation or {}).get("fallback_applied")) is True


class _InvalidThenValidOrderProvider:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            content = json.dumps(
                {
                    "orders": [{"unit_ref": "u_001", "type": "MOVE", "payload": {"to": "location_1"}}],
                    "order_rationale": {
                        "goal_supported": "Advance",
                        "why_this_advances_strategy": "Attempting initiative",
                    },
                    "explanation": {"summary": "first try"},
                }
            )
        else:
            content = json.dumps(
                {
                    "orders": [{"unit_ref": "u_fra_a_par", "type": "MOVE", "payload": {"to": "bur"}}],
                    "order_rationale": {
                        "goal_supported": "Advance",
                        "why_this_advances_strategy": "Legal grounded move",
                    },
                    "explanation": {"summary": "repaired"},
                }
            )
        return LLMResponse(schema_version=request.schema_version, content=content, model="repair-test", raw={})


def test_fake_unit_ids_trigger_repair_then_accept(tmp_path: Path) -> None:
    provider = _InvalidThenValidOrderProvider()
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=provider,
    )
    runtime.register_agent(agent_id="agent_llm")
    orders = runtime.submit_orders(_ctx(tmp_path))
    assert provider.calls == 2
    assert len(orders.orders) == 1
    assert orders.orders[0]["unit_ref"] == "u_fra_a_par"
    output_file = (
        tmp_path
        / "runtime"
        / "agent_llm"
        / "runtime"
        / "phase_outputs"
        / "1901_spring_movement_submit_orders.json"
    )
    payload = json.loads(output_file.read_text(encoding="utf-8"))
    assert payload["llm_meta"]["retry_attempted"] is True
    assert payload["llm_meta"]["fallback_applied"] is False
    assert payload["llm_meta"]["order_grounding"]["status"] == "accepted"
    assert payload["llm_meta"]["order_grounding"]["invalid_reasons"]


def test_illegal_destination_repeated_falls_back_to_legal_holds(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(
            responses_by_method={
                "submit_orders": json.dumps(
                    {
                        "orders": [{"unit_ref": "u_fra_a_par", "type": "MOVE", "payload": {"to": "location_1"}}],
                        "order_rationale": {
                            "goal_supported": "Advance",
                            "why_this_advances_strategy": "Try unknown destination",
                        },
                        "explanation": {"summary": "bad"},
                    }
                )
            }
        ),
    )
    runtime.register_agent(agent_id="agent_llm")
    orders = runtime.submit_orders(_ctx(tmp_path))
    assert len(orders.orders) == 2
    assert all(row["type"] == "HOLD" for row in orders.orders)
    output_file = (
        tmp_path
        / "runtime"
        / "agent_llm"
        / "runtime"
        / "phase_outputs"
        / "1901_spring_movement_submit_orders.json"
    )
    payload = json.loads(output_file.read_text(encoding="utf-8"))
    assert payload["llm_meta"]["fallback_applied"] is True
    assert payload["llm_meta"]["order_grounding"]["status"] == "fallback"
    assert payload["llm_meta"]["order_grounding"]["invalid_reasons"]


def test_winter_no_adjustment_requires_empty_orders(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(
            responses_by_method={
                "submit_orders": json.dumps(
                    {
                        "orders": [],
                        "order_rationale": {
                            "goal_supported": "No adjustment required",
                            "why_this_advances_strategy": "Complies with winter obligation",
                        },
                        "explanation": {"summary": "no adjustment"},
                    }
                )
            }
        ),
    )
    runtime.register_agent(agent_id="agent_llm")
    orders = runtime.submit_orders(_adjustment_ctx(delta=0))
    assert orders.orders == []
    output_file = (
        tmp_path
        / "runtime"
        / "agent_llm"
        / "runtime"
        / "phase_outputs"
        / "1901_winter_adjustment_submit_orders.json"
    )
    payload = json.loads(output_file.read_text(encoding="utf-8"))
    assert payload["llm_meta"]["fallback_applied"] is False


def test_adjustment_valid_build_from_legal_options_is_accepted(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(
            responses_by_method={
                "submit_orders": json.dumps(
                    {
                        "orders": [{"type": "BUILD", "payload": {"unit_type": "A", "province": "mar", "coast": None}}],
                        "order_rationale": {
                            "goal_supported": "Convert center lead into force",
                            "why_this_advances_strategy": "Uses legal winter build option",
                        },
                        "explanation": {"summary": "build"},
                    }
                )
            }
        ),
    )
    runtime.register_agent(agent_id="agent_llm")
    orders = runtime.submit_orders(_adjustment_ctx(delta=1))
    assert len(orders.orders) == 1
    assert orders.orders[0]["type"] == "BUILD"
    assert orders.orders[0]["payload"]["province"] == "mar"


class _InvalidThenValidAdjustmentProvider:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            payload = {
                "orders": [{"type": "BUILD", "payload": {"unit_type": "A", "province": "mun", "coast": None}}],
                "order_rationale": {
                    "goal_supported": "Build",
                    "why_this_advances_strategy": "First attempt invalid",
                },
            }
        else:
            payload = {
                "orders": [{"type": "BUILD", "payload": {"unit_type": "A", "province": "mar", "coast": None}}],
                "order_rationale": {
                    "goal_supported": "Build",
                    "why_this_advances_strategy": "Repaired to legal option",
                },
            }
        return LLMResponse(schema_version=request.schema_version, content=json.dumps(payload), model="adj-repair", raw={})


def test_adjustment_invalid_build_is_repaired(tmp_path: Path) -> None:
    provider = _InvalidThenValidAdjustmentProvider()
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=provider,
    )
    runtime.register_agent(agent_id="agent_llm")
    orders = runtime.submit_orders(_adjustment_ctx(delta=1))
    assert provider.calls == 2
    assert len(orders.orders) == 1
    assert orders.orders[0]["type"] == "BUILD"
    assert orders.orders[0]["payload"]["province"] == "mar"


def test_strict_adjustment_disband_payload_gets_one_durable_correction(tmp_path: Path) -> None:
    from llm_diplomacy.runner.recovery import ExperimentRecovery

    rejected = {
        "orders": [{"unit_ref": "u_fra_a_par", "type": "DISBAND", "payload": {}}],
        "order_rationale": {"goal_supported": "Reduce units", "why_this_advances_strategy": "Meet the limit."},
    }

    class CorrectionProvider:
        metadata = {"provider_name": "offline-test"}

        def __init__(self):
            self.requests = []

        def generate(self, request):
            self.requests.append(request)
            payload = rejected if len(self.requests) == 1 else {
                "orders": [{"type": "DISBAND", "payload": {"unit_ref": "u_fra_a_par"}}],
                "order_rationale": {"goal_supported": "Reduce units", "why_this_advances_strategy": "Meet the limit."},
            }
            return LLMResponse(request.schema_version, json.dumps(payload), "offline-test", {})

    provider = CorrectionProvider()
    recovery = ExperimentRecovery(tmp_path / "recovery", identity={"id": "adjustment-correction"}, resume=False)
    recovery.prepare()
    runtime = BaselineLLMAgentRuntime(
        reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime",
        provider=provider, halt_on_issue=True, recovery=recovery,
    )
    runtime.register_agent(agent_id="agent_llm")
    orders = runtime.submit_orders(_adjustment_ctx(delta=-1))

    assert len(provider.requests) == 2
    assert len(orders.orders) == 1
    assert orders.orders[0]["type"] == "DISBAND"
    assert orders.orders[0]["payload"] == {"unit_ref": "u_fra_a_par"}
    intent = next(row for row in recovery._journal_rows() if row.get("kind") == "order_correction_intent")
    attempts = [row for row in recovery._journal_rows()
                if row.get("kind") == "attempt" and row.get("outcome") == "received"]
    assert len(attempts) == 2
    assert intent["parent_attempt_id"] == attempts[0]["attempt_id"]
    assert intent["base_prompt_hash"] == attempts[0]["prompt_hash"]
    assert intent["correction_prompt_hash"] == attempts[1]["prompt_hash"]
    assert attempts[0]["prompt_hash"] != attempts[1]["prompt_hash"]
    feedback = json.loads(provider.requests[1].prompt.rsplit("\n", 1)[-1])
    assert feedback["engine_errors"] == [{"reason": "runtime_legality",
                                          "issue": "illegal adjustment order payload for DISBAND"}]
    assert feedback["rejected_answer"] == json.dumps(rejected)
    assert feedback["legal_options"]["adjustment"]["legal_options"] == [
        {"type": "DISBAND", "payload": {"unit_ref": "u_fra_a_par"}},
        {"type": "DISBAND", "payload": {"unit_ref": "u_fra_f_bre"}},
    ]


def test_strict_malformed_adjustment_response_does_not_get_semantic_correction(tmp_path: Path) -> None:
    from llm_diplomacy.runner.recovery import ExperimentRecovery

    class MalformedProvider:
        metadata = {"provider_name": "offline-test"}

        def __init__(self):
            self.calls = 0

        def generate(self, request):
            self.calls += 1
            content = "not json" if self.calls == 1 else json.dumps({
                "orders": [{"type": "DISBAND", "payload": {"unit_ref": "u_fra_a_par"}}],
                "order_rationale": {"goal_supported": "Reduce units", "why_this_advances_strategy": "Meet the limit."},
            })
            return LLMResponse(request.schema_version, content, "offline-test", {})

    provider = MalformedProvider()
    recovery = ExperimentRecovery(tmp_path / "recovery", identity={"id": "malformed-adjustment"}, resume=False)
    recovery.prepare()
    runtime = BaselineLLMAgentRuntime(
        reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime",
        provider=provider, halt_on_issue=True, recovery=recovery,
    )
    runtime.register_agent(agent_id="agent_llm")
    with pytest.raises(RuntimeError, match="Recovery required before fallback"):
        runtime.submit_orders(_adjustment_ctx(delta=-1))
    assert provider.calls == 1
    assert not any(row.get("kind") == "order_correction_intent" for row in recovery._journal_rows())


@pytest.mark.parametrize("bad_corrected_orders", [
    [],
    [{"type": "BUILD", "payload": {"unit_type": "A", "province": "par", "coast": None}}],
    [{"type": "DISBAND", "payload": {}}],
])
def test_strict_invalid_adjustment_correction_halts_and_never_dispatches_third(
        tmp_path: Path, bad_corrected_orders: list[dict[str, object]]) -> None:
    from llm_diplomacy.runner.recovery import ExperimentRecovery

    class InvalidCorrectionProvider:
        metadata = {"provider_name": "offline-test"}

        def __init__(self):
            self.requests = []

        def generate(self, request):
            self.requests.append(request)
            orders = ([{"unit_ref": "u_fra_a_par", "type": "DISBAND", "payload": {}}]
                      if len(self.requests) == 1 else bad_corrected_orders)
            return LLMResponse(request.schema_version, json.dumps({
                "orders": orders,
                "order_rationale": {"goal_supported": "Reduce units", "why_this_advances_strategy": "Meet the limit."},
            }), "offline-test", {})

    provider = InvalidCorrectionProvider()
    recovery_root = tmp_path / "recovery"
    recovery = ExperimentRecovery(recovery_root, identity={"id": "invalid-adjustment-correction"}, resume=False)
    recovery.prepare()
    runtime = BaselineLLMAgentRuntime(
        reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime",
        provider=provider, halt_on_issue=True, recovery=recovery,
    )
    runtime.register_agent(agent_id="agent_llm")
    with pytest.raises(RuntimeError, match="Recovery required before fallback"):
        runtime.submit_orders(_adjustment_ctx(delta=-1))
    assert len(provider.requests) == 2
    assert len([row for row in recovery._journal_rows() if row.get("kind") == "order_correction_intent"]) == 1

    resumed_provider = InvalidCorrectionProvider()
    resumed = ExperimentRecovery(recovery_root, identity={"id": "invalid-adjustment-correction"}, resume=True)
    resumed.prepare()
    resumed_runtime = BaselineLLMAgentRuntime(
        reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime",
        provider=resumed_provider, halt_on_issue=True, recovery=resumed,
    )
    with pytest.raises(RuntimeError, match="Recovery required before fallback"):
        resumed_runtime.submit_orders(_adjustment_ctx(delta=-1))
    assert resumed_provider.requests == []
    assert len([row for row in resumed._journal_rows()
                if row.get("kind") == "order_correction_intent"]) == 1


def test_adjustment_invalid_build_falls_back_to_deterministic_legal_build(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(
            responses_by_method={
                "submit_orders": json.dumps(
                    {
                        "orders": [{"type": "BUILD", "payload": {"unit_type": "A", "province": "mun", "coast": None}}],
                        "order_rationale": {
                            "goal_supported": "Build",
                            "why_this_advances_strategy": "Invalid attempt",
                        },
                    }
                )
            }
        ),
    )
    runtime.register_agent(agent_id="agent_llm")
    orders = runtime.submit_orders(_adjustment_ctx(delta=1))
    assert len(orders.orders) == 1
    assert orders.orders[0]["type"] == "BUILD"
    assert orders.orders[0]["payload"]["province"] == "mar"
    output_file = (
        tmp_path
        / "runtime"
        / "agent_llm"
        / "runtime"
        / "phase_outputs"
        / "1901_winter_adjustment_submit_orders.json"
    )
    payload = json.loads(output_file.read_text(encoding="utf-8"))
    assert payload["llm_meta"]["fallback_applied"] is True
    assert payload["llm_meta"]["order_grounding"]["legal_order_context"]["adjustment"]["required_count"] == 1


def test_submit_order_prompt_contains_legal_order_context_with_exact_units(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(
            responses_by_method={
                "submit_orders": json.dumps(
                    {
                        "orders": [{"unit_ref": "u_fra_a_par", "type": "HOLD", "payload": {"at": "par"}}],
                        "order_rationale": {
                            "goal_supported": "Defend",
                            "why_this_advances_strategy": "Hold while evaluating",
                        },
                        "passivity_review": {"justified": True, "reason": "No safe tactical move available."},
                    }
                )
            }
        ),
    )
    runtime.register_agent(agent_id="agent_llm")
    runtime.submit_orders(_ctx(tmp_path))
    output_file = (
        tmp_path
        / "runtime"
        / "agent_llm"
        / "runtime"
        / "phase_outputs"
        / "1901_spring_movement_submit_orders.json"
    )
    payload = json.loads(output_file.read_text(encoding="utf-8"))
    prompt = payload["llm_meta"]["prompt"]
    assert "# Legal Order Context" in prompt
    assert "u_fra_a_par" in prompt
    assert "u_fra_f_bre" in prompt
    assert isinstance(payload["llm_meta"]["prompt_size_chars"], int)
    assert isinstance(payload["llm_meta"]["estimated_input_tokens"], int)


class _MalformedThenValidReflectionProvider:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            content = "not valid reflection json"
        else:
            content = json.dumps(
                {
                    "schema_version": "reflection_compact_v3_campaign_memory",
                    "objective_observations": ["No private messages observed."],
                    "belief_updates": [],
                    "strategic_intent": {"short_term": "Reach out", "medium_term": "Coordinate", "risk_notes": []},
                    "memory_update": "repair_success",
                    "outcome_review": "The published order record and adjudication are the evidence for the next action.",
                    "campaign_plan": "Reach out conditionally and revise against public evidence.",
                    "commitment_memory": "No decision-relevant commitments yet.",
                    "explanation": {"summary": "repaired"},
                }
            )
        return LLMResponse(schema_version=request.schema_version, content=content, model="reflect-repair", raw={})


def test_reflection_malformed_then_valid_repair_succeeds(tmp_path: Path) -> None:
    provider = _MalformedThenValidReflectionProvider()
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=provider,
    )
    runtime.register_agent(agent_id="agent_llm")
    reflection = runtime.reflect(_ctx(tmp_path))
    assert reflection.ok is True
    assert reflection.reflection["memory_update"] == "repair_success"
    output_file = (
        tmp_path
        / "runtime"
        / "agent_llm"
        / "runtime"
        / "phase_outputs"
        / "1901_spring_movement_reflect.json"
    )
    payload = json.loads(output_file.read_text(encoding="utf-8"))
    assert payload["llm_meta"]["retry_attempted"] is True
    assert payload["llm_meta"]["fallback_applied"] is False


def test_reflection_with_fenced_json_is_accepted(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(
            responses_by_method={
                "reflect": (
                    "```json\n"
                    + json.dumps(
                        {
                                "schema_version": "reflection_compact_v3_campaign_memory",
                            "objective_observations": ["fenced"],
                            "belief_updates": [],
                            "strategic_intent": {"short_term": "a", "medium_term": "b", "risk_notes": []},
                                "memory_update": "ok",
                                    "outcome_review": "The public order record and adjudication provide the evidence for the next action.",
                                    "campaign_plan": "Maintain a conditional route.", "commitment_memory": "No decision-relevant commitments yet.",
                        }
                    )
                    + "\n```"
                )
            }
        ),
    )
    runtime.register_agent(agent_id="agent_llm")
    reflection = runtime.reflect(_ctx(tmp_path))
    assert reflection.ok is True
    assert reflection.reflection["memory_update"] == "ok"


def test_reflection_with_extra_prose_json_extracts_successfully(tmp_path: Path) -> None:
    content = (
        "Here is my analysis.\n"
        + json.dumps(
            {
                        "schema_version": "reflection_compact_v3_campaign_memory",
                "objective_observations": ["extra prose handled"],
                "belief_updates": [],
                "strategic_intent": {"short_term": "a", "medium_term": "b", "risk_notes": []},
                    "memory_update": "ok2",
                        "outcome_review": "The public order record and adjudication provide the evidence for the next action.",
                        "campaign_plan": "Maintain a conditional route.", "commitment_memory": "No decision-relevant commitments yet.",
            }
        )
        + "\nThanks."
    )
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(responses_by_method={"reflect": content}),
    )
    runtime.register_agent(agent_id="agent_llm")
    reflection = runtime.reflect(_ctx(tmp_path))
    assert reflection.ok is True
    assert reflection.reflection["memory_update"] == "ok2"


def test_reflection_missing_optional_explanation_is_accepted(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(
            responses_by_method={
                "reflect": json.dumps(
                    {
                        "schema_version": "1.0",
                        "objective_observations": [],
                        "belief_updates": [],
                        "strategic_intent": {"short_term": "a", "medium_term": "b", "risk_notes": []},
                        "memory_update": "no explanation",
                    }
                )
            }
        ),
    )
    runtime.register_agent(agent_id="agent_llm")
    reflection = runtime.reflect(_ctx(tmp_path))
    assert reflection.ok is True
    assert reflection.reflection["memory_update"] == "no explanation"


def test_reflection_missing_strategic_intent_is_normalized(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(
            responses_by_method={
                "reflect": json.dumps(
                    {
                        "schema_version": "1.0",
                        "objective_observations": ["x"],
                        "belief_updates": [],
                        "memory_update": "normalized",
                    }
                )
            }
        ),
    )
    runtime.register_agent(agent_id="agent_llm")
    reflection = runtime.reflect(_ctx(tmp_path))
    assert reflection.ok is True
    assert reflection.reflection["strategic_intent"]["short_term"] == ""
    assert reflection.reflection["memory_update"] == "normalized"


def test_overlong_malformed_reflection_fallback_remains_structured(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(
            responses_by_method={"reflect": "not_json_" + ("x" * 12000)}
        ),
    )
    runtime.register_agent(agent_id="agent_llm")
    reflection = runtime.reflect(_ctx(tmp_path))
    assert reflection.ok is True
    assert reflection.reflection["schema_version"] == "reflection_compact_v3_campaign_memory"
    assert isinstance(reflection.reflection["objective_observations"], list)
    assert bool((reflection.explanation or {}).get("fallback_applied")) is True
    output_file = (
        tmp_path
        / "runtime"
        / "agent_llm"
        / "runtime"
        / "phase_outputs"
        / "1901_spring_movement_reflect.json"
    )
    payload = json.loads(output_file.read_text(encoding="utf-8"))
    assert payload["llm_meta"]["likely_truncated"] is True
    assert payload["llm_meta"]["parse_error_category"] == "likely_truncated_output"
    failure = payload["llm_meta"]["reflection_failure"]
    assert isinstance(failure, dict)
    assert failure["schema_validation_error_category"] in {"invalid_field_shape", "missing_required_field", "validation_error", None}
    assert failure["repair_attempted"] is True


def test_reflection_writes_game_memory(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=runtime_root,
        provider=FakeLLMProvider(
            responses_by_method={
                "reflect": json.dumps(
                    {
                        "schema_version": "1.0",
                        "objective_observations": ["No private messages received."],
                        "belief_updates": [
                            {
                                "subject": "italy",
                                "belief": "No cooperation signal yet.",
                                "confidence": "low",
                                "basis": "No observed messages.",
                            }
                        ],
                        "strategic_intent": {
                            "short_term": "Open outreach next phase.",
                            "medium_term": "Reduce isolation.",
                            "risk_notes": ["Silence increases uncertainty."],
                        },
                        "memory_update": "note_a",
                        "explanation": {"summary": "r"},
                    }
                )
            }
        ),
    )
    runtime.register_agent(agent_id="agent_llm")
    reflection = runtime.reflect(_ctx(tmp_path))
    assert reflection.ok is True
    memory_file = runtime_root / "agent_llm" / "folio" / "game_memory" / "g1.json"
    payload = json.loads(memory_file.read_text(encoding="utf-8"))
    assert "No private messages received." in payload["objective_facts"]
    assert payload["belief_state"][0]["subject"] == "italy"
    assert payload["memory_log"][-1]["memory_update"] == "note_a"


def test_malformed_reflection_fallback_is_structured(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(responses_by_method={"reflect": '{"memory_update":"bad_only"}'}),
    )
    runtime.register_agent(agent_id="agent_llm")
    reflection = runtime.reflect(_ctx(tmp_path))
    assert reflection.ok is True
    assert reflection.reflection["objective_observations"] == ["Reflection unavailable; using fallback after parse failure."]
    assert reflection.reflection["belief_updates"] == []
    assert reflection.reflection["strategic_intent"]["risk_notes"] == []
    # Fresh malformed output falls back rather than entering legacy normalization.
    assert reflection.reflection["memory_update"] == "Fallback reflection: no reliable parsed reflection."
    output_file = (
        tmp_path
        / "runtime"
        / "agent_llm"
        / "runtime"
        / "phase_outputs"
        / "1901_spring_movement_reflect.json"
    )
    payload = json.loads(output_file.read_text(encoding="utf-8"))
    assert payload["llm_meta"]["fallback_applied"] is True


def test_reflection_rejects_hallucinated_commitments_without_messages(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.visible_state["private_messages"] = []
    ctx.visible_state["global_messages"] = []
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(
            responses_by_method={
                "reflect": json.dumps(
                    {
                        "schema_version": "1.0",
                        "objective_observations": ["Trust was established with Italy."],
                        "belief_updates": [
                            {
                                "subject": "italy",
                                "belief": "Italy made a commitment to cooperate.",
                                "confidence": "high",
                                "basis": "We promised support.",
                            }
                        ],
                        "strategic_intent": {
                            "short_term": "Coordinate with Italy.",
                            "medium_term": "Build alliance core.",
                            "risk_notes": [],
                        },
                        "memory_update": "Italy committed to us.",
                        "explanation": {"summary": "bad claim"},
                    }
                )
            }
        ),
    )
    runtime.register_agent(agent_id="agent_llm")
    reflection = runtime.reflect(ctx)
    assert isinstance(reflection.reflection["objective_observations"], list)
    assert "Fallback reflection" in reflection.reflection["memory_update"]


def test_reflection_personality_observation_without_consequence_falls_back(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(
            responses_by_method={
                "reflect": json.dumps(
                    {
                        "schema_version": "reflection_compact_v1",
                        "phase_summary": "Quiet phase.",
                        "strategic_position": "Stable position.",
                        "objective_update": {
                            "current_objective": "Maintain communication.",
                            "why_it_matters": "General stability.",
                        },
                        "relationship_updates": [
                            {
                                "agent_id": "victor",
                                "status": "information_source",
                                "evidence": "Victor values red lines.",
                                "value_to_my_objectives": "",
                                "risk_or_limit": "",
                                "next_use": "",
                            }
                        ],
                        "action_lessons": [],
                        "memory_candidates": ["Victor values red lines."],
                    }
                )
            }
        ),
    )
    runtime.register_agent(agent_id="agent_llm")
    reflection = runtime.reflect(_ctx(tmp_path))
    assert reflection.ok is True
    assert bool((reflection.explanation or {}).get("fallback_applied")) is True


def test_reflection_personality_observation_with_consequence_is_accepted(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(
            responses_by_method={
                "reflect": json.dumps(
                    {
                        "schema_version": "reflection_compact_v1",
                        "phase_summary": "Border remained stable.",
                        "strategic_position": "West is calmer than east.",
                        "objective_update": {
                            "current_objective": "Redeploy east while preserving western border.",
                            "why_it_matters": "Frees units for expansion pressure.",
                        },
                        "relationship_updates": [
                            {
                                "agent_id": "victor",
                                "status": "useful_defensive_partner",
                                "evidence": "Victor values red lines and respected the border.",
                                "value_to_my_objectives": "Lets me reduce western pressure and redeploy east.",
                                "risk_or_limit": "No expansion support offered yet.",
                                "next_use": "Offer explicit boundary terms next phase.",
                            }
                        ],
                        "action_lessons": ["Border clarity can create redeployment leverage."],
                        "memory_candidates": [
                            "Victor responds to explicit red lines; use clear borders to free units for eastern play."
                        ],
                    }
                )
            }
        ),
    )
    runtime.register_agent(agent_id="agent_llm")
    reflection = runtime.reflect(_ctx(tmp_path))
    assert reflection.ok is True
    assert bool((reflection.explanation or {}).get("fallback_applied")) is False
    assert reflection.reflection["relationship_updates"][0]["status"] == "useful_defensive_partner"


def test_reflection_free_form_relationship_status_is_preserved(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(
            responses_by_method={
                "reflect": json.dumps(
                    {
                        "schema_version": "reflection_compact_v1",
                        "phase_summary": "No major change.",
                        "strategic_position": "Balanced fronts.",
                        "objective_update": {
                            "current_objective": "Test one concrete partner.",
                            "why_it_matters": "Need tactical leverage soon.",
                        },
                        "relationship_updates": [
                            {
                                "agent_id": "victor",
                                "status": "  mystery_mode  ",
                                "evidence": "Signals are mixed.",
                                "value_to_my_objectives": "Unclear tactical value.",
                                "risk_or_limit": "Could remain low-value talk.",
                                "next_use": "Probe with specific ask.",
                            }
                        ],
                        "action_lessons": [],
                        "memory_candidates": [],
                    }
                )
            }
        ),
    )
    runtime.register_agent(agent_id="agent_llm")
    reflection = runtime.reflect(_ctx(tmp_path))
    assert reflection.ok is True
    assert reflection.reflection["relationship_updates"][0]["status"] == "mystery_mode"


def test_memory_candidates_filter_generic_social_only_lines(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=runtime_root,
        provider=FakeLLMProvider(
            responses_by_method={
                "reflect": json.dumps(
                    {
                        "schema_version": "reflection_compact_v1",
                        "phase_summary": "Quiet phase.",
                        "strategic_position": "Stable but not expanding.",
                        "objective_update": {
                            "current_objective": "Convert one relationship into support.",
                            "why_it_matters": "Need growth path.",
                        },
                        "relationship_updates": [],
                        "action_lessons": [],
                        "memory_candidates": [
                            "I maintained stable communication.",
                            "Communication remained cordial.",
                            "France honored the border and this lowered western defense pressure.",
                        ],
                    }
                )
            }
        ),
    )
    runtime.register_agent(agent_id="agent_llm")
    runtime.reflect(_ctx(tmp_path))
    payload = json.loads((runtime_root / "agent_llm" / "folio" / "game_memory" / "g1.json").read_text(encoding="utf-8"))
    candidates = payload.get("memory_candidates", [])
    assert "I maintained stable communication." not in candidates
    assert "Communication remained cordial." not in candidates
    assert any("lowered western defense pressure" in row for row in candidates)


def test_reflection_persists_structured_belief_update_with_phase_id(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    belief_update = {
        "subject": "victor",
        "prior_belief": "Victor's Galicia DMZ was only a claim.",
        "new_belief": "The narrow Galicia DMZ is currently reliable.",
        "evidence": "Victor kept Warsaw and Moscow out of Galicia in the resolution.",
        "strategic_consequence": "Balkan expansion can use less northern defense.",
        "next_test_or_action": "Verify the next orders remain outside Galicia.",
    }
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=runtime_root,
        provider=FakeLLMProvider(
            responses_by_method={
                "reflect": json.dumps(
                    {
                        "schema_version": "reflection_compact_v1",
                        "phase_summary": "Galicia remained clear after resolution.",
                        "strategic_position": "Northern risk is temporarily lower.",
                        "objective_update": {
                            "current_objective": "Use the opening to pressure the Balkans.",
                            "why_it_matters": "Converts verified border stability into expansion leverage.",
                        },
                        "relationship_updates": [],
                        "action_lessons": [],
                        "memory_candidates": [
                            "Victor kept Galicia clear, lowering northern defense pressure; verify the next orders."
                        ],
                        "belief_updates": [belief_update],
                    }
                ),
                "negotiate_tick": json.dumps(
                    {
                        "private_messages": [],
                        "global_messages": [],
                        "relationship_updates": [],
                        "withheld_information": [],
                    }
                ),
            }
        ),
    )
    runtime.register_agent(agent_id="agent_llm")
    reflection = runtime.reflect(_ctx(tmp_path))
    assert reflection.explanation.get("fallback_applied") is not True
    assert reflection.reflection["belief_updates"][0]["prior_belief"] == belief_update["prior_belief"]
    reflection_file = runtime_root / "agent_llm" / "runtime" / "phase_outputs" / "1901_spring_movement_reflect.json"
    reflection_prompt = json.loads(reflection_file.read_text(encoding="utf-8"))["llm_meta"]["prompt"]
    assert "Base it on what powers said, what they did, the current board" in reflection_prompt
    assert "Write belief_summary every reflection" in reflection_prompt
    memory = json.loads(
        (runtime_root / "agent_llm" / "folio" / "game_memory" / "g1.json").read_text(encoding="utf-8")
    )
    persisted = memory["belief_state"][-1]
    assert persisted["phase_id"] == "1901_spring_movement"
    assert persisted["new_belief"] == belief_update["new_belief"]
    assert persisted["evidence_class"] == "order_or_resolution"

    runtime.negotiate_tick(_ctx(tmp_path))
    prompt_file = runtime_root / "agent_llm" / "runtime" / "phase_outputs" / "1901_spring_movement_negotiate_tick.json"
    prompt = json.loads(prompt_file.read_text(encoding="utf-8"))["llm_meta"]["prompt"]
    assert "# Current Belief Summary (Private Interpretation)" in prompt
    assert "victor: The narrow Galicia DMZ is currently reliable." in prompt


def test_legacy_oversized_belief_updates_normalize_to_summary(tmp_path: Path) -> None:
    valid = {
        "subject": "victor",
        "prior_belief": "Old belief.",
        "new_belief": "New belief.",
        "evidence": "Observed order.",
        "strategic_consequence": "Defense changes.",
        "next_test_or_action": "Verify next order.",
    }
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(
            responses_by_method={
                "reflect": json.dumps(
                    {
                        "schema_version": "reflection_compact_v1",
                        "phase_summary": "A change occurred.",
                        "strategic_position": "Position changed.",
                        "objective_update": {"current_objective": "Respond.", "why_it_matters": "Safety."},
                        "relationship_updates": [],
                        "action_lessons": [],
                        "memory_candidates": [],
                        "belief_updates": [valid, valid, valid, valid],
                    }
                )
            }
        ),
    )
    runtime.register_agent(agent_id="agent_llm")
    reflection = runtime.reflect(_ctx(tmp_path))
    assert reflection.explanation.get("fallback_applied") is not True
    assert reflection.reflection["belief_updates"]
    assert "victor: New belief." in reflection.reflection["belief_summary"]


def test_legacy_reflection_without_belief_updates_remains_valid(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(
            responses_by_method={
                "reflect": json.dumps(
                    {
                        "schema_version": "reflection_compact_v1",
                        "phase_summary": "No material change.",
                        "strategic_position": "Stable position.",
                        "objective_update": {"current_objective": "Wait for evidence.", "why_it_matters": "Avoid unsupported moves."},
                        "relationship_updates": [],
                        "action_lessons": [],
                        "memory_candidates": [],
                    }
                )
            }
        ),
    )
    runtime.register_agent(agent_id="agent_llm")
    reflection = runtime.reflect(_ctx(tmp_path))
    assert reflection.explanation.get("fallback_applied") is not True
    assert reflection.reflection["belief_updates"] == []


def test_belief_summary_persists_revises_and_retains_history(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"

    def reflection_payload(summary: str) -> str:
        return json.dumps(
            {
                "schema_version": "reflection_compact_v1",
                "phase_summary": "Board pressure is changing.",
                "strategic_position": "Eastern risk requires attention.",
                "objective_update": {"current_objective": "Verify Galicia.", "why_it_matters": "Avoid surprise exposure."},
                "relationship_updates": [],
                "action_lessons": [],
                "memory_candidates": [],
                "belief_summary": summary,
            }
        )

    first = "Russia's Galicia DMZ appears credible after the resolution, but future orders must verify it."
    second = "Russia's entry into Galicia makes prior DMZ assurances unreliable; defend before relying on new promises."
    provider = FakeLLMProvider(responses_by_method={"reflect": reflection_payload(first)})
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=runtime_root, provider=provider)
    runtime.register_agent(agent_id="agent_llm")
    assert runtime.reflect(_ctx(tmp_path)).reflection["belief_summary_status"] == "produced"
    provider.responses_by_method["reflect"] = reflection_payload(second)
    revised = runtime.reflect(_ctx(tmp_path)).reflection
    assert revised["belief_summary"] == second
    assert revised["belief_summary_status"] == "revised"
    memory = json.loads((runtime_root / "agent_llm" / "folio" / "game_memory" / "g1.json").read_text(encoding="utf-8"))
    assert memory["belief_summary"] == second
    assert memory["belief_summary_history"][-1]["summary"] == first
    assert memory["belief_summary_history"][-1]["phase_id"] == "1901_spring_movement"

    provider.responses_by_method["reflect"] = reflection_payload("")
    maintained = runtime.reflect(_ctx(tmp_path)).reflection
    assert maintained["belief_summary"] == second
    assert maintained["belief_summary_status"] == "maintained"


def test_belief_summary_trim_fallback_and_all_turn_contexts(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    oversized = " ".join(["Evidence remains uncertain."] * 601)
    payload = json.dumps(
        {
            "schema_version": "reflection_compact_v1",
            "phase_summary": "No new material event.",
            "strategic_position": "Cautious position.",
            "objective_update": {"current_objective": "Wait for proof.", "why_it_matters": "Avoid unsupported risk."},
            "relationship_updates": [],
            "action_lessons": [],
            "memory_candidates": [],
            "belief_summary": oversized,
        }
    )
    provider = FakeLLMProvider(responses_by_method={"reflect": payload})
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=runtime_root, provider=provider)
    runtime.register_agent(agent_id="agent_llm")
    reflection = runtime.reflect(_ctx(tmp_path)).reflection
    assert len(reflection["belief_summary"].split()) <= 600
    assert reflection["belief_summary"].endswith(".")

    provider.responses_by_method["reflect"] = "not json"
    fallback = runtime.reflect(_ctx(tmp_path)).reflection
    assert fallback["belief_summary"] == reflection["belief_summary"]
    assert fallback["belief_summary_status"] == "fallback_retained"

    provider.responses_by_method["negotiate_tick"] = json.dumps(_compact_negotiate_payload())
    provider.responses_by_method["submit_orders"] = json.dumps(
        {"orders": [], "order_rationale": {"goal_supported": "wait", "why_this_advances_strategy": "avoid risk"}}
    )
    provider.responses_by_method["game_end_cleanup"] = json.dumps(
        {"persistent_memory_update": "", "public_profile_update": None}
    )
    provider.responses_by_method["reflect"] = payload
    runtime.negotiate_tick(_ctx(tmp_path))
    runtime.submit_orders(_ctx(tmp_path))
    runtime.reflect(_ctx(tmp_path))
    runtime.game_end_cleanup(_ctx(tmp_path))
    for method in ("negotiate_tick", "submit_orders", "reflect", "game_end_cleanup"):
        path = runtime_root / "agent_llm" / "runtime" / "phase_outputs" / f"1901_spring_movement_{method}.json"
        prompt = json.loads(path.read_text(encoding="utf-8"))["llm_meta"]["prompt"]
        assert "# Current Belief Summary (Private Interpretation)" in prompt
        assert reflection["belief_summary"] in prompt
        assert "Authoritative board state and adjudicated resolutions override it." in prompt
        assert prompt.index("# Authoritative Current Board State") < prompt.index("# Current Belief Summary")


def test_cleanup_updates_persistent_memory(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=runtime_root,
        provider=FakeLLMProvider(
            responses_by_method={
                "game_end_cleanup": json.dumps(
                    {
                        "persistent_memory_update": "lesson_x",
                        "public_profile_update": None,
                        "explanation": {"summary": "c"},
                    }
                )
            }
        ),
    )
    runtime.register_agent(agent_id="agent_llm")
    cleanup = runtime.game_end_cleanup(
        AgentContext(
            schema_version="1.0",
            experiment_id="exp",
            game_id="g1",
            phase_id="game_end_cleanup",
            agent_id="agent_llm",
            country_assignment="france",
            visible_state={"schema_version": "1.0", "game_id": "g1"},
        )
    )
    assert cleanup.ok is True
    persistent = runtime_root / "agent_llm" / "folio" / "persistent_memory.md"
    assert "lesson_x" in persistent.read_text(encoding="utf-8")


class _UsageProvider:
    def generate(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            schema_version=request.schema_version,
            content=(
                '{"orders":[],"order_rationale":{"goal_supported":"ok","why_this_advances_strategy":"ok"},'
                '"explanation":{"summary":"ok"}}'
            ),
            model="usage-provider",
            raw={"usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}},
        )


def test_usage_metadata_propagates_when_provider_supplies_usage(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=_UsageProvider(),
    )
    runtime.register_agent(agent_id="agent_llm")
    orders = runtime.submit_orders(_ctx(tmp_path))
    assert orders.usage["tokens"] == 20
    assert orders.usage["prompt_tokens"] == 12
    assert orders.usage["completion_tokens"] == 8
    assert orders.usage["usage_available"] is True


class _FailingProvider:
    def generate(self, request: LLMRequest) -> LLMResponse:
        raise TimeoutError("simulated timeout")


class _MalformedThenValidProvider:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                schema_version=request.schema_version,
                content="not json",
                model="retry-provider",
                raw={"usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}},
            )
        return LLMResponse(
            schema_version=request.schema_version,
            content=json.dumps(
                {
                    "orders": [{"unit_ref": "u_fra_a_par", "type": "HOLD", "payload": {"at": "par"}}],
                    "order_rationale": {
                        "goal_supported": "defensive posture",
                        "why_this_advances_strategy": "avoid unsupported risk",
                    },
                    "passivity_review": {"justified": True, "reason": "No safe alternative this phase."},
                    "explanation": {"summary": "repaired"},
                }
            ),
            model="retry-provider",
            raw={"usage": {"prompt_tokens": 6, "completion_tokens": 3, "total_tokens": 9}},
        )


class _FencedJsonProvider:
    def generate(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            schema_version=request.schema_version,
            content='```json\n{"done": true, "messages": [], "explanation": {"summary": "ok"}}\n```',
            model="fenced-provider",
            raw={},
        )


def test_provider_error_falls_back_without_crash(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=_FailingProvider(),
    )
    runtime.register_agent(agent_id="agent_llm")
    orders = runtime.submit_orders(_ctx(tmp_path))
    assert orders.ok is True
    assert len(orders.orders) == 2
    output_file = (
        tmp_path
        / "runtime"
        / "agent_llm"
        / "runtime"
        / "phase_outputs"
        / "1901_spring_movement_submit_orders.json"
    )
    payload = json.loads(output_file.read_text(encoding="utf-8"))
    assert payload["llm_meta"]["provider_error"]
    assert payload["llm_meta"]["parse_error"] is False


def test_malformed_then_valid_retry_recovers_without_fallback(tmp_path: Path) -> None:
    provider = _MalformedThenValidProvider()
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=provider,
    )
    runtime.register_agent(agent_id="agent_llm")
    orders = runtime.submit_orders(_ctx(tmp_path))
    assert provider.calls == 2
    assert orders.ok is True
    assert len(orders.orders) == 1
    output_file = (
        tmp_path
        / "runtime"
        / "agent_llm"
        / "runtime"
        / "phase_outputs"
        / "1901_spring_movement_submit_orders.json"
    )
    payload = json.loads(output_file.read_text(encoding="utf-8"))
    llm_meta = payload["llm_meta"]
    assert llm_meta["fallback_applied"] is False
    assert llm_meta["retry_attempted"] is True
    assert llm_meta["retry_count"] == 1
    assert llm_meta["retry_success"] is True


def test_json_extraction_accepts_fenced_json(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=_FencedJsonProvider(),
    )
    runtime.register_agent(agent_id="agent_llm")
    tick = runtime.negotiate_tick(_ctx(tmp_path))
    assert tick.ok is True
    assert tick.messages == []


def test_authorized_context_filters_private_messages(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=runtime_root,
        provider=FakeLLMProvider(),
    )
    runtime.register_agent(agent_id="agent_llm")
    runtime.negotiate_tick(_ctx(tmp_path))
    output_file = runtime_root / "agent_llm" / "runtime" / "phase_outputs" / "1901_spring_movement_negotiate_tick.json"
    payload = json.loads(output_file.read_text(encoding="utf-8"))
    prompt = payload["llm_meta"]["prompt"]
    assert "for llm" in prompt
    assert '"body": "secret"' not in prompt


def test_prompt_includes_previous_commitments_section(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=runtime_root,
        provider=FakeLLMProvider(),
    )
    runtime.register_agent(agent_id="agent_llm")
    runtime.negotiate_tick(_ctx(tmp_path))
    output_file = runtime_root / "agent_llm" / "runtime" / "phase_outputs" / "1901_spring_movement_negotiate_tick.json"
    payload = json.loads(output_file.read_text(encoding="utf-8"))
    prompt = payload["llm_meta"]["prompt"]
    assert "# Previous Commitments" in prompt


def test_prompt_includes_strategic_imperatives_and_ordering(tmp_path: Path) -> None:
    strategic = tmp_path / "strategic_imperatives.md"
    strategic.write_text("UNIVERSAL_WIN_OBJECTIVE", encoding="utf-8")
    doctrine = tmp_path / "victory_campaign_doctrine.md"
    doctrine.write_text("CAMPAIGN_DOCTRINE_TEST", encoding="utf-8")
    primer = tmp_path / "diplomacy_primer.md"
    primer.write_text("DIPLOMACY_PRIMER_TEST", encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=runtime_root,
        provider=FakeLLMProvider(),
        strategic_imperatives_path=strategic,
        victory_campaign_doctrine_path=doctrine,
        diplomacy_primer_path=primer,
    )
    runtime.register_agent(agent_id="agent_llm")
    runtime.negotiate_tick(_ctx(tmp_path))
    output_file = runtime_root / "agent_llm" / "runtime" / "phase_outputs" / "1901_spring_movement_negotiate_tick.json"
    payload = json.loads(output_file.read_text(encoding="utf-8"))
    prompt = payload["llm_meta"]["prompt"]
    i1 = prompt.index("# Strategic Imperatives")
    i1a = prompt.index("# Victory and Campaign Doctrine")
    i1b = prompt.index("# Diplomacy Primer")
    i2 = prompt.index("# Current Game Rules / Phase")
    i3 = prompt.index("# Soul")
    i4 = prompt.index("# Persistent Memory")
    i5 = prompt.index("# Authoritative Current Board State")
    i6 = prompt.index("# Latest Authoritative Resolution / Board Delta")
    i7 = prompt.index("# Current Belief Summary")
    i8 = prompt.index("# Current Game Memory")
    i9 = prompt.index("# Visible Communications")
    i10 = prompt.index("# Required JSON Schema")
    assert i1 < i1a < i1b < i2 < i3 < i4 < i5 < i6 < i7 < i8 < i9 < i10
    assert "UNIVERSAL_WIN_OBJECTIVE" in prompt
    assert "CAMPAIGN_DOCTRINE_TEST" in prompt
    assert "DIPLOMACY_PRIMER_TEST" in prompt


def test_prompt_includes_public_province_delta_without_hidden_orders(tmp_path: Path) -> None:
    context = _ctx(tmp_path)
    context.visible_state["supply_ownership"] = {"par": "france", "nwy": "england", "swe": "russia"}
    context.visible_state["latest_public_resolution"] = {
        "phase": "movement",
        "moves": [
            {"power": "england", "unit_id": "eng_f_nth", "from": "nth", "to": "nwy", "result": "moved"},
            {"power": "russia", "unit_id": "rus_f_swe", "from": "swe", "to": "swe", "result": "held"},
        ],
        "center_changes": [],
        "submitted_orders": [{"secret": "must_not_appear"}],
    }
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime", provider=FakeLLMProvider())
    runtime.register_agent(agent_id="agent_llm")
    runtime.negotiate_tick(context)
    prompt = json.loads((tmp_path / "runtime" / "agent_llm" / "runtime" / "phase_outputs" / "1901_spring_movement_negotiate_tick.json").read_text(encoding="utf-8"))["llm_meta"]["prompt"]
    assert "nwy" in prompt and "swe" in prompt
    assert "must_not_appear" not in prompt
    assert prompt.index("# Authoritative Current Board State") < prompt.index("# Current Belief Summary")


def test_mixed_game_one_llm_agent_runs_and_observatory_contains_llm_meta(tmp_path) -> None:
    cfg = {
        "schema_version": "1.0",
        "experiment_id": "exp_mixed_llm",
        "games": 1,
        "agents": [
            {"agent_id": "agent_llm", "runtime_kind": "llm"},
            {"agent_id": "agent_hold", "runtime_kind": "scripted", "scripted_kind": "hold"},
            {"agent_id": "agent_rand", "runtime_kind": "scripted", "scripted_kind": "random_legal"},
        ],
        "rule_engine": {"name": "diplomacy"},
        "negotiation": {"max_ticks": 1},
        "seed": 17,
        "llm_provider": "fake",
    }
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps(cfg, sort_keys=True), encoding="utf-8")
    loaded = load_experiment_config(cfg_path)
    ScriptedExperimentRunner(config=loaded, base_dir=tmp_path, max_years=1).run()

    exp_root = tmp_path / "experiments" / loaded.experiment_id
    report = ArtifactValidator(schema_version="1.0").validate_experiment_dir(exp_root)
    assert report.ok, [str(err) for err in report.errors]

    obs_dir = exp_root / "games" / "game_001" / "observatory"
    timeline_files = list(obs_dir.glob("*.md"))
    assert timeline_files
    assert any("LLM Meta" in file.read_text(encoding="utf-8") for file in timeline_files)
    assert any("communication_plan" in file.read_text(encoding="utf-8") for file in timeline_files)
    assert any("order_rationale" in file.read_text(encoding="utf-8") for file in timeline_files)
    assert any("previous_commitments" in file.read_text(encoding="utf-8") for file in timeline_files)
    assert any("effective_orders" in file.read_text(encoding="utf-8") for file in timeline_files)
    assert any("invalid_order_reasons" in file.read_text(encoding="utf-8") for file in timeline_files)
    assert any("legal_adjustment_options" in file.read_text(encoding="utf-8") for file in timeline_files)
    reflection_doc = obs_dir / "england_reflections.md"
    assert reflection_doc.exists()
    text = reflection_doc.read_text(encoding="utf-8")
    assert "Objective Observations" in text
    assert "Belief Updates" in text
    assert "Strategic Intent" in text


def test_capped_game_one_llm_plus_six_scripted_completes(tmp_path) -> None:
    agents = [{"agent_id": "agent_llm", "runtime_kind": "llm"}]
    for idx in range(1, 7):
        agents.append(
            {
                "agent_id": f"agent_scripted_{idx}",
                "runtime_kind": "scripted",
                "scripted_kind": "hold" if idx % 2 == 0 else "random_legal",
            }
        )
    cfg = {
        "schema_version": "1.0",
        "experiment_id": "exp_mixed_llm_7",
        "games": 1,
        "agents": agents,
        "rule_engine": {"name": "diplomacy"},
        "negotiation": {"max_ticks": 1},
        "seed": 23,
        "llm_provider": "fake",
    }
    cfg_path = tmp_path / "cfg7.json"
    cfg_path.write_text(json.dumps(cfg, sort_keys=True), encoding="utf-8")
    loaded = load_experiment_config(cfg_path)
    summary = ScriptedExperimentRunner(config=loaded, base_dir=tmp_path, max_years=1).run()
    assert summary.games_completed == 1
    exp_root = tmp_path / "experiments" / loaded.experiment_id
    assert (exp_root / "games" / "game_001" / "observatory" / "index.html").exists()


def test_live_negotiation_prompt_contains_compact_limits(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(),
    )
    runtime.register_agent(agent_id="agent_llm")
    runtime.negotiate_tick(_ctx(tmp_path))
    output_path = tmp_path / "runtime" / "agent_llm" / "runtime" / "phase_outputs" / "1901_spring_movement_negotiate_tick.json"
    prompt = json.loads(output_path.read_text(encoding="utf-8"))["llm_meta"]["prompt"]
    assert "private_messages: maximum 4" in prompt
    assert "global_messages: maximum 1" in prompt
    assert "relationship_updates: maximum 6" in prompt
    assert "withheld_information: maximum 2" in prompt
    assert "maximum 90 words" in prompt


def test_live_negotiation_prompt_requires_json_only_and_targeted_messages(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(),
    )
    runtime.register_agent(agent_id="agent_llm")
    runtime.negotiate_tick(_ctx(tmp_path))
    output_path = tmp_path / "runtime" / "agent_llm" / "runtime" / "phase_outputs" / "1901_spring_movement_negotiate_tick.json"
    prompt = json.loads(output_path.read_text(encoding="utf-8"))["llm_meta"]["prompt"]
    assert "Return only valid JSON." in prompt
    assert "Do not include markdown" in prompt
    assert "You do not need to contact every agent." in prompt
    assert "Every message must contain a concrete request, offer, test, warning, boundary" in prompt


def test_live_negotiation_schema_accepts_compact_valid_output(tmp_path: Path) -> None:
    payload = _compact_negotiate_payload(
        private_messages=[
            {
                "recipient": "england",
                "body": "Will you keep the Channel clear if I leave Picardy? Confirm before orders.",
                "purpose": "claim_test",
            }
        ],
        relationship_updates=[
            {"agent_id": "england", "status": "unknown", "next_use": "Test a Channel commitment."}
        ],
    )
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(responses_by_method={"negotiate_tick": json.dumps(payload)}),
    )
    runtime.register_agent(agent_id="agent_llm")
    result = runtime.negotiate_tick(_ctx(tmp_path))
    assert result.messages[0]["to"]["to_agent_id"] == "agent_eng"
    output_path = tmp_path / "runtime" / "agent_llm" / "runtime" / "phase_outputs" / "1901_spring_movement_negotiate_tick.json"
    assert json.loads(output_path.read_text(encoding="utf-8"))["llm_meta"]["fallback_applied"] is False


def test_live_negotiation_schema_rejects_missing_required_fields(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(responses_by_method={"negotiate_tick": '{"private_messages": []}'}),
    )
    runtime.register_agent(agent_id="agent_llm")
    result = runtime.negotiate_tick(_ctx(tmp_path))
    assert result.messages == []
    output_path = tmp_path / "runtime" / "agent_llm" / "runtime" / "phase_outputs" / "1901_spring_movement_negotiate_tick.json"
    meta = json.loads(output_path.read_text(encoding="utf-8"))["llm_meta"]
    assert meta["fallback_applied"] is True
    assert meta["validation_error_category"] == "missing_required_field"


def test_live_negotiation_schema_trims_excess_lists_and_records_it(tmp_path: Path) -> None:
    private_messages = [
        {"recipient": "england", "body": f"Request {index}: confirm Channel terms.", "purpose": "claim_test"}
        for index in range(5)
    ]
    relationship_updates = [
        {"agent_id": "england", "status": "unknown", "next_use": f"Test claim {index}."}
        for index in range(7)
    ]
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(
            responses_by_method={
                "negotiate_tick": json.dumps(
                    _compact_negotiate_payload(
                        private_messages=private_messages,
                        relationship_updates=relationship_updates,
                    )
                )
            }
        ),
    )
    runtime.register_agent(agent_id="agent_llm")
    result = runtime.negotiate_tick(_ctx(tmp_path))
    assert len(result.messages) == 4
    output_path = tmp_path / "runtime" / "agent_llm" / "runtime" / "phase_outputs" / "1901_spring_movement_negotiate_tick.json"
    meta = json.loads(output_path.read_text(encoding="utf-8"))["llm_meta"]
    assert meta["schema_limit_trimmed"] is True
    assert meta["trimmed_fields"] == {"private_messages": 1, "relationship_updates": 1}


def test_live_negotiation_truncated_output_is_classified_separately(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(responses_by_method={"negotiate_tick": '{"private_messages": ['}),
    )
    runtime.register_agent(agent_id="agent_llm")
    runtime.negotiate_tick(_ctx(tmp_path))
    output_path = tmp_path / "runtime" / "agent_llm" / "runtime" / "phase_outputs" / "1901_spring_movement_negotiate_tick.json"
    meta = json.loads(output_path.read_text(encoding="utf-8"))["llm_meta"]
    assert meta["fallback_applied"] is True
    assert meta["parse_error_category"] == "likely_truncated_output"
    assert meta["provider_error"] is None
    assert meta["negotiation_output_diagnostics"]["visible_text_length"] > 0


def test_live_negotiation_accepts_free_vocabulary_labels_without_schema_failure(tmp_path: Path) -> None:
    payload = _compact_negotiate_payload(
        private_messages=[
            {
                "recipient": "england",
                "body": "Keep the Channel clear while I leave Picardy; I will not enter it this spring.",
                "purpose": "  conditional_border_bargain  ",
            }
        ],
        global_messages=[
            {
                "body": "I propose a Baltic DMZ this spring while we clarify intentions.",
                "purpose": "limited_public_boundary",
            }
        ],
        relationship_updates=[
            {
                "agent_id": "england",
                "status": "  high_value_pending  ",
                "next_use": "Confirm the Channel bargain before finalizing orders.",
            }
        ],
    )
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(responses_by_method={"negotiate_tick": json.dumps(payload)}),
    )
    runtime.register_agent(agent_id="agent_llm")
    result = runtime.negotiate_tick(_ctx(tmp_path))
    assert len(result.messages) == 2
    output_path = tmp_path / "runtime" / "agent_llm" / "runtime" / "phase_outputs" / "1901_spring_movement_negotiate_tick.json"
    meta = json.loads(output_path.read_text(encoding="utf-8"))["llm_meta"]
    parsed = meta["parsed_payload"]
    assert meta["fallback_applied"] is False
    assert meta["validation_error_category"] is None
    assert parsed["private_messages"][0]["purpose"] == "conditional_border_bargain"
    assert parsed["global_messages"][0]["purpose"] == "limited_public_boundary"
    assert parsed["relationship_updates"][0]["status"] == "high_value_pending"


def test_live_negotiation_empty_or_non_string_labels_fail_safely(tmp_path: Path) -> None:
    payload = _compact_negotiate_payload(
        private_messages=[{"recipient": "england", "body": "Confirm Channel terms.", "purpose": ""}],
        relationship_updates=[{"agent_id": "england", "status": 7, "next_use": "Seek confirmation."}],
    )
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0",
        runtime_root=tmp_path / "runtime",
        provider=FakeLLMProvider(responses_by_method={"negotiate_tick": json.dumps(payload)}),
    )
    runtime.register_agent(agent_id="agent_llm")
    result = runtime.negotiate_tick(_ctx(tmp_path))
    assert result.messages == []
    output_path = tmp_path / "runtime" / "agent_llm" / "runtime" / "phase_outputs" / "1901_spring_movement_negotiate_tick.json"
    meta = json.loads(output_path.read_text(encoding="utf-8"))["llm_meta"]
    assert meta["fallback_applied"] is True
    assert meta["validation_error_category"] == "invalid_field_shape"

@pytest.mark.parametrize("text", [
    "A plain reflection with no JSON syntax.",
    "First line\r\nsecond line\nthird line",
    '{"malformed": JSON reflection text',
    "",
    pytest.param("é🙂" * 3000, id="unicode-long"),
])
def test_default_reflection_accepts_exact_text_and_bounds_only_next_context(tmp_path: Path, text: str) -> None:
    class CapturingProvider(FakeLLMProvider):
        def __init__(self):
            super().__init__(responses_by_method={"reflect": text})
            self.requests = []

        def generate(self, request):
            self.requests.append(request)
            return super().generate(request)

    provider = CapturingProvider()
    runtime = BaselineLLMAgentRuntime(schema_version="1.0", runtime_root=tmp_path / "runtime", provider=provider)
    runtime.register_agent(agent_id="agent_llm")

    result = runtime.reflect(_ctx(tmp_path))

    assert result.ok
    assert result.reflection["reflection_text"] == text
    archive = tmp_path / "runtime/agent_llm/folio/game_memory/g1/reflections/1901_spring_movement.txt"
    assert archive.read_bytes().decode("utf-8") == text
    raw_output = tmp_path / "runtime/agent_llm/runtime/errors/1901_spring_movement_reflect_attempt_0_raw.txt"
    assert raw_output.read_bytes().decode("utf-8") == text
    memory = json.loads((tmp_path / "runtime/agent_llm/folio/game_memory/g1.json").read_text(encoding="utf-8"))
    assert memory["free_text_reflection"]["raw_utf8_bytes"] == len(text.encode("utf-8"))
    next_context = AgentContext(**{**_ctx(tmp_path).__dict__, "phase_id": "1901_spring_retreat"})
    built = runtime.context_builder.build(next_context)
    excerpt = built["free_text_reflection"].split("\n", 1)[1]
    assert len(excerpt.encode("utf-8")) <= 4096
    assert ("excerpt_truncated_bytes:" in " ".join(built["context_warnings"])) == (len(text.encode("utf-8")) > 4096)
    assert result.explanation["protocol"] == "free_text_reflection_v1"
    assert "Required JSON Schema" not in provider.requests[0].prompt
    assert "outcome_review is required" not in provider.requests[0].prompt
    assert "relationship_updates: maximum" not in provider.requests[0].prompt






def test_free_text_reflection_keeps_provider_failure_out_of_successful_text_path(tmp_path: Path) -> None:
    class BrokenProvider:
        metadata = {"provider_name": "offline-broken"}

        def generate(self, _request):
            raise RuntimeError("transport failed")

    runtime = BaselineLLMAgentRuntime(schema_version="1.0", runtime_root=tmp_path / "runtime", provider=BrokenProvider())
    runtime.register_agent(agent_id="agent_llm")
    result = runtime.reflect(_ctx(tmp_path))

    assert result.explanation.get("fallback_applied") is True
    assert result.reflection.get("reflection_text") is None
    assert list((tmp_path / "runtime/agent_llm/folio/game_memory/g1/reflections").glob("*.txt")) == []

def test_free_text_protocol_leaves_orders_and_negotiation_schema_prompts_structured(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(schema_version="1.0", runtime_root=tmp_path / "runtime", provider=FakeLLMProvider())
    built = runtime.context_builder.build(_ctx(tmp_path))
    order_prompt = runtime.context_builder.build_prompt(method="submit_orders", built_context=built, free_text_reflection=True)
    negotiation_prompt = runtime.context_builder.build_prompt(method="negotiate_tick", built_context=built, free_text_reflection=True)

    assert "Required JSON Schema" in order_prompt
    assert "Return exactly one JSON object" in order_prompt
    assert "Required JSON Schema" in negotiation_prompt
    assert "private_messages" in negotiation_prompt
    assert "recipient" in negotiation_prompt


def test_prose_memory_same_phase_replay_is_idempotent_and_next_phase_private(tmp_path: Path) -> None:
    runtime = BaselineLLMAgentRuntime(schema_version="1.0", runtime_root=tmp_path / "runtime", provider=FakeLLMProvider())
    runtime.register_agent(agent_id="agent_llm")
    context = _ctx(tmp_path)
    runtime._save_free_text_reflection(context, "private insight")
    memory_file = tmp_path / "runtime/agent_llm/folio/game_memory/g1.json"
    before = memory_file.read_bytes()
    runtime._save_free_text_reflection(context, "private insight")
    assert memory_file.read_bytes() == before
    with pytest.raises(RuntimeError, match="different archived text"):
        runtime._save_free_text_reflection(context, "different text")

    same_phase = runtime.context_builder.build(context)
    assert same_phase["free_text_reflection"] == ""
    assert "free_text_reflection" not in same_phase["in_game_memory"]
    next_context = AgentContext(**{**context.__dict__, "phase_id": "1901_fall_movement"})
    built = runtime.context_builder.build(next_context)
    assert "private insight" in built["free_text_reflection"]
    from dataclasses import replace
    other_context = replace(next_context, agent_id="other_agent")
    assert "private insight" not in json.dumps(runtime.context_builder.build(other_context))


@pytest.mark.parametrize("saved_phase,requested_phase,visible", [
    ("1901_fall_movement", "1901_spring_movement", False),
    ("1901_spring_movement", "1901_spring_movement", False),
    ("1901_spring_movement", "1901_spring_negotiation", False),
    ("1901_spring_negotiation", "1901_spring_movement", True),
    ("1901_spring_movement", "1901_spring_retreat", True),
    ("1901_spring_retreat", "1901_fall_negotiation", True),
    ("1901_fall_negotiation", "1901_fall_movement", True),
    ("1901_fall_movement", "1901_fall_retreat", True),
    ("1901_fall_retreat", "1901_winter_negotiation", True),
    ("1901_winter_negotiation", "1901_winter_adjustment", True),
    ("1901_winter_adjustment", "1902_spring_negotiation", True),
    ("1901_fall_retreat", "game_end_cleanup", True),
    ("1901_spring_movement", "unknown_phase", False),
])
def test_prose_memory_is_available_only_after_a_prior_known_phase(
    tmp_path: Path, saved_phase: str, requested_phase: str, visible: bool,
) -> None:
    runtime = BaselineLLMAgentRuntime(schema_version="1.0", runtime_root=tmp_path / "runtime", provider=FakeLLMProvider())
    runtime.register_agent(agent_id="agent_llm")
    saved_context = AgentContext(**{**_ctx(tmp_path).__dict__, "phase_id": saved_phase})
    runtime._save_free_text_reflection(saved_context, "phase-specific private text")
    requested_context = AgentContext(**{**saved_context.__dict__, "phase_id": requested_phase})

    built = runtime.context_builder.build(requested_context)
    assert ("phase-specific private text" in built["free_text_reflection"]) is visible
    assert "free_text_reflection" not in built["in_game_memory"]
    if requested_phase == "unknown_phase":
        assert "free_text_reflection_phase_order_unknown" in built["context_warnings"]
