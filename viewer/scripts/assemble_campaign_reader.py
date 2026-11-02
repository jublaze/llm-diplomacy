"""Assemble a local campaign story and unchanged factual reports. No model calls."""
from pathlib import Path
import argparse
import hashlib
import json
import re


def digest(data):
    return hashlib.sha256(data).hexdigest()


def parse_blocks(body, records, games):
    blocks = []
    for paragraph in body.strip().split('\n\n'):
        text = paragraph.strip()
        if not text:
            continue
        quote = text.startswith('>')
        prefixes = [text[1:].split('|', 1)[0].strip()] if quote else re.findall(r'\[([A-Za-z0-9:_-]+)\]', text)
        refs = []
        for prefix in prefixes:
            if prefix in records:
                ids = [prefix]
            elif re.fullmatch(r'g\d+:\d{4}(?:_[a-z_]+)?', prefix):
                number, phase = prefix.split(':')
                game = games[int(number[1:])-1]
                phase = phase + '_winter_adjustment' if len(phase) == 4 else phase
                ids = [key for key, record in records.items() if record.get('kind') == 'board'
                       and record.get('experiment_id') == game['experiment_id']
                       and record.get('artifact_path', '').startswith(f"games/{game['source_game_id']}/")
                       and record['artifact_path'].endswith(f'/{phase}_state.json')]
            else:
                ids = [key for key in records if key.startswith(prefix)]
            if len(ids) != 1:
                raise ValueError(f'Unresolved source: {prefix}')
            refs.extend(ids)
        text = text[1:].split('|', 1)[1] if quote else re.sub(r'\s*\[[A-Za-z0-9:_-]+\]', '', text)
        if not refs:
            raise ValueError('Every authored passage requires evidence.')
        if quote and not any(text in records[key]['text'] for key in refs):
            raise ValueError('Unresolved or inexact quotation.')
        blocks.append({'kind': 'quote' if quote else 'paragraph', 'text': text, 'refs': list(dict.fromkeys(refs))})
    return blocks


