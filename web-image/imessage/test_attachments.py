import base64
import hashlib
import io
import json
import time
import unittest
from unittest.mock import Mock,patch
from test_image_runtime import ImageRuntimeTests
from test_image_pipe import ImageProtocolTests
from image_store import ImageStore
from image_outbound import Outbound
from image_pipe import transfer
from media_types import filename,validate

class AttachmentRuntimeTests(ImageRuntimeTests):
    def setUp(self):
        super().setUp();self.sender.images.cancel();self.g.config['attachments_enabled']=True
        data=b'%PDF-1.4\ncontent'
        self.key=self.sender.images.begin('document',1,len(data),hashlib.sha256(data).hexdigest(),'application/pdf','通知.pdf')['id']
        self.sender.images.append(self.key,1,0,base64.b64encode(data).decode());self.sender.images.commit(self.key,1)
        self.slave._bot_send_file.return_value=object()
    def test_file_transport_and_copy_share_content(self):
        seen=[]
        self.slave._bot_send_file.side_effect=lambda c,n,f:seen.append((n,f.read())) or True
        self.g.config['mirror_to_telegram']=True;self.sender.mirror=Mock()
        self.sender.tick(self.slave);self.finish()
        self.assertEqual(seen,[('通知.pdf',b'%PDF-1.4\ncontent')]);self.assertEqual(self.status(),'sent')
        self.sender.verifier=Mock();self.slave._bot_send_image.assert_not_called()
        self.assertEqual(self.sender.mirror.call_args[0][0]['data'],seen[0][1])
    def test_mp4_uses_video_transport(self):
        self.sender.images.cancel();data=b'\0\0\0\x18ftypmp42video'
        self.key=self.sender.images.begin('clip',1,len(data),hashlib.sha256(data).hexdigest(),'video/mp4','视频.mp4')['id']
        self.sender.images.append(self.key,1,0,base64.b64encode(data).decode());self.sender.images.commit(self.key,1)
        self.slave._bot_send_video.return_value=True
        self.sender.tick(self.slave);self.finish()
        self.slave._bot_send_video.assert_called_once();self.slave._bot_send_file.assert_not_called()
        self.assertEqual(self.status(),'sent')
    def test_attachment_switch_blocks_file(self):
        self.g.config['attachments_enabled']=False
        self.sender.tick(self.slave);self.finish();self.slave._bot_send_file.assert_not_called()
        self.assertEqual(self.status(),'blocked')
    def test_file_uncertain_never_retries(self):
        self.slave._bot_send_file.side_effect=TimeoutError()
        self.sender.tick(self.slave);self.finish();self.sender.tick(self.slave)
        self.slave._bot_send_file.assert_called_once();self.assertEqual(self.status(),'unknown')
# Parent image-specific cases are covered separately, not inherited as file cases.
for name in list(ImageRuntimeTests.__dict__):
    if name.startswith('test_'):setattr(AttachmentRuntimeTests,name,None)

class AttachmentProtocolTests(ImageProtocolTests):
    def test_file_metadata_and_at_most_once(self):
        self.cfg.update(attachments_enabled=True,attachments_since=0)
        data=b'file content';header=dict(self.header,filename='../../通知.txt',mime='text/plain',size=len(data),digest=hashlib.sha256(data).hexdigest())
        out=[];transfer(self.g,'put',self.stream([header,{'offset':0,'data':base64.b64encode(data).decode()},{'op':'commit'}]),out.append)
        item=ImageStore(self.g.root/'media').claim(self.epoch)
        self.assertEqual(item['filename'],'通知.txt');self.assertEqual(item['data'],data)
        out=[];transfer(self.g,'put',self.stream([header]),out.append);self.assertEqual(out[0]['status'],'duplicate')
    def test_file_disabled_and_old_fail_closed(self):
        h=dict(self.header,filename='file.pdf',mime='application/pdf')
        with self.assertRaisesRegex(ValueError,'disabled'):transfer(self.g,'put',self.stream([h]),lambda _:None)
        self.cfg.update(attachments_enabled=True,attachments_since=time.time()+1)
        with self.assertRaisesRegex(ValueError,'before_enable'):transfer(self.g,'put',self.stream([h]),lambda _:None)
    def test_gateway_requires_new_client_and_enable_time(self):
        import sqlite3,datetime
        self.cfg.update(attachments_enabled=True,attachments_since=0)
        with sqlite3.connect(self.cfg['bindings_db']) as db:
            db.execute('ALTER TABLE msglog ADD COLUMN file_id TEXT')
            db.execute('ALTER TABLE msglog ADD COLUMN mime TEXT')
            db.execute('INSERT INTO msglog VALUES (?,?,?,?,?,?,?,?)',('doc','通知.pdf','File',self.cfg['target'],'blueset.telegram',str(datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)),'file','application/pdf'))
        with patch('gateway.images_ready',return_value=True),patch.object(Outbound,'fetch_file',return_value=b'%PDF-1.4'):
            self.assertIsNone(self.g.run({'op':'poll','images':True})['message'])
            state=json.loads((self.g.root/'state.json').read_text());state['cursor']=0;(self.g.root/'state.json').write_text(json.dumps(state))
            result=self.g.run({'op':'poll','images':True,'attachments':True})['message']
        self.assertEqual(result['kind'],'attachment');self.assertEqual(result['filename'],'通知.pdf')
    def test_file_download_metadata_preserved(self):
        store=Outbound(self.cfg,fetch=lambda _:b'%PDF-1.4');key='b'*64
        item=store.grant(key,self.epoch,'id',time.time(),filename='通知.pdf',mime='application/pdf')
        self.assertEqual(item['kind'],'attachment')
        out=[];transfer(self.g,'get',self.stream([{'epoch':self.epoch,'image_id':key}]),out.append)
        self.assertEqual(out[0]['filename'],'通知.pdf');self.assertEqual(out[0]['kind'],'attachment')
        with self.assertRaises(ValueError):store.take(key,self.epoch)

class MediaValidationTests(unittest.TestCase):
    def test_path_controls_and_size(self):
        self.assertEqual(filename('../a\\b\r\n.txt'),'b.txt')
        self.assertEqual(filename('..'),'attachment.bin')
        long_name='照片'*80+'.pdf'
        self.assertLessEqual(len(filename(long_name).encode('utf-8')),240)
        self.assertTrue(filename(long_name).endswith('.pdf'))
        self.assertEqual(filename(filename(long_name)),filename(long_name))
        name='a'*179+' '+'.pdf'
        self.assertEqual(filename(filename(name)),filename(name))
        with self.assertRaises(ValueError):validate(b'x'*(25*1024*1024+1),'application/pdf')
        with self.assertRaises(ValueError):validate(b'fake','video/mp4')
        validate(b'\0\0\0\x18ftypmp42','video/mp4')
        with self.assertRaises(ValueError):validate(b'x','text/plain\r\nInjected')
