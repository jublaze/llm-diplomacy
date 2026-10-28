import pytest

from llm_diplomacy.agents.baseline_llm_runtime import BaselineLLMAgentRuntime
from llm_diplomacy.agents.llm_provider import FakeLLMProvider
from llm_diplomacy.runner.recovery import ExperimentRecovery
from test_baseline_llm_runtime import _ctx


@pytest.mark.parametrize("count,limit,allowed", [(2, 3, True), (3, 3, False), (3, 4, True), (4, 4, False)])
def test_runtime_enforces_limit_before_provider(tmp_path, monkeypatch, count, limit, allowed):
    recovery = ExperimentRecovery(tmp_path / "experiment", identity={"id": "cap"}, resume=False)
    recovery.prepare()
    monkeypatch.setattr(recovery, "attempt_count", lambda decision: count)
    monkeypatch.setattr(recovery, "attempt_limit", lambda decision, prompt: limit)

    class Spy(FakeLLMProvider):
        calls = 0

        def generate(self, request):
            self.calls += 1
            return super().generate(request)

    provider = Spy()
    runtime = BaselineLLMAgentRuntime(schema_version="1.0", runtime_root=tmp_path / "runtime",
                                     provider=provider, recovery=recovery, halt_on_issue=True)
    runtime.register_agent(agent_id="agent_llm")
    if allowed:
        assert runtime._invoke(method="game_end_cleanup", context=_ctx(tmp_path))[0] is not None
    else:
        with pytest.raises(RuntimeError, match="exhausted its request limit"):
            runtime._invoke(method="game_end_cleanup", context=_ctx(tmp_path))
    assert provider.calls == int(allowed)


def test_no_authorization_keeps_default_three(tmp_path):
    recovery = ExperimentRecovery(tmp_path, identity={"id": "cap"}, resume=False)
    recovery.prepare()
    assert recovery.attempt_limit("any", "prompt") == 3
