import io
import unittest
from unittest.mock import patch
from PIL import Image
from image_runtime import verify_image

class OrientationTests(unittest.TestCase):
    def sample(self, orientation, fmt='PNG'):
        im=Image.new('RGB',(40,20))
        for y in range(20):
            for x in range(40):im.putpixel((x,y),(x*6,y*12,(x+y)*3))
        exif=Image.Exif();exif[274]=orientation
        out=io.BytesIO();im.save(out,format=fmt,exif=exif)
        return im,out.getvalue()

    def test_all_exif_rotations_and_mirrors(self):
        methods={2:Image.Transpose.FLIP_LEFT_RIGHT,3:Image.Transpose.ROTATE_180,
                 4:Image.Transpose.FLIP_TOP_BOTTOM,5:Image.Transpose.TRANSPOSE,
                 6:Image.Transpose.ROTATE_270,7:Image.Transpose.TRANSVERSE,
                 8:Image.Transpose.ROTATE_90}
        for orientation,method in methods.items():
            with self.subTest(orientation=orientation):
                original,data=self.sample(orientation)
                result=verify_image(data,'image/png')
                with Image.open(io.BytesIO(result)) as actual:
                    expected=original.transpose(method)
                    self.assertEqual(actual.size,expected.size)
                    self.assertEqual(actual.tobytes(),expected.tobytes())
                    self.assertNotIn(274,actual.getexif())
                self.assertEqual(verify_image(result,'image/png'),result)

    def test_jpeg_portrait_and_no_double_rotation(self):
        _,data=self.sample(6,'JPEG')
        result=verify_image(data,'image/jpeg')
        with Image.open(io.BytesIO(result)) as actual:
            self.assertEqual(actual.size,(20,40));self.assertNotIn(274,actual.getexif())
        self.assertEqual(verify_image(result,'image/jpeg'),result)

    def test_upright_bytes_unchanged(self):
        for fmt,mime in [('PNG','image/png'),('JPEG','image/jpeg')]:
            _,data=self.sample(1,fmt)
            self.assertEqual(verify_image(data,mime),data)

    def test_output_limit(self):
        _,data=self.sample(6)
        with patch('image_store.MAX_BYTES',10):
            with self.assertRaisesRegex(ValueError,'normalized_image_size_exceeded'):
                verify_image(data,'image/png')

    def test_invalid_data_and_mime(self):
        with self.assertRaises(Exception):verify_image(b'invalid','image/jpeg')
        _,data=self.sample(6)
        with self.assertRaisesRegex(ValueError,'image_format_mismatch'):verify_image(data,'image/jpeg')

if __name__=='__main__':unittest.main()
