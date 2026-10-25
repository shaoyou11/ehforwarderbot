import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import requests
from health import bot_api_status
class Health(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.config=Path(self.tmp.name)/'config.yaml';self.config.write_text('token: test\nflags:\n  api_base_url: http://local/bot\n')
    def test_only_confirmed_bot_response_is_healthy(self):
        with patch('health.requests.post') as post:
            post.return_value.json.return_value={'ok':True,'result':{'is_bot':True}}
            self.assertEqual(bot_api_status(self.config),'可访问');self.assertEqual(post.call_args.kwargs['timeout'],(2,5))
            post.return_value.json.return_value={'ok':False}
            self.assertIn('检查失败',bot_api_status(self.config))
    def test_timeout_is_not_misreported_as_wechat_logout(self):
        with patch('health.requests.post',side_effect=requests.Timeout):self.assertIn('不等于微信掉线',bot_api_status(self.config))
if __name__=='__main__':unittest.main(verbosity=2)
