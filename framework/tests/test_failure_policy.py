import json
from pathlib import Path

import pytest

from llm_diplomacy.agents.baseline_llm_runtime import BaselineLLMAgentRuntime
from llm_diplomacy.agents.llm_provider import BudgetedLLMProvider, FakeLLMProvider, LLMResponse, ProviderHTTPError, ProviderRoute
from llm_diplomacy.guardrails.budget import BudgetConfig, BudgetController
from llm_diplomacy.metrics.provider_usage import ProviderUsageTracker
from llm_diplomacy.runner.recovery import ExperimentRecovery
from test_baseline_llm_runtime import _ctx


@pytest.mark.parametrize("method", ["negotiate_tick", "submit_orders", "reflect", "game_end_cleanup"])
@pytest.mark.parametrize("error,category", [
    (TimeoutError("timeout"), "provider_timeout"),
    (ProviderHTTPError(status_code=401, error_body="denied", metadata={}), "provider_auth"),
    (ProviderHTTPError(status_code=429, error_body='{"error":{"type":"rate_limit"}}', metadata={}), "provider_rate_limit"),
    (ProviderHTTPError(status_code=429, error_body='{"error":{"code":"credit_balance_exhausted"}}', metadata={}), "provider_quota"),
    (ProviderHTTPError(status_code=500, error_body="unavailable", metadata={}), "provider_transient_http"),
    (RuntimeError("unknown"), "provider_error"),
])
def test_provider_errors_never_build_repair_requests(tmp_path, method, error, category):
    class Failing:
        calls = 0

        def generate(self, request):
            self.calls += 1
            raise error

    inner = Failing()
    tracker = ProviderUsageTracker(tmp_path / "metrics")
    provider = BudgetedLLMProvider(inner=inner, budget=None, usage_tracker=tracker)
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path, provider=provider)
    runtime.register_agent(agent_id="agent_llm")
    payload, meta = runtime._invoke(method=method, context=_ctx(tmp_path))
    assert payload is None
    assert inner.calls == (3 if category in {"provider_timeout", "provider_rate_limit", "provider_transient_http"} else 1)
    assert meta["retry_attempted"] == (inner.calls > 1)
    assert not meta["parse_error"]
    assert meta["failure_category"] == category
    assert tracker.summary()["totals"]["estimated_cost_usd"] is None


@pytest.mark.parametrize("method", ["negotiate_tick", "submit_orders", "reflect", "game_end_cleanup"])
def test_budget_denial_is_one_blocked_attempt_and_no_request(tmp_path, method):
    budget = BudgetController(schema_version="1.0", experiment_id="exp", artifact_root=tmp_path / "budget",
                              config=BudgetConfig(max_provider_calls=0))
    tracker = ProviderUsageTracker(tmp_path / "metrics")
    provider = BudgetedLLMProvider(inner=FakeLLMProvider(), budget=budget, usage_tracker=tracker)
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path, provider=provider)
    runtime.register_agent(agent_id="agent_llm")
    result = getattr(runtime, method)(_ctx(tmp_path))
    assert result.ok
    assert tracker.summary()["totals"]["provider_calls"] == 0
    assert tracker.summary()["totals"]["budget_blocked_attempts"] == 1
    output = next((tmp_path / "agent_llm/runtime/phase_outputs").glob("*.json"))
    meta = json.loads(output.read_text())["llm_meta"]
    assert meta["fallback_applied"]
    assert meta["failure_category"] == "budget_blocked"


@pytest.mark.parametrize("content,count", [('{"private_messages":[],"global_messages":[],"relationship_updates":[],"withheld_information":[]}', 1), ("bad json", 2), ('{"private_messages":42}', 2)])
def test_only_invalid_model_output_gets_one_repair(tmp_path, content, count):
    class Spy(FakeLLMProvider):
        calls = []

        def generate(self, request):
            self.calls.append(request)
            return super().generate(request)

    provider = Spy(responses_by_method={"negotiate_tick": content})
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path, provider=provider)
    runtime.register_agent(agent_id="agent_llm")
    _, meta = runtime._invoke(method="negotiate_tick", context=_ctx(tmp_path))
    assert len(provider.calls) == count
    if count == 2:
        assert provider.calls[0].prompt != provider.calls[1].prompt
        assert meta["retry_attempts"][0]["failure_category"] == "invalid_output"


