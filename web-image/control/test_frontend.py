import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock,create_autospec,patch
from telegram import Bot
from telegram.ext import ApplicationHandlerStop
from frontend import Frontend
from protocol import atomic_json

class Commands(unittest.TestCase):
    def setUp(self):
        api=patch('frontend.bot_api_status',return_value='可访问');api.start();self.addCleanup(api.stop)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.f=Frontend.__new__(Frontend);self.f.root=Path(self.tmp.name);self.f.backend='web';self.f.admins=[1];self.f.secret=b'test';self.f.channel=MagicMock();self.f.bot=create_autospec(Bot,instance=True);self.f.channel.operations_ui.health_text.return_value='原生完整巡检内容';self.f.channel.operations_ui.markup.return_value.inline_keyboard=[]
        self.u=MagicMock();self.u.effective_user.id=1;self.u.effective_chat.type='private'
        atomic_json(self.f.root/'state.json',{'phase':'idle','active':'web','sync':{'mapped':311,'pending':152}})
    def run_command(self,cmd):
        self.u.effective_message.text='/'+cmd
        with self.assertRaises(ApplicationHandlerStop):self.f.command(self.u,None)
        return self.u.effective_message.reply_text.call_args
    def test_status_has_no_switch_keyboard(self):
        a=self.run_command('status');self.assertNotIn('切换到',str(a.kwargs['reply_markup']));self.assertIn('综合状态',a.args[0]);self.assertIn('311',a.args[0]);self.assertIn('/cw_login',a.args[0]);self.assertIn('Bot API：可访问',a.args[0])
    def test_web_status_is_connection_detail(self):
        self.assertIn('网页版微信连接',self.run_command('web_status').args[0])
    def test_backend_has_switch_keyboard(self):self.assertIn('reply_markup',self.run_command('backend').kwargs)
    def test_native_status_preserves_original_details(self):
        self.f.backend='comwechat';a=self.run_command('status');self.assertIn('原生完整巡检内容',a.args[0]);self.assertIn('ComWechat',a.args[0])
    def test_native_alias_calls_original_status(self):
        self.f.backend='comwechat';self.run_command('cw_status');self.f.channel.operations_ui.status.assert_called_once()
    def test_common_version_not_intercepted_as_native(self):
        from frontend import NATIVE_ONLY
        self.assertNotIn('version',NATIVE_ONLY)
    def test_unchanged_refresh_is_not_reported_as_failure(self):
        from telegram.error import BadRequest
        self.u.callback_query.data='efbview:status'
        self.u.callback_query.edit_message_text.side_effect=BadRequest('Message is not modified')
        with self.assertRaises(ApplicationHandlerStop):self.f.view_callback(self.u,None)

    def test_both_native_login_aliases_route_to_native_login(self):
        self.f.backend='comwechat'
        for name in ['login','cw_login']:self.run_command(name)
        self.assertEqual(self.f.channel.wechat_control.login.call_count,2)
    def test_inactive_native_login_does_not_start_native(self):
        self.assertIn('先',self.run_command('cw_login').args[0])
        self.f.channel.wechat_control.login.assert_not_called()
    def test_all_controller_panels_have_close(self):
        for name in ['status','backend','sync','web_status','help','cw_status','cw_login']:
            markup=self.run_command(name).kwargs['reply_markup']
            self.assertTrue(any(b.callback_data=='efbview:close' for row in markup.inline_keyboard for b in row),name)
    def test_close_deletes_panel(self):
        self.u.callback_query.data='efbview:close'
        with self.assertRaises(ApplicationHandlerStop):self.f.view_callback(self.u,None)
        self.u.callback_query.delete_message.assert_called_once()

    def test_private_umask_does_not_hide_status(self):
        import os
        old=os.umask(0o077)
        try:atomic_json(self.f.root/'status.json',{'phase':'idle'})
        finally:os.umask(old)
        self.assertEqual((self.f.root/'status.json').stat().st_mode&0o777,0o644)

if __name__=='__main__':unittest.main(verbosity=2)
