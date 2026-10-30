"""Prepare source-locked completed Terra reports for deliberate postgame release."""
import argparse
import copy
import json
from pathlib import Path

import full_postgame as common

POLICY = 'diplomacy_terra_postgame_release_v1'
SOURCES = {
    'g1': {'experiment': 'terra61-1028-g1', 'source': 'aa1e43c7d00eda2e8fbb587deaed4550a17a0dd947459725cb69ef0a3c5a80f4',
           'summary': 'dec50c49d3bc9f87ec9041ad460f8d9a9b336fb08370486089c55828cb266804',
           'checkpoint': 'e15e18e6038535c3362015e0bd2261a3f32639f41c355274f66b735aec8f3a31',
           'counts': dict(phases=56, messages=2651, orders=866, roster=7, evidence=4100, reflection=392, game_memory=7, persistent_memory=7)},
    'g2': {'experiment': 'terra61-1028-g2', 'source': 'd34ccccdf664c1127069664e64688875f81ea0034d8e1dc4ecb2ac95f3edfd76',
           'summary': '7ef63bceb256ea9c1cd0f121122f81ae690df440bb7a866d23023a679c6cb91b',
           'checkpoint': 'a0ef205506fe829f132a0807c329b49ab9ca4d5ca22890a0420c7e1ed21cf536',
           'counts': dict(phases=56, messages=2669, orders=886, roster=7, evidence=4127, reflection=381, game_memory=7, persistent_memory=7)},
}


def prepare(game, source, completed_root, destination):
    spec = SOURCES[game]; source = Path(source); root = Path(completed_root); destination = Path(destination)
    raw = source.read_bytes()
    common.require(common.digest(raw) == spec['source'], 'Source report differs from reviewed Terra export')
    common.require(not destination.exists() and not destination.resolve().is_relative_to(root.resolve()), 'Unsafe destination')
    summary_path = root / 'run_summary.json'; checkpoint_path = root / 'recovery/checkpoint.json'
    summary = common.read(summary_path); checkpoint = common.read(checkpoint_path)
    common.require(common.digest(summary_path.read_bytes()) == spec['summary'] and common.digest(checkpoint_path.read_bytes()) == spec['checkpoint'], 'Terminal evidence changed')
    common.require(summary.get('status') == 'completed' and summary.get('games_completed') == 1 and summary.get('games_failed') == 0 and checkpoint.get('cursor', {}).get('unit') == 'population_reports_completed', 'Completed game required')
    b = json.loads(raw)
    common.require(set(b) == common.ROOT_KEYS and b['source']['experiment_id'] == spec['experiment'] and b['source']['game_id'] == 'game_001' and common.counts(b) == spec['counts'], 'Source identity or record counts changed')
    for collection, keys in common.ROW_KEYS.items():
        for row in b[collection].values(): common.require(set(row) <= keys, 'Unknown source field in ' + collection)
    b = copy.deepcopy(b)
    for key in ['cost_and_call_metadata', 'provenance']: b['game'].pop(key, None)
    b['game']['title'] = 'Terra comparison — game 1 (fresh)' if game == 'g1' else 'Terra comparison — game 2 (carried memory)'
    b['game']['provenance'] = dict(release_policy=POLICY, source_report_sha256=spec['source'])
    model_keys = {'api_style', 'provider_name', 'reasoning_effort', 'requested_model', 'requested_model_profile', 'resolved_model'}
    b['game']['model_provenance'] = {k: v for k, v in b['game']['model_provenance'].items() if k in model_keys}
    b['game']['per_player_model_provenance'] = {p: {k: v for k, v in m.items() if k in model_keys} for p, m in b['game']['per_player_model_provenance'].items()}
    for row in b['private_records'].values():
        if row['record_type'] == 'reflection': row['payload'].pop('usage', None)
        if row['record_type'] == 'game_memory':
            archive = row['payload'].get('free_text_reflection')
            if isinstance(archive, dict) and 'archive_path' in archive:
                locator = archive.pop('archive_path').replace('\\', '/')
                marker = f"/experiments/.{spec['experiment']}.recovery-work/experiments/{spec['experiment']}/"
                common.require(locator.count(marker) == 1, 'Unknown reflection archive provenance')
                locator = locator.split(marker, 1)[1]
                common.require(common.safe_path(locator), 'Unsafe reflection archive locator')
                archive['unpublished_raw_archive_locator'] = locator; archive['raw_archive_available_publicly'] = False
    b['extensions'] = {k: b['extensions'][k] for k in ['normalization', 'postgame_records']}
    b['extensions']['audience'] = dict(schema_version='diplomacy_report_audience_v1', policy_version=POLICY,
        mode='full_postgame_public', reader_description='Completed Terra game: private during play, intentionally released after completion.',
        approved_for_static_public_distribution=True, projection=dict(generator='publication/terra_postgame.py', source_bundle_id=b['bundle_id'], source_bundle_sha256=spec['source']))
    targets = common.references(b)
    for sid, evidence in b['evidence_index'].items():
        common.require(common.safe_path(evidence['artifact_path']), 'Unsafe evidence locator')
        evidence.update(raw_artifact_available=False, normalized_targets=targets.get(sid, []), availability='normalized_content_only' if sid in targets else 'raw_unavailable')
    common.scan(b)
    report = common.encode(b)
    receipt = dict(policy_version=POLICY, game=game, experiment_id=spec['experiment'], source_report_sha256=spec['source'],
        source_completion_sha256=spec['summary'], source_checkpoint_sha256=spec['checkpoint'], report_sha256=common.digest(report),
        counts=common.counts(b), omitted_fields=common.OMISSIONS, display_title=b['game']['title'], visibility_semantics='Private during play; intentionally public after completion.',
        raw_evidence='Not included. Relative original locators and hashes are provenance, not download links.',
        evidence_with_normalized_targets=len(targets), raw_only_evidence=len(b['evidence_index'])-len(targets), unresolved_reference_count=0, scan_findings=0)
    common.scan(receipt); destination.mkdir(parents=True); (destination/'report.json').write_bytes(report); (destination/'release-receipt.json').write_bytes(common.encode(receipt))
    return receipt


