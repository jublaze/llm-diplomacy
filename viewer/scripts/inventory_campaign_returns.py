"""Inventory retained authored returns without reading, executing, or exporting them."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT_ROLES = {'global_messages': 'message', 'private_messages': 'message',
              'relationship_updates': 'relationship', 'withheld_information': 'withheld',
              'orders': 'order', 'order_rationale': 'rationale', 'explanation': 'explanation',
              'passivity_review': 'passivity', 'reflection': 'reflection', 'reflection_text': 'reflection',
              'persistent_memory_update': 'cleanup', 'public_profile_update': 'identity',
              'action_lessons': 'reflection', 'objective_update': 'reflection',
              'memory_candidates': 'reflection', 'phase_summary': 'reflection',
              'strategic_position': 'reflection', 'belief_summary': 'reflection',
              'outcome_review': 'reflection', 'campaign_plan': 'reflection',
              'commitment_memory': 'reflection', 'winter_assessment': 'reflection',
              'belief_updates': 'reflection', 'schema_version': 'operational'}


def sha(value):
    return hashlib.sha256(value).hexdigest()


def inventory(experiments):
    sources, fields = {}, {}
    def source(experiment, root, path):
        resolved = path.resolve()
        if not resolved.is_relative_to(root.resolve()):
            raise ValueError('Original source escapes experiment root.')
        raw = resolved.read_bytes()
        key = sha((experiment + '/' + path.relative_to(root).as_posix()).encode())
        sources[key] = {'path': str(resolved), 'sha256': sha(raw), 'experiment': experiment}
        return key, raw

    def add(source_id, pointer, value, role, provenance, presence='present'):
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
        if presence == 'absent':
            raw = b'absent-member-v1'
        key = sha((source_id + pointer).encode())
        fields[key] = {'source': source_id, 'pointer': pointer, 'value_sha256': sha(raw),
                       'role': role, 'provenance': provenance, 'presence': presence}

    def walk(source_id, pointer, value, role, provenance):
        if role == 'operational':
            add(source_id, pointer, value, role, provenance)
            return
        # Hash an operational recovery subtree as one declared exclusion candidate.
        if isinstance(value, dict) and value:
            for key, item in value.items():
                token = key.replace('~', '~0').replace('/', '~1')
                child_role = 'purpose' if key == 'purpose' else 'operational' if key == '_recovery' else role
                if key == '_recovery':
                    add(source_id, pointer + '/' + token, item, child_role, provenance)
                else:
                    walk(source_id, pointer + '/' + token, item, child_role, provenance)
        elif isinstance(value, list) and value:
            for index, item in enumerate(value):
                walk(source_id, pointer + '/' + str(index), item, role, provenance)
        else:
            add(source_id, pointer, value, role, provenance)

    def domain(source_id, pointer, value, provenance, free_reflection=False):
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            add(source_id, pointer, value, 'reflection' if free_reflection else 'unmapped', provenance)
            return
        if isinstance(decoded, dict):
            if not decoded:
                add(source_id, pointer + '#decoded', decoded, 'unmapped', provenance)
            for key, item in decoded.items():
                token = key.replace('~', '~0').replace('/', '~1')
                walk(source_id, pointer + '#decoded/' + token, item, ROOT_ROLES.get(key, 'unmapped'), provenance)
        else:
            add(source_id, pointer + '#decoded', decoded, 'unmapped', provenance)

    for experiment, root in experiments.items():
        if not root.is_dir():
            raise ValueError('Missing experiment root.')
        for path in sorted(root.glob('games/*/agents/*/runtime/phase_outputs/*.json')):
            source_id, raw = source(experiment, root, path)
            value = json.loads(raw)
            meta = value.get('llm_meta') if isinstance(value.get('llm_meta'), dict) else {}
            content = meta.get('raw_content')
            if 'raw_content' in meta:
                domain(source_id, '/llm_meta/raw_content', content, {'state': 'saved_final_return'}, 'reflect' in path.name)
            else:
                add(source_id, '/llm_meta/raw_content', None, 'operational', {'state': 'missing_saved_raw_return'}, 'absent')
            result = value.get('result')
            if isinstance(result, dict):
                if not result:
                    add(source_id, '/result', result, 'unmapped', {'state': 'empty_saved_result_copy'})
                for key, item in result.items():
                    role = {'messages': 'message', 'usage': 'operational', 'ok': 'operational'}.get(key, ROOT_ROLES.get(key, 'unmapped'))
                    token = key.replace('~', '~0').replace('/', '~1')
                    walk(source_id, '/result/' + token, item, role, {'state': 'saved_result_copy'})
            elif 'result' in value:
                add(source_id, '/result', result, 'unmapped', {'state': 'nonobject_saved_result_copy'})
            else:
                add(source_id, '/result', None, 'operational', {'state': 'missing_saved_result_copy'}, 'absent')
        path = root / 'recovery/journal.jsonl'
        if not path.is_file():
            raise ValueError('Missing retained journal; record a capture gap rather than assume no earlier returns.')
        source_id, raw = source(experiment, root, path)
        lines = [json.loads(line) for line in raw.splitlines()]
        validation = {row['attempt_id']: row.get('valid') for row in lines if row.get('kind') == 'response_validation' and row.get('attempt_id')}
        for line, row in enumerate(lines, 1):
            if row.get('kind') != 'attempt':
                continue
            response = row.get('response')
            provenance = {'state': 'retained_attempt', 'decision_id': row.get('decision_id'),
                          'attempt_id': row.get('attempt_id'), 'valid': validation.get(row.get('attempt_id'))}
            if isinstance(response, dict) and 'content' in response:
                domain(source_id, f'/line/{line}/response/content', response['content'], provenance,
                       'reflect' in (row.get('decision_id') or ''))
            else:
                pointer = f'/line/{line}/response/content' if isinstance(response, dict) else f'/line/{line}/response'
                presence = 'absent' if isinstance(response, dict) or 'response' not in row else 'present'
                add(source_id, pointer, response, 'operational', provenance, presence)
    return {'schema_version': 'campaign_return_inventory_v2', 'value_encoding': 'canonical_json_utf8_v1', 'sources': sources,
            'field_inventory': fields, 'unmapped_fields': [key for key, field in fields.items() if field['role'] == 'unmapped']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment', action='append', required=True, help='experiment-id=original-root')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Choose a new inventory path.')
    experiments = {value.split('=', 1)[0]: Path(value.split('=', 1)[1]) for value in args.experiment}
    if any(args.output.resolve().is_relative_to(root.resolve()) for root in experiments.values()):
        raise ValueError('Write inventory outside immutable experiment roots.')
    result = inventory(experiments)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    print(json.dumps({'sources': len(result['sources']), 'fields': len(result['field_inventory']), 'unmapped': len(result['unmapped_fields'])}))
