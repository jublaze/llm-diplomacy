"""Package or extract only the source-locked Luna successor release."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

from luna_successor_postgame import verify

MEMBERS = [f'{game}/{name}' for game in ('g2',) for name in ('release-receipt.json', 'report.json')]


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=('package','extract'));p.add_argument('--asset',required=True);p.add_argument('--data-root',required=True);p.add_argument('--lock',required=True);a=p.parse_args()
    asset=Path(a.asset);root=Path(a.data_root);lock=json.loads(Path(a.lock).read_bytes())
    if a.action == 'package':
        for game in ('g2',): verify(game, root/game, lock)
        if asset.exists(): raise ValueError('Asset already exists')
        with zipfile.ZipFile(asset,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
            for name in MEMBERS:
                info=zipfile.ZipInfo(name,date_time=(2026,10,30,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
                archive.writestr(info,(root/name).read_bytes())
    else:
        if hashlib.sha256(asset.read_bytes()).hexdigest()!=lock['asset_sha256']: raise ValueError('Luna successor asset checksum mismatch')
        if root.exists(): raise ValueError('Extraction destination exists')
        with zipfile.ZipFile(asset) as archive:
            if archive.namelist()!=MEMBERS or sum(x.file_size for x in archive.infolist())>40_000_000: raise ValueError('Unexpected archive members or size')
            root.mkdir(parents=True)
            for name in MEMBERS:
                target=root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.read(name))
        for game in ('g2',): verify(game,root/game,lock)
    print(hashlib.sha256(asset.read_bytes()).hexdigest())


if __name__=='__main__': main()
