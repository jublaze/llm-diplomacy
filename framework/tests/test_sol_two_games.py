import importlib.util
import json
import random
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from llm_diplomacy.agents.llm_provider import FakeLLMProvider, LLMResponse, parse_llm_provider_config
from llm_diplomacy.population import PopulationExperimentRunner, load_population_experiment_config
from llm_diplomacy.runner.scripted_experiment_runner import ScriptedExperimentRunner

spec = importlib.util.spec_from_file_location('sol_handoff', Path(__file__).parents[1]/'scripts/prepare_sol_second_game.py')
handoff = importlib.util.module_from_spec(spec); spec.loader.exec_module(handoff)
PLAYERS = ['birsan','dolos','metternich','shaver','major','birsan_ii','major_ii']
POWERS = ['austria','england','france','germany','italy','russia','turkey']


@pytest.mark.parametrize('interrupt', [False, True])
def test_two_strict_children_transfer_memory_into_actual_prompts(tmp_path, monkeypatch, interrupt):
    from llm_diplomacy.runner import scripted_experiment_runner as module
    requests = []
    original = FakeLLMProvider.generate
    def generate(self, request):
        requests.append(request)
        usage = {'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20}}
        if request.method == 'game_end_cleanup':
            return LLMResponse('1.0', json.dumps({'persistent_memory_update': 'transfer-marker-'+request.agent_id,
                'public_profile_update': None}), 'offline-mock', usage)
        if request.method == 'submit_orders':
            legal = request.context.get('legal_order_context', {})
            orders = [{'unit_ref':u['unit_ref'], **u['legal_orders'][0]}
                      for u in legal.get('your_units',[]) if u.get('legal_orders')]
            return LLMResponse('1.0', json.dumps({'orders':orders,
                'order_rationale':{'goal_supported':'hold','why_this_advances_strategy':'Preserve position'},
                'passivity_review':{'justified':True,'reason':'Offline deterministic hold'}}), 'offline-mock', usage)
        response = original(self, request)
        response.raw.update(usage)
        return response
    monkeypatch.setattr(FakeLLMProvider,'generate',generate)
    monkeypatch.setattr(module,'build_provider_from_config',lambda cfg:FakeLLMProvider())
    from llm_diplomacy.population import runner as population_module
    monkeypatch.setattr(population_module,'build_provider_from_config',lambda cfg:FakeLLMProvider())
    source=tmp_path/'experiments/sol-first'; source.mkdir(parents=True)
    destination=source.parent/'sol-second'
    provider={'model_profile':'openai_gpt_6_1_sol','reasoning_effort':'medium',
              'max_output_tokens':32000,'timeout_seconds':600,'service_tier':'default','prompt_cache_mode':'explicit'}
    raw={'schema_version':'1.0','experiment_id':'sol-first','dry_run':False,'agent_state_mode':'fresh',
        'agents':[{'agent_id':p,'blueprint':str(Path('blueprints',p.removesuffix('_ii')+'.yaml').resolve()),
                   'provider_config':dict(provider)} for p in PLAYERS],
        'runtime':{'kind':'baseline_llm','max_ticks':1,'llm_agent_ids':PLAYERS},
        'games':{'count':1,'max_year':1901,'country_assignment':'fixed','seed':20261016},
        'budget':{'max_provider_calls':6968,'max_input_tokens':79678408,'max_output_tokens':222976000,
                  'max_estimated_cost_usd':350,'stop_policy':'stop_experiment'},
        'provider_failure_policy':{'strict_recovery':True,'received_invalid_policy':'repeat_with_feedback_v1','max_wall_seconds':518400},
        'outputs':{'summary':True,'observatory':True}}
    (source/'launch.yaml').write_text(yaml.safe_dump(raw),encoding='utf8')
    cfg=load_population_experiment_config(source/'launch.yaml')
    result=PopulationExperimentRunner(config=cfg,base_dir=tmp_path,agents_root=source/'agents').run()
    summary=json.loads(result.summary_json_path.read_text(encoding='utf8'))
    assert summary['games'][0]['country_assignment']==dict(zip(POWERS,PLAYERS))
    template=json.loads(json.dumps(raw));template.update(experiment_id='sol-second',agent_state_mode='reuse')
    template['games'].update(country_assignment='seeded_shuffle',seed=20261028)
    template_path=tmp_path/'second.yaml';template_path.write_text(yaml.safe_dump(template),encoding='utf8')
    policy={'source_experiment_id':'sol-first','next_experiment_id':'sol-second','total_usd':350,
        'total_calls':6968,'total_input_tokens':79678408,'total_output_tokens':222976000,'total_wall_seconds':518400,
        'source_config_sha256':handoff.digest(source/'launch.yaml'),'template_sha256':handoff.digest(template_path)}
    policy_path=tmp_path/'policy.json';policy_path.write_text(json.dumps(policy),encoding='utf8')
    proof=tmp_path/'writers.json';proof.write_text(json.dumps({'verified_utc':datetime.now(UTC).isoformat(),
        'source_experiment_id':'sol-first','exact_writers':[],'escalated_bounded_inventory_verified':True}),encoding='utf8')
    script=Path(__file__).parents[1]/'scripts'
    args=(policy_path,template_path,source,destination,proof,handoff.runtime_code_sha(),
          handoff.digest(script/'run_population_experiment.py'),handoff.digest(script/'run_sealed_population.py'))
    receipt=handoff.prepare(*args)
    assert receipt['shared_total_usd']==350
    assert receipt['remaining_limits']['max_provider_calls']<6968
    source_memories={p:(source/'agents'/p/'persistent_memory.md').read_bytes() for p in PLAYERS}
    first_count = len(requests)
    for player in PLAYERS:
        assert ('transfer-marker-'+player).encode() in source_memories[player]
        assert b'Source experiment: sol-first; source game: game_001;' in source_memories[player]
        assert (destination/'agents'/player/'persistent_memory.md').read_bytes()==source_memories[player]
    second=load_population_experiment_config(destination/'launch.yaml')
    original_game=ScriptedExperimentRunner._run_single_game
    interrupted=[]
    def run_game(self,*,game_id,game_index):
        if interrupt and self.config.experiment_id=='sol-second' and not interrupted:
            interrupted.append(True);raise RuntimeError('offline interruption before child-two dispatch')
        return original_game(self,game_id=game_id,game_index=game_index)
    monkeypatch.setattr(ScriptedExperimentRunner,'_run_single_game',run_game)
    def runner():return PopulationExperimentRunner(config=second,base_dir=tmp_path,agents_root=destination/'agents')
    if interrupt:
        with pytest.raises(RuntimeError,match='offline interruption'):runner().run()
        result=runner().run(resume=True)
    else:
        result=runner().run()
    summary=json.loads(result.summary_json_path.read_text(encoding='utf8'))
    shuffled=PLAYERS.copy();random.Random(20261028).shuffle(shuffled)
    assert summary['games'][0]['country_assignment']==dict(zip(POWERS,shuffled))
    for player in PLAYERS:
        calls=[r for r in requests[first_count:] if r.agent_id==player]
        assert calls and 'transfer-marker-'+player in calls[0].prompt
        assert 'Source experiment: sol-first; source game: game_001;' in calls[0].prompt
        assert (source/'agents'/player/'persistent_memory.md').read_bytes()==source_memories[player]
    before=len(requests);runner().run(resume=True);assert len(requests)==before
    spec2=importlib.util.spec_from_file_location('sol_verify',script/'verify_sol_two_games.py')
    verify_module=importlib.util.module_from_spec(spec2);spec2.loader.exec_module(verify_module)
    report=verify_module.verify(source,destination,'offline-sol-arm')
    assert len(report['memory_handoff'])==7
    assert report['children'][0]['composite_game_id']!='sol-second--game_001'
    assert report['children'][1]['composite_game_id']=='sol-second--game_001'
    output=destination/'games/game_001/agents/birsan/runtime/phase_outputs/1901_spring_negotiation_negotiate_tick.json'
    data=json.loads(output.read_text(encoding='utf8'))
    data['llm_meta']['prompt']=data['llm_meta']['prompt'].replace('transfer-marker-birsan','removed-marker')
    output.write_text(json.dumps(data),encoding='utf8')
    with pytest.raises(ValueError,match='prompt did not receive'):
        verify_module.verify(source,destination,'offline-sol-arm')


def test_sol_profile_exact_model_effort_and_prices():
    cfg=parse_llm_provider_config({'model_profile':'openai_gpt_6_1_sol','reasoning_effort':'medium',
                                  'service_tier':'default','prompt_cache_mode':'explicit'})
    assert cfg.model=='gpt-6.1-sol' and cfg.reasoning_effort=='medium'
    assert cfg.service_tier=='default' and cfg.prompt_cache_mode=='explicit'
    assert cfg.input_cost_per_1k_tokens==.002 and cfg.output_cost_per_1k_tokens==.01
