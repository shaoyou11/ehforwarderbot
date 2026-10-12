"""Signed, expiring, admin-scoped requests; no Telegram polling in the worker."""
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

ACTIONS = {'web', 'comwechat', 'sync'}


def atomic_json(path, data, mode=0o644):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('.'+path.name+'.'+secrets.token_hex(6))
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode)
    os.fchmod(fd,mode)
    with os.fdopen(fd,'w') as f:
        json.dump(data,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
    os.replace(temporary,path)


def read_json(path, default=None):
    try:return json.loads(Path(path).read_text())
    except (OSError,ValueError):return {} if default is None else default


def signature(secret, action, actor, timestamp, nonce):
    value=f'{action}:{actor}:{timestamp}:{nonce}'.encode()
    return hmac.new(secret,value,hashlib.sha256).hexdigest()[:20]


def button(secret,action,actor,now=None):
    if action not in ACTIONS:raise ValueError('unknown action')
    timestamp=int(time.time() if now is None else now);nonce=secrets.token_hex(4)
    return f'efbctl:{action}:{timestamp}:{nonce}:{signature(secret,action,actor,timestamp,nonce)}'


def verify(secret,data,actor,admins,now=None):
    parts=data.split(':')
    if len(parts)!=5 or parts[0]!='efbctl' or parts[1] not in ACTIONS:raise ValueError('invalid action')
    _,action,stamp,nonce,mac=parts
    timestamp=int(stamp);now=time.time() if now is None else now
    if actor not in admins or now-timestamp>300 or timestamp-now>10:raise ValueError('expired or unauthorized')
    if len(nonce)!=8 or any(c not in '0123456789abcdef' for c in nonce):raise ValueError('invalid nonce')
    if not hmac.compare_digest(mac,signature(secret,action,actor,timestamp,nonce)):raise ValueError('invalid signature')
    return {'action':action,'actor':actor,'timestamp':timestamp,'nonce':nonce,'data':data}
