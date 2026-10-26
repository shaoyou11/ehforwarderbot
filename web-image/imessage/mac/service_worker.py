"""Launchd entry: existing managed bridge, gated by the local BlueBubbles dependency."""
import json
import hashlib
import subprocess
import os
from pathlib import Path
import time
import urllib.request
from image_api import Images


class BlueProbe:
    def __init__(self,config):self.api=Images(config)
    def __call__(self):
        try:
            request=urllib.request.Request(self.api.url('ping'))
            with self.api.opener.open(request,timeout=2) as response:raw=response.read(4097)
            return len(raw)<=4096 and json.loads(raw).get('status')==200
        except Exception:return False  # Never log a credential-bearing URL or response.


def login_key():
    # Console ownership changes at GUI login/logout. The boot token prevents PID/time reuse.
    console=Path('/dev/console').stat()
    result=subprocess.run(['/usr/sbin/sysctl','-n','kern.bootsessionuuid'],capture_output=True,text=True,timeout=3,check=True)
    return hashlib.sha256((result.stdout.strip()+':'+str(console.st_uid)+':'+str(console.st_ctime_ns)).encode()).hexdigest()


def blue_running():
    result=subprocess.run(['/bin/ps','-axo','comm=','-ww'],capture_output=True,text=True,timeout=3,check=True)
    return any(line.strip().endswith('/BlueBubbles.app/Contents/MacOS/BlueBubbles') for line in result.stdout.splitlines())


class BlueStarter:
    def __init__(self,home,key=login_key,running=blue_running):self.home,self.key,self.running=Path(home),key,running
    def __call__(self):
        try:
            if self.running():return 'already_running'
            key=self.key();path=self.home/'bluebubbles_start.json'
            if path.exists() and json.loads(path.read_text()).get('login_key')==key:return 'already_attempted'
            temp=path.with_name(path.name+'.new');temp.write_text(json.dumps({'login_key':key,'attempted':time.time()})+'\n');temp.chmod(0o600);os.replace(temp,path)
            # Existing app only, background/hidden. No preference, permission or SIP change.
            result=subprocess.run(['/usr/bin/open','-gj','-a','/Applications/BlueBubbles.app'],capture_output=True,text=True,timeout=10)
            return 'requested' if result.returncode==0 else 'failed'
        except Exception:return 'failed'


class GatedRemote:
    def __init__(self,remote,home,probe,clock=time.time,starter=None):
        self.remote,self.home,self.probe,self.clock=remote,Path(home),probe,clock
        self.starter=starter;self.start_result=None
        self.blue_ready=None;self.nas_ready=None;self.images_ready=None;self.desired=None;self.last_failure=None
    def record(self,**fields):
        data={'schema':1,'pid':os.getpid(),'updated':self.clock(),'blue_available':self.blue_ready,
              'nas_ready':self.nas_ready,'images_ready':self.images_ready,'desired_enabled':self.desired,
              'last_failure':self.last_failure,'blue_start':self.start_result,**fields}
        self.home.mkdir(parents=True,exist_ok=True,mode=0o700)
        temp=self.home/'service_status.json.new';temp.write_text(json.dumps(data,separators=(',',':'))+'\n');temp.chmod(0o600)
        os.replace(temp,self.home/'service_status.json')
    def call(self,op,**values):
        try:reply=self.remote.call(op,**values)
        except Exception as error:
            code='local_permission' if isinstance(error,PermissionError) else 'local_dependency_missing' if isinstance(error,FileNotFoundError) else 'remote_unavailable'
            self.last_failure={'code':code,'operation':op if op in ['control_sync','control_ack','poll','submit','status','off','on'] else 'other','time':self.clock()}
            self.record(phase='paused_connection');raise
        if op=='control_sync':
            control=reply.get('control',{});self.desired=control.get('enabled') if type(control.get('enabled')) is bool else None
            self.nas_ready=reply.get('ready') is True;self.images_ready=reply.get('images_ready') is True
            self.blue_ready=self.probe() if self.desired is True and self.nas_ready else None
            if self.desired is True and self.nas_ready and not self.blue_ready:
                if self.starter is not None:self.start_result=self.starter()
                self.last_failure={'code':'bluebubbles_waiting','operation':'readiness','time':self.clock()}
                # Pause locally; never alter NAS desired or call on/off. Existing managed_sync
                # acknowledges actual enabled=false/paused_backend and never polls messages.
                reply=dict(reply,ready=False)
            self.record(phase='syncing')
        elif op=='control_ack' and reply.get('acknowledged') is True:
            phase=values.get('phase')
            allowed={'enabled','closed','paused_backend','paused_connection','remote_mismatch','closed_remote_sync_pending','enable_failed'}
            self.record(phase=phase if phase in allowed else 'unknown',enabled=values.get('enabled') is True,
                        images_enabled=values.get('images_enabled') is True,acknowledged_at=self.clock())
        return reply


def run_service(store,config):
    from bridge import Bridge,SSH
    if config.get('managed_control') is not True:raise ValueError('managed_control_required_for_service')
    remote=GatedRemote(SSH(config),store.home,BlueProbe(config),starter=BlueStarter(store.home))
    try:Bridge(store,config,remote=remote).run()
    except BaseException:
        remote.last_failure={'code':'worker_exit','operation':'worker','time':time.time()}
        remote.record(phase='paused_connection');raise
