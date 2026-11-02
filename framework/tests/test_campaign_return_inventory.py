import importlib.util
import json
from pathlib import Path


spec = importlib.util.spec_from_file_location('inventory', Path(__file__).parents[2] / 'viewer/scripts/inventory_campaign_returns.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_inventory_keeps_earlier_rejected_and_unindexed_fields_without_exporting_text(tmp_path):
    outputs = tmp_path / 'games/game_001/agents/birsan/runtime/phase_outputs'
    outputs.mkdir(parents=True)
    domain = {'explanation': {'summary': 'A private explanation.'}, 'passivity_review': {'reason': 'A deliberate hold.'},
              'private_messages': [{'body': 'An original offer.', 'purpose': 'Buy a return.'}], 'new_authored_field': 'Needs review.'}
    (outputs / '1901_spring_movement_submit_orders.json').write_text(json.dumps({'llm_meta': {'raw_content': json.dumps(domain), 'prompt': 'DO NOT EXPORT PROMPT'}, 'result': {'explanation': domain['explanation']}}))
    journal = tmp_path / 'recovery/journal.jsonl'
    journal.parent.mkdir()
    journal.write_text('\n'.join(json.dumps(row) for row in [
        {'kind': 'attempt', 'attempt_id': 'earlier', 'decision_id': 'game/tick-1/negotiate', 'response': {'content': json.dumps({'withheld_information': [{'summary': 'Earlier private choice.'}]})}},
        {'kind': 'response_validation', 'attempt_id': 'earlier', 'valid': False},
        {'kind': 'attempt', 'attempt_id': 'timeout', 'response': None}]))
    result = module.inventory({'experiment': tmp_path})
    fields = list(result['field_inventory'].values())
    assert {'explanation', 'passivity', 'purpose', 'withheld', 'unmapped'}.issubset({field['role'] for field in fields})
    earlier = next(field for field in fields if field['role'] == 'withheld')
    assert earlier['provenance']['valid'] is False
    assert earlier['pointer'].startswith('/line/1/response/content#decoded/')
    assert result['unmapped_fields']
    assert 'DO NOT EXPORT PROMPT' not in json.dumps(result)
    assert 'Earlier private choice.' not in json.dumps(result)


def test_typed_values_escaped_keys_and_empty_or_missing_returns(tmp_path):
    outputs=tmp_path/'games/game_001/agents/birsan/runtime/phase_outputs';outputs.mkdir(parents=True)
    payload={'explanation':{'null_value':None,'string_value':'null','a/b~':False}}
    (outputs/'typed.json').write_text(json.dumps({'llm_meta':{'raw_content':json.dumps(payload)},'result':{}}))
    (outputs/'empty.json').write_text(json.dumps({'llm_meta':{'raw_content':'{}'},'result':{}}))
    (outputs/'missing.json').write_text('{}')
    journal=tmp_path/'recovery/journal.jsonl';journal.parent.mkdir()
    journal.write_text('\n'.join(json.dumps(row) for row in [
        {'kind':'attempt','attempt_id':'empty','response':{'content':'{}'}},
        {'kind':'attempt','attempt_id':'arrays','response':{'content':json.dumps({'global_messages':[],'private_messages':[],'relationship_updates':[],'withheld_information':[]})}}]))
    result=module.inventory({'experiment':tmp_path});fields=list(result['field_inventory'].values())
    null=next(f for f in fields if f['pointer'].endswith('/null_value'))
    string=next(f for f in fields if f['pointer'].endswith('/string_value'))
    assert null['value_sha256']!=string['value_sha256']
    assert any(f['pointer'].endswith('/a~1b~0') for f in fields)
    assert any(f['pointer']=='/llm_meta/raw_content#decoded' for f in fields)
    assert any(f['pointer']=='/line/1/response/content#decoded' for f in fields)
    assert len([f for f in fields if f['pointer'].startswith('/line/2/')])==4
    assert len([f for f in fields if f['presence']=='absent'])==2
