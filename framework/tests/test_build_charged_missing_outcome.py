from pathlib import Path
import pytest
from test_build_rule_activation import cross_boundary
root = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('action',['retry_unknown','abandon_unknown'])
@pytest.mark.parametrize('continuation',['none','wrong','timeout'])
def test_activated_charged_success_without_response_reaches_operator_decision(tmp_path,action,continuation):
 if action == 'abandon_unknown' and continuation != 'none': pytest.skip('abandoned decisions have no replacement')
 import json,hashlib,importlib.util
 from datetime import datetime,UTC
 from types import SimpleNamespace
 from test_build_replay_receipt import fixture,authorize,DECISION
 from test_build_rule_adoption import evidence
 from llm_diplomacy.guardrails.budget import BudgetConfig,BudgetController
 from llm_diplomacy.runner.recovery import atomic_json
 spec=importlib.util.spec_from_file_location('review_import_helper',root/'scripts/recover_provider_responses.py');recover=importlib.util.module_from_spec(spec);spec.loader.exec_module(recover)
 r,old,new=fixture(tmp_path/'experiments')
 cfg={'max_provider_calls':20,'max_input_tokens':10000,'max_output_tokens':10000,'max_estimated_cost_usd':20}
 (r.root/'metrics/budget/budget_state.json').unlink()
 budget=BudgetController(schema_version='1.0',experiment_id='game',artifact_root=r.root/'metrics/budget',config=BudgetConfig(**cfg))
 initial_usage={'prompt_tokens':10,'completion_tokens':3,'input_tokens':10,'output_tokens':3,'total_tokens':13,'estimated_cost_usd':.1}
 budget.record_after_call(game_id='game_001',phase_id='1908_winter_adjustment',agent_id='metternich',method='submit_orders',usage=initial_usage)
 ledger=r.root/'metrics/provider_attempts.jsonl';initial={'kind':'request','game_id':'game_001','phase_id':'1908_winter_adjustment','agent_id':'metternich','method':'submit_orders','input_tokens':10,'output_tokens':3,'estimated_cost_usd':.1,'error_type':None};ledger.write_text(json.dumps(initial)+'\n')
 authorize(r,old,new);r.authorize_build_rule_adoption(evidence=evidence(r));r.authorize_build_rule_activation();r.prepare_working_tree();cross_boundary(r)
 phase='1909_spring_negotiation';decision=f'game_001/{phase}/tick-1/france/metternich/negotiate_tick';prompt='same exact original prompt';ph=hashlib.sha256(prompt.encode()).hexdigest();attempt='late-original'
 context={'schema_version':'1.0','experiment_id':'game','game_id':'game_001','phase_id':phase,'agent_id':'metternich','country_assignment':'france','visible_state':{'year':1909,'season':'spring','phase':'movement'}}
 runtime=r.working_root/'experiments/game/games/game_001/agents/metternich/runtime/phase_inputs';runtime.mkdir(parents=True);atomic_json(runtime/f'{phase}_negotiate_tick.json',{'context':context,'method':'negotiate_tick'})
 r.record_prepared(decision_id=decision,prompt_hash=ph,attempt_id=attempt,provider={'requested_model':'gpt-6-luna'})
 budget.reserve_before_call(reservation_id='late-reserve',game_id='game_001',phase_id=phase,agent_id='metternich',method='negotiate_tick',input_tokens=100,output_tokens=100,cost_usd=.1)

 # Real producer order: known accounting and request ledger precede dispatch journal, then crash before response journal.
 known={'input_tokens':10,'output_tokens':20,'prompt_tokens':10,'completion_tokens':20,'total_tokens':30,'estimated_cost_usd':.01}
 budget.settle_reservation(reservation_id='late-reserve',usage=known)
 req={'kind':'request','game_id':'game_001','phase_id':phase,'agent_id':'metternich','method':'negotiate_tick','input_tokens':10,'output_tokens':20,'estimated_cost_usd':.01,'error_type':None};ledger.write_bytes(ledger.read_bytes()+(json.dumps(req)+'\n').encode())
 r.record_dispatch(decision_id=decision,prompt_hash=ph,attempt_id=attempt,provider={})
 from llm_diplomacy.population.runner import PopulationExperimentRunner
 cfg_obj=SimpleNamespace(schema_version='1.0',experiment_id='game',dry_run=False,game_count=1,raw={'provider_failure_policy':{'strict_recovery':True}})
 runner=PopulationExperimentRunner(config=cfg_obj,base_dir=tmp_path,agents_root=r.root/'agents');runner._recovery_store=lambda **kwargs:r;runner._run_impl=lambda **kwargs:'normal_continuation_reached'
 before_budget=(r.root/'metrics/budget/budget_state.json').read_bytes();before_ledger=ledger.read_bytes()
 events_path=r.root/'metrics/budget/budget_events.jsonl'; saved_events=events_path.read_bytes()
 events_path.write_bytes(b'\n'.join(saved_events.splitlines()[:-1])+b'\n')
 with pytest.raises(ValueError): r.prepare()
 events_path.write_bytes(saved_events)
 with pytest.raises(ValueError): r.resolve_unknown(decision_id=decision,attempt_id=attempt,prompt_hash='wrong',choice='retry_after_review')
 assert runner.run(resume=True,decision_action=action,decision_id=decision,attempt_id=attempt,prompt_hash=ph)=='normal_continuation_reached'
 assert (r.root/'metrics/budget/budget_state.json').read_bytes()==before_budget and ledger.read_bytes()==before_ledger
 assert sum(x.get('kind')=='unknown_resolved' and x.get('attempt_id')==attempt for x in r._journal_rows())==1

 r.prepare()
 assert not any(x.get('kind')=='attempt' and x.get('attempt_id')==attempt for x in r._journal_rows())
 if continuation == 'wrong':
  other=decision.replace('tick-1','tick-2')
  r.record_prepared(decision_id=other,prompt_hash='other',attempt_id='other',provider={})
  budget.reserve_before_call(reservation_id='other',game_id='game_001',phase_id=phase,agent_id='metternich',method='negotiate_tick',input_tokens=100,output_tokens=100,cost_usd=.1)
  budget.settle_reservation(reservation_id='other',usage=known)
  ledger.write_bytes(ledger.read_bytes()+(json.dumps(req)+'\n').encode())
  r.record_dispatch(decision_id=other,prompt_hash='other',attempt_id='other',provider={})
  r.record_response(decision_id=other,prompt_hash='other',attempt_id='other',provider={},response={'content':'{}'},usage=known,error_category=None,outcome='received')
  with pytest.raises(ValueError, match='unresolved dispatch precedes'):
   r.prepare()
 elif continuation == 'timeout':
  r.record_prepared(decision_id=decision,prompt_hash=ph,attempt_id='retry',provider={})
  budget.reserve_before_call(reservation_id='retry',game_id='game_001',phase_id=phase,agent_id='metternich',method='negotiate_tick',input_tokens=100,output_tokens=100,cost_usd=.1)
  budget.settle_reservation(reservation_id='retry',usage=None,uncertain=True)
  retry_req={**req,'error_type':'TimeoutError','input_tokens':None,'output_tokens':None,'estimated_cost_usd':None}
  ledger.write_bytes(ledger.read_bytes()+(json.dumps(retry_req)+'\n').encode())
  r.record_dispatch(decision_id=decision,prompt_hash=ph,attempt_id='retry',provider={})
  r.record_response(decision_id=decision,prompt_hash=ph,attempt_id='retry',provider={},response=None,usage=None,error_category='provider_timeout',outcome='unknown')
  r.prepare()
  assert [item['attempt_id'] for item in r.in_flight()] == ['retry']