def test_timeout_retry_reuses_decision_prompt_and_succeeds(tmp_path):
    class TimeoutThenSuccess(FakeLLMProvider):
        def __init__(self):
            super().__init__()
            self.requests = []

        def generate(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                raise TimeoutError("socket read timeout")
            return super().generate(request)

    inner = TimeoutThenSuccess()
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path, provider=inner)
    runtime.register_agent(agent_id="agent_llm")
    payload, meta = runtime._invoke(method="negotiate_tick", context=_ctx(tmp_path))
    assert payload is not None
    assert len(inner.requests) == 2
    assert inner.requests[0].prompt == inner.requests[1].prompt
    assert meta["retry_attempts"][1]["prompt_type"] == "transport_retry"
    assert meta["retry_attempts"][0]["attempt_id"]
    assert meta["retry_attempts"][0]["elapsed_seconds"] >= 0


def test_explicit_alternate_provider_recovers_permanent_primary_error(tmp_path):
    class Unauthorized:
        def generate(self, request):
            raise ProviderHTTPError(status_code=401, error_body="denied", metadata={"provider_name": "primary"})

    primary = BudgetedLLMProvider(inner=Unauthorized(), budget=None, provider_metadata={"provider_name": "primary"})
    alternate = BudgetedLLMProvider(inner=FakeLLMProvider(model="alternate-model"), budget=None,
                                    provider_metadata={"provider_name": "alternate"})
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path,
        provider=ProviderRoute([primary, alternate], [{"provider_name": "primary"}, {"provider_name": "alternate"}]))
    runtime.register_agent(agent_id="agent_llm")
    payload, meta = runtime._invoke(method="negotiate_tick", context=_ctx(tmp_path))
    assert payload is not None
    assert [row["route_index"] for row in meta["retry_attempts"]] == [0, 1]
    assert meta["provider_model"] == "alternate-model"
    assert meta["retry_attempts"][0]["failure_category"] == "provider_auth"
    assert meta["retry_attempts"][1]["provider_route_metadata"]["provider_name"] == "alternate"


def test_http_error_server_request_id_is_written_to_attempt_record(tmp_path):
    class Failing:
        def generate(self, request):
            raise ProviderHTTPError(status_code=429, error_body='{"error":{"type":"rate_limit"}}',
                metadata={"client_request_id": request.context["client_request_id"],
                          "provider_request_id": "server-review-id"})
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path,
        provider=BudgetedLLMProvider(inner=Failing(), budget=None))
    runtime.register_agent(agent_id="agent_llm")
    _payload, meta = runtime._invoke(method="negotiate_tick", context=_ctx(tmp_path))
    assert meta["retry_attempts"][0]["provider_request_id"] == "server-review-id"
    assert meta["retry_attempts"][0]["attempt_id"] == meta["retry_attempts"][0]["provider_error_details"]["client_request_id"]


def test_invalid_repair_plus_transport_retry_stays_within_three_requests(tmp_path):
    class InvalidThenTimeoutThenValid(FakeLLMProvider):
        def __init__(self):
            super().__init__(responses_by_method={"negotiate_tick": "bad json"})
            self.requests = []

        def generate(self, request):
            self.requests.append(request)
            if len(self.requests) == 2:
                raise TimeoutError("timeout")
            if len(self.requests) == 3:
                return FakeLLMProvider().generate(request)
            return super().generate(request)

    inner = InvalidThenTimeoutThenValid()
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path, provider=inner)
    runtime.register_agent(agent_id="agent_llm")
    payload, meta = runtime._invoke(method="negotiate_tick", context=_ctx(tmp_path))
    assert payload is not None
    assert len(inner.requests) == 3
    assert inner.requests[0].prompt != inner.requests[1].prompt
    assert inner.requests[1].prompt == inner.requests[2].prompt


def test_strict_policy_stops_before_fallback_or_repair(tmp_path):
    provider = FakeLLMProvider(responses_by_method={"submit_orders": "bad json"})
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path, provider=provider,
                                     halt_on_issue=True)
    runtime.register_agent(agent_id="agent_llm")
    with pytest.raises(RuntimeError, match="Recovery required before fallback"):
        runtime.submit_orders(_ctx(tmp_path))
    assert not list((tmp_path / "agent_llm/runtime/phase_outputs").glob("*.json"))