def verify(game, directory, lock):
    spec = SOURCES[game]; directory = Path(directory); locked = lock['games'][game]
    common.require({p.name for p in directory.iterdir()} == {'report.json', 'release-receipt.json'}, 'Unexpected Terra release files')
    report = common.read(directory/'report.json'); receipt = common.read(directory/'release-receipt.json')
    common.require(common.digest((directory/'report.json').read_bytes()) == locked['report_sha256'] and common.digest((directory/'release-receipt.json').read_bytes()) == locked['receipt_sha256'], 'Terra release checksum mismatch')
    common.require(set(report) == common.ROOT_KEYS and common.counts(report) == spec['counts'] and receipt['counts'] == spec['counts'], 'Terra release records changed')
    common.require(receipt['game'] == game and receipt['experiment_id'] == spec['experiment'] and receipt['source_report_sha256'] == spec['source'] and receipt['source_completion_sha256'] == spec['summary'] and receipt['source_checkpoint_sha256'] == spec['checkpoint'], 'Terra provenance mismatch')
    audience = report['extensions']['audience']; projection = audience['projection']
    common.require(audience['policy_version'] == POLICY and audience['mode'] == 'full_postgame_public' and audience['approved_for_static_public_distribution'] is True and projection['generator'] == 'publication/terra_postgame.py' and projection['source_bundle_sha256'] == spec['source'] and projection['source_bundle_id'] == report['bundle_id'] and report['source']['experiment_id'] == spec['experiment'] and report['source']['visibility_mode'] == 'omniscient_postgame', 'Terra audience provenance mismatch')
    targets = common.references(report)
    for sid, evidence in report['evidence_index'].items():
        common.require(common.safe_path(evidence['artifact_path']) and evidence.get('raw_artifact_available') is False and evidence.get('normalized_targets') == targets.get(sid, []) and evidence.get('availability') == ('normalized_content_only' if sid in targets else 'raw_unavailable'), 'Terra evidence closure changed')
    common.scan(report); common.scan(receipt)
    return receipt


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=('prepare','verify'));p.add_argument('--game',choices=SOURCES,required=True);p.add_argument('--source');p.add_argument('--completed-root');p.add_argument('--destination');p.add_argument('--data-root');p.add_argument('--lock')
    a=p.parse_args();result=prepare(a.game,a.source,a.completed_root,a.destination) if a.action=='prepare' else verify(a.game,a.data_root,common.read(a.lock));print(json.dumps(result,indent=2))
