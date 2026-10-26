"""Explicit SSH transport mock. Does not call gateway, Store, EFB or Messages."""
import base64
import hashlib
import json
from pathlib import Path
import shlex
from image_transfer import ImageTransfer
from bridge import DEFAULT_HOME

# Public synthetic bytes only, generated independently at each end. No private image.
MOCK_SERVER = r'''
import sys,json,base64,hashlib
mode=sys.argv[1]
print('IMESSAGE_STDIN_READY',flush=True)
header=json.loads(sys.stdin.readline(2049))
data=b'\x89PNG\r\n\x1a\n'+bytes(range(256))*8192
if mode=='put':
 print('IMESSAGE_IMAGE_BODY_READY',flush=True)
 result=bytearray()
 while True:
  value=json.loads(sys.stdin.readline(19901))
  if value.get('op')=='commit':break
  assert value['offset']==len(result)
  chunk=base64.b64decode(value['data'],validate=True)
  assert 0<len(chunk)<=8192 and len(result)+len(chunk)<=10*1024*1024
  result.extend(chunk)
 assert len(result)==header['size'] and hashlib.sha256(result).hexdigest()==header['digest']
 print(json.dumps({'status':'pending','id':'f'*64}))
else:
 print(json.dumps({'kind':'image','size':len(data),'digest':hashlib.sha256(data).hexdigest(),'mime':'image/png','created':1000}))
 for offset in range(0,len(data),8192):
  print(json.dumps({'offset':offset,'data':base64.b64encode(data[offset:offset+8192]).decode('ascii')}))
 print(json.dumps({'status':'complete'}))
'''


class MockOnly(ImageTransfer):
    def _command(self,mode):
        return 'python3 -u -c '+shlex.quote(MOCK_SERVER)+' '+mode


def main():
    config=json.loads((DEFAULT_HOME/'config.json').read_text())
    transport=MockOnly(config,clock=lambda:1001)
    data=b'\x89PNG\r\n\x1a\n'+bytes(range(256))*8192
    reply=transport.put('image-transport-mock',7,1000,data)
    assert reply.get('status')=='pending'
    print('One-SSH 2 MiB upload mock: passed')
    expected={'image_id':'f'*64,'size':len(data),'digest':hashlib.sha256(data).hexdigest(),'mime':'image/png','created':1000}
    received=transport.get(expected,7)
    assert received==data
    print('One-SSH 2 MiB download mock: passed')
    print('No messaging endpoints called; private transfer spools removed')

if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Image stream mock failed; details suppressed') from None
