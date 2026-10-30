from pathlib import Path
import pytest
from test_build_rule_activation import cross_boundary
root = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize("crash_point", ["intent", "settlement_event", "received"])
@pytest.mark.parametrize("missing_outcome", [False, True])
def test_verified_original_import_resumes_after_interruption(tmp_path, monkeypatch, crash_point, missing_outcome):
 import json,hashlib,importlib.util
 from datetime import datetime,UTC
 from types import SimpleNamespace
 from test_build_replay_receipt import fixture,authorize,DECISION
 from test_build_rule_adoption import evidence
 from llm_diplomacy.guardrails.budget import BudgetConfig,BudgetController
 from llm_diplomacy.runner.recovery import atomic_json, canonical_hash
 spec=importlib.util.spec_from_file_location('review_import_helper',root/'scripts/recover_provider_responses.py');recover=importlib.util.module_from_spec(spec);spec.loader.exec_module(recover)
 r,old,new=fixture(tmp_path)
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
 r.record_dispatch(decision_id=decision,prompt_hash=ph,attempt_id=attempt,provider={})
 if not missing_outcome: r.record_response(decision_id=decision,prompt_hash=ph,attempt_id=attempt,provider={},response=None,usage=None,error_category='provider_timeout',outcome='unknown')
 budget.settle_reservation(reservation_id='late-reserve',usage=None,uncertain=True)
 failed={'kind':'request','game_id':'game_001','phase_id':phase,'agent_id':'metternich','method':'negotiate_tick','input_tokens':None,'output_tokens':None,'estimated_cost_usd':None,'error_type':'TimeoutError'};ledger.write_bytes(ledger.read_bytes()+(json.dumps(failed)+'\n').encode())
 if not missing_outcome: r.halt(reason='provider_timeout',decision_id=decision)
 runner=SimpleNamespace(config=SimpleNamespace(schema_version='1.0',experiment_id='game',budget=cfg,players=[SimpleNamespace(agent_id='metternich')]),_player_provider=lambda p:{'model_profile':'openai_gpt_6_luna'})
 now=int(datetime.now(UTC).timestamp());payload={'private_messages':[],'global_messages':[],'relationship_updates':[],'withheld_information':[]}
 response={'id':'resp_late','status':'completed','model':'gpt-6-luna','created_at':now,'completed_at':now+10,'usage':{'input_tokens':10,'output_tokens':20,'total_tokens':30,'output_tokens_details':{'reasoning_tokens':5}},'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(payload)}]}]}
 response_path=tmp_path/'response.json';input_path=tmp_path/'input.json';atomic_json(response_path,response);atomic_json(input_path,{'data':[{'role':'user','content':[{'type':'input_text','text':prompt}]}],'has_more':False})
 plan=recover.build_plan(runner,r,{'responses':[{'attempt_id':attempt,'response_id':'resp_late','response_path':str(response_path),'input_path':str(input_path)}]})
 if crash_point == 'intent':
  legacy = {**{k:v for k,v in plan.items() if k not in {'validation_context','validation_context_sha256','provider_routes','provider_agents','provider_configuration'}}, 'observations': [{k:v for k,v in row.items() if k not in {'source_response','source_input','provider_pricing'}} for row in plan['observations']]}
  old_path = r.path/'provider_imports'/canonical_hash(legacy)
  old_path.mkdir(parents=True)
  before = r.journal_path.read_bytes()
  with pytest.raises(ValueError, match='legacy provider import'):
   recover.apply_plan(runner,r,plan)
  assert r.journal_path.read_bytes() == before
  old_path.rmdir()
 before_budget=(r.root/'metrics/budget/budget_state.json').read_bytes();before_journal=r.journal_path.read_bytes()
 if crash_point == 'intent':
  original = r._append
  def interrupted(row):
   if row.get('kind') == 'provider_import_intent_v1': raise OSError('interrupt intent')
   return original(row)
  monkeypatch.setattr(r, '_append', interrupted)
 elif crash_point == 'settlement_event':
  original = BudgetController._flush_state
  def interrupted(self):
   if 'late-reserve' not in self.pending_reservations: raise OSError('interrupt settlement')
   return original(self)
  monkeypatch.setattr(BudgetController, '_flush_state', interrupted)
 else:
  original = recover.reconcile_accounting
  def interrupted(*args, **kwargs): raise OSError('interrupt received')
  monkeypatch.setattr(recover, 'reconcile_accounting', interrupted)
 with pytest.raises(OSError, match='interrupt'):
  recover.apply_plan(runner,r,plan)
 if crash_point == 'intent': monkeypatch.setattr(r, '_append', original)
 elif crash_point == 'settlement_event': monkeypatch.setattr(BudgetController, '_flush_state', original)
 else: monkeypatch.setattr(recover, 'reconcile_accounting', original)
 if crash_point == 'received':
  transaction = r.path/'provider_imports'/canonical_hash(plan)/'transaction.json'
  saved = transaction.read_bytes()
  altered = json.loads(saved); altered['plan']['observations'][0]['response_id'] = 'resp_tampered'
  atomic_json(transaction, altered)
  with pytest.raises(ValueError, match='durable intent'):
   r.prepare()
  transaction.write_bytes(saved)
 r.prepare()
 receipt=recover.apply_plan(runner,r,plan)
 state=json.loads((r.root/'metrics/budget/budget_state.json').read_bytes())
 assert state['total_provider_calls']==2 and state['known_input_tokens']==20 and state['known_output_tokens']==23
 assert not state['pending_reservations']
 assert [x['outcome'] for x in r._journal_rows() if x.get('kind')=='attempt' and x.get('attempt_id')==attempt]==['unknown','received']
 before={p:p.read_bytes() for p in [r.journal_path,r.root/'metrics/budget/budget_state.json',ledger]}
 recover.apply_plan(runner,r,plan)
 assert {p:p.read_bytes() for p in before}==before
 (runtime/f'{phase}_negotiate_tick.json').unlink()
 r.prepare()
