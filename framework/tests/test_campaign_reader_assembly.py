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


def test_complete_edition_requires_cited_introductions_and_distinct_player_portraits(tmp_path):
    reader,prose,source=fixture(tmp_path)
    metadata={'title':'History','subtitle':'A connected account','memory_note':'Use not established.',
              'editorial_complete':True,'games':{'g':{'label':'Luna game 1','series':'Luna'}}}
    with pytest.raises(ValueError,match='introductions?'):
        module.assemble(reader,prose,[('g',source)],tmp_path/'missing',metadata)
    assert not (tmp_path/'missing').exists()
    metadata['games']['g']['introduction']={'title':'The opening position','body':'A position with an offer. [abc123]'}
    metadata['portraits']=[{'series':'Luna','player':'a','title':'The negotiator','body':'>abc123|A precise offer.'}]
    module.assemble(reader,prose,[('g',source)],tmp_path/'valid',metadata)
    story=json.loads((tmp_path/'valid/story.json').read_text())
    assert story['editorial_complete'] is True
    assert story['games'][0]['introduction']['blocks'][0]['refs']==['abc123']
    assert story['portraits'][0]['blocks'][0]['kind']=='quote'
    metadata['portraits'].append(dict(metadata['portraits'][0]))
    with pytest.raises(ValueError,match='duplicate'):
        module.assemble(reader,prose,[('g',source)],tmp_path/'duplicate',metadata)


def test_context_quotes_resolve_exact_ids_before_shared_prefixes_and_reject_inventions(tmp_path):
    reader,prose,source=fixture(tmp_path)
    data=json.loads(reader.read_text().split('>',1)[1].split('</script>')[0])
    data['abc123-longer']=dict(data['abc123'])
    inventory=tmp_path/'sources.json';inventory.write_text(json.dumps(data))
    metadata={'title':'History','subtitle':'Account','memory_note':'Unverified.',
              'games':{'g':{'label':'Game','series':'Luna','introduction':{'title':'Context','body':'>abc123|A precise offer.'}}}}
    module.assemble(inventory,prose,[('g',source)],tmp_path/'exact',metadata)
    metadata['games']['g']['introduction']['body']='> abc123|A precise offer.'
    module.assemble(inventory,prose,[('g',source)],tmp_path/'spaced-quote',metadata)
    metadata['games']['g']['introduction']['body']='>abc123|An invented offer.'
    with pytest.raises(ValueError,match='inexact'):
        module.assemble(inventory,prose,[('g',source)],tmp_path/'inexact',metadata)
    assert not (tmp_path/'inexact').exists()


