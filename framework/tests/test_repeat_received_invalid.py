import json

import pytest

from llm_diplomacy.agents.baseline_llm_runtime import BaselineLLMAgentRuntime
from llm_diplomacy.agents.llm_provider import FakeLLMProvider, LLMResponse
from llm_diplomacy.runner.recovery import ExperimentRecovery
from test_baseline_llm_runtime import _ctx


def runtime_with(recovery, provider, tmp_path):
    return BaselineLLMAgentRuntime(schema_version='1.0', runtime_root=tmp_path / 'runtime',
        provider=provider, recovery=recovery, halt_on_issue=True)


@pytest.mark.parametrize('field,limit', [('max_provider_calls', 4), ('max_input_tokens', 1),
                                       ('max_output_tokens', 1), ('max_estimated_cost_usd', .000001)])
def test_repeat_policy_obeys_total_budget_admission(tmp_path, field, limit):
    from llm_diplomacy.agents.llm_provider import BudgetedLLMProvider
    from llm_diplomacy.guardrails.budget import BudgetConfig, BudgetController
    class Invalid(FakeLLMProvider):
        calls = 0
        def generate(self, request):
            self.calls += 1
            return LLMResponse('1.0', 'not json', 'fake', {'usage': {'prompt_tokens': 10, 'completion_tokens': 5}})
    inner = Invalid()
    budget = BudgetController(schema_version='1.0', experiment_id='exp', artifact_root=tmp_path / 'budget',
        config=BudgetConfig(**{field:limit}))
    provider = BudgetedLLMProvider(inner=inner, budget=budget, input_cost_per_1k_tokens=.001,
        output_cost_per_1k_tokens=.005, provider_metadata={'max_output_tokens':100})
    recovery = ExperimentRecovery(tmp_path / 'exp', identity={'test': 'budget'}, resume=False,
        repeat_received_invalid=True)
    recovery.prepare()
    with pytest.raises(RuntimeError, match='Recovery required'):
        runtime_with(recovery, provider, tmp_path)._invoke(method='game_end_cleanup', context=_ctx(tmp_path))
    assert inner.calls == (4 if field == 'max_provider_calls' else 0)
    assert not recovery.in_flight()


@pytest.mark.parametrize('failure', ['unknown', 'authentication', 'credit'])
def test_correction_transport_and_permanent_errors_stop_without_replacement(tmp_path, failure):
    from llm_diplomacy.agents.llm_provider import ProviderNetworkError, ProviderHTTPError, BudgetedLLMProvider
    from llm_diplomacy.guardrails.budget import BudgetController, BudgetConfig
    class Provider(FakeLLMProvider):
        calls = 0
        def generate(self, request):
            self.calls += 1
            if self.calls == 1:
                return LLMResponse('1.0', 'not json', 'fake', {'usage':{'prompt_tokens':10,'completion_tokens':5}})
            if failure == 'unknown': raise ProviderNetworkError('offline transport failure')
            raise ProviderHTTPError(status_code=401 if failure == 'authentication' else 429,
                error_body=json.dumps({'error':{'code':'invalid_api_key' if failure == 'authentication' else 'insufficient_quota'}}), metadata={})
    provider = Provider()
    budget = BudgetController(schema_version='1.0', experiment_id='transport', artifact_root=tmp_path/'budget',
                              config=BudgetConfig(max_provider_calls=20,max_estimated_cost_usd=10))
    wrapped = BudgetedLLMProvider(inner=provider,budget=budget,input_cost_per_1k_tokens=.001,
                                 output_cost_per_1k_tokens=.005,provider_metadata={'max_output_tokens':100})
    recovery = ExperimentRecovery(tmp_path / 'exp', identity={'test': 'transport'}, resume=False,
        repeat_received_invalid=True)
    recovery.prepare()
    runtime = runtime_with(recovery, wrapped, tmp_path)
    with pytest.raises(RuntimeError, match='Recovery required'):
        runtime._invoke(method='game_end_cleanup', context=_ctx(tmp_path))
    assert provider.calls == 2
    if failure == 'unknown':
        before=budget.state_path.read_bytes()
        assert len(budget.pending_reservations)==1
        with pytest.raises(RuntimeError, match='unresolved provider dispatch'):
            runtime._invoke(method='game_end_cleanup', context=_ctx(tmp_path))
        assert provider.calls == 2 and len(recovery.in_flight()) == 1
        assert budget.state_path.read_bytes()==before


