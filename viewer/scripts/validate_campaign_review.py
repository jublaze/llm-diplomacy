"""Check a local editorial ledger. Hashes validate receipts, not human reading."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re

_spec = importlib.util.spec_from_file_location('campaign_evidence_fields', Path(__file__).with_name('export_campaign_evidence.py'))
_fields = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fields)

ROLES = {'identity', 'setup', 'message', 'purpose', 'relationship', 'withheld',
         'rationale', 'explanation', 'passivity', 'reflection', 'order', 'board',
         'memory_preparation', 'persistent_memory_input', 'current_game_memory',
         'cleanup', 'terminal', 'operational'}
GATES = {'sample_factual': {'samples', 'sample_evidence'},
         'sample_literary': {'samples', 'sample_evidence'},
         'full_factual': {'story'}, 'full_literary': {'story'},
         'desktop': {'story', 'html', 'bundle'}, 'mobile': {'story', 'html', 'bundle'}}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(ledger, base, require_gates=False):
    if ledger.get('schema_version') != 'campaign_editorial_review_v2' or ledger.get('value_encoding') != 'canonical_json_utf8_v1':
        raise ValueError('Unknown review ledger schema.')

    verified = set()
    def receipt(value):
        if not isinstance(value, dict) or not value.get('path') or not value.get('sha256'):
            raise ValueError('Missing exact receipt identity.')
        path = Path(value['path'])
        path = path if path.is_absolute() else base / path
        identity = (str(path), value['sha256'])
        if identity not in verified:
            if not path.is_file() or digest(path) != value['sha256']:
                raise ValueError(f'Missing or changed receipt: {path}')
            verified.add(identity)
        return path

    for value in ledger.get('sources', {}).values():
        receipt(value)
    inventory = ledger.get('field_inventory', {})
    dispositions = ledger.get('field_dispositions', {})
    if not inventory or set(inventory) != set(dispositions):
        raise ValueError('Each inventoried field needs one explicit disposition.')
    loaded_source = loaded_line = None
    data = lines = None
    for key, field in inventory.items():
        if field.get('role') not in ROLES:
            raise ValueError(f'Unreviewed field role: {key}')
        if field.get('source') not in ledger['sources'] or not isinstance(field.get('pointer'), str) or not re.fullmatch(r'[a-f0-9]{64}', field.get('value_sha256', '')):
            raise ValueError(f'Incomplete original field identity: {key}')
        source = ledger['sources'][field['source']]
        path = receipt(source)
        try:
            if loaded_source != field['source']:
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != source['sha256']:
                    raise ValueError('Source changed during verification.')
                lines = raw.splitlines() if path.suffix == '.jsonl' else None
                data = raw.decode('utf-8-sig') if path.suffix == '.md' else None if lines is not None else json.loads(raw)
                loaded_source, loaded_line = field['source'], None
            pointer = field['pointer']
            if lines is not None:
                match = re.fullmatch(r'/line/([1-9][0-9]*)(/.*)?', pointer)
                if not match:
                    raise ValueError('Invalid JSONL pointer.')
                if loaded_line != int(match[1]):
                    data = json.loads(lines[int(match[1])-1])
                    loaded_line = int(match[1])
                pointer = match[2] or ''
            try:
                value = data if path.suffix == '.md' and pointer == '' else _fields.read_field(data, pointer)
            except (KeyError, IndexError):
                if field.get('presence') != 'absent':
                    raise
                value_hash = hashlib.sha256(b'absent-member-v1').hexdigest()
            else:
                if field.get('presence') == 'absent':
                    raise ValueError('Purported absent member exists.')
                value_hash = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise ValueError(f'Unresolvable original field: {key}') from error
        if value_hash != field['value_sha256']:
            raise ValueError(f'Changed original field value: {key}')
        review = dispositions[key]
        if review.get('source_sha256') != source['sha256'] or review.get('value_sha256') != value_hash:
            raise ValueError(f'Stale field review binding: {key}')
        if not review.get('reviewer') or not review.get('reason'):
            raise ValueError(f'Missing reviewer or disposition reason: {key}')
        receipt(review.get('receipt'))
        status = review.get('status')
        if field.get('presence') == 'absent' and status not in {'excluded', 'capture_gap'}:
            raise ValueError('Missing source members require an explicit exclusion or capture gap.')
        if status == 'duplicate':
            other = review.get('duplicate_of')
            if other == key or other not in inventory or inventory[other]['value_sha256'] != field['value_sha256']:
                raise ValueError(f'Unverified duplicate: {key}')
            seen = {key}
            while dispositions[other]['status'] == 'duplicate':
                if other in seen:
                    raise ValueError('Duplicate disposition cycle.')
                seen.add(other)
                other = dispositions[other].get('duplicate_of')
                if other not in inventory:
                    raise ValueError('Missing duplicate origin.')
            if dispositions[other]['status'] != 'read':
                raise ValueError('Duplicate must ultimately reference a read field.')
        elif status not in {'read', 'excluded', 'capture_gap'}:
            raise ValueError(f'Pending or unknown field disposition: {key}')
        if status == 'excluded' and field['role'] != 'operational' and not review.get('scope_reason'):
            raise ValueError('Game-facing exclusions need an explicit scope reason.')
    required = {tuple(row) for row in ledger.get('required_player_years', [])}
    coverage = ledger.get('semantic_coverage', [])
    actual = {tuple(row.get('identity', [])) for row in coverage}
    if not required or actual != required or len(actual) != len(coverage):
        raise ValueError('Incomplete or duplicate player/year semantic coverage.')
    for row in coverage:
        if row.get('status') not in {'scene', 'continuity', 'eliminated', 'capture_gap'} or not row.get('reason') or not row.get('reviewer'):
            raise ValueError('Semantic coverage requires a human finding.')
        if row['status'] in {'scene', 'continuity'} and not row.get('episodes'):
            raise ValueError('Covered players need relevant scenes or continuity.')
        receipt(row.get('receipt'))
    artifacts = ledger.get('artifacts', {})
    for value in artifacts.values():
        receipt(value)
    if 'bundle' in artifacts:
        path = receipt(artifacts['bundle'])
        bundle = json.loads(path.read_text(encoding='utf-8-sig'))
        if bundle.get('schema_version') != 'campaign_asset_manifest_v1' or not bundle.get('files'):
            raise ValueError('Invalid bundle asset manifest.')
        seen = set()
        for item in bundle['files']:
            asset = (path.parent / item['path']).resolve()
            if not asset.is_relative_to(path.parent.resolve()) or item['path'] in seen or not asset.is_file() or digest(asset) != item['sha256']:
                raise ValueError('Changed or unsafe bundle asset.')
            seen.add(item['path'])
        actual = {file.relative_to(path.parent).as_posix() for file in path.parent.rglob('*') if file.is_file() and file.resolve() != path.resolve()}
        if actual != seen:
            raise ValueError('Unlisted or missing bundle asset.')
        by_path = {item['path']: item['sha256'] for item in bundle['files']}
        for name in ['story', 'html']:
            entry = bundle.get('entrypoints', {}).get(name)
            if entry not in by_path or name not in artifacts or by_path[entry] != artifacts[name]['sha256']:
                raise ValueError('Bundle entrypoint differs from reviewed artifact.')
    pending = []
    for name, inputs in GATES.items():
        gate = ledger.get('gates', {}).get(name, {})
        if gate.get('status') != 'pass':
            pending.append(name)
            continue
        if not gate.get('reviewer') or not inputs.issubset(gate.get('inputs', {})):
            raise ValueError(f'Incomplete gate: {name}')
        receipt(gate.get('receipt'))
        for key, expected in gate['inputs'].items():
            if key not in artifacts or artifacts[key]['sha256'] != expected:
                raise ValueError(f'Stale gate: {name}/{key}')
    if require_gates and pending:
        raise ValueError('Pending editorial gates: ' + ', '.join(pending))
    return {'fields': len(inventory), 'player_years': len(coverage),
            'field_capture_gaps': sum(r['status'] == 'capture_gap' for r in dispositions.values()),
            'semantic_capture_gaps': sum(r['status'] == 'capture_gap' for r in coverage),
            'pending_gates': pending, 'release_authorized': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ledger', type=Path)
    parser.add_argument('--require-gates', action='store_true')
    args = parser.parse_args()
    print(json.dumps(validate(json.loads(args.ledger.read_text(encoding='utf-8-sig')),
                              args.ledger.parent, args.require_gates)))