def test_strict_negotiation_halts_on_global_message_trimming(tmp_path):
    from llm_diplomacy.agents.llm_provider import LLMResponse

    class Spy:
        calls = 0
        prompts = []

        def generate(self, request):
            self.calls += 1
            self.prompts.append(request.prompt)
            return LLMResponse("1.0", json.dumps({
                "private_messages": [],
                "global_messages": [{"body": "first", "purpose": "offer"},
                                    {"body": "second", "purpose": "offer"}],
                "relationship_updates": [], "withheld_information": [],
            }), "offline", {})

    inner = Spy()
    recovery = ExperimentRecovery(tmp_path / "experiment", identity={"id": "loss"}, resume=False)
    recovery.prepare()
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime",
                                     provider=inner, halt_on_issue=True, recovery=recovery)
    runtime.register_agent(agent_id="agent_llm")
    with pytest.raises(RuntimeError, match="invalid_output"):
        runtime.negotiate_tick(_ctx(tmp_path))
    assert inner.calls == 2
    rejected = [row for row in recovery._journal_rows()
                if row.get("kind") == "response_validation" and row.get("valid") is False]
    assert len(rejected) == 2 and "trimmed" in rejected[0]["issue"]
    assert "trimmed" in rejected[1]["issue"]
    assert "NEGOTIATION CORRECTION REQUIRED" in inner.prompts[1]
    assert inner.prompts[0] in inner.prompts[1]
    assert not list((tmp_path / "runtime/agent_llm/runtime/phase_outputs").glob("*.json"))
    assert not any(row.get("kind") == "message_batch"
                   for row in recovery._journal_rows())


def test_strict_budget_block_replay_stays_blocked_and_unbillable(tmp_path):
    recovery = ExperimentRecovery(tmp_path / "experiment", identity={"id": "budget"}, resume=False)
    recovery.prepare()
    budget = BudgetController(schema_version="1.0", experiment_id="exp", artifact_root=tmp_path / "budget",
                              config=BudgetConfig(max_provider_calls=0))
    class NoCalls(FakeLLMProvider):
        calls = 0

        def generate(self, request):
            self.calls += 1
            return super().generate(request)

    inner = NoCalls()
    provider = BudgetedLLMProvider(inner=inner, budget=budget)
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime",
                                     provider=provider, halt_on_issue=True, recovery=recovery)
    runtime.register_agent(agent_id="agent_llm")
    for _ in range(2):
        with pytest.raises(RuntimeError, match="budget_blocked"):
            runtime._invoke(method="negotiate_tick", context=_ctx(tmp_path))
    assert inner.calls == 0
    assert budget.total_provider_calls == 0
    assert recovery.attempt_count_total() == 0
    assert recovery.in_flight() == []
    cached = recovery.cached_response(
        next(row["decision_id"] for row in recovery._journal_rows() if row.get("kind") == "attempt"),
        next(row["prompt_hash"] for row in recovery._journal_rows() if row.get("kind") == "attempt"),
    )
    assert cached["outcome"] == "blocked"


def test_each_country_has_a_separate_submit_decision_attempt_limit(tmp_path):
    from dataclasses import replace

    class Spy(FakeLLMProvider):
        calls = 0

        def generate(self, request):
            self.calls += 1
            return super().generate(request)

    recovery = ExperimentRecovery(tmp_path / "experiment", identity={"id": "countries"}, resume=False)
    recovery.prepare()
    provider = Spy()
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime",
                                     provider=provider, halt_on_issue=True, recovery=recovery)
    runtime.register_agent(agent_id="agent_llm")
    context = _ctx(tmp_path)
    for country in ("FR", "GER", "ITA", "AUS"):
        result, _meta = runtime._invoke(method="submit_orders",
                                        context=replace(context, country_assignment=country))
        assert result is not None
    assert provider.calls == 4
    assert len({row["decision_id"] for row in recovery._journal_rows()
                if row.get("kind") == "dispatch"}) == 4


def test_received_response_is_journaled_before_parse_and_reused(tmp_path):
    recovery = ExperimentRecovery(tmp_path / "experiment", identity={"id": "a"}, resume=False)
    recovery.prepare()
    class CountingFake(FakeLLMProvider):
        calls = 0

        def generate(self, request):
            self.calls += 1
            return super().generate(request)

    provider = CountingFake()
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime",
                                     provider=provider, recovery=recovery)
    runtime.register_agent(agent_id="agent_llm")
    context = _ctx(tmp_path)
    payload, _ = runtime._invoke(method="negotiate_tick", context=context)
    assert payload is not None
    lines_before = recovery.journal_path.read_text(encoding="utf-8").splitlines()
    calls_before = provider.calls
    payload_again, meta = runtime._invoke(method="negotiate_tick", context=context)
    assert payload_again == payload
    assert provider.calls == calls_before
    assert len(recovery.journal_path.read_text(encoding="utf-8").splitlines()) == len(lines_before)