def test_wall_guard_survives_reopen_and_cannot_be_cleared_by_resume(tmp_path, monkeypatch):
    from datetime import datetime, UTC, timedelta
    import llm_diplomacy.runner.recovery as module
    identity={'test':'deadline'}
    recovery=ExperimentRecovery(tmp_path, identity=identity, resume=False, max_wall_seconds=1)
    recovery.prepare()
    class Later(datetime):
        @classmethod
        def now(cls, tz=None): return datetime.now(UTC)+timedelta(seconds=2)
    monkeypatch.setattr(module, 'datetime', Later)
    reopened=ExperimentRecovery(tmp_path, identity=identity, resume=True, max_wall_seconds=1)
    reopened.prepare()
    with pytest.raises(RuntimeError, match='wall-time exhausted'): reopened.check_safe_halt()


@pytest.mark.parametrize('boundary', ['intent', 'prepared', 'response', 'validation'])
def test_interrupted_correction_boundaries_do_not_duplicate_inference(tmp_path, monkeypatch, boundary):
    class Provider(FakeLLMProvider):
        calls=0
        def generate(self, request):
            self.calls += 1
            if self.calls == 1: return LLMResponse('1.0', 'invalid', 'fake', {})
            return super().generate(request)
    recovery=ExperimentRecovery(tmp_path / 'exp', identity={'test':boundary}, resume=False,
                                repeat_received_invalid=True)
    recovery.prepare()
    provider=Provider();runtime=runtime_with(recovery, provider, tmp_path)
    name={'intent':'prepare_repeat_correction','prepared':'record_prepared',
          'response':'record_response','validation':'record_validation'}[boundary]
    original=getattr(recovery,name)
    fired=False
    def interrupt(*args, **kwargs):
        nonlocal fired
        result=original(*args, **kwargs)
        should_stop=(boundary=='intent' or boundary=='prepared' and provider.calls==1
                     or boundary=='response' and provider.calls==2
                     or boundary=='validation' and kwargs.get('valid') is True)
        if should_stop and not fired:
            fired=True
            raise KeyboardInterrupt('offline interruption')
        return result
    monkeypatch.setattr(recovery,name,interrupt)
    with pytest.raises(KeyboardInterrupt): runtime._invoke(method='game_end_cleanup', context=_ctx(tmp_path))
    monkeypatch.setattr(recovery,name,original)
    calls_before=provider.calls
    if boundary=='prepared':
        with pytest.raises(RuntimeError, match='unresolved provider dispatch'):
            runtime._invoke(method='game_end_cleanup', context=_ctx(tmp_path))
        assert provider.calls==calls_before
    else:
        assert runtime._invoke(method='game_end_cleanup', context=_ctx(tmp_path))[0] is not None
        assert provider.calls==2


def test_semantically_tampered_intent_rejected_on_reopen(tmp_path):
    recovery=ExperimentRecovery(tmp_path, identity={'test':'tamper'}, resume=False, repeat_received_invalid=True)
    recovery.prepare()
    recovery.record_response(decision_id='g/p/o/france/a/game_end_cleanup', prompt_hash='base', attempt_id='one',
        provider={}, response={'content':'invalid'}, usage=None, error_category=None, outcome='received')
    recovery.record_validation(decision_id='g/p/o/france/a/game_end_cleanup', prompt_hash='base', attempt_id='one',
        valid=False, issue='actual issue')
    intent=recovery.prepare_repeat_correction(decision_id='g/p/o/france/a/game_end_cleanup', parent_prompt_hash='base',
        feedback={'rejected_answer':'invalid','validator_issue':'actual issue'}, correction_prompt_hash='next')
    for field in ('parent_attempt_id','parent_prompt_hash','parent_response_hash','parent_validation_hash','feedback_hash'):
        with pytest.raises(ValueError): recovery._validate_repeat_correction({**intent,field:'changed'}, recovery._journal_rows())
    recovery._append({**{k:v for k,v in intent.items() if k not in {'sequence','previous_hash','sha256'}},
                      'feedback':{'rejected_answer':'changed','validator_issue':'actual issue'}})
    with pytest.raises(ValueError, match='actual received-invalid feedback'):
        ExperimentRecovery(tmp_path, identity={'test':'tamper'}, resume=True, repeat_received_invalid=True).prepare()


