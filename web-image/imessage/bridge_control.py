"""Private desired/acknowledged bridge controls, independent of HA policy revision."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time

PHASES={'enabled','closed','paused_backend','paused_connection','remote_mismatch','closed_remote_sync_pending','enable_failed'}

class Control:
    def __init__(self,root,clock=time.time):
        self.root=Path(root);self.clock=clock
    @contextlib.contextmanager
    def lock(self):
        with open(self.root/'control.lock','a') as f:
            fcntl.flock(f,fcntl.LOCK_EX)
            yield
    def read(self,name,default):
        try:return json.loads((self.root/name).read_text())
        except FileNotFoundError:return default
    def write(self,name,data):
        path=self.root/name;old=path.stat() if path.exists() else None
        fd,temp=tempfile.mkstemp(prefix='.control-',dir=self.root)
        try:
            with os.fdopen(fd,'w') as f:
                json.dump(data,f);f.flush();os.fsync(f.fileno())
            if old and os.geteuid()==0:os.chown(temp,old.st_uid,old.st_gid)
            os.chmod(temp,0o600);os.replace(temp,path)
        finally:
            if os.path.exists(temp):os.unlink(temp)
    def desired(self):return self.read('control.json',{'revision':0,'enabled':False,'images_enabled':False})
    def set(self,expected=None,**changes):
        if not changes or set(changes)-{'enabled','images_enabled'} or any(type(v) is not bool for v in changes.values()):raise ValueError('invalid_control')
        with self.lock():
            value=self.desired()
            if expected is not None and value['revision']!=expected:raise ValueError('stale_control')
            if any(value.get(k)!=v for k,v in changes.items()):
                value.update(changes);value.update(revision=value['revision']+1,updated=self.clock(),images_since=self.clock());self.write('control.json',value)
            return value
    def acknowledge(self,data,epoch):
        if (set(data)!={'revision','epoch','enabled','images_enabled','phase'} or type(data['enabled']) is not bool
            or type(data['images_enabled']) is not bool or type(data['revision']) is not int
            or type(data['epoch']) is not int or data['phase'] not in PHASES):raise ValueError('invalid_ack')
        with self.lock():
            if data['revision']!=self.desired()['revision'] or data['epoch']!=epoch:raise ValueError('stale_ack')
            self.write('peer.json',dict(data,updated=self.clock()))
    def summary(self):
        desired=self.desired();peer=self.read('peer.json',{})
        fresh=0<=self.clock()-peer.get('updated',0)<45
        match=(fresh and peer.get('revision')==desired['revision'] and peer.get('enabled')==desired['enabled']
               and peer.get('images_enabled')==desired['images_enabled'])
        if not fresh:status='Mac 离线或尚未连接；等待连接'
        elif peer.get('revision')==desired['revision'] and peer.get('phase')=='paused_backend':status='微信或转发通道暂未就绪'
        elif not match:status='操作待执行'
        elif peer.get('phase')=='enabled' and desired['enabled']:status='运行中'
        elif peer.get('phase')=='closed' and not desired['enabled']:status='已关闭（Mac 待机）'
        else:status='已连接，当前暂停或异常'
        return desired,status


def control_for(config):return Control(config.get('image_media_dir',config['state_dir']))

def permitted(config,images=False):
    if config.get('remote_control_enabled') is not True:return True
    c=control_for(config).desired()
    return c.get('enabled') is True and (not images or c.get('images_enabled') is True)
