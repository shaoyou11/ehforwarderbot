import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from types import SimpleNamespace as NS
from notification_queue import Store,Rejected,MAX_PENDING,MAX_HISTORY
from protocol import atomic_json

class QueueFixture:
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.db=self.root/'bindings.db'
        with sqlite3.connect(self.db) as db:
            db.executescript("CREATE TABLE chatassoc(master_uid TEXT,slave_uid TEXT);CREATE TABLE topicassoc(topic_chat_id TEXT,message_thread_id TEXT,slave_uid TEXT);INSERT INTO chatassoc VALUES ('blueset.telegram -1','blueset.wechat x');INSERT INTO topicassoc VALUES ('-2','42','blueset.wechat y');")
        self.now=1000.;self.store=Store(self.root/'notifications',self.db,lambda:self.now);self.store.initialize()
        self.policy={'enabled':True,'revision':'v1','routes':{'one':{'enabled':True,'sources':['ha'],'chat_id':-1,'target':'blueset.wechat x'}}}
        self.save();atomic_json(self.store.root/'sources.json',{'ha':{'token':'test-key'}},0o600)
    def save(self):atomic_json(self.store.root/'policy.json',self.policy,0o600)
    def payload(self,**kw):return dict(source='ha',event_id='event-1',route='one',origin='external',text='test',ttl=120,**kw)
    def submit(self,data=None):return self.store.submit(data or self.payload(),'test-key')
class QueueTests(QueueFixture,unittest.TestCase):
    def test_default_disabled(self):
        self.policy.pop('enabled');self.save()
        with self.assertRaises(Rejected):self.submit()
        self.assertEqual(self.store.counts(),{})
    def test_source_authentication_and_allowlist(self):
        with self.assertRaises(Rejected):self.store.submit(self.payload(),'wrong')
        self.policy['routes']['one']['sources']=[];self.save()
        with self.assertRaises(Rejected):self.submit()
    def test_per_route_default_off(self):
        self.policy['routes']['one'].pop('enabled');self.save()
        with self.assertRaises(Rejected):self.submit()
    def test_update_objects_and_non_external_origin_rejected(self):
        for change in [{'origin':'efb'},{'message':{}},{'update_id':1},{'text':'x'*4001},{'ttl':301},{'ttl':True}]:
            data=self.payload();data.update(change)
            with self.assertRaises(Rejected):self.submit(data)
    def test_idempotency_and_conflicting_id(self):
        self.submit();self.assertTrue(self.submit()['duplicate'])
        data=self.payload();data['text']='different'
        with self.assertRaises(Rejected):self.submit(data)
        self.assertEqual(self.store.counts(),{'pending':1})
    def test_exact_topic_binding_and_ambiguous_group(self):
        self.policy['routes']['one'].update(chat_id=-2,thread_id=42,target='blueset.wechat y');self.save();self.submit()
        with sqlite3.connect(self.db) as db:db.execute("INSERT INTO topicassoc VALUES ('-2','42','blueset.wechat z')")
        self.assertIsNone(self.store.claim(True,True));self.assertEqual(self.store.counts(),{'blocked':1})
    def test_binding_change_blocks_send(self):
        self.submit()
        with sqlite3.connect(self.db) as db:db.execute("UPDATE chatassoc SET slave_uid='blueset.wechat z'")
        self.assertIsNone(self.store.claim(True,True));self.assertEqual(self.store.counts(),{'blocked':1})
    def test_offline_and_busy_pause_without_claim(self):
        self.submit()
        self.assertIsNone(self.store.claim(True,False));self.assertIsNone(self.store.claim(False,True));self.assertEqual(self.store.counts(),{'pending':1})
    def test_expiry_no_catchup(self):
        self.submit();self.now+=121;self.assertIsNone(self.store.claim(True,True));self.assertEqual(self.store.counts(),{'expired':1})
    def test_disable_cancels_pending(self):
        self.submit();self.policy['enabled']=False;self.save();self.store.claim(True,True)
        self.policy['enabled']=True;self.save();self.assertIsNone(self.store.claim(True,True));self.assertEqual(self.store.counts(),{'cancelled':1})
    def test_revision_change_cancels_pending(self):
        self.submit();self.policy['revision']='v2';self.save();self.assertIsNone(self.store.claim(True,True));self.assertEqual(self.store.counts(),{'cancelled':1})
    def test_restart_unknown_intent_and_cancel_pending(self):
        self.submit();self.store.claim(True,True);data=self.payload();data['event_id']='event-2';self.submit(data)
        self.store.recover();self.assertEqual(self.store.counts(),{'unknown':1,'cancelled':1});self.assertIsNone(self.store.claim(True,True))
    def test_unknown_never_retries_even_after_late_success(self):
        self.submit();event=self.store.claim(True,True);self.store.finish(event,'unknown');self.store.finish(event,'sent')
        self.assertEqual(self.submit()['status'],'unknown');self.now+=60;self.assertIsNone(self.store.claim(True,True))
    def test_rate_limits_persist(self):
        self.submit();event=self.store.claim(True,True);self.store.finish(event,'sent')
        data=self.payload();data['event_id']='event-2';self.submit(data)
        self.now+=11;self.assertIsNone(self.store.claim(True,True));self.now+=20;self.assertIsNotNone(self.store.claim(True,True))
    def test_interactive_burst_preserves_order_and_spacing(self):
        self.policy['routes']['one']['interactive_source']='ha';self.save()
        self.submit();first=self.store.claim(True,True)
        second=self.payload();second['event_id']='event-2';self.submit(second)
        self.now+=20
        self.assertIsNone(self.store.claim(True,True))  # hung intent never overlaps
        self.store.finish(first,'sent');self.now+=2.9
        self.assertIsNone(self.store.claim(True,True));self.now+=.1
        self.assertEqual(self.store.claim(True,True)['event_id'],'event-2')

    def test_interactive_unknown_retains_cooldown(self):
        self.policy['routes']['one']['interactive_source']='ha';self.save()
        self.submit();first=self.store.claim(True,True);self.store.finish(first,'unknown')
        second=self.payload();second['event_id']='event-2';self.submit(second)
        self.now+=4;self.assertIsNone(self.store.claim(True,True))
        self.now+=26;self.assertIsNotNone(self.store.claim(True,True))

    def test_shared_source_route_cannot_enable_fast_pacing(self):
        self.policy['routes']['one'].update(interactive_source='ha',sources=['ha','other']);self.save()
        self.submit();first=self.store.claim(True,True);self.store.finish(first,'sent')
        second=self.payload();second['event_id']='event-2';self.submit(second)
        self.now+=4;self.assertIsNone(self.store.claim(True,True))

    def test_other_route_global_limit_unchanged(self):
        self.policy['routes']['one']['interactive_source']='ha'
        self.policy['routes']['two']=dict(self.policy['routes']['one']);self.save()
        self.submit();first=self.store.claim(True,True);self.store.finish(first,'sent')
        second=self.payload();second.update(event_id='event-2',route='two');self.submit(second)
        self.now+=4;self.assertIsNone(self.store.claim(True,True))
        self.now+=6;self.assertIsNotNone(self.store.claim(True,True))

    def test_pending_capacity(self):
        for i in range(MAX_PENDING):
            data=self.payload();data['event_id']=str(i);self.submit(data)
        with self.assertRaises(Rejected) as error:self.submit()
        self.assertEqual(error.exception.reason,'queue_full')
    def test_history_limit_fails_closed(self):
        self.submit()
        with patch('notification_queue.MAX_HISTORY',1):
            data=self.payload();data['event_id']='event-2'
            with self.assertRaises(Rejected):self.submit(data)
    def test_text_removed_from_terminal_record(self):
        self.submit();event=self.store.claim(True,True);self.store.finish(event,'sent')
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT text FROM events').fetchone()[0],'')

