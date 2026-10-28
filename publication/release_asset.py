"""Package deterministic data or extract only a source-locked reviewed asset."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile
from full_postgame import verify

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['package','extract']);p.add_argument('--asset',required=True);p.add_argument('--data-root',required=True);p.add_argument('--lock')
    a=p.parse_args();asset=Path(a.asset);root=Path(a.data_root)
    if a.action=='package':
        verify(root)
        if asset.exists(): raise ValueError('Asset already exists')
        with zipfile.ZipFile(asset,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
            for name in ['release-receipt.json','report.json']:
                info=zipfile.ZipInfo(name,date_time=(2026,10,28,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
                archive.writestr(info,(root/name).read_bytes())
    else:
        lock=json.loads(Path(a.lock).read_text())
        if hashlib.sha256(asset.read_bytes()).hexdigest()!=lock['asset_sha256']:raise ValueError('Asset checksum not pinned by reviewed source')
        if root.exists():raise ValueError('Extraction destination exists')
        with zipfile.ZipFile(asset) as archive:
            if archive.namelist()!=['release-receipt.json','report.json'] or sum(x.file_size for x in archive.infolist())>30_000_000:raise ValueError('Unexpected archive members or size')
            root.mkdir(parents=True)
            for name in archive.namelist():(root/name).write_bytes(archive.read(name))
        verify(root,a.lock)
    print(hashlib.sha256(asset.read_bytes()).hexdigest())

if __name__=='__main__':main()