def test_unresolved_dispatch_is_not_silently_reissued(tmp_path):
    recovery = ExperimentRecovery(tmp_path / "experiment", identity={"id": "a"}, resume=False)
    recovery.prepare()
    class Spy(FakeLLMProvider):
        calls = 0

        def generate(self, request):
            self.calls += 1
            return super().generate(request)
    provider = Spy()
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime",
                                     provider=provider, recovery=recovery)
    runtime.register_agent(agent_id="agent_llm")
    context = _ctx(tmp_path)
    context = type(context)(schema_version=context.schema_version, experiment_id=context.experiment_id,
                            game_id=context.game_id, phase_id=context.phase_id, agent_id=context.agent_id,
                            country_assignment=context.country_assignment,
                            visible_state={**context.visible_state, "_recovery_occurrence": "once"})
    # The matching prompt hash is recorded by seeding the exact hash from the runtime builder.
    from dataclasses import replace
    prompt_context = replace(context, visible_state={key: value for key, value in context.visible_state.items()
                                                       if key != "_recovery_occurrence"})
    built = runtime.context_builder.build(prompt_context)
    prompt = runtime.context_builder.build_prompt(method="submit_orders", built_context=built)
    prompt_hash = __import__("hashlib").sha256(prompt.encode("utf-8")).hexdigest()
    decision_id = "/".join((context.game_id, context.phase_id, "once",
                            str(context.country_assignment or ""), context.agent_id, "submit_orders"))
    recovery.record_prepared(decision_id=decision_id,
                             prompt_hash=prompt_hash, attempt_id="pending", provider={"provider_name": "mock"})
    recovery.record_dispatch(decision_id=decision_id,
                             prompt_hash=prompt_hash, attempt_id="pending", provider={"provider_name": "mock"})
    recovery.record_response(decision_id=decision_id, prompt_hash=prompt_hash, attempt_id="pending",
                             provider={"provider_name": "mock"}, response=None, usage=None,
                             error_category="provider_timeout", outcome="unknown")
    assert recovery.has_unresolved_dispatch(decision_id, prompt_hash)
    with pytest.raises(RuntimeError, match="unresolved provider dispatch"):
        runtime._invoke(method="submit_orders", context=context)
    assert provider.calls == 0
    recovery.resolve_unknown(decision_id=decision_id,
                            attempt_id="pending", prompt_hash=prompt_hash, choice="retry_after_review")
    runtime._invoke(method="submit_orders", context=context)
    assert provider.calls == 1


