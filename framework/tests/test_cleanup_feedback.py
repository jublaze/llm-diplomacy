import json

import pytest

from llm_diplomacy.runner.recovery import ExperimentRecovery


def test_cleanup_feedback_names_exact_extra_data_and_preserves_original():
    raw = '{"persistent_memory_update":"Lessons"},"public_profile_update":null}'
    feedback = ExperimentRecovery.cleanup_json_feedback(raw)
    with pytest.raises(json.JSONDecodeError) as issue:
        json.loads(raw)
    assert str(issue.value) in feedback
    assert "ONE complete JSON object" in feedback
    assert "Keep that field inside the same object" in feedback
    assert json.dumps(raw, ensure_ascii=False) in feedback


def test_valid_cleanup_cannot_become_an_extra_feedback_pass():
    with pytest.raises(ValueError, match="requires a rejected"):
        ExperimentRecovery.cleanup_json_feedback('{"persistent_memory_update":"Lessons","public_profile_update":null}')


@pytest.mark.parametrize("raw,issue", [('{}', 'persistent_memory_update'),
                                      ('{"persistent_memory_update":"x","public_profile_update":7}', 'public_profile_update')])
def test_schema_feedback_names_invalid_field(raw, issue):
    assert issue in ExperimentRecovery.cleanup_json_feedback(raw)


def test_cleanup_runtime_sends_reviewed_feedback_to_provider(tmp_path, monkeypatch):
    from llm_diplomacy.agents.baseline_llm_runtime import BaselineLLMAgentRuntime
    from llm_diplomacy.agents.llm_provider import FakeLLMProvider
    from test_baseline_llm_runtime import _ctx

    recovery = ExperimentRecovery(tmp_path / "experiment", identity={"id": "feedback"}, resume=False)
    recovery.prepare()
    feedback = "\nSpecific rejected-output JSON error"
    monkeypatch.setattr(recovery, "cleanup_retry_prompt", lambda decision, prompt: prompt + feedback)

    class Spy(FakeLLMProvider):
        prompt = None

        def generate(self, request):
            self.prompt = request.prompt
            return super().generate(request)

    provider = Spy()
    runtime = BaselineLLMAgentRuntime(schema_version="1.0", runtime_root=tmp_path / "runtime",
                                     provider=provider, recovery=recovery, halt_on_issue=True)
    runtime.register_agent(agent_id="agent_llm")
    assert runtime._invoke(method="game_end_cleanup", context=_ctx(tmp_path))[0] is not None
    assert provider.prompt.endswith(feedback)
    assert "Return exactly one JSON object" in provider.prompt


def test_russia_cleanup_extra_brace_feedback():
    raw = '{"explanation":{},"persistent_memory_update":"Lessons"},"public_profile_update":null}'
    feedback = ExperimentRecovery.cleanup_json_feedback(raw)
    assert str(_json_error(raw)) in feedback
    assert 'Keep that field inside the same object' in feedback
    assert json.dumps(raw, ensure_ascii=False) in feedback


def _json_error(raw):
    try:
        json.loads(raw)
    except json.JSONDecodeError as error:
        return error
    raise AssertionError("expected invalid JSON")


@pytest.mark.parametrize("pass_number", [1, 2, 3, 4, 5])
def test_russia_cleanup_requires_its_own_authorization(tmp_path, pass_number):
    recovery = ExperimentRecovery(tmp_path, identity={"id": "russia"}, resume=False)
    recovery.prepare()
    with pytest.raises(ValueError, match="explicit two-attempt authorization"):
        recovery.prepare_russia_cleanup_feedback(pass_number)


@pytest.mark.parametrize("pass_number", [1, 2, 3, 4])
def test_england_cleanup_requires_its_own_authorization(tmp_path, pass_number):
    recovery = ExperimentRecovery(tmp_path, identity={"id": "england"}, resume=False)
    recovery.prepare()
    with pytest.raises(ValueError, match="explicit two-attempt authorization"):
        recovery.prepare_england_cleanup_feedback(pass_number)


@pytest.mark.parametrize("outcome,valid", [("unknown", False), ("received", True)])
def test_england_conditional_retry_rejects_unknown_or_success(tmp_path, monkeypatch, outcome, valid):
    recovery = ExperimentRecovery(tmp_path, identity={"id": "england"}, resume=False)
    decision = "game_001/game_end_cleanup/once/england/dolos/game_end_cleanup"
    monkeypatch.setattr(recovery, "_england_cleanup_approval", lambda: {"decision_id": decision})
    monkeypatch.setattr(recovery, "_validate_england_cleanup", lambda *args: None)
    monkeypatch.setattr(recovery, "_read_checkpoint", lambda: {"halt_required": True, "decision_id": decision})
    monkeypatch.setattr(recovery, "in_flight", lambda: [])
    monkeypatch.setattr(recovery, "attempt_count", lambda d: 2)
    monkeypatch.setattr(recovery, "_journal_rows", lambda: [
        {"kind": "england_cleanup_feedback_intent", "pass_number": 2},
        {"kind": "attempt", "decision_id": decision, "attempt_id": "second", "outcome": outcome},
        {"kind": "response_validation", "attempt_id": "second", "valid": valid}])
    with pytest.raises(ValueError, match="rejected received answer"):
        recovery.prepare_england_cleanup_feedback(3)

@pytest.mark.parametrize("pass_number", [1, 2, 3, 4, 5])
def test_italy_cleanup_requires_its_own_authorization(tmp_path, pass_number):
    recovery = ExperimentRecovery(tmp_path, identity={"id": "italy"}, resume=False)
    recovery.prepare()
    with pytest.raises(ValueError, match="explicit three-attempt authorization"):
        recovery.prepare_italy_cleanup_feedback(pass_number)


@pytest.mark.parametrize("outcome,valid", [("unknown", False), ("received", True)])
def test_italy_conditional_retry_rejects_unknown_or_success(tmp_path, monkeypatch, outcome, valid):
    recovery = ExperimentRecovery(tmp_path, identity={"id": "italy"}, resume=False)
    decision = "game_001/game_end_cleanup/once/italy/major/game_end_cleanup"
    monkeypatch.setattr(recovery, "_italy_cleanup_approval", lambda: {"decision_id": decision})
    monkeypatch.setattr(recovery, "_validate_italy_cleanup", lambda *args: None)
    monkeypatch.setattr(recovery, "_read_checkpoint", lambda: {"halt_required": True, "decision_id": decision})
    monkeypatch.setattr(recovery, "in_flight", lambda: [])
    monkeypatch.setattr(recovery, "attempt_count", lambda d: 2)
    monkeypatch.setattr(recovery, "_journal_rows", lambda: [
        {"kind": "italy_cleanup_feedback_intent", "pass_number": 2},
        {"kind": "attempt", "decision_id": decision, "attempt_id": "second", "outcome": outcome},
        {"kind": "response_validation", "attempt_id": "second", "valid": valid}])
    with pytest.raises(ValueError, match="rejected received answer"):
        recovery.prepare_italy_cleanup_feedback(3)
