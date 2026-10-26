import base64
import hashlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from PIL import Image
from image_store import ImageStore,CHUNK_BYTES,MAX_BYTES
from media_types import validate
from image_runtime import verify_image,ImageSender
from image_outbound import Outbound

class MediaLimitTests(unittest.TestCase):
    def test_unified_limit_and_all_formats_at_boundary(self):
        from media_types import MAX_BYTES as validation_limit
        self.assertEqual(MAX_BYTES,25*1024*1024);self.assertEqual(MAX_BYTES,validation_limit)
        with tempfile.TemporaryDirectory() as tmp:
            store=ImageStore(tmp)
            for index,(mime,name) in enumerate([('image/jpeg',None),('image/png',None),('video/mp4','clip.mp4'),('application/pdf','doc.pdf')]):
                store.begin(str(index),1,MAX_BYTES,'a'*64,mime,name)
                with self.assertRaises(ValueError):store.begin('bad'+str(index),1,MAX_BYTES+1,'a'*64,mime,name)
    def test_full_25mib_upload_claim_integrity(self):
        data=b'%PDF-1.4\n'+b'x'*(MAX_BYTES-9);digest=hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            store=ImageStore(tmp);key=store.begin('large',1,len(data),digest,'application/pdf','large.pdf')['id']
            for offset in range(0,len(data),CHUNK_BYTES):store.append(key,1,offset,base64.b64encode(data[offset:offset+CHUNK_BYTES]).decode())
            store.commit(key,1);item=store.claim(1)
            self.assertEqual(hashlib.sha256(item['data']).hexdigest(),digest)
            self.assertIsNone(store.claim(1));store.finish(key,'sent')
            self.assertTrue(store.begin('large',1,len(data),digest,'application/pdf','large.pdf')['duplicate'])
    def test_large_jpeg_png_decoder_and_file_validation(self):
        for fmt,mime in [('JPEG','image/jpeg'),('PNG','image/png')]:
            out=io.BytesIO();Image.new('RGB',(20,20)).save(out,format=fmt)
            data=out.getvalue()+b'\0'*(MAX_BYTES-len(out.getvalue()))
            self.assertEqual(verify_image(data,mime),data)
        data=b'\0\0\0\x18ftypmp42'+b'\0'*(MAX_BYTES-12)
        validate(data,'video/mp4')
        with self.assertRaises(ValueError):validate(data+b'\0','video/mp4')
    def test_outbound_boundary(self):
        data=b'%PDF'+b'x'*(MAX_BYTES-4)
        with tempfile.TemporaryDirectory() as tmp:
            store=Outbound({'state_dir':tmp},fetch=lambda _:data)
            item=store.grant('a'*64,1,'id',time.time(),filename='big.pdf',mime='application/pdf')
            self.assertEqual(item['size'],MAX_BYTES)
            self.assertEqual(store.take('a'*64,1)[1],data)
            store.fetch=lambda _:data+b'x'
            with self.assertRaises(ValueError):store.grant('b'*64,1,'id',time.time(),filename='big.pdf',mime='application/pdf')
    def test_telegram_large_image_uses_document_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'tg.yaml';cfg.write_text('token: test\nflags:\n  api_base_url: http://example.invalid/\n')
            sender=ImageSender.__new__(ImageSender);sender.gateway=Mock(config={'telegram_config':str(cfg),'chat_id':'test'})
            reply=Mock();reply.json.return_value={'ok':True}
            with patch('requests.post',return_value=reply) as send:
                for size,method in [(100, 'sendPhoto'),(25*1024*1024,'sendDocument')]:
                    sender.mirror({'mime':'image/jpeg','data':b'x'*size})
                    self.assertTrue(send.call_args.args[0].endswith(method))
                self.assertEqual(send.call_count,2)
