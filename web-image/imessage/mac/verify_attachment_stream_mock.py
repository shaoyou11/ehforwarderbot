"""Explicit, isolated synthetic attachment stream. No messaging endpoints/state writes."""
import hashlib
import json
from pathlib import Path
import shlex
import tempfile
from image_transfer import ImageTransfer
from bridge import DEFAULT_HOME

MOCK_SERVER=r'''
import sys,json,base64,hashlib
mode=sys.argv[1]
print('IMESSAGE_STDIN_READY',flush=True)
header=json.loads(sys.stdin.readline(2049))
data=b'\x00\x00\x00\x18ftypisom'+bytes(range(256))*8192
name='mock-clip.mp4'
if mode=='put':
 assert header['filename']==name and header['mime']=='video/mp4'
 print('IMESSAGE_IMAGE_BODY_READY',flush=True)
 result=bytearray()
 while True:
  value=json.loads(sys.stdin.readline(19901))
  if value.get('op')=='commit':break
  assert value['offset']==len(result)
  chunk=base64.b64decode(value['data'],validate=True)
  assert 0<len(chunk)<=8192 and len(result)+len(chunk)<=10*1024*1024
  result.extend(chunk)
 assert bytes(result)==data and hashlib.sha256(result).hexdigest()==header['digest']
 print(json.dumps({'status':'pending','id':'f'*64}))
else:
 assert set(header)=={'epoch','image_id'}
 print(json.dumps({'kind':'attachment','filename':name,'size':len(data),'digest':hashlib.sha256(data).hexdigest(),'mime':'video/mp4','created':1000}))
 for offset in range(0,len(data),8192):
  print(json.dumps({'offset':offset,'data':base64.b64encode(data[offset:offset+8192]).decode()}))
 print(json.dumps({'status':'complete'}))
'''

class MockOnly(ImageTransfer):
    mock_name='mock-clip.mp4'
    def _command(self,mode):
        script=MOCK_SERVER.replace("name='mock-clip.mp4'",'name='+ascii(self.mock_name))
        return 'python3 -u -c '+shlex.quote(script)+' '+mode

def main():
    cfg=json.loads((DEFAULT_HOME/'config.json').read_text())
    with tempfile.TemporaryDirectory(prefix='attachment-stream-mock-') as directory:
        transfer=MockOnly(cfg,cache=Path(directory),clock=lambda:1001)
        data=b'\x00\x00\x00\x18ftypisom'+bytes(range(256))*8192
        assert transfer.put('attachment-stream-mock',7,1000,data,filename='mock-clip.mp4',mime='video/mp4')['status']=='pending'
        print('One-SSH 2 MiB attachment put: passed',flush=True)
        item={'kind':'attachment','filename':'mock-clip.mp4','image_id':'f'*64,'size':len(data),'digest':hashlib.sha256(data).hexdigest(),'mime':'video/mp4','created':1000}
        assert transfer.get(item,7)==data
        transfer.mock_name='📎'*20+'a'*155+'.mp4'
        assert transfer.put('attachment-unicode-mock',7,1000,data,filename=transfer.mock_name,mime='video/mp4')['status']=='pending'
        assert transfer.get(item|{'filename':transfer.mock_name},7)==data
        assert not list(Path(directory).iterdir())
        print('Unicode filename header (179 characters): passed',flush=True)
        print('One-SSH 2 MiB attachment get: passed; temporary spools removed',flush=True)
    print('No message endpoints, no shared queues, no production state modified',flush=True)

if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Isolated attachment stream mock failed; details suppressed') from None
