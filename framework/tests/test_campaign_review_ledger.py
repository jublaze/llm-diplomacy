import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('review', Path(__file__).parents[2] / 'viewer/scripts/validate_campaign_review.py')
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


def fixture(tmp_path):
    path = tmp_path / 'receipt.txt'
    path.write_text('A source-led review of one player year.')
    receipt = {'path': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    source = tmp_path / 'source.json'
    source.write_text(json.dumps({'explanation':'An original explanation.'}))
    source_receipt = {'path':source.name,'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
    value_hash = hashlib.sha256(json.dumps('An original explanation.').encode()).hexdigest()
    field = {'source': 'source', 'pointer': '/explanation', 'role': 'explanation', 'value_sha256': value_hash}
    bundle = tmp_path / 'bundle';bundle.mkdir(exist_ok=True)
    asset = bundle / 'app.js';asset.write_text('reviewed app')
    (bundle/'index.html').write_bytes(path.read_bytes())
    (bundle/'story.json').write_bytes(path.read_bytes())
    manifest = bundle/'asset-manifest.json';manifest.write_text(json.dumps({'schema_version':'campaign_asset_manifest_v1',
        'entrypoints':{'html':'index.html','story':'story.json'},
        'files':[{'path':file.name,'sha256':hashlib.sha256(file.read_bytes()).hexdigest()} for file in [asset,bundle/'index.html',bundle/'story.json']]}))
    bundle_receipt = {'path':str(manifest.relative_to(tmp_path)),'sha256':hashlib.sha256(manifest.read_bytes()).hexdigest()}
    ledger = {'schema_version': 'campaign_editorial_review_v2', 'value_encoding':'canonical_json_utf8_v1', 'sources': {'source': source_receipt},
              'field_inventory': {'one': field},
              'field_dispositions': {'one': {'status': 'read', 'reviewer': 'independent', 'reason': 'Read the original explanation.', 'receipt': receipt,
                                              'source_sha256':source_receipt['sha256'],'value_sha256':value_hash}},
              'required_player_years': [['experiment', 'game_001', 'birsan', 1901]],
              'semantic_coverage': [{'identity': ['experiment', 'game_001', 'birsan', 1901], 'status': 'scene', 'episodes': ['opening'],
                                     'reviewer': 'independent', 'reason': 'Offer, reply and actual recovery are followed.', 'receipt': receipt}],
              'artifacts': {**{key: receipt for key in ['story', 'html', 'samples', 'sample_evidence']},'bundle':bundle_receipt},
              'gates': {name: {'status': 'pass', 'reviewer': 'independent', 'receipt': receipt,
                               'inputs': {key: receipt['sha256'] for key in inputs}} for name, inputs in review.GATES.items()}}
    for gate in ledger['gates'].values():
        gate['inputs']={key:ledger['artifacts'][key]['sha256'] for key in gate['inputs']}
    return ledger


def test_changed_inventory_and_unmapped_roles_require_review(tmp_path):
    ledger = fixture(tmp_path)
    assert review.validate(ledger, tmp_path, True)['release_authorized'] is False
    ledger['field_inventory']['new-returned-field'] = copy.deepcopy(ledger['field_inventory']['one'])
    with pytest.raises(ValueError, match='disposition'):
        review.validate(ledger, tmp_path)
    del ledger['field_inventory']['new-returned-field']
    ledger['field_inventory']['one']['role'] = 'new_schema_field'
    with pytest.raises(ValueError, match='role'):
        review.validate(ledger, tmp_path)


def test_duplicate_requires_read_origin_and_exact_content(tmp_path):
    ledger = fixture(tmp_path)
    ledger['field_inventory']['two'] = copy.deepcopy(ledger['field_inventory']['one'])
    ledger['field_dispositions']['two'] = dict(ledger['field_dispositions']['one'], status='duplicate', duplicate_of='one')
    review.validate(ledger, tmp_path)
    ledger['field_inventory']['two']['value_sha256'] = 'b' * 64
    with pytest.raises(ValueError, match='Changed original field'):
        review.validate(ledger, tmp_path)


def test_semantic_coverage_and_final_gates_are_separate_from_source_hashes(tmp_path):
    ledger = fixture(tmp_path)
    ledger['semantic_coverage'] = []
    with pytest.raises(ValueError, match='semantic'):
        review.validate(ledger, tmp_path)
    ledger = fixture(tmp_path)
    ledger['gates']['full_literary']['inputs']['story'] = 'b' * 64
    with pytest.raises(ValueError, match='Stale gate'):
        review.validate(ledger, tmp_path)
    ledger['gates']['full_literary'] = {'status': 'pending'}
    assert 'full_literary' in review.validate(ledger, tmp_path)['pending_gates']
    with pytest.raises(ValueError, match='Pending'):
        review.validate(ledger, tmp_path, True)


def test_original_pointer_and_review_value_are_recomputed(tmp_path):
    ledger=fixture(tmp_path)
    ledger['field_inventory']['one']['pointer']='/missing'
    with pytest.raises(ValueError,match='Unresolvable'):
        review.validate(ledger,tmp_path)
    ledger=fixture(tmp_path)
    source=tmp_path/'source.json';source.write_text(json.dumps({'explanation':'A changed explanation.'}))
    ledger['sources']['source']['sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='Changed original field'):
        review.validate(ledger,tmp_path)
    ledger['field_inventory']['one']['value_sha256']=hashlib.sha256(json.dumps('A changed explanation.').encode()).hexdigest()
    with pytest.raises(ValueError,match='Stale field review'):
        review.validate(ledger,tmp_path)


def test_asset_contents_are_checked_beyond_manifest_hash(tmp_path):
    ledger=fixture(tmp_path)
    (tmp_path/'bundle/app.js').write_text('unreviewed app')
    with pytest.raises(ValueError,match='bundle asset'):
        review.validate(ledger,tmp_path,True)


def test_nonhex_digest_and_unlisted_asset_are_rejected(tmp_path):
    ledger=fixture(tmp_path)
    ledger['field_inventory']['one']['value_sha256']='z'*64
    with pytest.raises(ValueError,match='field identity'):
        review.validate(ledger,tmp_path)
    ledger=fixture(tmp_path)
    (tmp_path/'bundle/unlisted.js').write_text('unexpected')
    with pytest.raises(ValueError,match='Unlisted'):
        review.validate(ledger,tmp_path)