def assemble(reader, episodes, reports, output, metadata=None):
    if output.exists():
        raise ValueError('Output already exists; choose a new edition directory.')
    html_bytes = reader.read_bytes()
    source_bytes = episodes.read_bytes()
    match = re.search(r'<script[^>]*id="evidence-data"[^>]*>(.*?)</script>', html_bytes.decode('utf-8'), re.S)
    if reader.suffix == '.json':
        records = json.loads(html_bytes)
    elif match:
        records = json.loads(match[1])
    else:
        raise ValueError('Reader has no embedded evidence-data inventory.')
    for record in records.values():
        if record.get('kind') == 'group':
            if not record.get('parts') or any(part not in records for part in record['parts']):
                raise ValueError('Invalid evidence group.')
        elif any(not isinstance(record.get(key), str) for key in ['title', 'text', 'raw', 'experiment_id', 'artifact_path', 'pointer']) or not re.fullmatch(r'[a-f0-9]{64}', record.get('sha256', '')):
            raise ValueError('Invalid story evidence provenance; supply readable text and selected-domain raw extract.')
    games = []
    factual = {}
    for ident, path in reports:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', ident) or ident in factual:
            raise ValueError('Unsafe or duplicate report ID.')
        data = path.read_bytes()
        report = json.loads(data)
        if report['source']['visibility_mode'] != 'omniscient_postgame':
            raise ValueError('This assembly requires explicit full postgame research reports.')
        audience = report.get('extensions', {}).get('audience')
        if audience and not ((audience.get('mode') == 'omniscient_research' and audience.get('approved_for_static_public_distribution') is False) or (audience.get('mode') == 'full_postgame_public' and audience.get('approved_for_static_public_distribution') is True)):
            raise ValueError('This local assembly cannot change a report audience policy.')
        factual[ident] = (path, data, report)
        games.append({'id': ident, 'experiment_id': report['source']['experiment_id'], 'source_game_id': report['source']['game_id'], 'report_sha256': digest(data),
                      'label': (metadata or {}).get('games', {}).get(ident, {}).get('label', f'Game {len(games)+1}'), 'series': (metadata or {}).get('games', {}).get(ident, {}).get('series', 'Campaign'), 'assignments': {p['agent_id']: p['power'] for p in report['roster']}})
    if len({(g['experiment_id'],g['source_game_id']) for g in games}) != len(games):
        raise ValueError('Duplicate experiment/game binding.')
    sections = []
    for chunk in source_bytes.decode('utf-8-sig').replace('\r\n','\n').split('@@ ')[1:]:
        header, body = chunk.split('\n', 1)
        ident, number, years, title, players = header.split('|')
        game = games[int(number)-1]
        blocks = parse_blocks(body, records, games)
        sections.append({'id': ident, 'game': game['id'], 'years': years, 'title': title, 'players': players.split(','), 'blocks': blocks})
    if not sections or len({s['id'] for s in sections}) != len(sections):
        raise ValueError('Missing or duplicate episodes.')
    portraits = []
    for game in games:
        introduction = (metadata or {}).get('games', {}).get(game['id'], {}).get('introduction')
        if introduction:
            game['introduction'] = {'title': introduction['title'], 'blocks': parse_blocks(introduction['body'], records, games)}
    for portrait in (metadata or {}).get('portraits', []):
        scope = [g for g in games if g['series'] == portrait['series'] and portrait['player'] in g['assignments']]
        if not scope or any(p['series'] == portrait['series'] and p['player'] == portrait['player'] for p in portraits):
            raise ValueError('Invalid or duplicate series/player portrait.')
        portraits.append({k: portrait[k] for k in ['series', 'player', 'title']} | {'blocks': parse_blocks(portrait['body'], records, games)})
    if (metadata or {}).get('editorial_complete'):
        expected = {(g['series'], p) for g in games for p in g['assignments']}
        if any(not g.get('introduction', {}).get('blocks') for g in games) or {(p['series'],p['player']) for p in portraits} != expected:
            raise ValueError('Complete editions require each game introduction and series/player portrait.')
    authored = sections + portraits + [g['introduction'] for g in games if g.get('introduction')]
    used = {r for section in authored for block in section['blocks'] for r in block['refs']}
    used.update(part for r in list(used) for part in records[r].get('parts', []))
    records = {key:value for key,value in records.items() if value.get('kind') != 'board' or key in used}
    # Match original record locators to existing explorer targets; never use text similarity.
    matched = 0
    for record in records.values():
        if record.get('kind') == 'group':
            continue
        candidates = [g for g in games if g['experiment_id'] == record['experiment_id']]
        if not candidates:
            raise ValueError('Evidence belongs to an unbound experiment.')
        source_game = record.get('game_id')
        path_game = re.match(r'games/([^/]+)/',record['artifact_path'])
        if path_game:
            if source_game and source_game != path_game[1]:
                raise ValueError('Conflicting source game identity.')
            source_game = path_game[1]
        if source_game:
            candidates = [g for g in candidates if g['source_game_id'] == source_game]
            if not candidates:
                raise ValueError('Evidence belongs to an unbound game.')
        if len(candidates) != 1:
            # An experiment-level manifest or canonical folio is not a game occurrence.
            record.pop('explorer',None)
            continue
        game = candidates[0]
        report = factual[game['id']][2]
        phases = [p['phase_id'] for y in report['years'] for p in y['phases']]
        phase = re.search(r'\d{4}_(?:spring|fall|winter)_(?:negotiation|movement|retreat|adjustment)', record['title']+' '+record['artifact_path'])
        phase_id = phase[0] if phase else phases[0]
        if phase_id not in phases:
            season_prefix = phase_id.rsplit('_',1)[0] + '_'
            phase_id = next((p for p in phases if p.startswith(season_prefix)), phases[0])
        if 'outcome' in record['title'].lower():
            phase_id = phases[-1]
        source_ids = [k for k,s in report['evidence_index'].items() if s['sha256'] == record['sha256']
                      and s['artifact_path'] == record['artifact_path'] and s.get('json_pointer', '') == record['pointer']]
        target = None
        for collection, kind in [('messages','message'), ('private_records','record'), ('orders','order')]:
            candidates = [k for k,v in report[collection].items() if v.get('source_ref') in source_ids]
            if len(candidates) == 1:
                target = {'type': kind, 'id': candidates[0]}
                break
        if not target and len(source_ids) == 1:
            target = {'type': 'source', 'id': source_ids[0]}
        record['explorer'] = {'game': game['id'], 'phase': phase_id, 'target': target}
        matched += bool(target)
    metadata = metadata or {'title':'Campaign history','subtitle':'Negotiations, choices, and consequences.','memory_note':'Memory provenance requires a separate source-based review for this campaign.'}
    if any(not isinstance(metadata.get(k),str) or not metadata[k].strip() for k in ['title','subtitle','memory_note']):
        raise ValueError('Campaign metadata requires a title, subtitle, and reviewed memory note.')
    if metadata.get('publication_policy') and any(not g.get('series') or g['series'] == 'Campaign' for g in games):
        raise ValueError('Public stories require explicit series grouping.')
    for game in games:
        covered = {p for section in sections if section['game'] == game['id'] for p in section['players']}
        if covered != set(game['assignments']):
            raise ValueError('Incomplete or invalid game/player coverage.')
    story = {'schema_version': 'campaign_story_v1', 'title': metadata['title'],
             'subtitle': metadata['subtitle'],
             'audience': 'omniscient_postgame', 'editorial_complete': metadata.get('editorial_complete', False), 'games': games, 'episodes': sections, 'portraits': portraits, 'records': records,
             'provenance': {'reader_sha256': digest(html_bytes), 'authored_source_sha256': digest(source_bytes),
                            'exact_explorer_targets': matched,
                            'memory_note': metadata['memory_note'], 'memory_notes': metadata.get('memory_notes',{})}, 'publication_policy': metadata.get('publication_policy')}
    story_bytes = (json.dumps(story, ensure_ascii=False, separators=(',', ':'))+'\n').encode()
    output.mkdir(parents=True)
    entries = []
    for game in games:
        ident = game['id']
        path, data, report = factual[ident]
        dest = output/'bundles'/ident
        dest.mkdir(parents=True)
        (dest/'report.json').write_bytes(data)
        years = sorted(y['year'] for y in report['years'])
        audience = report.get('extensions', {}).get('audience') or {
            'policy_version': 'legacy_unversioned',
            'reader_description': 'Legacy research bundle without explicit audience metadata; not approved for static public distribution.',
        }
        entries.append({'id': ident, 'source_game_id': report['source']['game_id'], 'experiment_id': game['experiment_id'],
                        'bundle_id': report['bundle_id'], 'label': game['label'], 'title': f"{game['label']} · {years[0]}–{years[-1]}",
                        'years_played': [years[0],years[-1]], 'outcome_label': report['game']['outcome']['termination']['label'],
                        'schema_version': report['schema_version'], 'audience': 'omniscient_postgame',
                        'audience_mode': audience.get('mode','omniscient_research'), 'audience_policy_version': audience['policy_version'],
                        'audience_description': audience['reader_description'],
                        'approved_for_static_public_distribution': audience.get('approved_for_static_public_distribution',False), 'map_id': report['map']['map_id'],
                        'report_url': f'bundles/{ident}/report.json', 'sha256': digest(data), 'fixture': False})
    (output/'story.json').write_bytes(story_bytes)
    catalog = {'schema_version': 'diplomacy_report_catalog_v1', 'reports': entries,
               'campaign_story': {'story_url': 'story.json', 'sha256': digest(story_bytes), 'audience': 'omniscient_postgame', 'publication_policy': metadata.get('publication_policy')}}
    (output/'catalog.json').write_text(json.dumps(catalog,indent=2)+'\n',encoding='utf-8')
    return {'episodes':len(sections), 'games':len(games), 'records':len(records), 'exact_explorer_targets':matched}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reader-html', '--evidence', dest='reader_html', type=Path, required=True, help='Source inventory JSON, or legacy standalone reader HTML.')
    parser.add_argument('--episodes', type=Path, required=True)
    parser.add_argument('--report', action='append', required=True, help='catalog-id=report.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--metadata', type=Path, help='Explicit campaign title, subtitle, and reviewed memory_note JSON.')
    args = parser.parse_args()
    bindings = [(v.split('=',1)[0],Path(v.split('=',1)[1])) for v in args.report]
    metadata = json.loads(args.metadata.read_text(encoding='utf-8-sig')) if args.metadata else None
    print(json.dumps(assemble(args.reader_html,args.episodes,bindings,args.output,metadata)))
