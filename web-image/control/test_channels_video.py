import io
import unittest
from unittest.mock import MagicMock, patch
import requests
import channels_video as video


class ChannelsVideo(unittest.TestCase):
    def response(self, chunks, length='0', status=200):
        response=MagicMock();response.__enter__.return_value=response
        response.status_code=status;response.headers={'Content-Length':length}
        response.iter_content.return_value=iter(chunks)
        return response

    def session(self,response):
        session=MagicMock();session.__enter__.return_value=session;session.get.return_value=response
        return session

    def test_valid_video_is_returned_once(self):
        r=self.response([b'video']);session=self.session(r)
        with patch.object(video,'allowed_url',return_value=True),patch.object(video.requests,'Session',return_value=session),patch.object(video,'probe',return_value=True):
            f=video.download('https://video.qq.com/example')
            self.assertEqual(f.read(),b'video');f.close()
        session.get.assert_called_once()

    def test_oversize_header_skips_download(self):
        r=self.response([],length='99')
        with patch.object(video,'allowed_url',return_value=True),patch.object(video.requests,'Session',return_value=self.session(r)),patch.object(video.tempfile,'NamedTemporaryFile') as create:
            self.assertIsNone(video.download('https://video.qq.com/example',max_bytes=10))
            create.assert_not_called()

    def test_stream_overflow_and_invalid_video_are_closed(self):
        for limit,valid in [(2,True),(100,False)]:
            f=io.BytesIO();f.name='test.mp4'
            with patch.object(video,'allowed_url',return_value=True),patch.object(video.requests,'Session',return_value=self.session(self.response([b'content']))),patch.object(video.tempfile,'NamedTemporaryFile',return_value=f),patch.object(video,'probe',return_value=valid):
                self.assertIsNone(video.download('https://video.qq.com/example',max_bytes=limit))
                self.assertTrue(f.closed)

    def test_timeout_does_not_retry(self):
        session=self.session(self.response([]));session.get.side_effect=requests.Timeout()
        with patch.object(video,'allowed_url',return_value=True),patch.object(video.requests,'Session',return_value=session):
            self.assertIsNone(video.download('https://video.qq.com/example'))
        session.get.assert_called_once()

    def test_redirect_to_untrusted_host_not_fetched(self):
        r=self.response([],status=302);r.headers['Location']='https://example.invalid/media'
        session=self.session(r)
        with patch.object(video,'allowed_url',side_effect=[True,False]),patch.object(video.requests,'Session',return_value=session):
            self.assertIsNone(video.download('https://video.qq.com/example'))
        session.get.assert_called_once()

    def test_private_or_untrusted_destinations_rejected(self):
        self.assertFalse(video.allowed_url('http://localhost/video'))
        self.assertFalse(video.allowed_url('https://video.qq.com.evil.invalid/video'))
        with patch.object(video.socket,'getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
            self.assertFalse(video.allowed_url('https://video.qq.com/example'))

if __name__=='__main__':unittest.main()
