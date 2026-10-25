"""Offline checks for the dependencies changed in the Web runtime refresh."""
import asyncio
import io
import unittest


class DependencyCompatibility(unittest.TestCase):
    def test_runtime_timezone_and_certificate_store(self):
        import datetime
        import ssl
        from zoneinfo import ZoneInfo
        from pathlib import Path
        self.assertEqual(datetime.datetime(2026, 10, 11, tzinfo=ZoneInfo('Asia/Shanghai')).utcoffset(), datetime.timedelta(hours=8))
        self.assertTrue(Path('/etc/localtime').resolve(strict=True).is_file())
        self.assertGreater(ssl.create_default_context().cert_store_stats()['x509_ca'], 0)

    def test_chinese_response_without_declared_encoding(self):
        import requests
        response = requests.Response()
        response._content = '{"消息":"浙江省嵊泗中学，群聊绑定正常。"}'.encode()
        self.assertIn('嵊泗中学', response.text)

    def test_article_selectors_preserve_chinese_content(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup('<h1 id="activity-name">群消息</h1><span id="js_name">作者</span><div id="js_content"><p>中文内容</p><img data-src="image.png"></div>', 'html.parser')
        self.assertEqual(soup.select('#activity-name')[0].text, '群消息')
        self.assertEqual(soup.select('#js_name')[0].text, '作者')
        self.assertEqual(soup.select('#js_content')[0].p.text, '中文内容')
        self.assertEqual(soup.select('img[data-src]')[0]['data-src'], 'image.png')

    def test_database_transaction_rolls_back_failed_binding(self):
        from peewee import Model, SqliteDatabase, TextField, IntegerField
        db = SqliteDatabase(':memory:')
        class Binding(Model):
            uid = TextField(unique=True)
            thread = IntegerField()
            class Meta:
                database = db
        db.create_tables([Binding])
        Binding.create(uid='中文群', thread=123)
        with self.assertRaises(RuntimeError):
            with db.atomic():
                Binding.update(thread=456).where(Binding.uid == '中文群').execute()
                raise RuntimeError('interrupted sync')
        self.assertEqual(Binding.get(Binding.uid == '中文群').thread, 123)
        self.assertEqual(Binding.select().count(), 1)
        db.close()

    def test_http_client_and_cancellation(self):
        import anyio
        import httpx
        async def exercise():
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={'ok': True}))) as client:
                self.assertTrue((await client.get('https://example.invalid')).json()['ok'])
            with anyio.move_on_after(0) as scope:
                await anyio.sleep(10)
            self.assertTrue(scope.cancel_called)
        asyncio.run(exercise())

    def test_image_formats_thumbnail_and_animation(self):
        from PIL import Image, ImageOps
        for fmt in ('PNG','JPEG','WEBP'):
            buffer=io.BytesIO();Image.new('RGB',(320,240),'blue').save(buffer,format=fmt)
            buffer.seek(0)
            with Image.open(buffer) as picture:
                picture.load();thumbnail=ImageOps.exif_transpose(picture)
                thumbnail.thumbnail((64,64));self.assertEqual(thumbnail.size,(64,48))
        buffer=io.BytesIO()
        Image.new('RGB',(8,8),'red').save(buffer,format='GIF',save_all=True,append_images=[Image.new('RGB',(8,8),'blue')],duration=100,loop=0)
        buffer.seek(0)
        with Image.open(buffer) as picture:
            self.assertEqual(picture.n_frames,2);picture.seek(1);picture.convert('RGBA').load()

    def test_cffi_parser_and_cairo_image_renderer(self):
        from cffi import FFI
        import cairosvg
        from PIL import Image
        ffi = FFI()
        ffi.cdef('typedef struct { int width; int height; } Dimensions;')
        self.assertEqual(ffi.new('Dimensions *', {'width': 2}).width, 2)
        png = cairosvg.svg2png(bytestring=b'<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"><rect width="2" height="2" fill="red"/></svg>')
        self.assertEqual(Image.open(io.BytesIO(png)).size, (2, 2))

if __name__ == '__main__':
    unittest.main()
