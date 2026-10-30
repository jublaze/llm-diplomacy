import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from luna_successor_postgame import verify


class LunaSuccessorReleaseTests(unittest.TestCase):
    def test_locked_releases_and_reject_modified_report(self):
        source = os.environ.get('LUNA_SUCCESSOR_RELEASE_DATA')
        if not source:
            self.skipTest('released Terra data not supplied')
        lock = json.loads((Path(__file__).parent/'luna-successor-release-lock.json').read_bytes())
        for game in ('g2',):
            original = Path(source)/game
            self.assertEqual(verify(game, original, lock)['game'], game)
            with tempfile.TemporaryDirectory() as temp:
                copy = Path(temp)/game
                shutil.copytree(original, copy)
                report = copy/'report.json'
                report.write_bytes(report.read_bytes() + b' ')
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    verify(game, copy, lock)


if __name__ == '__main__': unittest.main()
