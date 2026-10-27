import base64
import datetime
import json
import sqlite3
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch,Mock
from bridge_control import Control,receive_media_allowed
from image_outbound import Outbound
from image_pipe import transfer
from test_image_pipe import ImageProtocolTests

class ReceiveTextOnlyTests(unittest.TestCase):
    stream=ImageProtocolTests.stream
    def setUp(self):
        ImageProtocolTests.setUp(self)
        self.cfg['remote_control_enabled']=True
        self.control=Control(self.cfg['state_dir'])
        self.control.clock=lambda:time.time()-10
        self.control.set(enabled=True,images_enabled=True)
        self.control.clock=time.time
        self.cfg.update(attachments_enabled=True,mirror_from_telegram=True,attachments_since=0)
        with sqlite3.connect(self.cfg['bindings_db']) as db:
            db.execute('ALTER TABLE msglog ADD COLUMN file_id TEXT')
            db.execute('ALTER TABLE msglog ADD COLUMN mime TEXT')
    def log(self,kind,direction='blueset.telegram',age=0):
        stamp=datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)-datetime.timedelta(seconds=age)
        with sqlite3.connect(self.cfg['bindings_db']) as db:
            db.execute('INSERT INTO msglog VALUES (?,?,?,?,?,?,?,?)',(kind+direction+str(age),kind,kind,self.cfg['target'],direction,str(stamp),'file','application/octet-stream'))
    def test_both_sources_all_media_skipped_text_kept(self):
        self.control.set(receive_text_only=True)
        for direction in ['blueset.telegram','blueset.wechat']:
            for kind in ['Image','File','Video','Audio','Voice']:self.log(kind,direction)
        self.log('Text')
        with patch('gateway.images_ready',return_value=True),patch.object(Outbound,'fetch_file') as fetch:
            self.assertEqual(self.g.run({'op':'poll','images':True,'attachments':True})['message']['text'],'Text')
            self.assertIsNone(self.g.run({'op':'poll','images':True,'attachments':True})['message'])
            fetch.assert_not_called()
    def test_toggle_no_mac_floor_epoch_or_ha_changes(self):
        before=self.control.desired();policy=self.store.policy()
        after=self.control.set(receive_text_only=True)
        self.assertEqual(after['revision'],before['revision'])
        self.assertEqual(after['images_since'],before['images_since'])
        self.assertTrue(after['images_enabled'])
        self.assertEqual(self.store.policy(),policy)
        out=self.g.run({'op':'submit','epoch':self.epoch,'event_id':'fresh','text':'hello'})
        self.assertEqual(out['status'],'pending')
        self.assertEqual(self.g.run({'op':'status'})['epoch'],self.epoch)
    def test_upload_still_allowed_and_no_replay(self):
        self.control.set(receive_text_only=True)
        values=[self.header,{'offset':0,'data':base64.b64encode(self.data).decode()},{'op':'commit'}]
        out=[];transfer(self.g,'put',self.stream(values),out.append)
        self.assertEqual(out[-1]['status'],'pending')
        out=[];transfer(self.g,'put',self.stream([self.header]),out.append)
        self.assertEqual(out[0]['status'],'duplicate')
    def test_preexisting_grant_blocked_even_after_reenable(self):
        store=Outbound(self.cfg,fetch=lambda _:self.data);key='a'*64
        store.grant(key,self.epoch,'file',time.time()-1)
        self.control.set(receive_text_only=True)
        for enabled in [True,False]:
            self.control.set(receive_text_only=enabled)
            out=[]
            with self.assertRaises(ValueError):transfer(self.g,'get',self.stream([{'epoch':self.epoch,'image_id':key}]),out.append)
            self.assertEqual(out,[])
    def test_reenable_skips_unpolled_old_media(self):
        self.control.set(receive_text_only=True);self.log('Image',age=1)
        self.control.set(receive_text_only=False)
        with patch('gateway.images_ready',return_value=True),patch.object(Outbound,'fetch_file') as fetch:
            self.assertIsNone(self.g.run({'op':'poll','images':True})['message']);fetch.assert_not_called()
        store=Outbound(self.cfg,fetch=lambda _:self.data)
        self.assertEqual(store.grant('b'*64,self.epoch,'new',time.time())['kind'],'image')
    def test_old_receive_buttons_rejected(self):
        before=self.control.desired()
        self.control.set(expected=before['revision'],expected_receive=0,receive_text_only=True)
        with self.assertRaises(ValueError):self.control.set(expected=before['revision'],expected_receive=0,receive_text_only=False)
    def test_panel_exposes_direction_switch(self):
        from bridge_ui import BridgeUI
        ui=object.__new__(BridgeUI);ui.g=self.g;ui.control=self.control;ui.sessions={}
        self.control.set(receive_text_only=True)
        text,rows=ui.render(1)
        self.assertIn('iMessage仅接收文字：开启',text)
        self.assertTrue(any('receive_all' in b.callback_data for row in rows for b in row))
        self.assertTrue(self.control.desired()['images_enabled'])

if __name__=='__main__':unittest.main()
