import base64
import hashlib
import tempfile
import unittest
from image_store import ImageStore, CHUNK_BYTES

class ImageStoreTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.now=10;self.store=ImageStore(tmp.name,clock=lambda:self.now)
        self.data=b'\x89PNG\r\n\x1a\n'+b'x'*10000
    def begin(self,event='event',epoch=1):
        return self.store.begin(event,epoch,len(self.data),hashlib.sha256(self.data).hexdigest(),'image/png')['id']
    def upload(self,key,epoch=1):
        for offset in range(0,len(self.data),CHUNK_BYTES):
            self.store.append(key,epoch,offset,base64.b64encode(self.data[offset:offset+CHUNK_BYTES]).decode())
    def test_complete_claim_once(self):
        key=self.begin();self.upload(key);self.upload(key)
        self.store.commit(key,1);self.assertEqual(self.store.claim(1)['data'],self.data)
        self.assertIsNone(self.store.claim(1));self.store.finish(key,'sent')
        self.assertEqual(self.store.commit(key,1)['status'],'sent')
    def test_expiry_clears_body(self):
        key=self.begin();self.upload(key);self.now=131
        with self.assertRaises(ValueError):self.store.append(key,1,0,base64.b64encode(self.data[:8192]).decode())
        # Rollback of a rejected operation is harmless; next maintenance removes it.
        self.assertIsNone(self.store.claim(1))
        with self.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM chunks').fetchone()[0],0)
    def test_off_and_epoch_drop_pending(self):
        key=self.begin();self.upload(key);self.store.commit(key,1)
        self.assertIsNone(self.store.claim(2));self.assertEqual(self.store.commit(key,1)['status'],'cancelled')
        key=self.begin('second');self.upload(key);self.store.cancel()
        self.assertEqual(self.store.commit(key,1)['status'],'cancelled')
    def test_no_replay_after_crash(self):
        key=self.begin();self.upload(key);self.store.commit(key,1);self.store.claim(1)
        self.store.recover();self.assertIsNone(self.store.claim(1))
        self.assertEqual(self.store.commit(key,1)['status'],'unknown')
    def test_incomplete_and_invalid_chunks(self):
        key=self.begin()
        with self.assertRaises(ValueError):self.store.commit(key,1)
        with self.assertRaises(ValueError):self.store.append(key,2,0,base64.b64encode(self.data[:8192]).decode())
        with self.assertRaises(ValueError):self.store.append(key,1,1,'eA==')
    def test_cap_and_conflict(self):
        for n in range(5):self.begin(str(n))
        with self.assertRaises(ValueError):self.begin('overflow')
        with self.assertRaises(ValueError):self.begin('0',epoch=2)

if __name__=='__main__':unittest.main()
