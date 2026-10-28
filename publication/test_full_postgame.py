import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys
import zipfile
from full_postgame import scan, references, verify, prepare, digest, encode

HERE=Path(__file__).parent

class PolicyTests(unittest.TestCase):
    def test_secret_and_local_path_scan(self):
        for value in ['sk-'+'z'*30, 'Bearer '+'q'*20, 'C:\\Users\\test\\raw.txt', '/home/test/private', 'person@example.com']:
            with self.assertRaises(ValueError): scan({'text':value})
        scan({'text':'A Paris HOLD. Private during play.'})

    def test_missing_evidence_is_rejected(self):
        with self.assertRaises(ValueError):references({'evidence_index':{},'orders':{'a':{'source_ref':'absent'}}})

    def setUp(self):
        self.data=os.environ.get('LUNA_RELEASE_DATA')

    def clone(self, root):
        for name in ['report.json','release-receipt.json']:(root/name).write_bytes((Path(self.data)/name).read_bytes())

    def test_locked_release_and_mutations(self):
        if not self.data:self.skipTest('Set LUNA_RELEASE_DATA to the separate reviewed asset')
        verify(self.data,HERE/'luna-release-lock.json')
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);self.clone(root);b=json.loads((root/'report.json').read_text(encoding='utf-8'))
            del b['messages'][next(iter(b['messages']))];(root/'report.json').write_bytes(encode(b))
            with self.assertRaises(ValueError):verify(root,HERE/'luna-release-lock.json')
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);self.clone(root);b=json.loads((root/'report.json').read_text(encoding='utf-8'))
            b['extensions']['audience']['approved_for_static_public_distribution']=False
            (root/'report.json').write_bytes(encode(b));receipt=json.loads((root/'release-receipt.json').read_text());receipt['report_sha256']=digest(encode(b));(root/'release-receipt.json').write_bytes(encode(receipt))
            with self.assertRaises(ValueError):verify(root,HERE/'luna-release-lock.json')

    def test_extra_operational_file_rejected(self):
        if not self.data:self.skipTest('Set LUNA_RELEASE_DATA')
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);self.clone(root);(root/'journal.jsonl').write_text('{}')
            with self.assertRaises(ValueError):verify(root,HERE/'luna-release-lock.json')

    def test_unfinished_game_rejected(self):
        source=os.environ.get('LUNA_SOURCE_REPORT')
        if not source:self.skipTest('Original report available only for offline preparation tests')
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);(root/'run_summary.json').write_text('{"status":"running"}');(root/'recovery').mkdir();(root/'recovery/checkpoint.json').write_text('{}')
            with self.assertRaises(ValueError):prepare(source,root,root.parent/(root.name+'-output'))

    def test_wrong_source_rejected_before_write(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);source=root/'wrong.json';source.write_text('{}')
            with self.assertRaises(ValueError):prepare(source,root,root/'out')
            self.assertFalse((root/'out').exists())

    def test_modified_asset_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);asset=root/'wrong.zip';asset.write_bytes(b'not the reviewed zip')
            result=subprocess.run([sys.executable,str(HERE/'release_asset.py'),'extract','--asset',str(asset),'--data-root',str(root/'out'),'--lock',str(HERE/'luna-release-lock.json')],capture_output=True)
            self.assertNotEqual(result.returncode,0);self.assertFalse((root/'out').exists())

    def test_archive_traversal_rejected_even_before_record_validation(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);asset=root/'traversal.zip'
            with zipfile.ZipFile(asset,'w') as archive:archive.writestr('../escape.txt','blocked')
            lock=root/'lock.json';lock.write_text(json.dumps({'asset_sha256':digest(asset.read_bytes())}))
            result=subprocess.run([sys.executable,str(HERE/'release_asset.py'),'extract','--asset',str(asset),'--data-root',str(root/'out'),'--lock',str(lock)],capture_output=True)
            self.assertNotEqual(result.returncode,0);self.assertFalse((root/'out').exists());self.assertFalse((root/'escape.txt').exists())

if __name__=='__main__':unittest.main()
