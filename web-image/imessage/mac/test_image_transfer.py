import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from image_transfer import ImageTransfer,upload_lines,read_download,CHUNK_BYTES
from image_api import MAX_BYTES

DATA=b'\x89PNG\r\n\x1a\n'+bytes(range(256))*100
META={'kind':'image','image_id':'a'*64,'size':len(DATA),'digest':hashlib.sha256(DATA).hexdigest(),'mime':'image/png','created':1000}


def download_file(data=DATA,metadata=None):
    header=dict(META if metadata is None else metadata)
    lines=[json.dumps(header)]
    for offset in range(0,len(data),CHUNK_BYTES):
        lines.append(json.dumps({'offset':offset,'data':base64.b64encode(data[offset:offset+CHUNK_BYTES]).decode()}))
    lines.append('{"status":"complete"}')
    return '\n'.join(lines)+'\n'


class FakeTransfer(ImageTransfer):
    def _execute(self,mode,header,input_path,output_path,active=None):
        self.header=header;self.mode=mode;self.private_mode=input_path.stat().st_mode&0o777
        if active is not None and not active():raise ConnectionError()
        if mode=='put':
            self.frames=[json.loads(x) for x in input_path.read_text().splitlines()]
            output_path.write_text('{"status":"pending","id":"mock"}\n')
        else:output_path.write_text(download_file())


class Tests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.t=FakeTransfer({},cache=Path(self.tmp.name),clock=lambda:1001)
    def tearDown(self):self.tmp.cleanup()
    def test_put_spools_chunks_once_and_cleans_files(self):
        self.assertEqual(self.t.put('mock-event',3,1000,DATA)['status'],'pending')
        chunks=self.t.frames[:-1]
        self.assertEqual(sum(len(base64.b64decode(x['data'])) for x in chunks),len(DATA))
        self.assertEqual([x['offset'] for x in chunks],list(range(0,len(DATA),CHUNK_BYTES)))
        self.assertEqual(self.t.private_mode,0o600);self.assertEqual(list(Path(self.tmp.name).iterdir()),[])
    def test_get_exact_bytes_and_no_persistent_cache(self):
        self.assertEqual(self.t.get(META,3),DATA);self.assertFalse(list(Path(self.tmp.name).iterdir()))
    def test_spool_line_bound(self):
        output=io.StringIO();upload_lines(output,DATA)
        self.assertTrue(all(len(line)<19900 for line in output.getvalue().splitlines()))
    def test_digest_corruption(self):
        with self.assertRaises(ValueError):read_download(io.StringIO(download_file(DATA+b'x')),META)
    def test_metadata_change(self):
        with self.assertRaises(ValueError):read_download(io.StringIO(download_file(metadata=META|{'mime':'image/jpeg'})),META)
    def test_noncontiguous_offset(self):
        lines=download_file().splitlines();chunk=json.loads(lines[1]);chunk['offset']=1;lines[1]=json.dumps(chunk)
        with self.assertRaises(ValueError):read_download(io.StringIO('\n'.join(lines)+'\n'),META)
    def test_truncation_and_extra_records(self):
        for text in [download_file().rsplit('{"status"',1)[0],download_file()+'{}\n']:
            with self.assertRaises(ValueError):read_download(io.StringIO(text),META)
    def test_malicious_id_or_oversized(self):
        for item in [META|{'image_id':'../../secret'},META|{'size':MAX_BYTES+1},META|{'digest':'not-a-digest'}]:
            with self.assertRaises(ValueError):self.t.get(item,3)
    def test_expired_and_future(self):
        for created in [1,2000]:
            with self.assertRaises(ValueError):self.t.put('event',1,created,DATA)
            with self.assertRaises(ValueError):self.t.get(META|{'created':created},3)
    def test_closed_during_transfer_no_read_result(self):
        with self.assertRaises(ConnectionError):self.t.get(META,3,active=lambda:False)
        self.assertFalse(list(Path(self.tmp.name).iterdir()))

    def test_payload_not_in_process_arguments_or_environment(self):
        captured={}
        class Process:
            returncode=0
            def __init__(self,args,**kw):
                captured.update(args=args,env=kw['env']);self.pid=999999
                Path(kw['env']['IMESSAGE_IMAGE_OUTPUT']).write_text('{"status":"pending"}\n')
            def communicate(self,timeout=None):return ('{"finished":true}\n','')
            def poll(self):return 0
        config={'credential_helper':'unused','ssh_label':'mock','expected_host_digest':hashlib.sha256(b'mock-host').hexdigest()}
        real=ImageTransfer(config,cache=Path(self.tmp.name),clock=lambda:1001)
        with patch('image_transfer.runpy.run_path',return_value={'extract':lambda label:('mock-host','mock-user','mock-password')}),patch('image_transfer.subprocess.Popen',Process):
            self.assertEqual(real.put('mock-event',1,1000,DATA)['status'],'pending')
        encoded=base64.b64encode(DATA[:8192]).decode()
        self.assertNotIn(encoded,' '.join(captured['args']))
        self.assertTrue(all(encoded not in str(value) for value in captured['env'].values()))
        self.assertLess(len(captured['env']['IMESSAGE_IMAGE_HEADER']),2048)
        self.assertIn('StrictHostKeyChecking=yes',Path(__file__).with_name('ssh_images.exp').read_text())

if __name__=='__main__':unittest.main()
