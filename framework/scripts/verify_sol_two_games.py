"""Validate completed Sol children and emit a local series handoff for the viewer."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from llm_diplomacy.population import PopulationExperimentRunner, load_population_experiment_config
from llm_diplomacy.runner.recovery import canonical_hash


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(first, second, arm_id):
    first, second = Path(first).resolve(strict=True), Path(second).resolve(strict=True)
    receipt = json.loads((second/'handoff_receipt.json').read_text(encoding='utf8'))
    if receipt.get('sha256') != canonical_hash({k:v for k,v in receipt.items() if k!='sha256'}):
        raise ValueError('invalid memory handoff receipt')
    if Path(receipt['source_root']).resolve() != first:
        raise ValueError('handoff belongs to another first child')
    children=[]
    for ordinal, run in enumerate((first,second),1):
        cfg=load_population_experiment_config(run/'launch.yaml')
        if cfg.experiment_id != run.name or cfg.game_count != 1:
            raise ValueError('unexpected child identity')
        runner=PopulationExperimentRunner(config=cfg,base_dir=run.parent.parent,agents_root=run/'agents')
        recovery=runner._recovery_store();checkpoint=recovery._read_checkpoint()
        recovery._validate_journal_semantics(recovery._journal_rows())
        if (checkpoint['identity'] != recovery.identity or checkpoint.get('halt_required') or recovery.in_flight()
                or checkpoint['cursor'].get('unit') != 'population_reports_completed'):
            raise ValueError('child lacks a matching terminal source/checkpoint')
        summary=json.loads((run/'population_summary.json').read_text(encoding='utf8'))
        game=summary['games'][0]
        children.append({'ordinal':ordinal,'experiment_id':run.name,'game_id':'game_001',
            'composite_game_id':run.name+'--game_001','root':str(run),'seed':cfg.seed,
            'assignment':game['country_assignment'],'source_identity':checkpoint['identity'],
            'checkpoint_sha256':digest(run/'recovery/checkpoint.json'),
            'journal_sha256':digest(run/'recovery/journal.jsonl'),
            'summary_sha256':digest(run/'population_summary.json'),
            'ledger_sha256':digest(run/'metrics/provider_attempts.jsonl')})
    memory=[]
    expected={item['path']:item['sha256'] for item in receipt['destination_files']}
    for player in sorted(children[1]['assignment'].values()):
        relative=f'agents/{player}/persistent_memory.md'
        incoming=second/'recovery/initial_snapshot/experiments'/second.name/relative
        source=first/relative
        if digest(incoming) != expected[relative] or digest(source) != expected[relative]:
            raise ValueError('incoming memory differs from the recorded parent handoff')
        text=incoming.read_text(encoding='utf8');presented=text if len(text)<=3500 else text[:3497]+'...'
        path=second/f'games/game_001/agents/{player}/runtime/phase_outputs/1901_spring_negotiation_negotiate_tick.json'
        output=json.loads(path.read_text(encoding='utf8'));prompt=output['llm_meta']['prompt']
        section=prompt.split('# Persistent Memory\n',1)[1].split('\n\n# Authoritative Current Board State\n',1)[0]
        if section != presented:
            raise ValueError('recorded child-two prompt did not receive the expected incoming memory')
        memory.append({'player_id':player,'incoming_sha256':digest(incoming),
            'presented_text_sha256':hashlib.sha256(section.encode('utf8')).hexdigest(),
            'incoming_chars':len(text),'presented_chars':len(section),'truncated':len(text)>3500,
            'recorded_prompt_sha256':hashlib.sha256(prompt.encode('utf8')).hexdigest(),
            'outgoing_sha256':digest(second/relative)})
    return {'version':1,'arm_id':arm_id,'state':'COMPLETE_LOCAL_REVIEW_REQUIRED',
            'children':children,'memory_handoff':memory,'handoff_receipt_sha256':digest(second/'handoff_receipt.json'),
            'publication_status':'not published; separate viewer review and approval required'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('first','second','arm-id','output'):p.add_argument('--'+name,required=True)
    args=p.parse_args();result=verify(args.first,args.second,args.arm_id)
    output=Path(args.output)
    with output.open('x',encoding='utf8') as f:json.dump(result,f,indent=2);f.write('\n')
