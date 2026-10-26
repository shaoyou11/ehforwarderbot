import base64
import hashlib
import io
import json
import time
from unittest.mock import patch
import test_gateway as fixtures
import unittest
from image_pipe import transfer
from image_store import ImageStore
from image_outbound import Outbound

class ImageProtocolTests(unittest.TestCase):
    def setUp(self):
        fixtures.GatewayTests.setUp(self);self.epoch=self.g.run({'op':'on'})['epoch']
        self.data=b'\xff\xd8\xffimage';self.cfg['images_enabled']=True
        self.header={'epoch':self.epoch,'event_id':'image1','created':time.time(),'size':len(self.data),'digest':hashlib.sha256(self.data).hexdigest(),'mime':'image/jpeg'}
        self.ready=patch('image_pipe.images_ready',return_value=True);self.ready.start();self.addCleanup(self.ready.stop)
    def stream(self, values):return io.BytesIO(('\n'.join(json.dumps(v) for v in values)+'\n').encode())
    def test_runtime_does_not_open_root_private_mirror_database(self):
        from types import SimpleNamespace
        from image_runtime import start
        folder=self.root/'imessage';folder.mkdir()
        self.cfg['mirror_to_telegram']=True
        (folder/'image-runtime-config.json').write_text(json.dumps(self.cfg))
        with patch('image_runtime.threading.Thread'):
            sender=start(SimpleNamespace(root=self.root))
        self.assertIsNone(sender.gateway.mirror)
        self.assertTrue(sender.gateway.config['mirror_to_telegram'])
        self.assertFalse((self.g.root/'mirrors.sqlite').exists())

    def test_put_once_and_no_duplicate_body_request(self):
        values=[self.header,{'offset':0,'data':base64.b64encode(self.data).decode()},{'op':'commit'}]
        out=[];transfer(self.g,'put',self.stream(values),out.append)
        self.assertEqual(out[0],'IMESSAGE_IMAGE_BODY_READY');self.assertEqual(out[-1]['status'],'pending')
        out=[];transfer(self.g,'put',self.stream([self.header]),out.append)
        self.assertEqual(out[0]['status'],'duplicate')
    def test_old_or_disabled_rejected(self):
        self.header['created']=time.time()-121
        with self.assertRaises(ValueError):transfer(self.g,'put',self.stream([self.header]),lambda x:None)
        self.header['created']=time.time();self.g.run({'op':'off'})
        with self.assertRaises(ValueError):transfer(self.g,'put',self.stream([self.header]),lambda x:None)
    def test_gap_cancels_only_upload(self):
        with self.assertRaises(ValueError):transfer(self.g,'put',self.stream([self.header,{'offset':8192,'data':'eA=='}]),lambda x:None)
        store=ImageStore(self.g.root/'media')
        with store.db() as db:
            self.assertEqual(db.execute('SELECT status FROM images').fetchone()[0],'cancelled')
            self.assertEqual(db.execute('SELECT count(*) FROM chunks').fetchone()[0],0)
    def test_download_only_consumed_grant_and_epoch(self):
        store=Outbound(self.cfg,fetch=lambda _:self.data);key='a'*64
        store.grant(key,self.epoch,'opaque-file',time.time())
        with self.assertRaises(ValueError):store.take(key,self.epoch+1)
        out=[];transfer(self.g,'get',self.stream([{'epoch':self.epoch,'image_id':key}]),out.append)
        self.assertEqual(out[0]['kind'],'image');self.assertEqual(out[-1],{'status':'complete'})
        with self.assertRaises(ValueError):store.take(key,self.epoch)
    def test_gateway_images_opt_in_and_off_cancels(self):
        with self.g.db() as db:pass
        import sqlite3,datetime
        with sqlite3.connect(self.cfg['bindings_db']) as db:
            db.execute('ALTER TABLE msglog ADD COLUMN file_id TEXT')
            db.execute('INSERT INTO msglog VALUES (?,?,?,?,?,?,?)',('pic','', 'Image',self.cfg['target'],'blueset.telegram',str(datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)),'file'))
        # Text-only old client must never receive image data.
        self.assertIsNone(self.g.run({'op':'poll'})['message'])
        state=json.loads((self.g.root/'state.json').read_text());state['cursor']=0;(self.g.root/'state.json').write_text(json.dumps(state))
        with patch('gateway.images_ready',return_value=True),patch.object(Outbound,'fetch_file',return_value=self.data):
            item=self.g.run({'op':'poll','images':True})['message']
        self.assertEqual(item['kind'],'image');self.g.run({'op':'off'})
        with self.assertRaises(ValueError):Outbound(self.cfg).take(item['image_id'],self.epoch)

if __name__=='__main__':unittest.main()
