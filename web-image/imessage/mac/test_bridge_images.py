import tempfile
import unittest
from pathlib import Path
from bridge import Bridge,Store,incoming_images
from test_bridge import Remote,Blue,message

PNG=b'\x89PNG\r\n\x1a\nmock'

class Images:
    def __init__(self):self.downloads=0;self.sends=0;self.fail_read=False;self.fail_send=False
    def download(self,message,guid):
        self.downloads+=1
        if self.fail_read:raise FileNotFoundError()
        return PNG
    def send(self,event,data):
        self.sends+=1
        if self.fail_send:raise TimeoutError()

class Transfers:
    def __init__(self):self.puts=0;self.gets=0;self.fail_put=False
    def put(self,*a,**kw):
        self.puts+=1
        if self.fail_put:raise TimeoutError()
        return {'status':'pending'}
    def get(self,*a,**kw):self.gets+=1;return PNG

class MediaRemote(Remote):
    images_ready=True
    def call(self,op,**kw):return super().call(op,**kw)|{'images_ready':self.images_ready}

def photo():return message()|{'text':None,'attachments':[{'guid':'mock-attachment','mimeType':'image/png','totalBytes':len(PNG)}]}

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name));self.r=MediaRemote();self.b=Blue();self.i=Images();self.t=Transfers();self.now=1000
        self.config={'chat_guid':'mock','validated_single_imessage_chat':True,'images_enabled':True}
        self.worker=Bridge(self.store,self.config,self.r,self.b,lambda:self.now,images=self.i,image_remote=self.t)
        self.worker.on();self.now=1002
    def tearDown(self):self.temp.cleanup()
    def test_images_default_off_no_poll_optin(self):
        self.config.pop('images_enabled');self.b.items=[photo()];self.worker.tick()
        self.assertEqual(self.i.downloads,0)
        self.assertNotIn('images',[kw for op,kw in self.r.calls if op=='poll'][0])
    def test_upload_only_once_across_overlap(self):
        self.b.items=[photo()];self.worker.tick();self.worker.tick()
        self.assertEqual(self.t.puts,1);self.assertEqual(self.i.downloads,1)
        self.assertFalse([x for x in self.r.calls if x[0]=='submit'])
    def test_waiting_attachment_can_be_read_later_without_repeated_send(self):
        self.b.items=[photo()];self.i.fail_read=True;self.worker.tick()
        self.assertEqual(self.t.puts,0)
        self.i.fail_read=False;self.now=1004;self.worker.tick();self.worker.tick()
        self.assertEqual(self.t.puts,1)
    def test_unknown_upload_not_retried_and_text_not_disabled(self):
        self.t.fail_put=True;self.b.items=[photo()];self.worker.tick();self.worker.tick()
        self.assertEqual(self.t.puts,1);self.assertEqual(self.store.get()['enabled'],1)
    def test_download_send_once_and_unknown_not_retried(self):
        item={'id':'image-event','kind':'image','created':1001}
        self.i.fail_send=True
        self.r.message=item;self.worker.tick();self.r.message=item;self.worker.tick()
        self.assertEqual(self.t.gets,1);self.assertEqual(self.i.sends,1);self.assertEqual(self.store.get()['enabled'],1)
    def test_old_outgoing_reaction_and_sticker_ignored(self):
        for change in [{'isFromMe':True},{'dateCreated':999999},{'associatedMessageType':2000}]:
            self.assertFalse(incoming_images(photo()|change,1002000,1000000))
        self.assertFalse(incoming_images(photo()|{'attachments':[{'guid':'one','mimeType':'image/png','isSticker':True}]},1002000,1000000))
    def test_off_fences_images(self):
        self.worker.off();self.worker.image_upload(photo(),photo()['attachments'][0],'1');self.assertEqual(self.t.puts,0)
    def test_text_still_works_with_image_feature_on(self):
        self.b.items=[message()];self.worker.tick();self.assertEqual(len([x for x in self.r.calls if x[0]=='submit']),1)

    def test_enabling_image_readiness_does_not_backfill_earlier_photos(self):
        self.r.images_ready=False;self.b.items=[photo()];self.worker.tick()
        self.r.images_ready=True;self.now=1005;self.worker.tick()
        self.assertEqual(self.t.puts,0)
        self.now=1006;self.b.items=[photo()|{'dateCreated':1006000}];self.worker.tick()
        self.assertEqual(self.t.puts,1)

if __name__=='__main__':unittest.main()
