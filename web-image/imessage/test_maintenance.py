import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from diagnostics import record,summary
from image_store import ImageStore
from image_outbound import Outbound
import test_gateway as fixtures

class Diagnostics(unittest.TestCase):
    def test_safe_reason_only_and_expiry(self):
        with tempfile.TemporaryDirectory() as d:
            cfg={'state_dir':d}
            record(cfg,'wechat_send',RuntimeError('PRIVATE_MESSAGE_AND_TOKEN'))
            saved=Path(d,'diagnostic-wechat_send.json').read_text()
            self.assertNotIn('PRIVATE_MESSAGE',saved);self.assertIn('处理失败',summary(cfg))
            record(cfg,'wechat_send',ValueError('voice_duration_invalid'))
            self.assertIn('10 分钟',summary(cfg))
            with patch('diagnostics.time.time',return_value=0):self.assertIn('无已记录',summary(cfg))
    def test_record_failure_does_not_escape(self):
        record({'state_dir':'/missing-diagnostic-parent-for-test'},'runtime',ValueError('x'))

class History(unittest.TestCase):
    def test_image_history_does_not_limit_new_but_preserves_dedup(self):
        with tempfile.TemporaryDirectory() as d:
            store=ImageStore(d,clock=lambda:1000)
            with store.db() as db:
                db.executemany('INSERT INTO images VALUES (?,?,?,?,?,?,?,?)',((str(i),1,1,'x','image/png',1,2,'sent') for i in range(10000)))
            data=b'\x89PNG\r\n\x1a\n';digest=hashlib.sha256(data).hexdigest()
            first=store.begin('new',1,len(data),digest,'image/png')
            self.assertFalse(first['duplicate']);self.assertTrue(store.begin('new',1,len(data),digest,'image/png')['duplicate'])
            with store.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM images').fetchone()[0],10001)
    def test_outbound_history_does_not_limit_new(self):
        with tempfile.TemporaryDirectory() as d:
            out=Outbound({'state_dir':d},clock=lambda:1000,fetch=lambda _:b'\x89PNG\r\n\x1a\n')
            with out.db() as db:
                db.executemany('INSERT INTO grants VALUES (?,?,?,?,?,?,?,?)',((str(i),1,1,'image/png','x',1,None,'taken') for i in range(10000)))
            key='a'*64;out.grant(key,1,'mock',999)
            with self.assertRaisesRegex(ValueError,'already_claimed'):out.grant(key,1,'mock',999)

class GatewayHistory(fixtures.GatewayTests):
    def test_large_history_keeps_delivery_and_dedup(self):
        import sqlite3
        self.g.run({'op':'on'})
        with sqlite3.connect(self.g.root/'delivery.sqlite') as db:
            db.execute('CREATE TABLE delivered(id TEXT PRIMARY KEY)')
            db.executemany('INSERT INTO delivered VALUES (?)',((str(i),) for i in range(100000)))
        self.message('fresh')
        self.assertIsNotNone(self.g.run({'op':'poll'})['message'])
        self.assertIsNone(self.g.run({'op':'poll'})['message'])
