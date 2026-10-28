"""One reviewed completed Luna release; never runs games or edits raw evidence."""
import argparse
import copy
import hashlib
import json
import re
from pathlib import Path, PurePosixPath

POLICY = 'diplomacy_full_postgame_release_v1'
MODE = 'full_postgame_public'
SOURCE_SHA = '9a29c5ea3380c15710b6d45d9ac289212c98391fac7adef246fe1760e07146f7'
COMPLETION_SHA = '89417852c4b8265d9f1d3e63ed31c0cd79da091b17a3812e369484d23700b872'
CHECKPOINT_SHA = 'c5ffc844d63890801b67fed721674967ac670096853e84870c81fbb7a8243958'
EXPECTED = dict(phases=56, messages=1503, orders=877, reflection=343,
                game_memory=7, persistent_memory=7, roster=7, evidence=2914)
ROOT_KEYS = set('schema_version bundle_id source map game roster summary strategic_arcs years messages orders private_records evidence_index extensions'.split())
ROW_KEYS = {
    'messages': set('channel chronological_position message_id phase_id recipient_agent_ids sender_agent_id source_ref text thread_id visibility'.split()),
    'orders': set('adjudication_source_ref effective_status notation order_type payload power source_ref submitted_source_ref unit unit_id'.split()),
    'private_records': set('agent_id payload record_id record_type source_ref visibility'.split()),
    'evidence_index': set('artifact_path json_pointer kind sha256 source_id visibility'.split()),
}
OMISSIONS = ['game.cost_and_call_metadata', 'game.provenance replaced with release provenance',
             'provider base_url/provider routing diagnostics', 'reflection payload usage',
             'game_memory free_text_reflection archive_path renamed as unavailable raw locator',
             'raw API prompts/responses/errors, journals and runtime files (not exported)']
DESCRIPTION = ('Owner-authorized full postgame release of the completed Luna campaign, '
               'including private-during-play correspondence, reflections and memories. '
               'Visibility labels describe play-time access. Raw operational files are not published.')

def digest(data):
    return hashlib.sha256(data).hexdigest()

def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def require(condition, message):
    if not condition:
        raise ValueError(message)

def safe_path(value):
    p = PurePosixPath(value)
    return bool(value) and not p.is_absolute() and '\\' not in value and ':' not in value and '..' not in p.parts

def scan(value):
    patterns = [r'\bsk-[A-Za-z0-9_-]{20,}', r'\bAKIA[A-Z0-9]{16}\b',
                r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
                r'\bBearer\s+[A-Za-z0-9._-]{16,}',
                r'(?<![A-Za-z0-9])[A-Za-z]:[\\/](?!/)',
                r'/(?:Users|home|tmp|var)/[^\s"<>]+',
                r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b',
                r'https?://[^/\s:@]+:[^/\s@]+@']
    def visit(v, pointer=''):
        if isinstance(v, dict):
            for k, item in v.items():
                require(k.lower() not in {'api_key', 'access_token', 'authorization', 'password', 'raw_prompt', 'raw_response'}, 'Disallowed operational field at ' + pointer)
                visit(item, pointer + '/' + k)
        elif isinstance(v, list):
            for i, item in enumerate(v): visit(item, pointer + '/' + str(i))
        elif isinstance(v, str):
            require(not any(re.search(p, v) for p in patterns), 'Sensitive pattern at ' + pointer)
    visit(value)

def counts(b):
    result = dict(phases=sum(len(y['phases']) for y in b['years']), messages=len(b['messages']),
                  orders=len(b['orders']), roster=len(b['roster']), evidence=len(b['evidence_index']))
    for kind in ['reflection', 'game_memory', 'persistent_memory']:
        result[kind] = sum(x['record_type'] == kind for x in b['private_records'].values())
    return result

