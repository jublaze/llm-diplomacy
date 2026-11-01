"""Verify and extract the separately reviewed narrative asset. No provider calls."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

POLICY = 'diplomacy_campaign_story_release_v1'


def verify(story, lock):
    if hashlib.sha256(story).hexdigest() != lock['story_sha256']:
        raise ValueError('Narrative checksum mismatch.')
    value = json.loads(story)
    if value['publication_policy'] != POLICY or value['audience'] != 'omniscient_postgame':
        raise ValueError('Unapproved narrative policy.')
    if {g['id']:g['report_sha256'] for g in value['games']} != lock['reports']:
        raise ValueError('Narrative report bindings changed.')
    if len(value['episodes']) != lock['episodes'] or len(value['records']) != lock['records']:
        raise ValueError('Narrative inventory changed.')
    for e in value['episodes']:
        for b in e['blocks']:
            if not b['refs'] or any(r not in value['records'] for r in b['refs']):
                raise ValueError('Unresolved source.')
            if b['kind'] == 'quote' and not any(b['text'] in value['records'][r].get('text','') for r in b['refs']):
                raise ValueError('Inexact quotation.')
    return value


def extract(asset, output, lock):
    if output.exists():
        raise ValueError('Output exists.')
    data = asset.read_bytes()
    if len(data) != lock['asset_bytes'] or hashlib.sha256(data).hexdigest() != lock['asset_sha256']:
        raise ValueError('Unreviewed narrative asset.')
    with zipfile.ZipFile(asset) as z:
        if z.namelist() != ['story.json'] or z.getinfo('story.json').file_size != lock['story_bytes']:
            raise ValueError('Unexpected asset members.')
        story = z.read('story.json')
    verify(story,lock)
    output.mkdir(parents=True)
    (output/'story.json').write_bytes(story)


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--asset',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--lock',type=Path,required=True)
    a=p.parse_args()
    extract(a.asset,a.output,json.loads(a.lock.read_text(encoding='utf-8')))
    print('Verified narrative asset extracted.')
