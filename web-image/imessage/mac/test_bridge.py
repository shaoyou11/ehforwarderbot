import tempfile
import hashlib
from unittest.mock import patch
import unittest
from pathlib import Path
from bridge import Bridge, Store, BlueBubbles, SSH, incoming_text, lock


class Remote:
    def __init__(self):
        self.enabled=False; self.epoch=1; self.calls=[]; self.message=None; self.fail=None; self.ready=True
    def call(self,op,**kw):
        self.calls.append((op,kw))
        if self.fail==op:raise ConnectionError()
        if op=='off':self.enabled=False
        if op=='on':self.enabled=True
        if op=='poll':
            m,self.message=self.message,None
            return {'enabled':self.enabled,'epoch':self.epoch,'message':m}
        return {'enabled':self.enabled,'epoch':self.epoch,'ready':self.ready,'status':'pending'}


class Blue:
    def __init__(self):self.items=[];self.sent=[];self.fail=False;self.windows=[]
    def messages(self,after,before):self.windows.append((after,before));return self.items
    def send(self,event,text):
        self.sent.append((event,text))
        if self.fail:raise ConnectionError()


def message(identifier='msg-one',**values):
    return dict(guid=identifier,text='mock text',isFromMe=False,dateCreated=1001000,attachments=[],**values)


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name));self.r=Remote();self.b=Blue();self.now=1000
        self.worker=Bridge(self.store,{'chat_guid':'mock-chat','validated_single_imessage_chat':True},self.r,self.b,lambda:self.now)
    def tearDown(self):self.tmp.cleanup()
    def enable(self):self.worker.on();self.now=1002
    def test_default_closed_and_no_send(self):
        self.assertFalse(self.store.get()['enabled']);self.worker.tick();self.assertEqual(self.b.sent,[])
        self.assertEqual([x[0] for x in self.r.calls],['off'])
    def test_every_enable_closes_first_and_starts_now(self):
        self.enable();self.assertEqual([x[0] for x in self.r.calls],['off','on'])
        self.assertEqual(self.store.get()['start_ms'],1000000)
        self.worker.off();self.now=1100;self.worker.on();self.assertEqual(self.store.get()['cursor_ms'],1100000)
    def test_persistent_dedupe_for_submit(self):
        self.enable();self.b.items=[message()];self.worker.tick();self.worker.tick()
        self.assertEqual(len([x for x in self.r.calls if x[0]=='submit']),1)
        self.assertIsInstance([x for x in self.r.calls if x[0]=='submit'][0][1]['epoch'],int)
        fresh=Store(self.store.home);self.assertFalse(fresh.intent('to_wechat','msg-one'))
    def test_failed_submit_not_retried(self):
        self.enable();self.r.fail='submit';self.b.items=[message()];self.worker.tick();self.r.fail=None;self.worker.tick()
        self.assertEqual(len([x for x in self.r.calls if x[0]=='submit']),1)
        with self.store.db() as db:self.assertEqual(db.execute('SELECT status FROM ledger').fetchone()[0],'unknown')
    def test_send_failure_consumed_and_never_retried(self):
        self.enable();self.b.fail=True;self.r.message={'id':'out-one','text':'mock','created':1001};self.worker.tick()
        self.b.fail=False;self.r.message={'id':'out-one','text':'mock','created':1001};self.worker.tick();self.assertEqual(len(self.b.sent),1)
    def test_unknown_intent_after_restart(self):
        self.store.intent('to_imessage','old');self.store.recover()
        with self.store.db() as db:self.assertEqual(db.execute('SELECT status FROM ledger').fetchone()[0],'unknown')
        self.assertFalse(self.store.intent('to_imessage','old'))
    def test_off_disables_locally_even_if_remote_unreachable(self):
        self.enable();self.r.fail='off';self.assertFalse(self.worker.off())
        self.assertEqual(self.store.get()['enabled'],0);self.assertEqual(self.store.get()['pending_off'],1)
        with self.assertRaises(ConnectionError):self.worker.on()
        self.assertEqual(len([x for x in self.r.calls if x[0]=='on']),1)
    def test_off_fences_old_epoch(self):
        self.enable();epoch=self.store.get()['epoch'];self.worker.off()
        self.worker.emit('to_imessage','old','mock',epoch);self.assertFalse(self.b.sent)
    def test_disconnect_drops_gap(self):
        self.enable();self.r.fail='status';self.worker.tick();self.now=1010;self.r.fail=None;self.worker.tick()
        self.assertEqual(self.b.windows[-1][0],1010000)
    def test_filter_own_system_reaction_attachments_and_age(self):
        base=message()
        for change in [{'isFromMe':True},{'associatedMessageType':2000},{'associatedMessageGuid':'r'},
                       {'attachments':[{}]},{'itemType':1},{'groupActionType':1},{'dateCreated':800000},
                       {'dateCreated':999999},{'hasPayloadData':True},{'text':'\ufffc'}]:
            self.assertIsNone(incoming_text(base|change,1002000,1000000))
        self.assertEqual(incoming_text(base,1002000,1000000),'mock text')
    def test_stale_remote_message_not_sent(self):
        self.enable();self.r.message={'id':'expired','text':'mock','created':100};self.worker.tick();self.assertFalse(self.b.sent)
    def test_worker_lock_excludes_duplicate(self):
        with lock(self.store.home/'worker.lock',True):
            with self.assertRaises(BlockingIOError):
                with lock(self.store.home/'worker.lock',True):pass
    def test_remote_epoch_change_closes(self):
        self.enable();self.r.epoch=2;self.assertFalse(self.worker.tick());self.assertEqual(self.store.get()['enabled'],0)
    def test_no_body_persisted(self):
        self.enable();self.b.items=[message()];self.worker.tick()
        with self.store.db() as db:
            schema=db.execute("SELECT sql FROM sqlite_master WHERE name='ledger'").fetchone()[0]
            self.assertNotIn('text TEXT',schema)
        self.assertNotIn(b'mock text',self.store.path.read_bytes())
    def test_unvalidated_allowlist_cannot_enable(self):
        self.worker.config['validated_single_imessage_chat']=False
        with self.assertRaises(ValueError):self.worker.on()
        self.assertFalse(self.r.calls)

    def test_remote_missing_or_future_original_timestamp_rejected(self):
        self.enable()
        for item in [{'id':'missing','text':'mock'}, {'id':'future','text':'mock','created':1003}]:
            self.r.message=item;self.worker.tick()
        self.assertFalse(self.b.sent)
    def test_age_rechecked_before_intent(self):
        self.enable();self.now=1122
        self.worker.emit('to_imessage','aged-during-wait','mock',self.store.get()['epoch'],created=1001)
        self.assertFalse(self.b.sent)
        with self.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM ledger').fetchone()[0],0)
    def test_query_never_accepts_unscoped_chat_and_overflow_fails_closed(self):
        blue=BlueBubbles({'chat_guid':'only-mock-chat'})
        with patch.object(blue,'request',return_value={'data':[],'metadata':{'total':0}}) as req:
            blue.messages(10,20)
            self.assertEqual(req.call_args.args[0],'message/query')
            self.assertEqual(req.call_args.args[1]['chatGuid'],'only-mock-chat')
        with patch.object(blue,'request',return_value={'data':[],'metadata':{'total':101}}):
            with self.assertRaises(ConnectionError):blue.messages(10,20)
    def test_changed_credential_target_cannot_start_ssh(self):
        config={'credential_helper':'unused','ssh_label':'mock','expected_host_digest':'wrong'}
        with patch('bridge.runpy.run_path',return_value={'extract':lambda label:('mock-host','mock-user','mock-secret')}), patch('bridge.subprocess.run') as ssh:
            with self.assertRaises(ConnectionError):SSH(config).command('unused',{})
            ssh.assert_not_called()
    def test_oversized_stdin_request_rejected_before_ssh(self):
        config={'credential_helper':'unused','ssh_label':'mock','expected_host_digest':hashlib.sha256(b'mock-host').hexdigest()}
        with patch('bridge.runpy.run_path',return_value={'extract':lambda label:('mock-host','mock-user','mock-secret')}), patch('bridge.subprocess.run') as ssh:
            with self.assertRaises(ValueError):SSH(config).command('unused',{'text':'x'*20000})
            ssh.assert_not_called()


    def test_temporary_backend_busy_pauses_without_disabling(self):
        self.enable();self.r.ready=False
        self.assertTrue(self.worker.tick())
        self.assertEqual(self.store.get()['enabled'],1)
        self.assertEqual(self.store.get()['phase'],'paused_backend')
        self.assertFalse(self.b.windows)
        self.r.ready=True;self.now=1010;self.worker.tick()
        self.assertEqual(self.b.windows[-1][0],1010000)

    def test_delayed_database_insert_is_forwarded_once(self):
        self.enable();self.now=1010;self.worker.tick()
        self.now=1015;self.b.items=[message()]
        self.worker.tick()
        self.assertLessEqual(self.b.windows[-1][0],1001000)
        self.worker.tick()
        self.assertEqual(len([x for x in self.r.calls if x[0]=='submit']),1)
    def test_reconnect_floor_prevents_old_messages_in_overlap(self):
        self.enable();self.r.fail='status';self.worker.tick()
        self.now=1010;self.r.fail=None;self.b.items=[message()]
        self.worker.tick()
        self.now=1012;self.worker.tick()
        self.assertEqual(self.store.get()['start_ms'],1010000)
        self.assertEqual([x for x in self.r.calls if x[0]=='submit'],[])
    def test_overlap_has_age_limit(self):
        self.enable();self.now=1300;self.b.items=[message()]
        self.worker.tick()
        self.assertEqual(self.b.windows[-1][0],1180000)
        self.assertEqual([x for x in self.r.calls if x[0]=='submit'],[])

if __name__=='__main__':unittest.main()