class RuntimeTests(QueueFixture,unittest.TestCase):
    def runtime(self):
        from notification_runtime import Notifications
        obj=object.__new__(Notifications);obj.store=self.store;obj.inflight=None
        import threading
        obj.frontend=NS(root=self.root,activity_lock=threading.Lock(),inflight=0,last_activity=0,channel=NS(master_messages=NS(message_queue=None)))
        atomic_json(self.root/'state.json',{'active':'web','phase':'idle'})
        return obj
    def test_backend_switch_cancels_pending(self):
        self.submit();obj=self.runtime();atomic_json(self.root/'state.json',{'active':'comwechat','phase':'idle'});obj.tick()
        self.assertEqual(self.store.counts(),{'cancelled':1})
    def test_busy_ordinary_chat_does_not_send(self):
        self.submit();obj=self.runtime();obj.frontend.inflight=1;slave=NS(bot=NS(alive=True))
        with patch('ehforwarderbot.coordinator.slaves',{'blueset.wechat':slave}):obj.tick()
        self.assertEqual(self.store.counts(),{'pending':1})

    def test_transport_receipt_success(self):
        import time
        self.now=time.time();self.submit();obj=self.runtime();chat=object()
        slave=NS(bot=NS(core=NS(s=NS(request=Mock())),alive=True,search=Mock(return_value=[chat])),_bot_send_msg=Mock(return_value=object()))
        with patch('ehforwarderbot.coordinator.slaves',{'blueset.wechat':slave}):
            obj.tick();obj.inflight[1].join(2)
        slave._bot_send_msg.assert_called_once_with(chat,'test')
        self.assertEqual(self.store.counts(),{'sent':1});self.assertEqual(obj.frontend.inflight,0)
    def test_transport_timeout_unknown_no_retry(self):
        import time
        self.now=time.time();self.submit();obj=self.runtime()
        slave=NS(bot=NS(core=NS(s=NS(request=Mock())),alive=True,search=Mock(return_value=[object()])),_bot_send_msg=Mock(side_effect=TimeoutError))
        with patch('ehforwarderbot.coordinator.slaves',{'blueset.wechat':slave}):
            obj.tick();obj.inflight[1].join(2);obj.tick()
        self.assertEqual(slave._bot_send_msg.call_count,1);self.assertEqual(self.store.counts(),{'unknown':1})
    def test_missing_live_contact_is_blocked(self):
        import time
        self.now=time.time();self.submit();obj=self.runtime()
        slave=NS(bot=NS(core=NS(s=NS(request=Mock())),alive=True,search=Mock(return_value=[])),_bot_send_msg=Mock())
        with patch('ehforwarderbot.coordinator.slaves',{'blueset.wechat':slave}):obj.tick();obj.inflight[1].join(2)
        slave._bot_send_msg.assert_not_called();self.assertEqual(self.store.counts(),{'blocked':1})
    def test_notification_deadline_does_not_change_other_threads(self):
        import threading
        from notification_runtime import bounded_transport
        original=Mock();bot=NS(core=NS(s=NS(request=original)))
        with bounded_transport(bot):
            bot.core.s.request('POST','local-test',timeout=60)
            thread=threading.Thread(target=lambda:bot.core.s.request('POST','local-test',timeout=60));thread.start();thread.join(2)
        self.assertEqual(original.call_args_list[0].kwargs['timeout'],(3,8))
        self.assertEqual(original.call_args_list[1].kwargs['timeout'],60)
        self.assertIs(bot.core.s.request,original)

    def test_hung_send_does_not_spawn_more_workers(self):
        import time,threading
        self.now=time.time();self.submit();obj=self.runtime();gate=threading.Event()
        slave=NS(bot=NS(core=NS(s=NS(request=Mock())),alive=True,search=Mock(return_value=[object()])),_bot_send_msg=Mock(side_effect=lambda *a:gate.wait(3)))
        with patch('ehforwarderbot.coordinator.slaves',{'blueset.wechat':slave}):
            obj.tick();event,thread,started=obj.inflight;obj.inflight=(event,thread,started-21)
            obj.tick();self.assertEqual(self.store.counts(),{'unknown':1});obj.tick();gate.set();thread.join(2)
        self.assertEqual(slave._bot_send_msg.call_count,1);self.assertEqual(self.store.counts(),{'unknown':1})

