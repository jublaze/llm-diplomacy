"""Prepare, never launch, the second Terra game from sealed first-game evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from llm_diplomacy.population import PopulationExperimentRunner, load_population_experiment_config
from llm_diplomacy.runner.recovery import RecoveryLock, canonical_hash
sys.path.insert(0,str(Path(__file__).resolve().parent))
from recover_provider_responses import reconcile_accounting, ReadOnlyBudget


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def remaining_limits(policy, budget):
    keys = {'max_provider_calls': ('total_calls', 'total_provider_calls'),
            'max_input_tokens': ('total_input_tokens', 'budget_admission_estimated_input_tokens'),
            'max_output_tokens': ('total_output_tokens', 'budget_admission_estimated_output_tokens')}
    result = {}
    for field, (total, spent) in keys.items():
        value = budget.get(spent)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError('Missing or invalid arm accounting: '+spent)
        result[field] = policy[total] - value
        if result[field] <= 0:
            raise ValueError('Arm total exhausted: '+field)
    exposure = budget.get('budget_admission_estimated_cost_usd')
    if not isinstance(exposure, (int, float)) or not math.isfinite(exposure) or exposure < 0:
        raise ValueError('Arm USD exposure is unknown or invalid')
    remaining = (Decimal(str(policy['total_usd'])) - Decimal(str(exposure))).quantize(Decimal('.000001'), rounding=ROUND_FLOOR)
    if remaining <= 0:
        raise ValueError('Arm USD total exhausted')
    result['max_estimated_cost_usd'] = float(remaining)
    return result


def verify_receipt(destination, policy_path, template_path, source):
    receipt = json.loads((destination/'handoff_receipt.json').read_text())
    body = {k:v for k,v in receipt.items() if k != 'sha256'}
    if receipt.get('sha256') != canonical_hash(body):
        raise ValueError('Handoff receipt integrity mismatch')
    if receipt['policy_sha256'] != digest(policy_path) or receipt['template_sha256'] != digest(template_path):
        raise ValueError('Handoff policy/template changed')
    if Path(receipt['source_root']) != source:
        raise ValueError('Handoff source changed')
    for record in receipt['source_files']:
        if Path(record['path']).is_absolute() or '..' in Path(record['path']).parts:
            raise ValueError('Unsafe receipt source path')
        if digest(Path(receipt['source_root'])/record['path']) != record['sha256']:
            raise ValueError('Sealed source changed')
    for record in receipt['destination_files']:
        if Path(record['path']).is_absolute() or '..' in Path(record['path']).parts:
            raise ValueError('Unsafe receipt destination path')
        if digest(destination/record['path']) != record['sha256']:
            raise ValueError('Prepared destination changed; never overwrite it')
    return receipt


def prepare(policy_path, template_path, source, destination, no_writer_proof):
    policy_path, template_path, source, destination = map(lambda p: Path(p).resolve(),
                                                        (policy_path, template_path, source, destination))
    if destination == source or source in destination.parents or destination in source.parents:
        raise ValueError('Source and destination must be isolated')
    if destination.exists():
        return verify_receipt(destination, policy_path, template_path, source)
    policy = json.loads(policy_path.read_text())
    if policy['total_usd'] != 175 or policy['total_calls'] != 6968 or policy['total_input_tokens'] != 79678408 or policy['total_output_tokens'] != 222976000 or policy['total_wall_seconds'] != 518400:
        raise ValueError('Policy differs from the approved Terra arm totals')
    if policy['source_config_sha256'] != digest(source/'launch.yaml') or policy['template_sha256'] != digest(template_path):
        raise ValueError('Reviewed config/template changed')
    if source.name != policy['source_experiment_id'] or destination != source.parent/policy['next_experiment_id']:
        raise ValueError('Source/destination identities do not match the arm policy')
    if list(destination.parent.glob(f'.{destination.name}.handoff-*')):
        raise ValueError('Partial handoff evidence exists; review it before retrying')
    proof_path = Path(no_writer_proof).resolve()
    proof = json.loads(proof_path.read_text())
    age = (datetime.now(UTC)-datetime.fromisoformat(proof['verified_utc'])).total_seconds()
    if not -5 <= age <= 120 or proof.get('source_experiment_id') != policy['source_experiment_id'] or proof.get('exact_writers') != [] or proof.get('escalated_bounded_inventory_verified') is not True:
        raise ValueError('Fresh parent-verified exact-writer absence is required')
    config = load_population_experiment_config(source/'launch.yaml')
    if config.experiment_id != policy['source_experiment_id'] or config.game_count != 1:
        raise ValueError('Unexpected first-game identity/count')
    runner = PopulationExperimentRunner(config=config,base_dir=source.parents[1],agents_root=source/'agents')
    recovery = runner._recovery_store()
    with RecoveryLock(recovery.path/'writer.lock'):
        if destination.exists():
            return verify_receipt(destination, policy_path, template_path, source)
        status = recovery.status()
        checkpoint = recovery._read_checkpoint()
        if not status['checkpoint_valid'] or status.get('in_flight') or recovery.safe_halt_path.exists() or checkpoint.get('cursor',{}).get('unit') != 'population_reports_completed' or (recovery.path/'commit_manifest.json').exists():
            raise ValueError('First game is not terminal, settled and fully committed')
        summary = json.loads((source/'run_summary.json').read_text())
        if summary.get('status') != 'completed' or summary.get('games_completed') != 1:
            raise ValueError('First-game reports are incomplete')
        state_path = source/'metrics/budget/budget_state.json'
        persisted = json.loads(state_path.read_text())
        controller = ReadOnlyBudget.from_raw(schema_version=config.schema_version,experiment_id=config.experiment_id,
            artifact_root=state_path.parent,raw=config.budget)
        if controller is None:
            raise ValueError('First-game budget configuration is missing')
        budget = controller.status()
        for key in ('budget_admission_estimated_input_tokens','budget_admission_estimated_output_tokens','budget_admission_estimated_cost_usd'):
            if persisted.get(key) != budget[key]:
                raise ValueError('Stored admission exposure differs from validated known plus reserves')
        ledger = [json.loads(line) for line in (source/'metrics/provider_attempts.jsonl').read_text().splitlines() if line.strip()]
        if sum(r.get('kind') == 'request' for r in ledger) != budget.get('total_provider_calls'):
            raise ValueError('First-game calls/ledger do not reconcile')
        accounting_proof = reconcile_accounting(runner,recovery,dry_run=True,preserve_handled_http=True)
        limits = remaining_limits(policy,budget)
        first = recovery._journal_rows()[0]
        deadline = datetime.fromisoformat(first['started_at'])+timedelta(seconds=policy['total_wall_seconds'])
        seconds = math.floor((deadline-datetime.now(UTC)).total_seconds())
        if seconds <= 0:
            raise ValueError('Terra arm wall-time exhausted')
        next_config = yaml.safe_load(template_path.read_text())
        if next_config['experiment_id'] != policy['next_experiment_id'] or next_config['games']['count'] != 1 or next_config['games']['country_assignment'] != 'fixed' or next_config['agent_state_mode'] != 'reuse':
            raise ValueError('Unexpected second-game identity/assignment/state mode')
        if next_config['agents'] != config.raw['agents']:
            raise ValueError('Player identities/provider settings changed')
        for player in config.players:
            provider = runner._player_provider(player)
            if provider.get('model') != 'gpt-6.1-terra' or provider.get('reasoning_effort') != 'medium' or provider.get('service_tier') != 'default' or provider.get('prompt_cache_mode') != 'explicit':
                raise ValueError('Exact Terra 6.1 medium Standard/no-cache is required')
        next_config['dry_run'] = False
        next_config['budget'].update(limits)
        next_config['provider_failure_policy'].update(max_wall_seconds=seconds,deadline_utc=deadline.isoformat())
        staged = destination.parent/f'.{destination.name}.handoff-{uuid.uuid4().hex}'
        staged.mkdir(parents=True,exist_ok=False)
        source_paths = ['launch.yaml','recovery/checkpoint.json','recovery/journal.jsonl',
                        'metrics/budget/budget_state.json','metrics/provider_attempts.jsonl','run_summary.json']
        destination_paths = ['launch.yaml']
        for player in config.players:
            for directory in ('memory','games'):
                (staged/'agents'/player.agent_id/directory).mkdir(parents=True,exist_ok=True)
            for name in ('manifest.json','soul.md','public_profile.md','persistent_memory.md'):
                relative = f'agents/{player.agent_id}/{name}'
                src = source/relative
                if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in [src,*src.parents] if p != source.parent):
                    raise ValueError('Folio source links are not allowed')
                (staged/relative).write_bytes(src.read_bytes())
                source_paths.append(relative);destination_paths.append(relative)
        (staged/'launch.yaml').write_text(yaml.safe_dump(next_config,sort_keys=False),encoding='utf-8')
        receipt = {'schema_version':1,'state':'PREPARED_PARENT_LAUNCH_ONLY','source_root':str(source),
            'policy_sha256':digest(policy_path),'template_sha256':digest(template_path),'no_writer_proof_sha256':digest(proof_path),
            'shared_total_usd':175,'source_admission_exposure':budget['budget_admission_estimated_cost_usd'],
            'remaining_limits':limits,'arm_deadline_utc':deadline.isoformat(),'accounting_proof':accounting_proof,
            'source_files':[{'path':p,'sha256':digest(source/p)} for p in source_paths],
            'destination_files':[{'path':p,'sha256':digest(staged/p)} for p in destination_paths]}
        receipt['sha256']=canonical_hash(receipt)
        (staged/'handoff_receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
        for path in staged.rglob('*'):
            if path.is_file():
                with path.open('r+b') as handle: os.fsync(handle.fileno())
        os.replace(staged,destination)
        return verify_receipt(destination,policy_path,template_path,source)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('policy','template','source','destination','no-writer-proof'): parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    receipt=prepare(args.policy,args.template,args.source,args.destination,args.no_writer_proof)
    print(json.dumps({'state':receipt['state'],'remaining_limits':receipt['remaining_limits'],'sha256':receipt['sha256']}))
