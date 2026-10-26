import unittest
from activate_images import check,TEXT_BASE_SHA
class Tests(unittest.TestCase):
    def test_not_ready_is_rejected(self):
        for status in [{'ready':True},{'ready':False,'images_ready':True}]:
            with self.assertRaises(ValueError):check({'validated_single_imessage_chat':True},status,TEXT_BASE_SHA,'candidate')
    def test_unknown_live_source_is_rejected(self):
        with self.assertRaises(ValueError):check({'validated_single_imessage_chat':True},{'ready':True,'images_ready':True},'different-source','candidate')
    def test_whitelist_validation_is_required(self):
        with self.assertRaises(ValueError):check({}, {'ready':True,'images_ready':True},TEXT_BASE_SHA,'candidate')
    def test_reviewed_sources_allowed(self):
        for digest in [TEXT_BASE_SHA,'candidate']:check({'validated_single_imessage_chat':True},{'ready':True,'images_ready':True},digest,'candidate')
if __name__=='__main__':unittest.main()