def references(b):
    found = {}
    def visit(v, pointer=''):
        if isinstance(v, dict):
            for k, item in v.items():
                at = pointer + '/' + k.replace('~', '~0').replace('/', '~1')
                if k in {'source_ref', 'source_refs', 'chronology_source_ref', 'submitted_source_ref', 'adjudication_source_ref'}:
                    for ref in item if isinstance(item, list) else [item]:
                        if ref is not None:
                            require(isinstance(ref, str) and ref in b['evidence_index'], 'Unresolved evidence reference at ' + at)
                            found.setdefault(ref, []).append(pointer)
                visit(item, at)
        elif isinstance(v, list):
            for i, item in enumerate(v): visit(item, pointer + '/' + str(i))
    visit({k: v for k, v in b.items() if k != 'evidence_index'})
    return {k: sorted(set(v)) for k, v in found.items()}

def prepare(source, completed_root, destination):
    source = Path(source); root = Path(completed_root); destination = Path(destination)
    raw = source.read_bytes()
    require(digest(raw) == SOURCE_SHA, 'Source report is not the reviewed Luna export')
    require(not destination.exists(), 'Destination already exists')
    require(not destination.resolve().is_relative_to(root.resolve()), 'Output overlaps raw experiment')
    summary_path = root / 'run_summary.json'; checkpoint_path = root / 'recovery/checkpoint.json'
    summary = read(summary_path); checkpoint = read(checkpoint_path)
    require(summary.get('status') == 'completed' and summary.get('games_completed') == 1 and summary.get('games_failed') == 0, 'Completed game required')
    require(checkpoint.get('cursor', {}).get('unit') == 'population_reports_completed', 'Terminal cleanup/reports checkpoint required')
    require(digest(summary_path.read_bytes()) == COMPLETION_SHA and digest(checkpoint_path.read_bytes()) == CHECKPOINT_SHA, 'Completion evidence is not the reviewed terminal state')
    b = json.loads(raw); require(set(b) == ROOT_KEYS, 'Unknown top-level source fields')
    require(b['source']['experiment_id'] == 'luna1912-1018-r2' and b['source']['game_id'] == 'game_001', 'Wrong experiment')
    require(counts(b) == EXPECTED, 'Unexpected source record counts')
    for collection, keys in ROW_KEYS.items():
        for row in b[collection].values(): require(set(row) <= keys, 'Unknown source field in ' + collection)
    b = copy.deepcopy(b)
    for key in ['cost_and_call_metadata', 'provenance']: b['game'].pop(key, None)
    b['game']['provenance'] = dict(release_policy=POLICY, source_report_sha256=SOURCE_SHA)
    model_keys = {'api_style', 'provider_name', 'reasoning_effort', 'requested_model', 'requested_model_profile', 'resolved_model'}
    model = b['game']['model_provenance']
    b['game']['model_provenance'] = {k: v for k, v in model.items() if k in model_keys}
    b['game']['per_player_model_provenance'] = {p: {k: v for k, v in model.items() if k in model_keys}
        for p, model in b['game']['per_player_model_provenance'].items()}
    for row in b['private_records'].values():
        if row['record_type'] == 'reflection': row['payload'].pop('usage', None)
        if row['record_type'] == 'game_memory':
            archive = row['payload'].get('free_text_reflection')
            if isinstance(archive, dict) and 'archive_path' in archive:
                locator = archive.pop('archive_path').replace('\\', '/')
                prefix = 'experiments/.luna1912-1018-r2.recovery-work/experiments/luna1912-1018-r2/'
                require(locator.startswith(prefix), 'Unknown reflection archive provenance')
                locator = locator.removeprefix(prefix)
                require(safe_path(locator), 'Unsafe reflection archive locator')
                archive['unpublished_raw_archive_locator'] = locator
                archive['raw_archive_available_publicly'] = False
    b['extensions'] = {k: b['extensions'][k] for k in ['normalization', 'postgame_records']}
    b['extensions']['audience'] = dict(schema_version='diplomacy_report_audience_v1', policy_version=POLICY,
        mode=MODE, reader_description=DESCRIPTION, approved_for_static_public_distribution=True,
        projection=dict(generator='publication/full_postgame.py', source_bundle_id=b['bundle_id'], source_bundle_sha256=SOURCE_SHA))
    targets = references(b)
    for sid, evidence in b['evidence_index'].items():
        require(safe_path(evidence['artifact_path']), 'Unsafe original evidence locator')
        evidence.update(raw_artifact_available=False, normalized_targets=targets.get(sid, []),
                        availability='normalized_content_only' if sid in targets else 'raw_unavailable')
    scan(b)
    destination.mkdir(parents=True)
    (destination / 'report.json').write_bytes(encode(b))
    receipt = dict(policy_version=POLICY, source_report_sha256=SOURCE_SHA,
        source_completion_sha256=digest(summary_path.read_bytes()), source_checkpoint_sha256=digest(checkpoint_path.read_bytes()),
        report_sha256=digest(encode(b)), counts=counts(b), omitted_fields=OMISSIONS,
        visibility_semantics='Private during play; intentionally public after completion.',
        raw_evidence='Not included. Relative original locators and hashes are provenance, not download links.',
        evidence_with_normalized_targets=len(targets), raw_only_evidence=len(b['evidence_index'])-len(targets),
        unresolved_reference_count=0, scan_findings=0)
    (destination / 'release-receipt.json').write_bytes(encode(receipt))
    verify(destination)
    return receipt

