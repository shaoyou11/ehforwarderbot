import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from bridge import Bridge,Store,launch,lock
from test_bridge import Blue,message
from test_bridge_images import Images,Transfers,photo

class Remote:
    def __init__(self):
        self.calls=[];self.revision=1;self.epoch=10;self.enabled=True;self.images=True;self.ready=True;self.images_ready=True
        self.fail=None;self.ack={'acknowledged':True};self.hook=None;self.item=None
    def call(self,op,**kw):
        self.calls.append((op,kw))
        if self.hook:self.hook(op)
        if op==self.fail:raise ConnectionError()
        if op=='off':self.enabled=False;return {'enabled':False}
        if op=='on':self.enabled=True
        if op=='control_ack':return self.ack
        if op=='control_sync':return {'control':{'revision':self.revision,'enabled':self.enabled,'images_enabled':self.images},'enabled':self.enabled,'epoch':self.epoch,'ready':self.ready,'images_ready':self.images_ready}
        if op=='poll':return {'enabled':self.enabled,'epoch':self.epoch,'message':self.item}
        return {'enabled':self.enabled,'epoch':self.epoch,'ready':self.ready,'status':'pending'}

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name));self.now=1000
        self.r=Remote();self.blue=Blue();self.images=Images();self.transfer=Transfers()
        self.config={'managed_control':True,'chat_guid':'mock','validated_single_imessage_chat':True}
        self.b=Bridge(self.store,self.config,self.r,self.blue,lambda:self.now,self.images,self.transfer)
    def tearDown(self):self.tmp.cleanup()
    def sync(self):return self.b.managed_sync()
    def test_sync_applies_actual_and_acknowledges(self):
        self.assertIsNotNone(self.sync());s=self.store.get();self.assertEqual(s['enabled'],1);self.assertEqual(s['control_acked_at'],1000)
        self.assertEqual([x[0] for x in self.r.calls],['control_sync','control_ack'])
        self.assertEqual(self.r.calls[-1][1],{'revision':1,'epoch':10,'enabled':True,'images_enabled':True,'phase':'enabled'})
    def test_same_revision_does_not_reset_floor_or_toggle(self):
        self.sync();self.now=1010;self.sync();self.assertEqual(self.store.get()['start_ms'],1000000)
        self.assertFalse([op for op,k in self.r.calls if op in ['on','off']])
    def test_idle_stays_alive_and_image_preference_acknowledged(self):
        self.r.enabled=False;self.assertTrue(self.b.tick());self.assertEqual(self.r.calls[-1][1]['phase'],'closed');self.assertTrue(self.r.calls[-1][1]['images_enabled'])
        self.assertFalse(self.store.get()['enabled']);self.assertFalse(self.blue.windows)
    def test_remote_enable_starts_now(self):
        self.r.enabled=False;self.sync();self.now=1020;self.r.enabled=True;self.r.epoch+=1;self.sync();self.assertEqual(self.store.get()['start_ms'],1020000)
    def test_epoch_change_starts_now(self):
        self.sync();self.now=1010;self.r.epoch+=1;self.sync();self.assertEqual(self.store.get()['start_ms'],1010000)
    def test_picture_toggle_does_not_reset_text(self):
        self.sync();self.r.images=False;self.now=1005;self.sync();self.assertFalse(self.b.image_active('10'));self.r.images=True;self.now=1010;self.sync()
        self.assertEqual(self.store.get()['start_ms'],1000000);self.assertEqual(self.store.get()['image_start_ms'],1010000)
    def test_unobserved_off_on_revision_resets_only_image_floor(self):
        self.sync();self.now=1010;self.r.revision+=2;self.sync()
        s=self.store.get();self.assertEqual(s['start_ms'],1000000);self.assertEqual(s['cursor_ms'],1000000)
        self.assertEqual(s['epoch'],'10');self.assertEqual(s['image_start_ms'],1010000)
        self.assertEqual(self.b.image_floor_ms,1010000);self.assertFalse([op for op,k in self.r.calls if op in ['on','off']])
        self.now=1020;self.sync();self.assertEqual(self.store.get()['image_start_ms'],1010000)
    def test_sync_failure_fences_without_ack_or_toggle(self):
        self.sync();self.r.fail='control_sync';self.now=1005;self.assertIsNone(self.sync());s=self.store.get();self.assertFalse(s['enabled']);self.assertEqual(s['control_acked_at'],1000);self.assertEqual(s['phase'],'paused_connection')
    def test_failed_ack_is_not_confirmation(self):
        self.r.ack={'error':'stale_ack'};self.assertIsNone(self.sync());self.assertEqual(self.store.get()['control_acked_at'],0);self.assertFalse(self.store.get()['enabled'])
    def test_wrong_ack_field_is_not_confirmation(self):
        self.r.ack={'ok':True};self.assertIsNone(self.sync())
    def test_stale_ack_echo_rejected(self):
        self.r.ack={'acknowledged':True,'epoch':9};self.assertIsNone(self.sync())
    def test_backend_down_acknowledges_actual_false(self):
        self.r.ready=False;self.sync();self.assertFalse(self.r.calls[-1][1]['enabled']);self.assertEqual(self.r.calls[-1][1]['phase'],'paused_backend')
    def test_image_unready_keeps_text_enabled(self):
        self.r.images_ready=False;self.sync();self.assertTrue(self.store.get()['enabled']);self.assertTrue(self.r.calls[-1][1]['images_enabled'])
    def test_invalid_revision_types_rejected(self):
        for value in [True,'1',-1,None]:
            self.r.revision=value;self.assertIsNone(self.sync())
        self.assertFalse([x for x in self.r.calls if x[0]=='control_ack'])
    def test_invalid_epoch_types_rejected(self):
        for value in [True,'1',-1,None]:self.r.epoch=value;self.assertIsNone(self.sync())
    def test_invalid_flags_rejected(self):
        for value in [1,'true',None]:self.r.images=value;self.assertIsNone(self.sync())
    def test_unvalidated_allowlist_acknowledges_failed(self):
        self.config['validated_single_imessage_chat']=False;self.sync();self.assertEqual(self.r.calls[-1][1]['phase'],'enable_failed');self.assertFalse(self.r.calls[-1][1]['enabled'])
    def test_off_race_during_sync_never_acknowledges_enabled(self):
        self.r.hook=lambda op:self.store.set(enabled=0,pending_off=1,off_requested=1) if op=='control_sync' else None
        self.assertIsNone(self.sync());self.assertFalse([x for x in self.r.calls if x[0]=='control_ack'])
    def test_off_race_during_ack_does_not_stamp_confirmation(self):
        self.r.hook=lambda op:self.store.set(enabled=0,pending_off=1,off_requested=1) if op=='control_ack' else None
        self.assertIsNone(self.sync());self.assertEqual(self.store.get()['control_acked_at'],0)
    def test_pending_explicit_close_reconciles_once(self):
        self.store.set(enabled=0,pending_off=1,off_requested=1);self.sync();self.sync();self.assertEqual([op for op,k in self.r.calls].count('off'),1)
    def test_failed_desktop_close_keeps_fence(self):
        self.r.fail='off';self.assertFalse(self.b.off());self.assertIsNone(self.sync());self.assertFalse([x for x in self.r.calls if x[0]=='control_sync'])
    def test_stale_close_reconciler_cannot_override_newer_local_state(self):
        self.store.set(off_requested=0);self.assertTrue(self.b._reconcile_managed_close());self.assertFalse(self.r.calls)
    def test_traffic_reconnection_drops_gap(self):
        self.sync();self.b.disconnected=True;self.now=1030;self.sync();self.assertEqual(self.store.get()['start_ms'],1030000)
    def test_control_reconnection_drops_gap(self):
        self.sync();self.r.fail='control_sync';self.sync();self.r.fail=None;self.now=1030;self.sync();self.assertEqual(self.store.get()['start_ms'],1030000)
    def test_image_off_fences_download_and_upload(self):
        self.r.images=False;self.sync();self.b.image_upload(photo(),photo()['attachments'][0],'10');self.b.image_download({'id':'image','created':1000},'10');self.assertEqual(self.transfer.puts+self.transfer.gets,0)
    def test_unknown_ledger_preserved_across_migration(self):
        self.store.intent('to_imessage','old');self.store.recover();Store(self.store.home);self.assertFalse(self.store.intent('to_imessage','old'))
        with self.store.db() as db:self.assertEqual(db.execute('SELECT status FROM ledger').fetchone()[0],'unknown')
    def test_queued_text_from_disconnected_gap_is_not_replayed(self):
        self.sync();self.b.disconnected=True;self.now=1030
        self.r.item={'id':'during-gap','text':'mock','created':1020};self.b.tick();self.assertFalse(self.blue.sent)
        self.now=1031;self.r.item={'id':'new-after-recovery','text':'mock','created':1031};self.b.tick();self.assertEqual(len(self.blue.sent),1)
    def test_existing_worker_lock_prevents_duplicate_launch(self):
        with lock(self.store.home/'worker.lock',True),patch('bridge.subprocess.Popen') as popen:launch(self.store.home);popen.assert_not_called()
    def test_idle_loop_waits_fifteen_seconds(self):
        self.r.enabled=False
        with patch('bridge.time.sleep',side_effect=InterruptedError) as sleep:
            with self.assertRaises(InterruptedError):self.b.run()
        sleep.assert_called_once_with(15)
    def test_active_loop_waits_five_seconds(self):
        with patch('bridge.time.sleep',side_effect=InterruptedError) as sleep:
            with self.assertRaises(InterruptedError):self.b.run()
        sleep.assert_called_once_with(5)

if __name__=='__main__':unittest.main()