@pytest.mark.parametrize('method', ['submit_orders','negotiate_tick'])
def test_rejected_orders_and_negotiations_produce_no_phase_effects(tmp_path, method):
    root=tmp_path/'runtime'
    class Provider(FakeLLMProvider):
        calls=0
        def generate(self, request):
            self.calls+=1
            assert self.calls < 10, 'offline provider failed to reach the valid fixture'
            assert not list(root.glob('**/phase_outputs/*.json'))
            if self.calls<=4: return LLMResponse('1.0','invalid','fake',{})
            if method=='submit_orders':
                return LLMResponse('1.0',json.dumps({'orders':[
                    {'unit_ref':'u_fra_a_par','type':'HOLD','payload':{'at':'par'}},
                    {'unit_ref':'u_fra_f_bre','type':'HOLD','payload':{'at':'bre'}}],
                    'order_rationale':{'goal_supported':'Survive','why_this_advances_strategy':'Hold position'},
                    'passivity_review':{'justified':True,'reason':'Offline test fixture'}}),'fake',{})
            return super().generate(request)
    recovery=ExperimentRecovery(tmp_path/'exp',identity={'test':method},resume=False,repeat_received_invalid=True)
    recovery.prepare();provider=Provider();runtime=runtime_with(recovery,provider,tmp_path)
    getattr(runtime, method)(_ctx(tmp_path))
    assert provider.calls==5
    outputs=list(root.glob('**/phase_outputs/*.json'))
    assert len(outputs)==1


def test_repeat_invalid_chain_replays_without_new_calls(tmp_path):
    class Provider(FakeLLMProvider):
        def __init__(self):
            super().__init__()
            self.prompts = []

        def generate(self, request):
            self.prompts.append(request.prompt)
            if len(self.prompts) <= 8:
                return LLMResponse(schema_version='1.0', content=f'{{"broken":{len(self.prompts)}}},"extra":null}}', model='fake', raw={})
            return super().generate(request)

    recovery = ExperimentRecovery(tmp_path / 'exp', identity={'test': 'repeat'}, resume=False,
                                  repeat_received_invalid=True)
    recovery.prepare()
    provider = Provider()
    runtime = BaselineLLMAgentRuntime(schema_version='1.0', runtime_root=tmp_path / 'runtime',
        provider=provider, recovery=recovery, halt_on_issue=True)
    payload, _ = runtime._invoke(method='game_end_cleanup', context=_ctx(tmp_path))
    assert payload is not None and len(provider.prompts) == 9
    intents = [r for r in recovery._journal_rows() if r['kind'] == 'repeat_correction_intent']
    assert len(intents) == 8
    for index, intent in enumerate(intents):
        raw = f'{{"broken":{index+1}}},"extra":null}}'
        assert intent['feedback']['rejected_answer'] == raw
        with pytest.raises(json.JSONDecodeError) as error:
            json.loads(raw)
        assert str(error.value) == intent['feedback']['parser_error']
        assert json.dumps(raw) in provider.prompts[index + 1]
    assert len(set(provider.prompts)) == 9
    archived={path:path.read_bytes() for path in (tmp_path/'runtime').glob('**/errors/raw-*.txt')}
    assert len(archived)==9
    reopened = ExperimentRecovery(tmp_path / 'exp', identity={'test': 'repeat'}, resume=True,
                                  repeat_received_invalid=True)
    reopened.prepare()
    runtime.recovery = reopened
    runtime._invoke(method='game_end_cleanup', context=_ctx(tmp_path))
    assert len(provider.prompts) == 9
    assert all(path.read_bytes()==value for path,value in archived.items())
    assert len(recovery._journal_rows()) >= len(intents)