@pytest.mark.parametrize("field,marker", [
    ("campaign_plan", "campaign-loss-marker"),
    ("commitment_memory", "commitment-loss-marker"),
    ("belief_summary", "belief-loss-marker"),
    ("action_lessons", "lesson-loss-marker"),
    ("memory_candidates", "memory-loss-marker"),
    ("action_lessons", [{"lesson": "LOST_FACT"}]),
    ("memory_candidates", [{"memory": "LOST_FACT"}]),
    ("relationship_updates", ["LOST_FACT"]),
    ("action_lessons", {"lesson": "LOST_FACT"}),
    ("memory_candidates", {"memory": "LOST_FACT"}),
    ("relationship_updates", {"agent_id": "LOST_FACT"}),
    ("action_lessons", ["Keep the valid lesson.", {"lesson": "LOST_FACT"}]),
    ("memory_candidates", ["Keep the valid memory.", {"memory": "LOST_FACT"}]),
    ("relationship_updates", [{"agent_id": "other", "status": "unknown"}, "LOST_FACT"]),
])
def test_strict_reflection_rejects_semantic_truncation_before_effects(tmp_path, field, marker):
    class Spy(FakeLLMProvider):
        calls = 0

        def generate(self, request):
            self.calls += 1
            base = {
                "schema_version": "reflection_compact_v3_campaign_memory",
                "phase_summary": "Board position is stable.",
                "strategic_position": "Wait for confirmed movement.",
                "objective_update": {"current_objective": "Protect the center.", "why_it_matters": "Avoid unsupported exposure."},
                "outcome_review": "No notable outcome.",
                "campaign_plan": "Maintain a conditional route.",
                "commitment_memory": "No outstanding commitments.",
                "winter_assessment": None,
                "belief_summary": "No new belief.",
                "belief_updates": [],
                "relationship_updates": [],
                "action_lessons": [],
                "memory_candidates": [],
            }
            if isinstance(marker, (list, dict)):
                base[field] = marker
            elif field in {"campaign_plan", "commitment_memory", "belief_summary"}:
                base[field] = " ".join(["word"] * 700) + " " + marker
            else:
                base[field] = [f"lesson {index}" for index in range(3)] + [marker]
            return LLMResponse("1.0", json.dumps(base), "offline", {})

    recovery = ExperimentRecovery(tmp_path / "experiment", identity={"id": field}, resume=False)
    recovery.prepare()
    provider = Spy()
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime",
                                     provider=provider, halt_on_issue=True, recovery=recovery)
    runtime.register_agent(agent_id="agent_llm")
    context = _ctx(tmp_path)
    memory_path = runtime._game_memory_json_path(context)
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text(json.dumps(runtime._load_game_memory_structured(context), sort_keys=True, indent=2), encoding="utf-8")
    before_memory = memory_path.read_bytes()
    with pytest.raises(RuntimeError, match="Recovery required before fallback"):
        runtime.reflect(context)
    assert provider.calls == 2
    assert memory_path.read_bytes() == before_memory
    rejected = [row for row in recovery._journal_rows()
                if row.get("kind") == "response_validation" and row.get("valid") is False]
    assert rejected
    assert field.split("_")[0] in rejected[-1]["issue"] or "reflection" in rejected[-1]["issue"]
    assert recovery.status()["halt_required"] is True
    if isinstance(marker, (list, dict)):
        received = [row for row in recovery._journal_rows()
                    if row.get("kind") == "attempt" and row.get("outcome") == "received"]
        assert len(received) == 2
        assert "LOST_FACT" in received[0]["response"]["content"]
        assert "LOST_FACT" in received[1]["response"]["content"]
    with pytest.raises(RuntimeError, match="Recovery required before fallback"):
        runtime.reflect(context)
    assert provider.calls == 2
    assert memory_path.read_bytes() == before_memory


def test_successful_receipt_head_failure_reopens_and_replays_without_dispatch(tmp_path, monkeypatch):
    import llm_diplomacy.runner.recovery as recovery_module

    root = tmp_path / "experiment"
    recovery = ExperimentRecovery(root, identity={"id": "head-gap"}, resume=False)
    recovery.prepare()

    class CountingFake(FakeLLMProvider):
        calls = 0

        def generate(self, request):
            self.calls += 1
            return super().generate(request)

    provider = CountingFake()
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime",
                                     provider=provider, recovery=recovery)
    runtime.register_agent(agent_id="agent_llm")
    original_atomic_json = recovery_module.atomic_json
    failed = False

    def fail_response_head(path, value):
        nonlocal failed
        if Path(path) == recovery.journal_head_path and not failed:
            rows = recovery.journal_path.read_text(encoding="utf-8").splitlines()
            if rows and json.loads(rows[-1]).get("kind") == "attempt" and json.loads(rows[-1]).get("outcome") == "received":
                failed = True
                raise OSError("injected head publication failure")
        return original_atomic_json(path, value)

    monkeypatch.setattr(recovery_module, "atomic_json", fail_response_head)
    with pytest.raises(OSError, match="head publication"):
        runtime._invoke(method="negotiate_tick", context=_ctx(tmp_path))
    assert provider.calls == 1
    rows = recovery._journal_rows()
    decision = next(row["decision_id"] for row in rows if row.get("kind") == "dispatch")
    assert len([row for row in rows if row.get("kind") == "dispatch" and row.get("decision_id") == decision]) == 1
    monkeypatch.setattr(recovery_module, "atomic_json", original_atomic_json)
    resumed = ExperimentRecovery(root, identity={"id": "head-gap"}, resume=True)
    resumed.prepare()
    replay = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime",
                                    provider=provider, recovery=resumed)
    replay.register_agent(agent_id="agent_llm")
    payload, _ = replay._invoke(method="negotiate_tick", context=_ctx(tmp_path))
    assert payload is not None
    assert provider.calls == 1
    assert len([row for row in resumed._journal_rows() if row.get("kind") == "dispatch" and row.get("decision_id") == decision]) == 1
