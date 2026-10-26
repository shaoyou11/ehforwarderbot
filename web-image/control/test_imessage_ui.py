import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'imessage'))
import unittest
from unittest.mock import MagicMock
from types import SimpleNamespace
from telegram import InlineKeyboardMarkup,InlineKeyboardButton
from telegram.ext import ApplicationHandlerStop
from bridge_ui import BridgeUI
import test_gateway as fixtures

class UI(unittest.TestCase):
    def setUp(self):
        fixtures.GatewayTests.setUp(self)
        self.g.run({'op':'on'})
        self.cfg['image_media_dir']=str(self.g.root/'media');self.g.root.joinpath('media').mkdir()
        self.f=SimpleNamespace(channel=SimpleNamespace(bot_manager=SimpleNamespace(dispatcher=MagicMock())),
            authorized=lambda u:u.effective_user.id==1 and u.effective_chat.type=='private',
            navigation=lambda:InlineKeyboardMarkup([[InlineKeyboardButton('关闭',callback_data='efbview:close')]]))
        self.ui=BridgeUI(self.f,self.g);self.ui.control.set(enabled=True,images_enabled=True)
        self.u=MagicMock();self.u.effective_user.id=1;self.u.effective_chat.type='private'
    def click(self,action):
        _,rows=self.ui.render(1);prefix=rows[0][0].callback_data.rsplit(':',1)[0]
        self.u.callback_query.data=prefix+':'+action
        with self.assertRaises(ApplicationHandlerStop):self.ui.callback(self.u,None)
    def test_explicit_controls_and_no_false_success(self):
        text,rows=self.ui.render(1)
        self.assertIn('等待连接',text);self.assertIn('关闭 iMessage',rows[0][0].text);self.assertIn('关闭图片',rows[1][0].text)
    def test_bridge_close_cancels_only_own_route(self):
        self.click('bridge_off');p=self.store.policy()
        self.assertFalse(p['routes']['imessage_test']['enabled'])
        self.assertEqual(p['routes']['ha_test'],self.policy['routes']['ha_test']);self.assertEqual(p['revision'],'original')
        self.assertFalse(self.ui.control.desired()['enabled'])
    def test_image_close_keeps_text_route(self):
        self.click('images_off');self.assertTrue(self.store.policy()['routes']['imessage_test']['enabled'])
        self.assertTrue(self.ui.control.desired()['enabled']);self.assertFalse(self.ui.control.desired()['images_enabled'])
    def test_unauthorized_user_cannot_change(self):
        self.u.effective_user.id=2;before=self.ui.control.desired();self.click('bridge_off')
        self.assertEqual(before,self.ui.control.desired())
    def test_old_button_cannot_reverse_new_choice(self):
        _,rows=self.ui.render(1);old=rows[0][0].callback_data
        self.click('images_off');self.u.callback_query.data=old
        with self.assertRaises(ApplicationHandlerStop):self.ui.callback(self.u,None)
        self.assertTrue(self.ui.control.desired()['enabled'])
    def test_command_has_close(self):
        with self.assertRaises(ApplicationHandlerStop):self.ui.command(self.u,None)
        markup=self.u.effective_message.reply_text.call_args.kwargs['reply_markup']
        self.assertTrue(any(b.callback_data=='efbview:close' for row in markup.inline_keyboard for b in row))

if __name__=='__main__':unittest.main()
