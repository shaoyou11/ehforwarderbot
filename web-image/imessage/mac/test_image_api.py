import unittest
from unittest.mock import Mock
from image_api import Images, MAX_BYTES, image_kind, multipart, NoRedirect

class ImagesTest(unittest.TestCase):
    def test_formats_and_limits(self):
        self.assertEqual(image_kind(b'\xff\xd8\xffx')[0], 'jpg')
        self.assertEqual(image_kind(b'\x89PNG\r\n\x1a\nx')[0], 'png')
        for data in [b'', b'<svg/>', b'\xff\xd8\xff' + b'x' * MAX_BYTES]:
            with self.assertRaises(ValueError): image_kind(data)

    def test_multipart_ordinary_mode_fixed_filename(self):
        body, content_type = multipart('fixed-chat', 'event/../bad', b'\xff\xd8\xffx')
        self.assertIn(b'apple-script', body)
        self.assertNotIn(b'private-api', body)
        self.assertNotIn(b'event/../bad', body)
        self.assertIn(b'name="attachment"', body)
        self.assertIn(b'\xff\xd8\xffx', body)
        self.assertTrue(content_type.startswith('multipart/form-data; boundary='))

    def test_reject_foreign_or_outgoing_attachment_before_network(self):
        opener = Mock(); api = Images({}, opener)
        for msg, guid in [({'isFromMe': True}, 'one'),
                          ({'isFromMe': False, 'attachments': []}, 'one'),
                          ({'isFromMe': False}, '../other')]:
            with self.assertRaises(ValueError): api.download(msg, guid)
        opener.open.assert_not_called()

    def test_redirect_refused(self):
        with self.assertRaises(ValueError):
            NoRedirect().redirect_request(None, None, 302, '', {}, 'elsewhere')

if __name__ == '__main__': unittest.main()