class HttpTests(QueueFixture,unittest.TestCase):
    def test_authenticated_request_and_disabled_gate(self):
        import threading,urllib.request,urllib.error
        from http.server import HTTPServer
        from notification_ingress import Handler
        server=HTTPServer(('127.0.0.1',0),Handler);server.store=self.store
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            url='http://127.0.0.1:'+str(server.server_port)+'/v1/notifications'
            def send(token):
                req=urllib.request.Request(url,data=json.dumps(self.payload()).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
                return urllib.request.urlopen(req,timeout=3)
            with self.assertRaises(urllib.error.HTTPError) as error:send('wrong')
            self.assertEqual(error.exception.code,401)
            with send('test-key') as response:self.assertEqual(json.load(response)['status'],'pending')
            self.policy['enabled']=False;self.save()
            with self.assertRaises(urllib.error.HTTPError) as error:send('test-key')
            self.assertEqual(error.exception.code,503)
        finally:server.shutdown();server.server_close();thread.join(3)

class RollbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.backup=self.root/'baseline';self.backup.mkdir()
        self.image='ghcr.io/shaoyou11/efb-web-standby@sha256:'+'0'*64
        atomic_json(self.backup/'manifest.json',{'image':self.image})
        atomic_json(self.root/'controller-state/state.json',{'active':'web','phase':'idle'})
        atomic_json(self.root/'controller-state/web-health.json',{'inflight':0,'queue_size':0})
        atomic_json(self.root/'controller-state/notifications/policy.json',{'enabled':True,'revision':'v1'})
        (self.root/'controller-state/backups').mkdir()
        (self.root/'docker-compose.yaml').write_text('services:\n  web-standby:\n    image: current-image\n')
        self.original=(self.root/'docker-compose.yaml').read_bytes()
    def test_dry_run_has_no_mutations(self):
        from notification_switch import rollback
        with patch('notification_switch.subprocess.run') as run:
            self.assertEqual(rollback(self.root,self.backup)['action'],'rollback');run.assert_not_called()
        self.assertEqual((self.root/'docker-compose.yaml').read_bytes(),self.original)
        self.assertTrue(json.loads((self.root/'controller-state/notifications/policy.json').read_text())['enabled'])
    def test_failed_rollback_restores_previous_deployment(self):
        import subprocess
        from notification_switch import rollback
        calls=[]
        def run(args,**kwargs):
            calls.append(args)
            if args[:2]==['docker','compose'] and sum(x[:2]==['docker','compose'] for x in calls)==1:raise subprocess.CalledProcessError(1,args)
        with patch('notification_switch.subprocess.run',side_effect=run):
            with self.assertRaises(subprocess.CalledProcessError):rollback(self.root,self.backup,True)
        self.assertEqual((self.root/'docker-compose.yaml').read_bytes(),self.original)
        self.assertEqual(calls[-1],['systemctl','start','efb-backend-control.service'])
        self.assertFalse(json.loads((self.root/'controller-state/notifications/policy.json').read_text())['enabled'])

if __name__=='__main__':unittest.main()
