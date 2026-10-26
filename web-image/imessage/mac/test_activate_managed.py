import unittest
from activate_managed import check
class Tests(unittest.TestCase):
    def test_ready_same_source(self):check({'validated_single_imessage_chat':True},{'ready':True},'one','one')
    def test_no_whitelist(self):
        with self.assertRaises(ValueError):check({}, {'ready':True},'one','one')
    def test_backend_down(self):
        with self.assertRaises(ValueError):check({'validated_single_imessage_chat':True},{'ready':False},'one','one')
    def test_live_source_changed(self):
        with self.assertRaises(ValueError):check({'validated_single_imessage_chat':True},{'ready':True},'one','two')
