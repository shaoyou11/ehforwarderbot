import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from image_transfer import ImageTransfer,read_download
from test_image_transfer import download_file
from test_attachment_api import PDF,MP4
from image_api import MAX_BYTES

META={'kind':'attachment','image_id':'a'*64,'size':len(PDF),'digest':hashlib.sha256(PDF).hexdigest(),'mime':'application/pdf','created':1000,'filename':'报告.pdf'}

class FileTransfer(ImageTransfer):
    def _execute(self,mode,header,input_path,output_path,active=None):
        self.header=header;self.private=input_path.stat().st_mode&0o777
        if active is not None and not active():raise ConnectionError('cancelled')
        if mode=='put':
            self.frames=input_path.read_text().splitlines();output_path.write_text('{"status":"pending"}\n')
        else:output_path.write_text(download_file(PDF,META))

class Tests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.t=FileTransfer({},cache=Path(self.tmp.name),clock=lambda:1001)
    def tearDown(self):self.tmp.cleanup()
    def test_put_header_filename_and_bytes(self):
        self.t.put('file-event',10,1000,PDF,filename='../../报告.pdf',mime='application/pdf')
        self.assertEqual(self.t.header,{'epoch':10,'event_id':'file-event','created':1000,'size':len(PDF),'digest':META['digest'],'mime':'application/pdf','filename':'报告.pdf'})
        self.assertNotIn('kind',self.t.header);self.assertEqual(base64.b64decode(json.loads(self.t.frames[0])['data']),PDF)
        self.assertEqual(self.t.private,0o600);self.assertFalse(list(Path(self.tmp.name).iterdir()))
    def test_get_exact_kind_filename_and_bytes(self):
        self.assertEqual(self.t.get(META,10),PDF);self.assertEqual(self.t.header,{'epoch':10,'image_id':'a'*64});self.assertFalse(list(Path(self.tmp.name).iterdir()))
    def test_get_rejects_changed_filename_and_kind(self):
        for change in [{'filename':'different.pdf'},{'kind':'image'}]:
            with self.assertRaises(ValueError):read_download(io.StringIO(download_file(PDF,META|change)),META)
    def test_get_rejects_changed_size_digest_mime_created(self):
        for change in [{'size':len(PDF)+1},{'digest':'b'*64},{'mime':'application/octet-stream'},{'created':1001}]:
            with self.assertRaises(ValueError):read_download(io.StringIO(download_file(PDF,META|change)),META)
    def test_corrupt_bytes_even_with_metadata_unchanged(self):
        with self.assertRaises(ValueError):read_download(io.StringIO(download_file(b'X'+PDF[1:],META)),META)
    def test_corrupt_mp4_with_matching_digest_rejected(self):
        bad=META|{'mime':'video/mp4','filename':'clip.mp4'}
        with self.assertRaises(ValueError):read_download(io.StringIO(download_file(PDF,bad)),bad)
    def test_valid_mp4_no_transcode(self):
        item=META|{'size':len(MP4),'digest':hashlib.sha256(MP4).hexdigest(),'mime':'video/mp4','filename':'clip.mp4'}
        self.assertEqual(read_download(io.StringIO(download_file(MP4,item)),item),MP4)
    def test_get_rejects_malicious_name_unsafe_mime_and_unknown_kind(self):
        for change in [{'filename':'../../secret'},{'filename':'bad\r\nx.pdf'},{'filename':'a'*181},{'filename':None},{'mime':'application/pdf\r\nInjected:yes'},{'mime':None},{'kind':'unknown'},{'mime':'image/png'}]:
            with self.assertRaises(ValueError):self.t.get(META|change,10)
    def test_get_rejects_oversize_invalid_digest_or_id(self):
        for change in [{'size':MAX_BYTES+1},{'size':True},{'image_id':'../secret'},{'digest':None},{'digest':'bad'}]:
            with self.assertRaises(ValueError):self.t.get(META|change,10)
    def test_expired_future_and_cancelled(self):
        for created in [0,2000]:
            with self.assertRaises(ValueError):self.t.put('event',10,created,PDF,filename='file.pdf',mime='application/pdf')
            with self.assertRaises(ValueError):self.t.get(META|{'created':created},10)
        with self.assertRaises(ConnectionError):self.t.get(META,10,active=lambda:False)
        self.assertFalse(list(Path(self.tmp.name).iterdir()))
    def test_truncated_extra_or_noncontiguous_frames(self):
        normal=download_file(PDF,META)
        for text in [normal.rsplit('{"status"',1)[0],normal+'{}\n',normal.replace('"offset": 0','"offset": 1')]:
            with self.assertRaises(ValueError):read_download(io.StringIO(text),META)
    def test_quoted_filename_matches_poll_exactly(self):
        item=META|{'filename':'report 1.pdf'}
        self.assertEqual(read_download(io.StringIO(download_file(PDF,item)),item),PDF)
    def test_unicode_header_bounded_and_payload_not_in_environment(self):
        captured={}
        class Process:
            returncode=0;pid=999999
            def __init__(self,args,**kw):
                captured.update(args=args,env=kw['env'])
                Path(kw['env']['IMESSAGE_IMAGE_OUTPUT']).write_text('{"status":"pending"}\n')
            def communicate(self,timeout=None):return ('{"finished":true}\n','')
            def poll(self):return 0
        transfer=ImageTransfer({},cache=Path(self.tmp.name),clock=lambda:1001)
        name='📎'*20+'a'*155+'.mp4'
        data=MP4+b'private-synthetic-body'*1000
        with patch.object(transfer,'_credentials',return_value=('mock','mock','mock')),patch('image_transfer.subprocess.Popen',Process):
            transfer.put('unicode-file',10,1000,data,filename=name,mime='video/mp4')
        header=captured['env']['IMESSAGE_IMAGE_HEADER']
        self.assertEqual(json.loads(header)['filename'],name);self.assertLessEqual(len(header.encode('utf-8')),2048)
        self.assertEqual(bytes.fromhex(captured['env']['IMESSAGE_IMAGE_HEADER_HEX']),header.encode('utf-8'))
        encoded=base64.b64encode(data[:8192]).decode()
        self.assertNotIn(encoded,' '.join(captured['args']));self.assertTrue(all(encoded not in str(v) for v in captured['env'].values()))
        self.assertFalse(list(Path(self.tmp.name).iterdir()))
    def test_put_rejects_oversize_or_invalid_mp4_before_transport(self):
        for data,mime,name in [(PDF,'video/mp4','clip.mp4'),(b'x'*(MAX_BYTES+1),'application/octet-stream','x.bin')]:
            with self.assertRaises(ValueError):self.t.put('event',10,1000,data,filename=name,mime=mime)
        self.assertFalse(hasattr(self.t,'header'))

if __name__=='__main__':unittest.main()
