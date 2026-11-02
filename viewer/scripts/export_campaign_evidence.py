"""Export selected, hash-verified graph extracts for human narrative authoring.

This is an authoring input, not approval to publish a graph or source file.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3


def read_field(data, pointer):
    if not pointer:
        return data
    if pointer.startswith('#decoded'):
        return read_field(json.loads(data), pointer[len('#decoded'):])
    if pointer.startswith('#Legal Order Context'):
        section = data.split('# Legal Order Context', 1)[1].lstrip()
        return read_field(json.JSONDecoder().raw_decode(section)[0], pointer[len('#Legal Order Context'):])
    if not pointer.startswith('/'):
        raise ValueError('Invalid field pointer.')
    token, separator, rest = pointer[1:].partition('/')
    token = token.replace('~1', '/').replace('~0', '~')
    tail = '/' + rest if separator else ''
    # A real JSON-pointer key takes precedence over the encoded-string operator.
    if isinstance(data, dict) and token in data:
        return read_field(data[token], tail)
    if isinstance(data, list) and token.isdecimal():
        return read_field(data[int(token)], tail)
    for marker in ['#decoded', '#Legal Order Context']:
        positions = [match.start() for match in re.finditer(re.escape(marker), token)]
        for position in reversed(positions):
            prefix = token[:position]
            if isinstance(data, dict) and prefix in data:
                return read_field(data[prefix], token[position:] + tail)
    raise KeyError(token)


def source_field(raw, pointer):
    if pointer.startswith('/line/'):
        match = re.fullmatch(r'/line/([1-9][0-9]*)(/.*)?', pointer)
        if not match:
            raise ValueError('Invalid JSONL field pointer.')
        data = json.loads(raw.splitlines()[int(match[1])-1])
        return read_field(data, match[2] or '')
    return read_field(json.loads(raw), pointer)


def reviewed_supplement(pointer, value):
    legal = re.fullmatch(r'/llm_meta/prompt#Legal Order Context/legal_order_signatures_by_unit/[^/]+', pointer)
    returned = re.fullmatch(r'(?:/llm_meta/raw_content|/line/[1-9][0-9]*/response/content)#decoded/(?:explanation|passivity_review|order_rationale|withheld_information|relationship_updates)(?:/.*)?', pointer)
    if not (legal or returned):
        raise ValueError('Supplement must select a game-facing returned field or unit legal catalogue.')
    forbidden = {'_recovery', 'prompt', 'raw_content', 'authorization', 'api_key', 'headers', 'credentials'}
    def check(item):
        if isinstance(item, dict):
            if forbidden.intersection(str(key).lower() for key in item):
                raise ValueError('Supplement contains an operational wrapper.')
            for child in item.values():
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)
    check(value)


def board_excerpt(text):
    board = json.loads(text)
    result = board['adjudication']; state = result['output_state']
    before = board['board_before']['supply_ownership']; after = state['supply_ownership']
    powers = sorted({power for power in after.values() if power})
    lines = ['CENTERS AFTER THIS PHASE', ' · '.join(f'{p}: {list(after.values()).count(p)}' for p in powers),
             '\nOWNERSHIP CHANGES']
    changes = [f'{province.upper()}: {before.get(province) or "unowned"} → {owner}' for province, owner in after.items() if before.get(province) != owner]
    lines.extend(changes or ['No ownership changes.'])
    units = {u['unit_id']: u for u in board['board_before']['units']}
    movements = {o['unit_id']: o['result'] for o in result.get('movement_outcomes', [])}
    supports = {o['unit_id']: o['status'] for o in result.get('support_outcomes', [])}
    lines.append('\nORDERS AND ADJUDICATION')
    for item in result.get('validated_orders', []):
        order = item.get('effective_order'); unit = units.get(item.get('unit_id'), {})
        label = f"{unit.get('owner', '')} {unit.get('type', '')} {unit.get('province', '').upper()}".strip()
        if order:
            payload = ' · '.join(f'{key.replace("_", " ")}: {str(value).upper()}' for key, value in order['payload'].items() if value is not None and key != 'via_convoy')
            status = supports.get(item.get('unit_id')) or movements.get(item.get('unit_id'), '')
            lines.append(f"{label or order.get('power', '')} · {order['type']} · {payload}" + (f' → {status}' if status else ''))
        else:
            lines.append(f"{label} · no effective order: {item.get('reason', item.get('validation', ''))}")
    lines.append('\nRETREATS AND ADJUSTMENTS')
    for key in ['retreat_outcomes', 'adjustment_outcomes']:
        seen = set()
        for item in result.get(key, []):
            unit = units.get(item.get('unit_id'), {})
            power = item.get('power') or unit.get('owner', '')
            origin = item.get('from') or item.get('province') or unit.get('province', '')
            identity = (item.get('unit_id'), power, origin, item['result'], item.get('to'))
            if identity in seen:
                continue
            seen.add(identity)
            lines.append(f"{power} · {origin} · {item['result']}" + (f" → {item['to']}" if item.get('to') else ''))
    lines.append('\nACTIVE UNITS / UNITS AWAITING RETREAT')
    lines.extend(f"{u['owner']} · {u['type']} {u['province'].upper()} · {u['status']}" for u in state['units'] if u['status'] != 'destroyed')
    return '\n'.join(lines)


def export(graph, roots, prose, output, supplemental=None):
    if output.exists():
        raise ValueError('Output exists.')
    refs = set(re.findall(r'\[([A-Za-z0-9:_-]+)\]|^>\s*([A-Za-z0-9:_-]+)\|', prose, re.M))
    prefixes = {a or b for a, b in refs}
    rows = []
    for path in [graph] if isinstance(graph, Path) else graph:
        with sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            rows.extend(dict(r) for r in db.execute('SELECT n.*,s.artifact_path,s.sha256 FROM nodes n JOIN sources s ON n.source_id=s.id'))
    extra_ids = {r['id'] for r in supplemental or []}
    if extra_ids.intersection(r['id'] for r in rows) or len(extra_ids) != len(supplemental or []):
        raise ValueError('Duplicate supplemental evidence ID.')
    rows.extend(supplemental or [])
    ids = {row['id'] for row in rows}
    for prefix in prefixes:
        if re.fullmatch(r'g\d+:\d{4}(?:_[a-z_]+)?', prefix):
            continue
        if prefix not in ids and sum(r['id'].startswith(prefix) for r in rows) != 1:
            raise ValueError(f'Unresolved or ambiguous source: {prefix}')
    resolved = {p if p in ids else next((r['id'] for r in rows if r['id'].startswith(p)), '') for p in prefixes}
    selected = [r for r in rows if r['id'] in resolved or r['kind'] == 'board']
    records = {}
    labels = {'message': 'A message to its named recipient; not shared knowledge.',
              'decision': 'Saved returned rationale; not hidden provider reasoning or proof of execution.',
              'board': 'Board/adjudication record; use settled ownership for center gains.',
              'reflection': 'Saved retrospective account by this player.',
              'input_memory': 'Memory section evidenced in a saved prompt; not proof of subsequent use.'}
    for row in selected:
        root = roots[row['experiment_id']].resolve()
        path = (root / row['artifact_path']).resolve()
        if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Missing, changed or unsafe original source.')
        text = row['text']
        if row['id'] in extra_ids:
            value = source_field(path.read_bytes(), row['pointer'])
            reviewed_supplement(row['pointer'], value)
            expected = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
            if expected != text:
                raise ValueError('Supplemental extract differs from its original returned field.')
        records[row['id']] = {k: row[k] for k in ['kind','title','text','experiment_id','game_id','artifact_path','pointer','sha256']}
        if row['kind'] == 'board':
            records[row['id']]['text'] = board_excerpt(text)
        elif row['id'] in extra_ids and not isinstance(value, str):
            records[row['id']]['text'] = '\n\n'.join(f'{key.replace("_", " ").capitalize()}\n{item}' for key, item in value.items()) if isinstance(value, dict) else '\n'.join(map(str, value))
        records[row['id']].update(context=row.get('context') or labels.get(row['kind'], 'Selected original source extract.'),
                                 raw=json.dumps({'kind':row['kind'],'extract':row['text']},ensure_ascii=False))
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(records,ensure_ascii=False)+'\n',encoding='utf-8')
    return {'records':len(records),'verified_sources':len({(r['experiment_id'],r['artifact_path']) for r in selected})}


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--graph',type=Path,action='append',required=True)
    p.add_argument('--experiment-root',action='append',required=True,help='experiment-id=original-root')
    p.add_argument('--episodes',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--supplement',type=Path,help='Selected returned fields omitted from the graph; exact pointers and hashes required.')
    p.add_argument('--metadata',type=Path,help='Include citations from game introductions and player portraits.')
    a=p.parse_args()
    prose = a.episodes.read_text(encoding='utf-8-sig')
    if a.metadata:
        metadata = json.loads(a.metadata.read_text(encoding='utf-8-sig'))
        prose += '\n' + '\n'.join(g['introduction']['body'] for g in metadata.get('games', {}).values() if g.get('introduction'))
        prose += '\n' + '\n'.join(p['body'] for p in metadata.get('portraits', []))
    print(json.dumps(export(a.graph,dict((x.split('=',1)[0],Path(x.split('=',1)[1])) for x in a.experiment_root),prose,a.output,
                           json.loads(a.supplement.read_text(encoding='utf-8-sig')) if a.supplement else None)))
