"""Deliver startup QR directly while channel initialization waits for login."""
import io,os,time
from pathlib import Path
import requests,yaml
from pyqrcode import QRCode
from protocol import atomic_json

def relay(uuid,status):
    root=os.environ.get('EFB_BACKEND_CONTROL')
    if not root:return
    c=yaml.safe_load(Path('/data/profiles/web/blueset.telegram/config.yaml').read_text())
    url=c['flags']['api_base_url']+c['token']
    if status in (0,408):
        photo=io.BytesIO();QRCode('https://login.weixin.qq.com/l/'+uuid).png(photo,scale=8);photo.seek(0)
        response=requests.post(url+'/sendPhoto',data={'chat_id':c['admins'][0],'caption':'网页版微信登录二维码：请扫描并在手机确认。请使用最新一张二维码。'},files={'photo':('login.png',photo,'image/png')},timeout=(5,20))
        response.raise_for_status()
        if not response.json().get('ok'):raise RuntimeError('QR delivery failed')
        atomic_json(Path(root)/'web-login.json',{'updated':time.time(),'qr_delivered':True,'phase':'waiting_scan'})
    elif status==200:
        atomic_json(Path(root)/'web-login.json',{'updated':time.time(),'qr_delivered':False,'phase':'logged_in'})
