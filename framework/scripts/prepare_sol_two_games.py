"""Prepare a fresh, sealed two-game Sol arm; launching requires explicit owner approval."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from llm_diplomacy.population import PopulationExperimentRunner, load_population_experiment_config
from llm_diplomacy.runner.recovery import RecoveryLock, canonical_hash

PLAYERS = ['birsan', 'dolos', 'metternich', 'shaver', 'major', 'birsan_ii', 'major_ii']
BUDGET = {'max_provider_calls': 6968, 'max_input_tokens': 79678408,
          'max_output_tokens': 222976000, 'max_estimated_cost_usd': 350.0,
          'stop_policy': 'stop_experiment'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_files():
    paths = [p for p in (ROOT/'src/llm_diplomacy').rglob('*')
             if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc']
    paths += [p for p in (ROOT/'maps').rglob('*') if p.is_file()]
    paths += list((ROOT/'config/model_profiles').glob('*.yaml'))
    paths += list((ROOT/'blueprints').glob('*.yaml'))
    paths += [ROOT/n for n in ['diplomacy_primer.md', 'strategic_imperatives.md', 'victory_campaign_doctrine.md']]
    paths += [Path(__file__), ROOT/'scripts/run_population_experiment.py',
              ROOT/'scripts/prepare_sol_second_game.py', ROOT/'scripts/run_sealed_population.py',
              ROOT/'scripts/recover_provider_responses.py', ROOT/'scripts/verify_sol_two_games.py']
    if any(p.is_symlink() for p in paths):
        raise ValueError('sealed inputs may not be symlinks')
    return {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(set(paths))}


def prepare(experiment_id):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', experiment_id):
        raise ValueError('invalid experiment ID')
    arm_id = experiment_id
    experiment_id = arm_id + '-g1'
    destination = ROOT/'experiments'/experiment_id
    if destination.exists():
        raise ValueError('destination exists; choose a fresh experiment ID')
    provider = {'model_profile': 'openai_gpt_6_1_sol', 'reasoning_effort': 'medium',
                'max_output_tokens': 32000, 'timeout_seconds': 600,
                'service_tier': 'default', 'prompt_cache_mode': 'explicit',
                'api_key_env_var': 'LLM_DIPLOMACY_OPENAI_API_KEY'}
    config = {'schema_version': '1.0', 'experiment_id': experiment_id, 'dry_run': True,
        'agent_state_mode': 'fresh',
        'agents': [{'agent_id': p, 'blueprint': str(ROOT/'blueprints'/f'{p.removesuffix("_ii")}.yaml'),
                    'provider_config': dict(provider)} for p in PLAYERS],
        'runtime': {'kind': 'baseline_llm', 'max_ticks': 3, 'llm_agent_ids': PLAYERS},
        'games': {'count': 1, 'max_year': 1912, 'country_assignment': 'fixed', 'seed': 20261016},
        'budget': dict(BUDGET), 'outputs': {'observatory': True, 'summary': True},
        'provider_failure_policy': {'strict_recovery': True,
            'received_invalid_policy': 'repeat_with_feedback_v1', 'max_wall_seconds': 518400},
        'strategic_imperatives_path': str(ROOT/'strategic_imperatives.md'),
        'victory_campaign_doctrine_path': str(ROOT/'victory_campaign_doctrine.md'),
        'diplomacy_primer_path': str(ROOT/'diplomacy_primer.md')}
    destination.mkdir(parents=True)
    path = destination/'prepared.yaml'; path.write_text(yaml.safe_dump(config, sort_keys=False), encoding='utf8')
    second = json.loads(json.dumps(config))
    second.update(experiment_id=arm_id+'-g2', agent_state_mode='reuse')
    second['games'].update(country_assignment='seeded_shuffle', seed=20261028)
    (destination/'second-template.yaml').write_text(yaml.safe_dump(second, sort_keys=False), encoding='utf8')
    runner = PopulationExperimentRunner(config=load_population_experiment_config(path), base_dir=ROOT,
                                       agents_root=destination/'agents')
    plan = runner._planned_assignments([{'agent_id': p} for p in PLAYERS])
    record = {'state': 'PREPARED_NOT_AUTHORIZED', 'config_sha256': digest(path),
              'arm_id': arm_id,
              'source_files': source_files(), 'budget_proposed': BUDGET,
              'planned_assignments': plan, 'model': 'gpt-6.1-sol', 'reasoning_effort': 'medium',
              'second_template_sha256': digest(destination/'second-template.yaml'),
              'next_experiment_id': arm_id+'-g2',
              'execution_entrypoint': str(ROOT/'scripts/run_population_experiment.py')}
    (destination/'preparation.json').write_text(json.dumps(record, indent=2)+'\n', encoding='utf8')
    second_runner = PopulationExperimentRunner(
        config=load_population_experiment_config(destination/'second-template.yaml'), base_dir=ROOT,
        agents_root=ROOT/'experiments'/second['experiment_id']/'agents')
    code_manifest = {name.removeprefix('src/llm_diplomacy/'): value
                     for name, value in record['source_files'].items() if name.startswith('src/llm_diplomacy/')}
    series = {'version': 1, 'arm_id': arm_id, 'state': 'PREPARED_NOT_EXECUTED',
        'model': 'gpt-6.1-sol', 'reasoning_effort': 'medium', 'proposed_shared_budget': BUDGET,
        'proposed_shared_wall_seconds': 518400, 'runtime_code_sha256': canonical_hash(code_manifest),
        'entrypoint_sha256': record['source_files'].get('scripts/run_population_experiment.py'),
        'wrapper_sha256': record['source_files'].get('scripts/run_sealed_population.py'),
        'children': [
            {'ordinal': 1, 'experiment_id': experiment_id, 'root': str(destination), 'game_id': 'game_001',
             'seed': 20261016, 'assignment': plan[0]['assignment'], 'memory_input': 'fresh'},
            {'ordinal': 2, 'experiment_id': second['experiment_id'],
             'root': str(ROOT/'experiments'/second['experiment_id']), 'game_id': 'game_001',
             'seed': 20261028, 'assignment': second_runner._planned_assignments(
                 [{'agent_id': p} for p in PLAYERS])[0]['assignment'],
             'memory_input': 'pending verified first-child terminal handoff'}]}
    (destination/'arm-series.json').write_text(json.dumps(series, indent=2)+'\n', encoding='utf8')
    return destination


def launch(destination, *, owner_reference, approved_usd, approve_live, resume=False):
    destination = Path(destination).resolve(strict=True)
    if destination.parent != (ROOT/'experiments').resolve() or destination.is_symlink():
        raise ValueError('prepared run must be inside this framework experiments directory')
    if not approve_live or not owner_reference.strip():
        raise ValueError('explicit owner initiation and approval reference are required')
    if not math.isfinite(approved_usd) or approved_usd != BUDGET['max_estimated_cost_usd']:
        raise ValueError('approval must match the proposed finite total USD cap')
    with RecoveryLock(destination/'launch-owner.lock'):
        path = destination/'prepared.yaml'
        record = json.loads((destination/'preparation.json').read_text(encoding='utf8'))
        if digest(path) != record['config_sha256'] or source_files() != record['source_files']:
            raise ValueError('prepared config or sealed source changed; prepare again')
        if digest(destination/'second-template.yaml') != record['second_template_sha256']:
            raise ValueError('second-game template changed')
        active = destination/'launch.yaml'; approval_path = destination/'owner-initiation.json'
        config = yaml.safe_load(path.read_text(encoding='utf8')); config['dry_run'] = False
        if resume:
            approval = json.loads(approval_path.read_text(encoding='utf8'))
            if (digest(active) != approval['launch_sha256']
                    or yaml.safe_load(active.read_text(encoding='utf8')) != config
                    or approval.get('preparation_sha256') != digest(destination/'preparation.json')
                    or approval.get('budget') != BUDGET or approval.get('approved_total_usd') != approved_usd
                    or approval.get('model') != 'gpt-6.1-sol' or approval.get('reasoning_effort') != 'medium'):
                raise ValueError('approved launch configuration changed')
        else:
            evidence = any((destination/'recovery'/name).exists()
                           for name in ('checkpoint.json','journal.jsonl','commit_manifest.json'))
            if active.exists() or approval_path.exists() or evidence:
                raise ValueError('launch already consumed; inspect status before a reviewed resume')
            active.write_text(yaml.safe_dump(config, sort_keys=False), encoding='utf8')
            approval = {'owner_reference': owner_reference, 'model': record['model'],
                        'reasoning_effort': 'medium', 'approved_total_usd': approved_usd,
                        'budget': BUDGET, 'preparation_sha256': digest(destination/'preparation.json'),
                        'launch_sha256': digest(active)}
            approval_path.write_text(json.dumps(approval, indent=2)+'\n', encoding='utf8')
            policy = {'schema_version': 1, 'source_experiment_id': destination.name,
                'next_experiment_id': record['next_experiment_id'], 'total_usd': approved_usd,
                'total_calls': BUDGET['max_provider_calls'], 'total_input_tokens': BUDGET['max_input_tokens'],
                'total_output_tokens': BUDGET['max_output_tokens'], 'total_wall_seconds': 518400,
                'source_config_sha256': digest(active), 'template_sha256': record['second_template_sha256'],
                'owner_reference': owner_reference}
            (destination/'arm-policy.json').write_text(json.dumps(policy, indent=2)+'\n', encoding='utf8')
        command = [sys.executable, '-u', '-B', str(ROOT/'scripts/run_population_experiment.py'),
                   '--config', str(active), '--output-root', str(ROOT), '--agents-root', str(destination/'agents')]
        if resume:
            command.append('--resume')
        return subprocess.run(command, cwd=ROOT, check=False).returncode


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    prep = sub.add_parser('prepare'); prep.add_argument('--experiment-id', required=True)
    start = sub.add_parser('launch'); start.add_argument('--run-root', required=True)
    start.add_argument('--owner-reference', required=True); start.add_argument('--approve-total-usd', type=float, required=True)
    start.add_argument('--approve-live-sol-medium', action='store_true'); start.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if args.action == 'prepare':
        print(prepare(args.experiment_id))
    else:
        raise SystemExit(launch(args.run_root, owner_reference=args.owner_reference,
            approved_usd=args.approve_total_usd, approve_live=args.approve_live_sol_medium, resume=args.resume))