def test_supplement_verifies_returned_field_without_exporting_prompt(tmp_path):
    import hashlib
    import sqlite3
    spec=importlib.util.spec_from_file_location('exporter',Path(__file__).parents[2]/'viewer/scripts/export_campaign_evidence.py')
    exporter=importlib.util.module_from_spec(spec);spec.loader.exec_module(exporter)
    original={'llm_meta':{'prompt':'PRIVATE PROMPT MUST STAY LOCAL',
                         'raw_content':json.dumps({'explanation':{'summary':'The promised support was unavailable.'}})}}
    source=tmp_path/'orders.json';source.write_text(json.dumps(original))
    graph=tmp_path/'evidence.sqlite'
    with sqlite3.connect(graph) as db:
        db.execute('create table sources(id text,artifact_path text,sha256 text)')
        db.execute('create table nodes(id text,source_id text,experiment_id text,game_id text,kind text,title text,text text,pointer text)')
    row={'id':'b'*64,'kind':'decision','title':'Returned explanation','experiment_id':'e','game_id':'game_001',
         'artifact_path':'orders.json','pointer':'/llm_meta/raw_content#decoded/explanation/summary',
         'text':'The promised support was unavailable.','sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
    output=tmp_path/'out.json'
    exporter.export([graph],{'e':tmp_path},'The plan changed. ['+'b'*12+']',output,[row])
    assert 'PRIVATE PROMPT' not in output.read_text()
    assert json.loads(output.read_text())[row['id']]['text']==row['text']
    row['text']='An invented explanation.'
    with pytest.raises(ValueError,match='differs'):
        exporter.export(graph,{'e':tmp_path},'Claim. ['+'b'*12+']',tmp_path/'bad.json',[row])
    assert not (tmp_path/'bad.json').exists()


def test_readable_board_keeps_rejections_support_cuts_and_excludes_destroyed_roster_entries():
    spec=importlib.util.spec_from_file_location('exporter',Path(__file__).parents[2]/'viewer/scripts/export_campaign_evidence.py')
    exporter=importlib.util.module_from_spec(spec);spec.loader.exec_module(exporter)
    unit={'unit_id':'u','owner':'austria','province':'ser','type':'A','status':'active'}
    ghost={'unit_id':'old','owner':'russia','province':'rum','type':'F','status':'destroyed'}
    board={'board_before':{'supply_ownership':{'ser':'austria'},'units':[unit,ghost]},
           'adjudication':{'output_state':{'supply_ownership':{'ser':'turkey'},'units':[unit,ghost]},
                           'validated_orders':[{'unit_id':'u','effective_order':{'power':'austria','type':'SUPPORT_HOLD','payload':{'target':'gre'}}},
                                               {'unit_id':'old','effective_order':None,'reason':'missing_retreat_order'}],
                           'support_outcomes':[{'unit_id':'u','status':'cut'}],
                           'retreat_outcomes':[{'unit_id':'old','from':'rum','to':None,'result':'destroyed'},
                                               {'unit_id':'old','from':'rum','to':None,'result':'destroyed','order_id':None}]}}
    text=exporter.board_excerpt(json.dumps(board))
    assert 'SER: austria → turkey' in text
    assert '→ cut' in text and 'missing_retreat_order' in text
    assert 'russia · rum · destroyed' in text
    assert text.count('russia · rum · destroyed')==1
    assert 'russia · F RUM' not in text
    context={'llm_meta':{'prompt':'Preface\n# Legal Order Context\n'+json.dumps({'legal_order_signatures_by_unit':{'u':['HOLD::{}']}})+'\n# Visible Communications\nPrivate'}}
    assert exporter.read_field(context,'/llm_meta/prompt#Legal Order Context/legal_order_signatures_by_unit/u')==['HOLD::{}']
    encoded={'raw_content':json.dumps({'a#decoded':{'x/y~':'literal'}}),'a#decoded':{'x':1}}
    assert exporter.read_field(encoded,'/a#decoded/x')==1
    assert exporter.read_field(encoded,'/raw_content#decoded/a#decoded/x~1y~0')=='literal'


def test_supplement_jsonl_fields_and_wrapper_boundary():
    spec=importlib.util.spec_from_file_location('exporter',Path(__file__).parents[2]/'viewer/scripts/export_campaign_evidence.py')
    exporter=importlib.util.module_from_spec(spec);spec.loader.exec_module(exporter)
    raw=(json.dumps({'response':{'content':json.dumps({'withheld_information':[{'summary':'A concealed attack.'}]})}})+'\n{}').encode()
    pointer='/line/1/response/content#decoded/withheld_information/0/summary'
    value=exporter.source_field(raw,pointer)
    assert value=='A concealed attack.'
    exporter.reviewed_supplement(pointer,value)
    for pointer,value in [('/llm_meta',{'prompt':'private'}),
                          ('/llm_meta/raw_content#decoded/explanation',{'_recovery':{'prompt':'private'}})]:
        with pytest.raises(ValueError,match='Supplement'):
            exporter.reviewed_supplement(pointer,value)


def test_assembly_rejects_legacy_readable_record_without_raw_provenance(tmp_path):
    reader,prose,source=fixture(tmp_path)
    data=json.loads(reader.read_text().split('>',1)[1].split('</script>')[0])
    data['abc123']['raw']=None
    inventory=tmp_path/'legacy.json';inventory.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='provenance'):
        module.assemble(inventory,prose,[('g',source)],tmp_path/'invalid')
    assert not (tmp_path/'invalid').exists()
