import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from bridge import Bridge,Store,incoming_attachments
from test_bridge import Blue,message
from test_bridge_images import Images,MediaRemote,photo,PNG
from test_managed_control import Remote as ManagedRemote
from test_attachment_api import PDF,MP4
from image_api import MAX_BYTES


def file_message(created=1001000,guid='message-file',**changes):
    return message()|{'guid':guid,'dateCreated':created,'text':None,'attachments':[{'guid':'file-guid','mimeType':'application/pdf','transferName':'报告.pdf','totalBytes':len(PDF)}]}|changes

def item(created=1001,**changes):
    import hashlib
    return {'kind':'attachment','id':'file-event','image_id':'a'*64,'created':created,'size':len(PDF),'digest':hashlib.sha256(PDF).hexdigest(),'mime':'application/pdf','filename':'报告.pdf'}|changes

class Files:
    def __init__(self):self.reads=[];self.sends=[];self.fail_read=False;self.fail_send=False;self.data=PDF;self.hook=None
    def download(self,message,guid):
        self.reads.append(guid)
        if self.fail_read:raise FileNotFoundError()
        return self.data
    def send(self,event,data,filename,mime):
        self.sends.append((event,data,filename,mime))
        if self.hook:self.hook()
        if self.fail_send:raise TimeoutError()

