"""Explicit stateless 25-MiB SSH probe. Never calls messaging APIs/queues/services."""
import hashlib
import json
from pathlib import Path
import shlex
import tempfile
import time
from image_transfer import ImageTransfer
from image_api import MAX_BYTES
from bridge import DEFAULT_HOME

FORMATS=[('image/jpeg',b'\xff\xd8\xff','mock25.jpg'),('image/png',b'\x89PNG\r\n\x1a\n','mock25.png'),
         ('application/pdf',b'%PDF-1.4\n','mock25.pdf'),('video/mp4',b'\x00\x00\x00\x18ftypisom','mock25.mp4')]

SERVER=r'''
import sys,json,base64,hashlib
mode,mime,created=sys.argv[1],sys.argv[2],float(sys.argv[3])
formats={'image/jpeg':(b'\xff\xd8\xff','mock25.jpg'),'image/png':(b'\x89PNG\r\n\x1a\n','mock25.png'),'application/pdf':(b'%PDF-1.4\n','mock25.pdf'),'video/mp4':(b'\x00\x00\x00\x18ftypisom','mock25.mp4')}
prefix,name=formats[mime];limit=25*1024*1024;remaining=limit-len(prefix)
data=prefix+(bytes(range(256))*((remaining+255)//256))[:remaining]
file=not mime.startswith('image/')
print('IMESSAGE_STDIN_READY',flush=True)
header=json.loads(sys.stdin.readline(2049))
if mode=='put':
 assert set(header)==({'epoch','event_id','created','size','digest','mime','filename'} if file else {'epoch','event_id','created','size','digest','mime'})
 assert header['size']==limit and header['mime']==mime and header['created']==created
 if file:assert header['filename']==name
 print('IMESSAGE_IMAGE_BODY_READY',flush=True)
 received=bytearray()
 while True:
  value=json.loads(sys.stdin.readline(19901))
  if value.get('op')=='commit':break
  assert value['offset']==len(received)
  chunk=base64.b64decode(value['data'],validate=True)
  assert 0<len(chunk)<=8192 and len(received)+len(chunk)<=limit
  received.extend(chunk)
 assert bytes(received)==data and hashlib.sha256(received).hexdigest()==header['digest']
 print(json.dumps({'status':'pending','id':'f'*64}),flush=True)
else:
 assert set(header)=={'epoch','image_id'}
 meta={'kind':'attachment' if file else 'image','size':limit,'digest':hashlib.sha256(data).hexdigest(),'mime':mime,'created':created}
 if file:meta['filename']=name
 print(json.dumps(meta),flush=True)
 for offset in range(0,len(data),8192):
  print(json.dumps({'offset':offset,'data':base64.b64encode(data[offset:offset+8192]).decode()}))
 print(json.dumps({'status':'complete'}),flush=True)
'''

class MockOnly(ImageTransfer):
    def _command(self,mode):
        return 'python3 -u -c '+shlex.quote(SERVER)+' '+shlex.quote(mode)+' '+shlex.quote(self.mime)+' '+repr(self.created)


def main():
    assert MAX_BYTES==25*1024*1024
    cfg=json.loads((DEFAULT_HOME/'config.json').read_text());records=[]
    with tempfile.TemporaryDirectory(prefix='media25-isolated-') as directory:
        transfer=MockOnly(cfg,cache=Path(directory),clock=time.time)
        for mime,prefix,name in FORMATS:
            remaining=MAX_BYTES-len(prefix);data=prefix+(bytes(range(256))*((remaining+255)//256))[:remaining]
            digest=hashlib.sha256(data).hexdigest();transfer.mime=mime;transfer.created=time.time()
            options={} if mime.startswith('image/') else {'filename':name,'mime':mime}
            start=time.monotonic()
            assert transfer.put('media25-mock-'+mime.replace('/','-'),7,transfer.created,data,**options)['status']=='pending'
            uploaded=time.monotonic()-start;assert uploaded<75
            print(json.dumps({'mime':mime,'direction':'put','bytes':len(data),'seconds':round(uploaded,3),'deadline_seconds':75}),flush=True)
            item={'kind':'image' if mime.startswith('image/') else 'attachment','image_id':'f'*64,'size':len(data),'digest':digest,'mime':mime,'created':transfer.created}
            if item['kind']=='attachment':item['filename']=name
            start=time.monotonic();received=transfer.get(item,7);downloaded=time.monotonic()-start
            assert received==data and downloaded<75 and not list(Path(directory).iterdir())
            print(json.dumps({'mime':mime,'direction':'get','bytes':len(received),'seconds':round(downloaded,3),'deadline_seconds':75}),flush=True)
            records.append({'mime':mime,'bytes':len(data),'put_seconds':round(uploaded,3),'get_seconds':round(downloaded,3),'digest_verified':True})
    report={'payload_limit':MAX_BYTES,'transport_deadline':75,'message_max_age':120,'no_message_endpoints':True,'production_unchanged':True,'records':records}
    out=Path(__file__).resolve().parent.parent/'output'/'media25-stream-verification.json';out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,indent=2)+'\n');out.chmod(0o600)
    print('All 25-MiB image/file/video put/get probes passed; no production state or message endpoints touched.',flush=True)

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Isolated media25 probe failed: '+type(error).__name__+': '+str(error)[:100],flush=True)
        raise SystemExit(1) from None
