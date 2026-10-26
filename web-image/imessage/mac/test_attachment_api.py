import io
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock
from attachment_api import Attachments,clean_filename,safe_mime,validate_file,multipart_file
from image_api import MAX_BYTES,NoRedirect

PDF=b'%PDF-1.4\nmock'
MP4=b'\x00\x00\x00\x18ftypisom' + b'mock-video'

def doc():return {'isFromMe':False,'attachments':[{'guid':'mock-file','transferName':'报告.pdf','mimeType':'application/pdf','totalBytes':len(PDF)}]}

class Response(io.BytesIO):
    def __exit__(self,*args):self.close()

class API(Attachments):
    def url(self,path,query=None):
        self.path=path;self.query=query
        return 'http://127.0.0.1:1234/api/v1/'+path

class Tests(unittest.TestCase):
    def test_filename_path_controls_and_quotes(self):
        for value,expected in [('../../etc/x.pdf','x.pdf'),('C:\\folder\\报告.pdf','报告.pdf'),('a\r\n\x00"b.pdf','ab.pdf'),('\u202efile.txt','file.txt'),('..','attachment.bin'),('', 'attachment.bin'),(None,'attachment.bin')]:
            self.assertEqual(clean_filename(value),expected)
    def test_filename_normalized_and_quote_removed_safely(self):
        self.assertEqual(clean_filename('cafe\u0301.pdf'),'café.pdf')
        body,_=multipart_file('chat','event',PDF,'a"b.pdf','application/pdf')
        self.assertIn(b'filename="ab.pdf"',body);self.assertIn(b"filename*=UTF-8''ab.pdf",body)
        self.assertIn(b'name="name"\r\n\r\nab.pdf',body)
    def test_filename_unicode_length_bound(self):
        self.assertLessEqual(len(clean_filename('文'*300)),180);self.assertLessEqual(len(clean_filename('文'*300).encode()),240)
        self.assertEqual(len(clean_filename('a'*300)),180);self.assertEqual(clean_filename('报告 1.pdf'),'报告 1.pdf')
    def test_filename_truncation_is_idempotent_and_keeps_short_extension(self):
        for value in ['a'*179+' .pdf','📎'*175+'.mp4','文'*170+'.pdf']:
            cleaned=clean_filename(value);self.assertEqual(clean_filename(cleaned),cleaned)
            self.assertLessEqual(len(cleaned),180);self.assertLessEqual(len(cleaned.encode('utf-8')),240)
        self.assertTrue(clean_filename('📎'*175+'.mp4').endswith('.mp4'))
    def test_canonical_unicode_names_fit_actual_local_filesystem(self):
        with tempfile.TemporaryDirectory(prefix='attachment-name-test-') as directory:
            for original in ['📎'*175+'.mp4','文'*170+'.pdf']:
                name=clean_filename(original);path=Path(directory)/name
                path.write_bytes(b'');self.assertTrue(path.is_file());self.assertLessEqual(len(name.encode()),240)
    def test_safe_mime(self):
        self.assertEqual(safe_mime('Application/PDF'),'application/pdf')
        self.assertEqual(safe_mime('video/quicktime'),'video/quicktime')
        for value in [None,'text/plain\r\nX: yes','text/html; charset=utf8','invalid','x/'+'a'*200]:self.assertEqual(safe_mime(value),'application/octet-stream')
    def test_ordinary_document_without_image_decoding(self):validate_file(PDF,'application/pdf','报告.pdf')
    def test_non_mp4_video_audio_and_binary_no_transcode(self):
        for mime,name in [('video/quicktime','clip.mov'),('audio/mpeg','audio.mp3'),('application/octet-stream','data.bin')]:validate_file(b'original',mime,name)
    def test_mp4_header_by_mime_and_extension(self):
        validate_file(MP4,'video/mp4','movie.mp4');validate_file(MP4,'application/octet-stream','movie.MP4')
        for mime,name in [('video/mp4','movie.bin'),('application/octet-stream','movie.mp4')]:
            with self.assertRaises(ValueError):validate_file(PDF,mime,name)
    def test_empty_oversized_and_nonbytes(self):
        for value in [b'',b'x'*(MAX_BYTES+1),'not bytes',bytearray(b'x')]:
            with self.assertRaises(ValueError):validate_file(value,'application/octet-stream','file.bin')
    def test_size_limit_accepts_exact_limit(self):validate_file(b'x'*MAX_BYTES,'application/octet-stream','file.bin')
    def test_reject_image_and_unsanitized_metadata(self):
        for mime,name in [('image/png','pic.png'),('application/pdf','../foo.pdf'),('application/pdf','bad\n.pdf'),('bad/mime;foo','file')]:
            with self.assertRaises(ValueError):validate_file(PDF,mime,name)
    def test_multipart_preserves_display_name_and_plain_method(self):
        body,kind=multipart_file('fixed-chat','event',PDF,'../../报告.pdf','application/pdf')
        self.assertIn('name="name"\r\n\r\n报告.pdf'.encode(),body);self.assertIn(b'name="attachment"',body)
        self.assertIn(b'filename*=UTF-8\'\'%E6%8A%A5%E5%91%8A.pdf',body);self.assertIn(PDF,body);self.assertIn(b'apple-script',body);self.assertNotIn(b'private-api',body)
        self.assertNotIn(b'../../',body);self.assertTrue(kind.startswith('multipart/form-data; boundary='))
    def test_multipart_header_injection_removed(self):
        body,_=multipart_file('chat','event',PDF,'x"\r\nInjected:yes.pdf','application/pdf\r\nX:test')
        self.assertNotIn(b'\r\nInjected:',body);self.assertNotIn(b'\r\nX:test',body)
    def test_download_original_no_force_and_bytes_unchanged(self):
        opener=Mock();opener.open.return_value=Response(PDF);api=API({'chat_guid':'fixed'},opener)
        self.assertEqual(api.download(doc(),'mock-file'),PDF);self.assertEqual(api.query,{'original':'true','force':'false'})
        self.assertEqual(api.path,'attachment/mock-file/download');self.assertEqual(opener.open.call_count,1)
    def test_download_rejects_foreign_outgoing_missing_image_sticker_before_network(self):
        opener=Mock();api=API({'chat_guid':'fixed'},opener)
        for msg,guid in [(doc()|{'isFromMe':True},'mock-file'),(doc()|{'chatGuid':'other'},'mock-file'),(doc(),'../bad'),(doc(),'missing'),(doc()|{'attachments':[doc()['attachments'][0]|{'mimeType':'image/png'}]},'mock-file'),(doc()|{'attachments':[doc()['attachments'][0]|{'isSticker':True}]},'mock-file')]:
            with self.assertRaises(ValueError):api.download(msg,guid)
        opener.open.assert_not_called()
    def test_download_bounded_and_validates_mp4(self):
        for data,mime,name in [(b'x'*(MAX_BYTES+1),'application/pdf','x.pdf'),(PDF,'video/mp4','x.mp4')]:
            opener=Mock();opener.open.return_value=Response(data);api=API({'chat_guid':'fixed'},opener)
            msg=doc();msg['attachments'][0].update(mimeType=mime,transferName=name)
            with self.assertRaises(ValueError):api.download(msg,'mock-file')
    def test_send_one_request_and_no_retry_on_error(self):
        opener=Mock();opener.open.side_effect=TimeoutError();api=API({'chat_guid':'fixed'},opener)
        with self.assertRaises(TimeoutError):api.send('event',PDF,'x.pdf','application/pdf')
        self.assertEqual(opener.open.call_count,1)
    def test_send_success_and_reject_nonconfirmation(self):
        for status in [200,500]:
            opener=Mock();opener.open.return_value=Response(json.dumps({'status':status,'data':{'mock':True}}).encode());api=API({'chat_guid':'fixed'},opener)
            if status==200:self.assertEqual(api.send('event',PDF,'x.pdf','application/pdf'),{'mock':True})
            else:
                with self.assertRaises(ConnectionError):api.send('event',PDF,'x.pdf','application/pdf')
            self.assertEqual(api.path,'message/attachment');self.assertEqual(opener.open.call_count,1)
    def test_redirect_refused(self):
        with self.assertRaises(ValueError):NoRedirect().redirect_request(None,None,302,'',{},'https://elsewhere.invalid')

if __name__=='__main__':unittest.main()
