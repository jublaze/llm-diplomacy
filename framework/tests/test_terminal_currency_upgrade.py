import copy
import hashlib
import json
from pathlib import Path
import pytest
import llm_diplomacy.runner.recovery as module
from llm_diplomacy.runner.recovery import ExperimentRecovery, atomic_json, canonical_hash


def fixture(tmp_path):
    root = tmp_path / 'game'
    (root/'recovery').mkdir(parents=True)
    (root/'metrics/budget').mkdir(parents=True)
    pkg = Path(module.__file__).parents[1]
    target = {p.relative_to(pkg).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(pkg.rglob('*')) if p.is_file() and not p.is_symlink()
              and '__pycache__' not in p.parts and p.suffix != '.pyc'}
    source = dict(target)
    source.pop('runner/known_http_reservations.py')
    source.pop('runner/reservation_currency_trace.py')
    source['runner/recovery.py'] = 'old-recovery'
    source['runner/build_rule_accounting.py'] = 'old-accounting'
    before = {'experiment_id':'game','code_sha256':canonical_hash(source),'config':'same'}
    after = {**before,'code_sha256':canonical_hash(target)}
    rows = [{'kind':'created','sequence':0,'sha256':'head0'}, {'kind':'commit_completed','sequence':1,'sha256':'head1'}]
    cp = {'version':1,'identity':before,'cursor':{'unit':'population_reports_completed'},
          'journal_anchor':{'count':2,'head':'head1'}}
    atomic_json(root/'recovery/terminal-currency-source-checkpoint.json',{**cp,'sha256':canonical_hash(cp)})
    (root/'recovery/terminal-currency-source-budget.json').write_bytes(b'budget')
    (root/'metrics/provider_attempts.jsonl').write_bytes(b'ledger\n')
    (root/'metrics/budget/budget_events.jsonl').write_bytes(b'events\n')
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    transition={'sha256':'transition','to_identity':before,'target_code_manifest':source}
    upgrade={'kind':'terminal_currency_accounting_upgrade_v1','sequence':2,'previous_hash':'head1',
             'source_journal_count':2,'source_journal_head':'head1','transition_sha256':'transition',
             'from_identity':before,'to_identity':after,'source_code_manifest':source,'target_code_manifest':target,
             'source_checkpoint_sha256':sha(root/'recovery/terminal-currency-source-checkpoint.json'),
             'source_budget_sha256':sha(root/'recovery/terminal-currency-source-budget.json'),
             'source_ledger_bytes':7,'source_ledger_sha256':sha(root/'metrics/provider_attempts.jsonl'),
             'source_budget_events_bytes':7,'source_budget_events_sha256':sha(root/'metrics/budget/budget_events.jsonl')}
    return ExperimentRecovery(root, identity=after,resume=True),transition,rows+[upgrade]


def test_terminal_receipt_binds_exact_checkpoint_and_package(tmp_path):
    r,t,rows=fixture(tmp_path)
    r._validate_terminal_currency_upgrade(t,rows)


@pytest.mark.parametrize('change',['head','config','manifest','checkpoint','ledger','events','budget','duplicate','nonterminal'])
def test_terminal_receipt_rejects_tampering(tmp_path,change):
    r,t,rows=fixture(tmp_path);rows=copy.deepcopy(rows);u=rows[-1]
    if change=='head': u['source_journal_head']='wrong'
    if change=='config': u['to_identity']['config']='changed'
    if change=='manifest': u['target_code_manifest']['runner/recovery.py']='wrong'
    if change=='checkpoint': u['source_checkpoint_sha256']='wrong'
    if change=='ledger': u['source_ledger_sha256']='wrong'
    if change=='events': u['source_budget_events_sha256']='wrong'
    if change=='budget': u['source_budget_sha256']='wrong'
    if change=='duplicate': rows.append(copy.deepcopy(u))
    if change=='nonterminal':
        p=r.path/'terminal-currency-source-checkpoint.json';cp=json.loads(p.read_bytes());cp.pop('sha256');cp['cursor']['unit']='phase_started';atomic_json(p,{**cp,'sha256':canonical_hash(cp)});u['source_checkpoint_sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
    with pytest.raises(ValueError): r._validate_terminal_currency_upgrade(t,rows)
