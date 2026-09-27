import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import sqlite3
from protocol import button,verify,atomic_json
from sync import synchronize
from worker import Worker

class ProtocolTests(unittest.TestCase):
    def test_signature_expiry_actor_and_tampering(self):
        secret=b'secret';value=button(secret,'web',12,now=1000)
        self.assertEqual(verify(secret,value,12,[12],now=1001)['action'],'web')
        for data,actor,now in [(value,13,1001),(value,12,1400),(value.replace(':web:',':sync:'),12,1001)]:
            with self.assertRaises(ValueError):verify(secret,data,actor,[12],now=now)
        self.assertLessEqual(len(value.encode()),64)

class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.profiles={}
        for backend in ['web','comwechat']:
            p=self.root/backend;p.mkdir();(p/'blueset.telegram').mkdir();self.profiles[backend]=str(p)
            with sqlite3.connect(p/'blueset.telegram/tgdata.db') as c:
                c.executescript('CREATE TABLE chatassoc(id INTEGER PRIMARY KEY,master_uid TEXT,slave_uid TEXT); CREATE TABLE topicassoc(id INTEGER PRIMARY KEY,topic_chat_id TEXT,message_thread_id TEXT,slave_uid TEXT); CREATE TABLE msglog(value TEXT);')
                c.execute('INSERT INTO msglog VALUES (?)',(backend+'-history',))
            (p/'blueset.telegram/config.yaml').write_text('token: '+backend+'-secret\nflags:\n  send_image_as_file: false\n  local_bot_api: true\n')
        atomic_json(self.root/'mapping.json',[{'source':'honus.comwechat person','target':'blueset.wechat one'}])
        self.config={'control_root':str(self.root),'profiles':self.profiles,'pending_bindings':2}
    def db(self,backend):return sqlite3.connect(Path(self.profiles[backend])/'blueset.telegram/tgdata.db')
    def test_bidirectional_sync_preserves_unmapped_history_and_secrets(self):
        with self.db('web') as c:c.execute("INSERT INTO topicassoc VALUES (1,'forum','42','blueset.wechat one')")
        with self.db('comwechat') as c:c.execute("INSERT INTO topicassoc VALUES (1,'forum','99','honus.comwechat unknown')")
        synchronize(self.config,'web')
        with self.db('comwechat') as c:
            self.assertEqual(c.execute('SELECT count(*) FROM topicassoc').fetchone()[0],2)
            self.assertEqual(c.execute('SELECT value FROM msglog').fetchone()[0],'comwechat-history')
            c.execute("UPDATE topicassoc SET message_thread_id='43' WHERE slave_uid='honus.comwechat person'")
        synchronize(self.config,'comwechat')
        with self.db('web') as c:self.assertEqual(c.execute('SELECT message_thread_id FROM topicassoc').fetchone()[0],'43')
        self.assertIn('web-secret',(Path(self.profiles['web'])/'blueset.telegram/config.yaml').read_text())
        self.assertFalse(synchronize(self.config,'comwechat')['changed'])
    def test_mapped_filters_sync_without_overwriting_unknown_rules(self):
        for backend,rules in [('web',{'blueset.wechat one':{'policy':'silent'}}),('comwechat',{'honus.comwechat unknown':{'policy':'filtered'}})]:
            p=Path(self.profiles[backend])/'blueset.telegram/delivery-policies.json'
            p.write_text(json.dumps({'version':1,'rules':rules,'settings':{}}))
        synchronize(self.config,'web')
        p=Path(self.profiles['comwechat'])/'blueset.telegram/delivery-policies.json'
        rules=json.loads(p.read_text())['rules']
        self.assertEqual(rules['honus.comwechat person']['policy'],'silent')
        self.assertEqual(rules['honus.comwechat unknown']['policy'],'filtered')

    def test_unbind_known_identity_propagates_but_unknown_is_retained(self):
        with self.db('comwechat') as c:
            c.execute("INSERT INTO chatassoc VALUES (1,'a','honus.comwechat person')")
            c.execute("INSERT INTO chatassoc VALUES (2,'b','honus.comwechat unknown')")
        synchronize(self.config,'web')
        with self.db('comwechat') as c:self.assertEqual(c.execute('SELECT slave_uid FROM chatassoc').fetchall(),[('honus.comwechat unknown',)])
    def test_unmapped_topic_conflict_rolls_back(self):
        with self.db('web') as c:c.execute("INSERT INTO topicassoc VALUES (1,'forum','42','blueset.wechat one')")
        with self.db('comwechat') as c:c.execute("INSERT INTO topicassoc VALUES (1,'forum','42','honus.comwechat unknown')")
        with self.assertRaises(ValueError):synchronize(self.config,'web')
        with self.db('comwechat') as c:self.assertEqual(c.execute('SELECT slave_uid FROM topicassoc').fetchone()[0],'honus.comwechat unknown')

class SwitchTests(unittest.TestCase):
    def test_stale_request_preserves_waiting_login(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'secret').write_bytes(b'secret');(root/'processed').mkdir()
            atomic_json(root/'state.json',{'active':'web','phase':'awaiting_login'})
            w=Worker({'control_root':tmp,'admins':[12]})
            data=button(b'secret','web',12);r=verify(b'secret',data,12,[12]);r['requested_backend']='comwechat'
            p=root/(r['nonce']+'.json');atomic_json(p,r)
            with patch.object(w,'notify'):w.process(p)
            self.assertEqual(w.state['phase'],'awaiting_login')
    def test_delivered_qr_allows_target_to_wait_for_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'secret').write_bytes(b'secret')
            w=Worker({'control_root':tmp})
            now=time.time();atomic_json(root/'web-login.json',{'updated':now,'qr_delivered':True})
            with patch.object(w,'running',return_value=True):h=w.wait_ready('web',now-1)
            self.assertFalse(h['wechat_online'])

    def test_switch_stops_source_before_target_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'secret').write_bytes(b'secret');atomic_json(root/'state.json',{'active':'web','phase':'idle'});atomic_json(root/'web-health.json',{'updated':time.time(),'queue_size':0,'inflight':0})
            w=Worker({'control_root':tmp,'containers':{'web':'web-container','comwechat':'native-container'},'watchdog':'watchdog'})
            calls=[];running={'web':True,'comwechat':False}
            def run(args,timeout=45):
                calls.append(args)
                if args[:2]==['docker','stop']:
                    for key,name in w.config['containers'].items():
                        if args[-1]==name:running[key]=False
                if args[:2]==['docker','start']:
                    for key,name in w.config['containers'].items():
                        if args[-1]==name:running[key]=True
                return ''
            with patch.object(w,'run',side_effect=run),patch.object(w,'running',side_effect=lambda b:running[b]),patch.object(w,'synchronize'),patch.object(w,'notify'),patch.object(w,'wait_ready',side_effect=TimeoutError):
                with self.assertRaises(TimeoutError):w.switch('comwechat')
            self.assertTrue(running['web']);self.assertFalse(running['comwechat'])
            stop=next(i for i,a in enumerate(calls) if a[:2]==['docker','stop'] and a[-1]=='web-container')
            start=next(i for i,a in enumerate(calls) if a[:2]==['docker','start'] and a[-1]=='native-container')
            self.assertLess(stop,start)

if __name__=='__main__':unittest.main(verbosity=2)
