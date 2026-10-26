"""Explicit 25-MiB product boundary. No network, accounts or message endpoints."""
import tempfile
import unittest
from pathlib import Path
from image_api import MAX_BYTES,image_kind
from attachment_api import validate_file
from image_transfer import ImageTransfer
from bridge import incoming_images,incoming_attachments
from test_bridge_images import photo
from test_bridge_attachments import file_message

LIMIT=25*1024*1024
FORMATS=[(b'\xff\xd8\xff','image/jpeg','image.jpg'),(b'\x89PNG\r\n\x1a\n','image/png','image.png'),(b'%PDF-1.4\n','application/pdf','file.pdf'),(b'\x00\x00\x00\x18ftypisom','video/mp4','video.mp4')]

class Tests(unittest.TestCase):
    def test_jpeg_png_file_mp4_accept_exact_25_mib_reject_plus_one(self):
        for prefix,mime,name in FORMATS:
            with self.subTest(mime=mime):
                data=prefix+b'x'*(LIMIT-len(prefix))
                if mime.startswith('image/'):
                    self.assertEqual(image_kind(data)[1],mime)
                    with self.assertRaises(ValueError):image_kind(data+b'x')
                else:
                    validate_file(data,mime,name)
                    with self.assertRaises(ValueError):validate_file(data+b'x',mime,name)
                self.assertEqual(MAX_BYTES,LIMIT)
    def test_incoming_metadata_accepts_exact_and_rejects_plus_one(self):
        for source,filter_fn in [(photo,incoming_images),(file_message,incoming_attachments)]:
            message=source();message['attachments'][0]['totalBytes']=LIMIT
            self.assertEqual(len(filter_fn(message,1002000,1000000)),1)
            message['attachments'][0]['totalBytes']=LIMIT+1
            self.assertEqual(filter_fn(message,1002000,1000000),[])
    def test_get_rejects_oversize_before_transport_for_images_and_files(self):
        class NoNetwork(ImageTransfer):
            def _execute(self,*a,**kw):raise AssertionError('must reject before SSH')
        with tempfile.TemporaryDirectory() as directory:
            transfer=NoNetwork({},cache=Path(directory),clock=lambda:1001)
            for _,mime,name in FORMATS:
                item={'kind':'image' if mime.startswith('image/') else 'attachment','image_id':'a'*64,'size':LIMIT+1,'digest':'b'*64,'mime':mime,'created':1000}
                if item['kind']=='attachment':item['filename']=name
                with self.assertRaises(ValueError):transfer.get(item,1)
            self.assertFalse(list(Path(directory).iterdir()))
    def test_put_rejects_oversize_before_spooling_for_images_and_files(self):
        class NoNetwork(ImageTransfer):
            def _execute(self,*a,**kw):raise AssertionError('must reject before SSH')
        with tempfile.TemporaryDirectory() as directory:
            transfer=NoNetwork({},cache=Path(directory),clock=lambda:1001)
            for prefix,mime,name in FORMATS:
                data=prefix+b'x'*(LIMIT+1-len(prefix))
                options={} if mime.startswith('image/') else {'filename':name,'mime':mime}
                with self.assertRaises(ValueError):transfer.put('mock-event',1,1000,data,**options)
            self.assertFalse(list(Path(directory).iterdir()))

if __name__=='__main__':unittest.main()