def test_default_policy_still_halts_on_malformed_output(tmp_path):
    class Provider(FakeLLMProvider):
        def generate(self, request):
            return LLMResponse(schema_version='1.0', content='invalid', model='fake', raw={})
    recovery = ExperimentRecovery(tmp_path / 'exp', identity={'test': 'default'}, resume=False)
    recovery.prepare()
    runtime = BaselineLLMAgentRuntime(schema_version='1.0', runtime_root=tmp_path / 'runtime',
        provider=Provider(), recovery=recovery, halt_on_issue=True)
    with pytest.raises(RuntimeError, match='Recovery required'):
        runtime._invoke(method='game_end_cleanup', context=_ctx(tmp_path))
    assert not any(r['kind'] == 'repeat_correction_intent' for r in recovery._journal_rows())


def test_oversized_prompt_stops_before_new_dispatch(tmp_path, monkeypatch):
    recovery=ExperimentRecovery(tmp_path/'exp',identity={'test':'input-bound'},resume=False,repeat_received_invalid=True)
    recovery.prepare()
    class Provider(FakeLLMProvider):
        calls=0
        def generate(self,request):
            self.calls+=1
            raise AssertionError('must not dispatch')
    provider=Provider();runtime=runtime_with(recovery,provider,tmp_path)
    monkeypatch.setattr(runtime.context_builder,'build_prompt',lambda **kwargs:'x'*272000)
    with pytest.raises(RuntimeError,match='short-context pricing bound'):
        runtime._invoke(method='game_end_cleanup',context=_ctx(tmp_path))
    assert provider.calls==0
    assert not any(r['kind']=='dispatch_prepared' for r in recovery._journal_rows())


def test_absolute_arm_deadline_cannot_restart_with_a_new_child_clock(tmp_path):
    from datetime import datetime, UTC, timedelta
    deadline=(datetime.now(UTC)-timedelta(seconds=1)).isoformat()
    store=ExperimentRecovery(tmp_path,identity={'test':'arm-time'},resume=False,
        max_wall_seconds=500000,deadline_utc=deadline)
    store.prepare()
    with pytest.raises(RuntimeError,match='total arm wall-time exhausted'):store.check_safe_halt()


def test_repeat_typed_response_does_not_adopt_json_extracted_from_prose(tmp_path):
    class Provider(FakeLLMProvider):
        calls=0
        def generate(self,request):
            self.calls+=1
            good=super().generate(request)
            if self.calls==1:return LLMResponse('1.0','extra prose\n'+good.content,'fake',{})
            assert 'json_decode_failed' in request.prompt or 'Expecting value' in request.prompt
            return good
    recovery=ExperimentRecovery(tmp_path/'exp',identity={'test':'strict-json'},resume=False,repeat_received_invalid=True)
    recovery.prepare();provider=Provider()
    assert runtime_with(recovery,provider,tmp_path)._invoke(method='game_end_cleanup',context=_ctx(tmp_path))[0] is not None
    assert provider.calls==2


def test_new_experiment_game001_promotes_once_without_rewriting_inherited_memory(tmp_path):
    from dataclasses import replace
    recovery=ExperimentRecovery(tmp_path/'exp',identity={'test':'memory-scope'},resume=False,repeat_received_invalid=True)
    recovery.prepare()
    source=tmp_path/'agents';path=source/'agent_llm/persistent_memory.md';path.parent.mkdir(parents=True)
    inherited=b'Original bytes\r\nSource game: game_001; old lesson\r\n';path.write_bytes(inherited)
    runtime=BaselineLLMAgentRuntime(schema_version='1.0',runtime_root=tmp_path/'runtime',provider=FakeLLMProvider(),
        recovery=recovery,halt_on_issue=True,source_folio_root=source)
    context=replace(_ctx(tmp_path),experiment_id='luna-successor',game_id='game_001')
    runtime._promote_persistent_memory_to_source(context,'New Luna lesson')
    first=path.read_bytes();assert first.startswith(inherited)
    assert first.count(b'New Luna lesson')==1
    runtime._promote_persistent_memory_to_source(context,'New Luna lesson')
    assert path.read_bytes()==first
    next_context=replace(context,experiment_id='terra-child-two')
    runtime._promote_persistent_memory_to_source(next_context,'Second child lesson')
    second=path.read_bytes();assert second.startswith(first) and second.count(b'Second child lesson')==1
    runtime._promote_persistent_memory_to_source(next_context,'Second child lesson')
    assert path.read_bytes()==second
