import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from campaign_asset import extract, verify, POLICY


class CampaignAssetTests(unittest.TestCase):
    def fixture(self):
        story={'publication_policy':POLICY,'audience':'omniscient_postgame','games':[{'id':'g','report_sha256':'a'*64}],
               'episodes':[{'blocks':[{'kind':'quote','text':'Exact words.','refs':['s']}]}], 'records':{'s':{'text':'Exact words.'}}}
        data=json.dumps(story).encode()
        return data,{'story_sha256':hashlib.sha256(data).hexdigest(),'reports':{'g':'a'*64},'episodes':1,'records':1}

    def test_bytes_bindings_and_quotes(self):
        data,lock=self.fixture()
        verify(data,lock)
        with self.assertRaisesRegex(ValueError,'checksum'):verify(data+b' ',lock)
        bad=dict(lock,reports={'other':'a'*64})
        with self.assertRaisesRegex(ValueError,'bindings'):verify(data,bad)
        s=json.loads(data);s['episodes'][0]['blocks'][0]['text']='Invented.';data=json.dumps(s).encode()
        with self.assertRaisesRegex(ValueError,'quotation'):verify(data,dict(lock,story_sha256=hashlib.sha256(data).hexdigest()))

    def test_exact_zip_members_and_no_overwrite(self):
        data,lock=self.fixture()
        with tempfile.TemporaryDirectory() as d:
            asset=Path(d)/'asset.zip';output=Path(d)/'out'
            for extra in [False,True]:
                with zipfile.ZipFile(asset,'w') as z:
                    z.writestr('story.json',data)
                    if extra:z.writestr('../outside.txt','bad')
                lock.update(asset_bytes=asset.stat().st_size,asset_sha256=hashlib.sha256(asset.read_bytes()).hexdigest(),story_bytes=len(data))
                if extra:
                    with self.assertRaisesRegex(ValueError,'members'):extract(asset,Path(d)/'other',lock)
                else:
                    extract(asset,output,lock)
                    self.assertEqual((output/'story.json').read_bytes(),data)
                    with self.assertRaisesRegex(ValueError,'exists'):extract(asset,output,lock)


if __name__ == '__main__':unittest.main()
