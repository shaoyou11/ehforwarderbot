"""Track only our QR messages; clean up after a fresh, ready login heartbeat."""
import io,os,time,fcntl
from pathlib import Path
from contextlib import contextmanager
import requests,yaml
from pyqrcode import QRCode
from protocol import atomic_json,read_json

@contextmanager
def locked(root):
    with open(Path(root)/'qr.lock','a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        yield

def config():
    return yaml.safe_load(Path('/data/profiles/web/blueset.telegram/config.yaml').read_text())

def relay(uuid,status):
    root=os.environ.get('EFB_BACKEND_CONTROL')
    if not root:return
    root=Path(root);status=int(status)
    if status in (0,408):
        c=config();url=c['flags']['api_base_url']+c['token']
        photo=io.BytesIO();QRCode('https://login.weixin.qq.com/l/'+uuid).png(photo,scale=8);photo.seek(0)
        response=requests.post(url+'/sendPhoto',data={'chat_id':c['admins'][0],'caption':'网页版微信登录二维码：请扫描并在手机确认。登录就绪后自动撤回。'},files={'photo':('login.png',photo,'image/png')},timeout=(5,20))
        response.raise_for_status();data=response.json()
        if not data.get('ok'):raise RuntimeError('QR delivery failed')
        with locked(root):
            state=read_json(root/'web-login.json');pending=state.get('messages',[])
            pending.append({'chat_id':data['result']['chat']['id'],'message_id':data['result']['message_id']})
            atomic_json(root/'web-login.json',{'updated':time.time(),'qr_delivered':True,'phase':'waiting_scan','messages':pending})
    elif status==200:
        with locked(root):
            state=read_json(root/'web-login.json');state.update(updated=time.time(),qr_delivered=False,phase='logged_in')
            atomic_json(root/'web-login.json',state)

def cleanup(root=None,now=None):
    root=Path(root or os.environ['EFB_BACKEND_CONTROL']);now=time.time() if now is None else now
    with locked(root):
        state=read_json(root/'web-login.json');h=read_json(root/'web-health.json')
        if state.get('phase')!='logged_in' or not h.get('ready') or h.get('wechat_online') is not True or now-h.get('updated',0)>20:return 0
        if not state.get('messages'):return 0
        if not state.get('verified_since'):
            state['verified_since']=now;atomic_json(root/'web-login.json',state);return 0
        if now-state['verified_since']<5:return 0
        pending=state['messages'][:5]
    c=config();url=c['flags']['api_base_url']+c['token'];deleted=[]
    for message in pending:
        try:
            r=requests.post(url+'/deleteMessage',json=message,timeout=(5,10));data=r.json()
            if data.get('ok') or (data.get('error_code')==400 and 'message to delete not found' in data.get('description','').lower()):deleted.append(message)
        except (requests.RequestException,ValueError):pass
    with locked(root):
        state=read_json(root/'web-login.json');state['messages']=[m for m in state.get('messages',[]) if m not in deleted];state['last_cleanup']=now;atomic_json(root/'web-login.json',state)
    return len(deleted)

if __name__=='__main__':cleanup()
