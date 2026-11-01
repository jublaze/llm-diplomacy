import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('campaign_reader', Path(__file__).parents[2]/'viewer/scripts/assemble_campaign_reader.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture(tmp_path):
    report = {'source': {'experiment_id':'e','game_id':'game_001','visibility_mode':'omniscient_postgame'}, 'bundle_id':'e:game_001:v1', 'schema_version':'diplomacy_report_bundle_v1', 'map':{'map_id':'standard_v1'}, 'roster':[{'agent_id':'a','power':'france'}], 'years':[{'year':1901,'phases':[{'phase_id':'1901_spring_movement'},{'phase_id':'1901_fall_movement'}]}], 'game':{'outcome':{'termination':{'label':'Cutoff'}}}, 'evidence_index':{'s':{'artifact_path':'messages.jsonl','json_pointer':'/line/1','sha256':'a'*64}}, 'messages':{'m':{'source_ref':'s'}}, 'orders':{}, 'private_records':{}}
    record = {'kind':'message','title':'1901_fall_negotiation · a → b','text':'A precise offer.','raw':'original','experiment_id':'e','artifact_path':'messages.jsonl','pointer':'/line/1','sha256':'a'*64}
    reader=tmp_path/'reader.html';reader.write_text('<script id="evidence-data" type="application/json">'+json.dumps({'abc123':record})+'</script>')
    prose=tmp_path/'episodes.txt';prose.write_text('@@ opening|1|1901|A bargain|a\nA recorded offer. [abc123]\n\n>abc123|A precise offer.')
    source=tmp_path/'report.json';source.write_text(json.dumps(report))
    return reader,prose,source


def test_assembly_preserves_report_bytes_and_maps_fall_correspondence_to_fall(tmp_path):
    reader,prose,source=fixture(tmp_path)
    original=source.read_bytes()
    output=tmp_path/'output'
    result=module.assemble(reader,prose,[('g',source)],output)
    story=json.loads((output/'story.json').read_text())
    assert (output/'bundles/g/report.json').read_bytes()==original
    assert source.read_bytes()==original
    assert story['records']['abc123']['explorer']=={'game':'g','phase':'1901_fall_movement','target':{'type':'message','id':'m'}}
    assert result['exact_explorer_targets']==1
    assert story['episodes'][0]['blocks'][1]['text']=='A precise offer.'


def test_assembly_refuses_existing_editions_and_inexact_quotes(tmp_path):
    reader,prose,source=fixture(tmp_path)
    existing=tmp_path/'existing';existing.mkdir()
    with pytest.raises(ValueError,match='already exists'):module.assemble(reader,prose,[('g',source)],existing)
    prose.write_text('@@ opening|1|1901|A bargain|a\n>abc123|An invented quote.')
    with pytest.raises(ValueError,match='inexact'):module.assemble(reader,prose,[('g',source)],tmp_path/'bad')
    assert not (tmp_path/'bad').exists()


def test_assembly_cannot_bind_evidence_to_another_experiment(tmp_path):
    reader,prose,source=fixture(tmp_path)
    reader.write_text(reader.read_text().replace('"experiment_id": "e"','"experiment_id": "other"'))
    with pytest.raises(ValueError,match='unbound experiment'):module.assemble(reader,prose,[('g',source)],tmp_path/'bad')


def test_two_games_in_same_experiment_remain_distinct(tmp_path):
    reader,prose,source=fixture(tmp_path)
    first=json.loads(source.read_text());second=json.loads(source.read_text())
    first['evidence_index']['s']['artifact_path']='games/game_001/messages.jsonl'
    second['source']['game_id']='game_002';second['bundle_id']='e:game_002:v1'
    second['evidence_index']['s']['artifact_path']='games/game_002/messages.jsonl'
    first_path=tmp_path/'one.json';first_path.write_text(json.dumps(first))
    second_path=tmp_path/'two.json';second_path.write_text(json.dumps(second))
    reader.write_text(reader.read_text().replace('"artifact_path": "messages.jsonl"','"artifact_path": "games/game_002/messages.jsonl"'))
    prose.write_text(prose.read_text().replace('opening|1|','opening|2|')+'\n\n@@ first-context|1|1901|First game context|a\nCompare the later offer. [abc123]')
    output=tmp_path/'output'
    module.assemble(reader,prose,[('one',first_path),('two',second_path)],output)
    story=json.loads((output/'story.json').read_text())
    assert story['records']['abc123']['explorer']['game']=='two'
    assert [g['source_game_id'] for g in story['games']]==['game_001','game_002']
    assert 'Luna' not in story['provenance']['memory_note']


def test_json_inventory_and_explicit_series_metadata(tmp_path):
    reader,prose,source=fixture(tmp_path)
    inventory=tmp_path/'sources.json'
    inventory.write_text(reader.read_text().split('>',1)[1].split('</script>')[0])
    metadata={'title':'History','subtitle':'A connected account','memory_note':'Use not established.',
              'games':{'g':{'label':'Terra game 1','series':'Terra'}}}
    module.assemble(inventory,prose,[('g',source)],tmp_path/'out',metadata)
    story=json.loads((tmp_path/'out/story.json').read_text())
    assert story['games'][0]['series']=='Terra'
    assert story['games'][0]['label']=='Terra game 1'


def test_graph_export_checks_original_hash_and_missing_citations(tmp_path):
    import hashlib
    import sqlite3
    spec=importlib.util.spec_from_file_location('exporter',Path(__file__).parents[2]/'viewer/scripts/export_campaign_evidence.py')
    exporter=importlib.util.module_from_spec(spec);spec.loader.exec_module(exporter)
    source=tmp_path/'message.json';source.write_text('Original message')
    graph=tmp_path/'evidence.sqlite'
    with sqlite3.connect(graph) as db:
        db.execute('create table sources(id text,artifact_path text,sha256 text)')
        db.execute('create table nodes(id text,source_id text,experiment_id text,game_id text,kind text,title text,text text,pointer text)')
        db.execute('insert into sources values(?,?,?)',('s','message.json',hashlib.sha256(source.read_bytes()).hexdigest()))
        db.execute('insert into nodes values(?,?,?,?,?,?,?,?)',('a'*64,'s','e','game_001','message','Offer','Original message','/line/1'))
    assert exporter.export(graph,{'e':tmp_path},'An offer. ['+'a'*12+']',tmp_path/'out.json')['records']==1
    with pytest.raises(ValueError,match='Unresolved'):exporter.export(graph,{'e':tmp_path},'An offer. ['+'b'*12+']',tmp_path/'bad.json')
    source.write_text('Changed')
    with pytest.raises(ValueError,match='changed'):exporter.export(graph,{'e':tmp_path},'An offer. ['+'a'*12+']',tmp_path/'changed.json')
