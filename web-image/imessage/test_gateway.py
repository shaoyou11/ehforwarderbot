import datetime
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'control'))
from gateway import Gateway
from notification_queue import Store

class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); db=self.root/'tg.db'
        with sqlite3.connect(db) as c:
            c.executescript('CREATE TABLE chatassoc(master_uid TEXT,slave_uid TEXT); CREATE TABLE msglog(slave_message_id TEXT,text TEXT,msg_type TEXT,slave_origin_uid TEXT,sent_to TEXT,time TEXT);')
            c.execute('INSERT INTO chatassoc VALUES (?,?)',('blueset.telegram 1','blueset.wechat test'))
        self.cfg={'state_dir':str(self.root/'bridge'),'control_code':str(Path(__file__).resolve().parents[1]/'control'),'notification_root':str(self.root/'notice'),'bindings_db':str(db),'backend_state':str(self.root/'backend.json'),'chat_id':'1','target':'blueset.wechat test','route':'imessage_test','source':'imessage','source_token':'test-only'}
        (self.root/'backend.json').write_text(json.dumps({'active':'web','phase':'idle'}))
        self.store=Store(self.cfg['notification_root'],db); self.store.initialize()
        self.policy={'enabled':True,'revision':'original','routes':{'ha_test':{'enabled':True,'sources':['ha'],'chat_id':'1','target':'blueset.wechat test'},'imessage_test':{'enabled':False,'sources':['imessage'],'chat_id':'1','target':'blueset.wechat test'}}}
        (self.store.root/'policy.json').write_text(json.dumps(self.policy))
        (self.store.root/'sources.json').write_text(json.dumps({'imessage':{'token':'test-only'}}))
        self.g=Gateway(self.cfg)
    def message(self,text='hello',target='blueset.wechat test',direction='blueset.telegram',age=0):
        with sqlite3.connect(self.cfg['bindings_db']) as c:
            c.execute('INSERT INTO msglog VALUES (?,?,?,?,?,?)',('id-'+text,text,'Text',target,direction,str(datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)-datetime.timedelta(seconds=age))))
    def test_disabled_and_enable_skip_history(self):
        self.message('old');self.assertFalse(self.g.run({'op':'poll'})['enabled'])
        self.g.run({'op':'on'});self.assertIsNone(self.g.run({'op':'poll'})['message'])
        self.message();self.assertEqual(self.g.run({'op':'poll'})['message']['text'],'hello')
        self.assertIsNone(self.g.run({'op':'poll'})['message'])
    def test_scope_direction_and_expiry(self):
        self.g.run({'op':'on'});self.message(target='blueset.wechat other');self.message(direction='blueset.wechat');self.message(age=400)
        self.assertIsNone(self.g.run({'op':'poll'})['message'])
    def test_switch_preserves_ha(self):
        epoch=self.g.run({'op':'on'})['epoch']
        event={'op':'submit','epoch':epoch,'event_id':'one','text':'hello'}
        self.assertEqual(self.g.run(event)['status'],'pending')
        self.assertTrue(self.g.run(event)['duplicate'])
        self.g.run({'op':'off'});p=self.store.policy()
        self.assertEqual(p['routes']['ha_test'],self.policy['routes']['ha_test']);self.assertEqual(p['revision'],'original')
        self.assertEqual(self.store.counts(),{'cancelled':1})
        self.g.run({'op':'on'});self.assertEqual(self.g.run(event)['error'],'stale_epoch')
    def test_duplicate_source_message_not_delivered_twice(self):
        self.g.run({'op':'on'});self.message('same')
        self.assertIsNotNone(self.g.run({'op':'poll'})['message'])
        self.message('same');self.assertIsNone(self.g.run({'op':'poll'})['message'])
    def test_reconnect_skips_backlog(self):
        self.g.run({'op':'on'});self.message('during-outage')
        path=self.root/'bridge/state.json';state=json.loads(path.read_text());state['last_poll']=0;path.write_text(json.dumps(state))
        self.assertIsNone(self.g.run({'op':'poll'})['message'])
        self.message('new');self.assertEqual(self.g.run({'op':'poll'})['message']['text'],'new')
    def test_log_timezone(self):
        self.cfg['log_timezone']='Asia/Shanghai';self.g=Gateway(self.cfg)
        self.g.run({'op':'on'})
        with sqlite3.connect(self.cfg['bindings_db']) as c:
            when=datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)+datetime.timedelta(hours=8,seconds=-400)
            c.execute('INSERT INTO msglog VALUES (?,?,?,?,?,?)',('old','old','Text',self.cfg['target'],'blueset.telegram',str(when)))
        self.assertIsNone(self.g.run({'op':'poll'})['message'])
    def test_backend_switch_pauses(self):
        self.g.run({'op':'on'})
        (self.root/'backend.json').write_text(json.dumps({'active':'comwechat','phase':'idle'}))
        self.message();self.assertFalse(self.g.run({'op':'poll'})['enabled'])
        (self.root/'backend.json').write_text(json.dumps({'active':'web','phase':'idle'}))
        self.assertIsNone(self.g.run({'op':'poll'})['message'])
    def test_queue_is_bounded(self):
        epoch=self.g.run({'op':'on'})['epoch']
        for n in range(5):
            self.assertEqual(self.g.run({'op':'submit','epoch':epoch,'event_id':str(n),'text':'hello'})['status'],'pending')
        self.assertEqual(self.g.run({'op':'submit','epoch':epoch,'event_id':'overflow','text':'hello'})['error'],'imessage_queue_full')
    def test_telegram_outgoing_copy_is_opt_in_and_failed_rows_excluded(self):
        self.cfg['mirror_from_telegram']=True;self.g=Gateway(self.cfg)
        self.g.run({'op':'on'});self.message('outgoing',direction='blueset.wechat')
        item=self.g.run({'op':'poll'})['message'];self.assertEqual(item['text'],'[Telegram 发出]\n'+'outgoing')
        self.assertIsNone(self.g.run({'op':'poll'})['message'])
        self.message('failed',direction='blueset.wechat')
        with sqlite3.connect(self.cfg['bindings_db']) as c:c.execute("UPDATE msglog SET slave_message_id='__fail__.0' WHERE text='failed'")
        self.assertIsNone(self.g.run({'op':'poll'})['message'])
    def test_binding_change_fails_closed(self):
        self.g.run({'op':'on'})
        with sqlite3.connect(self.cfg['bindings_db']) as c:c.execute("UPDATE chatassoc SET slave_uid='blueset.wechat changed'")
        self.assertFalse(self.g.run({'op':'poll'})['enabled'])
        self.assertIn('error',self.g.run({'op':'on'}))

if __name__=='__main__':unittest.main()