class Transfers:
    def __init__(self):self.puts=[];self.gets=[];self.fail_put=False;self.fail_get=False;self.data=PDF;self.hook=None
    def put(self,*args,**kw):
        self.puts.append((args,kw))
        if self.fail_put:raise TimeoutError()
        if self.hook:self.hook()
        if not kw['active']():raise ConnectionError()
        return {'status':'pending'}
    def get(self,*args,**kw):
        self.gets.append((args,kw))
        if self.hook:self.hook()
        if self.fail_get:raise ValueError('digest mismatch')
        return self.data

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(Path(self.tmp.name));self.now=1000;self.r=MediaRemote();self.blue=Blue();self.images=Images();self.files=Files();self.t=Transfers()
        self.cfg={'chat_guid':'fixed','validated_single_imessage_chat':True,'images_enabled':True,'attachments_enabled':True}
        self.b=Bridge(self.s,self.cfg,self.r,self.blue,lambda:self.now,self.images,self.t,self.files);self.b.on();self.now=1002
    def tearDown(self):self.tmp.cleanup()
    def test_explicit_config_required_and_both_poll_flags(self):
        self.cfg.pop('attachments_enabled');self.blue.items=[file_message()];self.b.tick()
        self.assertFalse(self.files.reads);self.assertNotIn('attachments',[kw for op,kw in self.r.calls if op=='poll'][-1])
        self.cfg['attachments_enabled']=True;self.b.tick();self.assertEqual([kw for op,kw in self.r.calls if op=='poll'][-1],{'images':True,'attachments':True})
    def test_image_switch_disables_attachment_but_text_continues(self):
        self.cfg['images_enabled']=False;self.blue.items=[file_message(),message()];self.b.tick()
        self.assertFalse(self.files.reads);self.assertTrue([op for op,kw in self.r.calls if op=='submit'])
    def test_file_upload_once_preserves_clean_name_mime_bytes(self):
        f=file_message();f['attachments'][0]['transferName']='../../报告.pdf';self.blue.items=[f];self.b.tick();self.b.tick()
        self.assertEqual(len(self.t.puts),1);args,kw=self.t.puts[0];self.assertEqual(args[3],PDF);self.assertEqual(kw['filename'],'报告.pdf');self.assertEqual(kw['mime'],'application/pdf')
        self.assertEqual(len(self.files.reads),1);self.assertFalse([op for op,kw in self.r.calls if op=='submit'])
    def test_file_download_once_no_transcode(self):
        self.r.message=item();self.b.tick();self.r.message=item();self.b.tick()
        self.assertEqual(len(self.t.gets),1);self.assertEqual(self.files.sends,[('file-event',PDF,'报告.pdf','application/pdf')]);self.assertFalse(self.blue.sent)
    def test_upload_unknown_never_retried_and_text_stays_enabled(self):
        self.t.fail_put=True;self.blue.items=[file_message()];self.b.tick();self.t.fail_put=False;self.b.tick()
        self.assertEqual(len(self.t.puts),1);self.assertTrue(self.s.get()['enabled'])
        with self.s.db() as db:self.assertEqual(db.execute("SELECT status FROM ledger WHERE direction='attachment_to_wechat'").fetchone()[0],'unknown')
    def test_download_unknown_never_retried(self):
        self.files.fail_send=True;self.r.message=item();self.b.tick();self.files.fail_send=False;self.r.message=item();self.b.tick()
        self.assertEqual(len(self.files.sends),1);self.assertEqual(len(self.t.gets),1)
    def test_digest_error_never_sends_or_retries(self):
        self.t.fail_get=True;self.r.message=item();self.b.tick();self.t.fail_get=False;self.r.message=item();self.b.tick()
        self.assertFalse(self.files.sends);self.assertEqual(len(self.t.gets),1)
    def test_readonly_download_can_be_retried_before_intent(self):
        self.files.fail_read=True;self.blue.items=[file_message()];self.b.tick();self.assertFalse(self.t.puts)
        self.files.fail_read=False;self.now=1003;self.b.tick();self.assertEqual(len(self.t.puts),1)
    def test_off_fences_both_directions(self):
        self.b.off();self.b.attachment_upload(file_message(),file_message()['attachments'][0],'1');self.b.attachment_download(item(),'1')
        self.assertFalse(self.files.reads);self.assertFalse(self.files.sends);self.assertFalse(self.t.gets)
    def test_closed_after_get_cancels_before_send(self):
        self.t.hook=lambda:self.s.set(enabled=0);self.r.message=item();self.b.tick();self.assertFalse(self.files.sends)
        with self.s.db() as db:self.assertEqual(db.execute("SELECT status FROM ledger WHERE direction='attachment_to_imessage'").fetchone()[0],'cancelled')
    def test_expired_during_get_cancels_before_send(self):
        self.t.hook=lambda:setattr(self,'now',1200);self.r.message=item();self.b.tick();self.assertFalse(self.files.sends)
    def test_new_attachment_floor_excludes_older_than_deployment_with_old_text_floor(self):
        self.s.set(start_ms=1,cursor_ms=1);self.blue.items=[file_message(created=999000),message('old-text')|{'dateCreated':999000}];self.r.message=item(created=999)
        self.b.tick();self.assertFalse(self.files.reads);self.assertFalse(self.t.gets);self.assertTrue([op for op,kw in self.r.calls if op=='submit'])
    def test_worker_construction_floor_is_independent_of_original_text_start(self):
        self.now=1010;self.s.set(start_ms=1);fresh=Bridge(self.s,self.cfg,self.r,self.blue,lambda:self.now,self.images,self.t,self.files)
        self.blue.items=[file_message(created=1009000)];fresh.tick();self.assertFalse(self.files.reads)
    def test_disconnect_gap_not_replayed(self):
        self.r.fail='status';self.b.tick();self.r.fail=None;self.now=1020;self.blue.items=[file_message(created=1010000)];self.r.message=item(created=1010);self.b.tick()
        self.assertFalse(self.files.reads);self.assertFalse(self.t.gets)
    def test_backend_image_readiness_gap_not_replayed(self):
        self.r.images_ready=False;self.b.tick();self.now=1020;self.r.images_ready=True;self.blue.items=[file_message(created=1010000)];self.r.message=item(created=1010);self.b.tick()
        self.assertFalse(self.files.reads);self.assertFalse(self.t.gets)
    def test_filter_outgoing_system_sticker_images_expired_and_future(self):
        f=file_message()
        for change in [{'isFromMe':True},{'associatedMessageType':2000},{'associatedMessageGuid':'reaction'},{'dateCreated':900000},{'dateCreated':1003000},{'attachments':[f['attachments'][0]|{'isSticker':True}]},{'attachments':[f['attachments'][0]|{'mimeType':'image/png'}]},{'attachments':[f['attachments'][0]|{'totalBytes':MAX_BYTES+1}]}]:
            self.assertFalse(incoming_attachments(f|change,1002000,1000000))
        self.assertEqual(len(incoming_attachments(f,1002000,1000000)),1)
    def test_default_missing_mime_uses_octet_stream(self):
        f=file_message();f['attachments'][0].pop('mimeType');self.blue.items=[f];self.b.tick();self.assertEqual(self.t.puts[0][1]['mime'],'application/octet-stream')
    def test_invalid_mp4_rejected_before_intent_and_transfer(self):
        f=file_message();f['attachments'][0].update(mimeType='video/mp4',transferName='clip.mp4');self.blue.items=[f];self.b.tick();self.assertFalse(self.t.puts)
    def test_valid_mp4_preserves_bytes(self):
        f=file_message();f['attachments'][0].update(mimeType='video/mp4',transferName='clip.mp4');self.files.data=MP4;self.blue.items=[f];self.b.tick();self.assertEqual(self.t.puts[0][0][3],MP4)
    def test_oversized_download_cannot_be_uploaded(self):
        self.files.data=b'x'*(MAX_BYTES+1);self.blue.items=[file_message()];self.b.tick();self.assertFalse(self.t.puts)
    def test_attachment_identifier_is_distinct_from_image_ledger(self):
        self.blue.items=[file_message()];self.b.tick()
        with self.s.db() as db:
            row=db.execute('SELECT direction,event_id FROM ledger').fetchone();self.assertEqual(row[0],'attachment_to_wechat');self.assertTrue(row[1].startswith('attachment-'))
    def test_resume_keeps_unknown_dedupe(self):
        self.t.fail_put=True;self.blue.items=[file_message()];self.b.tick();self.t.fail_put=False;self.s.recover()
        fresh=Bridge(Store(self.s.home),self.cfg,self.r,self.blue,lambda:1000,self.images,self.t,self.files);fresh.tick();self.assertEqual(len(self.t.puts),1)
    def test_images_and_text_unchanged_with_attachments_on(self):
        self.t.data=PNG;self.blue.items=[message(),photo()];self.b.tick()
        self.assertTrue([op for op,kw in self.r.calls if op=='submit']);self.assertEqual(self.images.downloads,1);self.assertEqual(len(self.t.puts),1);self.assertNotIn('filename',self.t.puts[0][1]);self.assertFalse(self.files.reads)

class ManagedTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(Path(self.tmp.name));self.now=1000;self.r=ManagedRemote();self.blue=Blue();self.files=Files();self.t=Transfers()
        self.cfg={'chat_guid':'fixed','validated_single_imessage_chat':True,'managed_control':True,'attachments_enabled':True}
        self.b=Bridge(self.s,self.cfg,self.r,self.blue,lambda:self.now,Images(),self.t,self.files);self.b.managed_sync();self.now=1002
    def tearDown(self):self.tmp.cleanup()
    def test_revision_without_bool_change_resets_attachment_only(self):
        start=self.s.get()['start_ms'];self.r.revision+=2;self.now=1010;self.r.item=item(created=1005);self.blue.items=[file_message(created=1005000)];self.b.tick()
        self.assertFalse(self.t.gets);self.assertFalse(self.t.puts);self.assertEqual(self.s.get()['start_ms'],start);self.assertEqual(self.s.get()['epoch'],'10');self.assertEqual(self.b.attachment_since(),1010000)
    def test_picture_off_then_on_drops_gap_files(self):
        self.r.images=False;self.b.tick();self.now=1010;self.r.images=True;self.r.item=item(created=1005);self.blue.items=[file_message(created=1005000)];self.b.tick();self.assertFalse(self.files.reads);self.assertFalse(self.t.gets)
    def test_control_disconnect_recovery_drops_gap(self):
        self.r.fail='control_sync';self.b.tick();self.r.fail=None;self.now=1010;self.r.item=item(created=1005);self.blue.items=[file_message(created=1005000)];self.b.tick();self.assertFalse(self.files.reads);self.assertFalse(self.t.gets)
    def test_epoch_change_fences_previous_and_new_starts_now(self):
        self.r.epoch+=1;self.now=1010;self.r.item=item(created=1005);self.b.tick();self.assertFalse(self.t.gets);self.assertFalse(self.b.attachment_active('10'));self.assertTrue(self.b.attachment_active('11'))
    def test_reenable_after_remote_close_drops_gap(self):
        self.r.enabled=False;self.b.tick();self.now=1010;self.r.enabled=True;self.r.epoch+=1;self.r.item=item(created=1005);self.b.tick();self.assertFalse(self.t.gets)
    def test_attachments_since_extra_field_accepted_without_changing_text_floor(self):
        original=self.r.call
        def call(op,**kw):
            result=original(op,**kw)
            if op=='control_sync':result['control']['attachments_since']=1001.0
            return result
        self.r.call=call;self.assertIsNotNone(self.b.managed_sync());self.assertEqual(self.s.get()['start_ms'],1000000)
    def test_upgrade_worker_preserves_text_state_and_refreshes_file_floor(self):
        self.s.set(start_ms=800000,cursor_ms=900000)
        self.now=1010
        def tick():
            self.assertIsNotNone(self.b.managed_sync())
            state=self.s.get();self.assertEqual(state['start_ms'],800000);self.assertEqual(state['cursor_ms'],900000);self.assertEqual(state['epoch'],'10')
            self.assertEqual(self.b.attachment_floor_ms,1010000)
            raise InterruptedError()
        with patch.object(self.b,'tick',side_effect=tick):
            with self.assertRaises(InterruptedError):self.b.run(preserve_text_state=True)
    def test_upgrade_worker_refuses_inactive_state(self):
        self.s.set(enabled=0)
        with self.assertRaises(ValueError):self.b.run(preserve_text_state=True)
    def test_control_ack_shape_unchanged(self):
        ack=[kw for op,kw in self.r.calls if op=='control_ack'][-1];self.assertEqual(set(ack),{'revision','epoch','enabled','images_enabled','phase'})

if __name__=='__main__':unittest.main()
