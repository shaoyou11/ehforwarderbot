import unittest
from activate_attachments import check,CASES
from attachment_names import filename
class Tests(unittest.TestCase):
    def setUp(self):
        self.cfg={'managed_control':True,'validated_single_imessage_chat':True};self.status={'enabled':True,'ready':True,'images_ready':True,'epoch':10};self.local={'enabled':1,'pending_off':0,'phase':'enabled','epoch':'10'};self.remote={'attachments_enabled':True,'attachments_since':1000,'canonical':[filename(n) for n in CASES]}
    def test_ready(self):check(self.cfg,self.status,self.local,self.remote,'old','old')
    def test_rejects_unready_and_epoch_mismatch(self):
        for change in [{'ready':False},{'images_ready':False},{'epoch':11}]:
            with self.assertRaises(ValueError):check(self.cfg,self.status|change,self.local,self.remote,'old','old')
    def test_rejects_canonical_mismatch_and_disabled_nas_files(self):
        for change in [{'canonical':[]},{'attachments_enabled':False},{'attachments_since':None}]:
            with self.assertRaises(ValueError):check(self.cfg,self.status,self.local,self.remote|change,'old','old')
    def test_rejects_changed_source_or_closed_local(self):
        with self.assertRaises(ValueError):check(self.cfg,self.status,self.local,self.remote,'new','old')
        with self.assertRaises(ValueError):check(self.cfg,self.status,self.local|{'enabled':0},self.remote,'old','old')