def verify(root, lock=None):
    root = Path(root); require({p.name for p in root.iterdir()} == {'report.json', 'release-receipt.json'}, 'Unexpected data files')
    b = read(root/'report.json'); receipt = read(root/'release-receipt.json')
    require(set(b) == ROOT_KEYS, 'Unknown released top-level fields')
    require(receipt['source_report_sha256'] == SOURCE_SHA and receipt['policy_version'] == POLICY, 'Unreviewed source/policy')
    require(receipt['source_completion_sha256'] == COMPLETION_SHA and receipt['source_checkpoint_sha256'] == CHECKPOINT_SHA, 'Wrong terminal provenance')
    require(digest((root/'report.json').read_bytes()) == receipt['report_sha256'], 'Changed released report')
    require(counts(b) == EXPECTED and receipt['counts'] == EXPECTED, 'Missing intended records')
    a = b['extensions']['audience']
    require(a['schema_version'] == 'diplomacy_report_audience_v1' and a['approved_for_static_public_distribution'] is True, 'Missing explicit full-postgame release declaration')
    require(a['policy_version'] == POLICY and a['mode'] == MODE and a['projection']['source_bundle_sha256'] == SOURCE_SHA, 'Release policy/provenance mismatch')
    require(b['source']['visibility_mode'] == 'omniscient_postgame', 'Play-time visibility altered')
    targets = references(b)
    for sid, e in b['evidence_index'].items():
        require(e.get('raw_artifact_available') is False and safe_path(e['artifact_path']), 'Unsafe or misleading raw evidence')
        require(e.get('normalized_targets') == targets.get(sid, []), 'Changed evidence closure')
        require(e.get('availability') == ('normalized_content_only' if sid in targets else 'raw_unavailable'), 'Missing unavailable-evidence disclosure')
    scan(b); scan(receipt)
    if lock:
        expected = read(lock)
        require(receipt['report_sha256'] == expected['report_sha256'], 'Report not pinned in source release lock')
        require(digest((root/'release-receipt.json').read_bytes()) == expected['receipt_sha256'], 'Receipt not pinned in source release lock')
    return receipt

if __name__ == '__main__':
    p=argparse.ArgumentParser(); p.add_argument('action',choices=['prepare','verify']); p.add_argument('--source'); p.add_argument('--completed-root'); p.add_argument('--destination'); p.add_argument('--data-root'); p.add_argument('--lock')
    args=p.parse_args()
    value=prepare(args.source,args.completed_root,args.destination) if args.action=='prepare' else verify(args.data_root,args.lock)
    print(json.dumps(value,indent=2))
