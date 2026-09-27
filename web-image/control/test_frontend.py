import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from telegram.ext import ApplicationHandlerStop
from frontend import Frontend
from protocol import atomic_json

class Commands(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.f=Frontend.__new__(Frontend);self.f.root=Path(self.tmp.name);self.f.backend='web';self.f.admins=[1];self.f.secret=b'test';self.f.channel=MagicMock()
        self.u=MagicMock();self.u.effective_user.id=1;self.u.effective_chat.type='private'
        atomic_json(self.f.root/'state.json',{'phase':'idle','active':'web','sync':{'mapped':311,'pending':152}})
    def run_command(self,cmd):
        self.u.effective_message.text='/'+cmd
        with self.assertRaises(ApplicationHandlerStop):self.f.command(self.u,None)
        return self.u.effective_message.reply_text.call_args
    def test_status_has_no_switch_keyboard(self):
        a=self.run_command('status');self.assertNotIn('reply_markup',a.kwargs);self.assertIn('综合状态',a.args[0]);self.assertIn('就绪',a.args[0])
    def test_web_status_is_connection_detail(self):
        self.assertIn('网页版微信连接',self.run_command('web_status').args[0])
    def test_backend_has_switch_keyboard(self):self.assertIn('reply_markup',self.run_command('backend').kwargs)
    def test_native_status_preserves_original_handler(self):
        self.f.backend='comwechat';self.u.effective_message.text='/status';self.f.command(self.u,None);self.u.effective_message.reply_text.assert_not_called()
    def test_native_alias_calls_original_status(self):
        self.f.backend='comwechat';self.run_command('cw_status');self.f.channel.operations_ui.status.assert_called_once()
    def test_common_version_not_intercepted_as_native(self):
        from frontend import NATIVE_ONLY
        self.assertNotIn('version',NATIVE_ONLY)
    def test_private_umask_does_not_hide_status(self):
        import os
        old=os.umask(0o077)
        try:atomic_json(self.f.root/'status.json',{'phase':'idle'})
        finally:os.umask(old)
        self.assertEqual((self.f.root/'status.json').stat().st_mode&0o777,0o644)

if __name__=='__main__':unittest.main(verbosity=2)
