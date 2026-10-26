import tempfile
import unittest
from pathlib import Path
from activate_media25 import check_limits,media_floor
from bridge import Store

class Tests(unittest.TestCase):
    def test_ready_matching_nas_25(self):check_limits({'media':25*1024*1024,'store':25*1024*1024})
    def test_reject_previous_or_mismatched_limit(self):
        for value in [{'media':10*1024*1024,'store':25*1024*1024},{'media':25*1024*1024,'store':10*1024*1024}]:
            with self.assertRaises(ValueError):check_limits(value)
    def test_reject_unconfirmed_limit(self):
        with self.assertRaises(ValueError):check_limits({})
    def test_media_floor_prevents_backfill_without_resetting_text_state(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory);s=Store(home);s.set(enabled=1,epoch='10',start_ms=100,cursor_ms=200,images_enabled=1)
            media_floor(home,1000);state=s.get()
            self.assertEqual((state['epoch'],state['start_ms'],state['cursor_ms'],state['enabled'],state['images_enabled']),('10',100,200,1,1))
            self.assertEqual((state['image_start_ms'],state['attachment_start_ms']),(1000,1000))

if __name__=='__main__':unittest.main()
