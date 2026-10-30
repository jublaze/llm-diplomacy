import importlib.util
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from llm_diplomacy.population import PopulationExperimentRunner
from llm_diplomacy.runner.recovery import ExperimentRecovery
from llm_diplomacy.guardrails.budget import BudgetController,BudgetConfig

candidate=Path(os.environ.get('BUILD_RULE_CANDIDATE_ROOT',Path(__file__).parents[1]))
spec=importlib.util.spec_from_file_location('terra_handoff',candidate/'scripts/prepare_terra_second_game.py')
handoff=importlib.util.module_from_spec(spec);spec.loader.exec_module(handoff)


def test_remaining_limits_deduct_all_admission_exposure():
    policy={'total_usd':175,'total_calls':6968,'total_input_tokens':79678408,'total_output_tokens':222976000}
    budget={'total_provider_calls':1400,'budget_admission_estimated_input_tokens':22000000,
            'budget_admission_estimated_output_tokens':8200000,'budget_admission_estimated_cost_usd':63.25}
    result=handoff.remaining_limits(policy,budget)
    assert result=={'max_provider_calls':5568,'max_input_tokens':57678408,
                    'max_output_tokens':214776000,'max_estimated_cost_usd':111.75}
    for key in budget:
        with pytest.raises(ValueError):handoff.remaining_limits(policy,{**budget,key:None})
    with pytest.raises(ValueError):handoff.remaining_limits(policy,{**budget,'budget_admission_estimated_cost_usd':175})


@pytest.fixture
def completed_source(tmp_path,monkeypatch):
    source=tmp_path/'experiments/terra-one';source.mkdir(parents=True)
    destination=source.parent/'terra-two'
    provider={'model_profile':'openai_gpt_6_1_terra','reasoning_effort':'medium','max_output_tokens':32000,
              'service_tier':'default','prompt_cache_mode':'explicit'}
    config={'schema_version':'1.0','experiment_id':'terra-one','dry_run':False,'agent_state_mode':'fresh',
        'agents':[{'agent_id':'birsan','blueprint':str(Path('blueprints/birsan.yaml').resolve()),'provider_config':provider}],
        'runtime':{'kind':'baseline_llm','llm_agent_ids':['birsan'],'max_ticks':3},
        'games':{'count':1,'max_year':1912,'country_assignment':'fixed','seed':20261016},
        'outputs':{'observatory':True,'summary':True},
        'budget':{'max_estimated_cost_usd':175,'max_provider_calls':6968,'max_input_tokens':79678408,'max_output_tokens':222976000},
        'provider_failure_policy':{'strict_recovery':True,'received_invalid_policy':'repeat_with_feedback_v1','max_wall_seconds':518400}}
    (source/'launch.yaml').write_text(yaml.safe_dump(config))
    template={**config,'experiment_id':'terra-two','agent_state_mode':'reuse'}
    template_path=tmp_path/'template.yaml';template_path.write_text(yaml.safe_dump(template))
    policy={'source_experiment_id':'terra-one','next_experiment_id':'terra-two','total_usd':175,
        'total_calls':6968,'total_input_tokens':79678408,'total_output_tokens':222976000,'total_wall_seconds':518400,
        'source_config_sha256':handoff.digest(source/'launch.yaml'),'template_sha256':handoff.digest(template_path)}
    policy_path=tmp_path/'policy.json';policy_path.write_text(json.dumps(policy))
    recovery=ExperimentRecovery(source,identity={'fixture':'terminal','code_sha256':handoff.runtime_code_sha()},resume=False,repeat_received_invalid=True)
    recovery.prepare();recovery.checkpoint(cursor={'unit':'population_reports_completed'},state={},applied=['game_001','population_reports'])
    monkeypatch.setattr(PopulationExperimentRunner,'_recovery_store',lambda self:recovery)
    # Accounting reconciliation has its own real journal/reservation/import tests.
    monkeypatch.setattr(handoff,'reconcile_accounting',lambda *a,**k:{'offline_fixture':'reconciled'})
    (source/'run_summary.json').write_text(json.dumps({'status':'completed','games_completed':1}))
    root=source/'metrics/budget';root.mkdir(parents=True)
    budget=BudgetController(schema_version='1.0',experiment_id='terra-one',artifact_root=root,config=BudgetConfig(**config['budget']))
    for _ in range(3):
        budget.record_after_call(game_id='game_001',phase_id='p',agent_id='birsan',method='submit_orders',
            usage={'prompt_tokens':50,'completion_tokens':83,'estimated_cost_usd':4.0})
    budget.reserve_before_call(reservation_id='uncertain',game_id='game_001',phase_id='p',agent_id='birsan',
        method='submit_orders',input_tokens=100,output_tokens=1000,cost_usd=.25)
    budget.settle_reservation(reservation_id='uncertain',usage=None,uncertain=True)
    (source/'metrics/provider_attempts.jsonl').write_text(('\n'.join(json.dumps({'kind':'request'}) for _ in range(4)))+'\n')
    folio=source/'agents/birsan';folio.mkdir(parents=True)
    for name in ('manifest.json','soul.md','public_profile.md','persistent_memory.md'):
        (folio/name).write_text('retained '+name)
    proof_path=tmp_path/'no-writer.json'
    proof_path.write_text(json.dumps({'verified_utc':datetime.now(UTC).isoformat(),'source_experiment_id':'terra-one',
        'exact_writers':[],'escalated_bounded_inventory_verified':True}))
    scripts=Path(handoff.__file__).parent
    return (policy_path,template_path,source,destination,proof_path,handoff.runtime_code_sha(),
            handoff.digest(scripts/'run_population_experiment.py'),handoff.digest(scripts/'run_sealed_population.py'))


