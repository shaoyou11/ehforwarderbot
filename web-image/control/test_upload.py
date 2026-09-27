import io,unittest
from unittest.mock import patch,MagicMock
from efb_wechat_slave.vendor.itchat.components import messages
class Upload(unittest.TestCase):
    def invoke(self,responses):
        f=io.BytesIO(b'a'*1100000);core=MagicMock();core.loginInfo={'BaseRequest':{}};core.storageClass.userName='test'
        with patch.object(messages,'upload_chunk_file',side_effect=responses) as send:
            try:r=messages.upload_file(core,'test.bin',preparedFile={'fileSize':1100000,'fileMd5':'test','file_':f})
            finally:self.assertTrue(f.closed)
            return r,send.call_count
    def test_failed_chunk_stops(self):
        r,count=self.invoke([{'BaseResponse':{'Ret':1}}]);self.assertFalse(r);self.assertEqual(count,1)
    def test_success_chunks(self):
        r,count=self.invoke([{'BaseResponse':{'Ret':0}},{'BaseResponse':{'Ret':0}},{'BaseResponse':{'Ret':0},'MediaId':'test'}]);self.assertTrue(r);self.assertEqual(count,3)
    def test_timeout_closes_without_retry(self):
        with self.assertRaises(TimeoutError):self.invoke([TimeoutError()])
if __name__=='__main__':unittest.main(verbosity=2)
