import tempfile,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
from protocol import atomic_json,read_json
from qr_relay import cleanup
class Cleanup(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.message={'chat_id':1,'message_id':2}
        atomic_json(self.root/'web-login.json',{'phase':'logged_in','messages':[self.message]})
        atomic_json(self.root/'web-health.json',{'ready':True,'wechat_online':True,'updated':100})
    def test_waits_for_ready_and_second_confirmation(self):
        with patch('qr_relay.requests.post') as post:
            self.assertEqual(cleanup(self.root,100),0);self.assertEqual(cleanup(self.root,104),0);post.assert_not_called()
    def test_success_removes_only_tracked_qr(self):
        cleanup(self.root,100)
        with patch('qr_relay.config',return_value={'flags':{'api_base_url':'http://local/bot'},'token':'test'}),patch('qr_relay.requests.post') as post:
            post.return_value.json.return_value={'ok':True}
            self.assertEqual(cleanup(self.root,106),1)
            self.assertEqual(post.call_args.kwargs['json'],self.message)
            self.assertEqual(read_json(self.root/'web-login.json')['messages'],[])
    def test_scanned_but_not_logged_in_is_preserved(self):
        s=read_json(self.root/'web-login.json');s['phase']='waiting_scan';atomic_json(self.root/'web-login.json',s)
        with patch('qr_relay.requests.post') as post:cleanup(self.root,100);post.assert_not_called()
    def test_failed_delete_remains_for_retry(self):
        cleanup(self.root,100)
        with patch('qr_relay.config',return_value={'flags':{'api_base_url':'http://local/bot'},'token':'test'}),patch('qr_relay.requests.post') as post:
            post.return_value.json.return_value={'ok':False,'error_code':500}
            self.assertEqual(cleanup(self.root,106),0)
            self.assertEqual(len(read_json(self.root/'web-login.json')['messages']),1)
if __name__=='__main__':unittest.main(verbosity=2)