def test_atomic_idempotent_handoff_preserves_source_and_remaining_budget(completed_source):
    args=completed_source;source=args[2];destination=args[3]
    before={p.relative_to(source):p.read_bytes() for p in source.rglob('*') if p.is_file() and p.name!='writer.lock'}
    receipt=handoff.prepare(*args)
    assert receipt['runtime_code_sha256']==handoff.runtime_code_sha()
    assert receipt['runtime_entrypoint_sha256']==args[-2] and receipt['runtime_wrapper_sha256']==args[-1]
    assert receipt['remaining_limits']['max_estimated_cost_usd']==162.75
    config=yaml.safe_load((destination/'launch.yaml').read_text())
    assert config['budget']['max_provider_calls']==6964
    assert config['provider_failure_policy']['deadline_utc']==receipt['arm_deadline_utc']
    assert config['agent_state_mode']=='reuse' and config['games']['count']==1
    assert handoff.prepare(*args)==receipt
    assert all((source/p).read_bytes()==data for p,data in before.items())
    assert (destination/'agents/birsan/persistent_memory.md').read_bytes()==(source/'agents/birsan/persistent_memory.md').read_bytes()
    (destination/'launch.yaml').write_text('changed')
    with pytest.raises(ValueError,match='destination changed'):handoff.prepare(*args)


def test_wrong_runtime_hash_cannot_prepare_second_game(completed_source):
    args=completed_source
    with pytest.raises(ValueError, match='runtime package'):
        handoff.prepare(*args[:5], '0' * 64, *args[6:])
    assert not args[3].exists()


@pytest.mark.parametrize('blocker',['inflight','partial_commit','partial_handoff','exhausted','accounting'])
def test_handoff_failures_never_materialize_next_game(completed_source,monkeypatch,blocker):
    args=completed_source;source=args[2];destination=args[3]
    if blocker=='inflight':
        original=ExperimentRecovery.status
        monkeypatch.setattr(ExperimentRecovery,'status',lambda self:{**original(self),'in_flight':[{}]})
    elif blocker=='partial_commit':(source/'recovery/commit_manifest.json').write_text('{}')
    elif blocker=='partial_handoff':(destination.parent/f'.{destination.name}.handoff-incomplete').mkdir()
    elif blocker=='exhausted':
        p=source/'metrics/budget/budget_state.json';data=json.loads(p.read_text());data['budget_admission_estimated_cost_usd']=175;p.write_text(json.dumps(data))
    else:monkeypatch.setattr(handoff,'reconcile_accounting',lambda *a,**k:(_ for _ in ()).throw(ValueError('unreconciled')))
    with pytest.raises((ValueError,FileNotFoundError)):handoff.prepare(*args)
    assert not destination.exists()
