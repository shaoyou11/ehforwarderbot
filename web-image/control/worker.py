"""Single host worker. Only fixed Docker services are controlled; no bot polling."""
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from protocol import atomic_json,read_json,verify
from sync import synchronize

NAMES={'web':'微信网页版','comwechat':'ComWechat'}

class Worker:
    def __init__(self,config):
        self.config=config;self.root=Path(config['control_root']);self.secret=(self.root/'secret').read_bytes()
        self.state=read_json(self.root/'state.json',{'active':'web','phase':'idle'})
    def run(self,args,timeout=45):
        p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
        if p.returncode:raise RuntimeError('command failed')
        return p.stdout
    def running(self,backend):
        state=self.run(['docker','inspect','-f','{{.State.Running}}',self.config['containers'][backend]],10).strip()
        if state not in {'true','false'}:raise RuntimeError('container state unavailable')
        return state=='true'
    def save(self,**changes):
        self.state.update(changes);self.state['updated']=time.time();atomic_json(self.root/'state.json',self.state)
    def notify(self,text):
        # Token remains inside the running EFB container's private profile.
        backend=self.state['active'];container=self.config['containers'][backend];profile='web' if backend=='web' else 'comwechat'
        code="""import json,sys,yaml,requests
from pathlib import Path
c=yaml.safe_load(Path('/data/profiles/'+sys.argv[1]+'/blueset.telegram/config.yaml').read_text())
r=requests.post(c['flags']['api_base_url']+c['token']+'/sendMessage',json={'chat_id':c['admins'][0],'text':sys.argv[2]},timeout=(5,15));r.raise_for_status()
"""
        try:self.run(['docker','exec',container,'python','-c',code,profile,text],25)
        except Exception:pass
    def wait_ready(self,backend,since,seconds=90):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            h=read_json(self.root/f'{backend}-health.json')
            if self.running(backend) and h.get('updated',0)>since and h.get('ready'):return h
            login=read_json(self.root/'web-login.json') if backend=='web' else {}
            if self.running(backend) and login.get('updated',0)>since and login.get('qr_delivered'):
                return {'ready':False,'wechat_online':False}
            if not self.running(backend):raise RuntimeError('target exited')
            time.sleep(2)
        raise TimeoutError('target readiness timeout')
    def synchronize(self):
        active=self.state['active'];other='comwechat' if active=='web' else 'web'
        if not self.running(active) or self.running(other):raise RuntimeError('backend exclusivity check failed')
        result=synchronize(self.config,active);self.save(sync=result);return result
    def switch(self,target):
        previous=self.state['active']
        if target==previous:return
        if self.running(target):raise RuntimeError('target already running')
        self.save(phase='switching',last_result='正在检查并同步')
        self.synchronize()
        deadline=time.monotonic()+30
        while True:
            h=read_json(self.root/f'{previous}-health.json')
            if time.time()-h.get('updated',0)<=20 and h.get('queue_size') in (0,None) and h.get('inflight',0)==0:break
            if time.monotonic()>=deadline:raise RuntimeError('source queue not drained')
            time.sleep(1)
        # Stop errors also require reconciliation: Docker may have stopped it
        # even when the client timed out. Never leave both frontends down.
        since=time.time()
        try:
            self.run(['docker','stop','-t','35',self.config['containers'][previous]],45)
            if self.running(previous):raise RuntimeError('source failed to stop')
            if target=='comwechat':self.run(['docker','start',self.config['watchdog']],20)
            else:self.run(['docker','stop','-t','15',self.config['watchdog']],25)
            self.run(['docker','start',self.config['containers'][target]],20)
            health=self.wait_ready(target,since)
            if self.running(previous):raise RuntimeError('multiple active backends')
            self.save(previous=previous,active=target,phase='idle' if health.get('wechat_online') is not False else 'awaiting_login',last_result='切换成功，登录状态请查看 /status')
            self.notify('EFB 已切换到 '+('微信网页版' if target=='web' else 'ComWechat')+'。请发送 /status 查看登录状态。')
        except Exception:
            self.run(['docker','stop','-t','15',self.config['containers'][target]],25)
            if previous=='web':self.run(['docker','stop','-t','15',self.config['watchdog']],25)
            else:self.run(['docker','start',self.config['watchdog']],20)
            self.run(['docker','start',self.config['containers'][previous]],20)
            self.save(active=previous,phase='failed',last_result='目标启动未通过检查，已恢复原方案')
            self.notify('EFB 切换未通过检查，已恢复原方案。请发送 /status 查看。')
            raise
    def process(self,path):
        if (self.root/'processed'/path.name).exists():
            path.replace(self.root/'processed'/(path.stem+'.duplicate.'+str(time.time_ns())+'.json'))
            return
        request=read_json(path)
        request_validated=False
        try:
            checked=verify(self.secret,request['data'],request['actor'],self.config['admins'])
            if checked['nonce']!=path.stem:raise ValueError('nonce mismatch')
            if request.get('requested_backend')!=self.state['active']:raise ValueError('stale backend request')
            request_validated=True
            if checked['action']=='sync':
                previous_phase=self.state.get('phase','idle')
                self.save(phase='syncing');result=self.synchronize();self.save(phase='awaiting_login' if previous_phase=='awaiting_login' else 'idle',last_result='已同步确认映射，未识别项继续保留');self.notify('EFB 配置同步完成。已映射 '+str(result['mapped'])+' 条，待核对 '+str(result['pending'])+' 条。')
            else:self.switch(checked['action'])
        except ValueError:
            if request_validated:
                self.save(phase='failed',last_result='同步校验发现冲突，保留原绑定并停止切换')
                self.notify('同步校验发现冲突，保留原绑定并停止切换。请发送 /backend 查看。')
                return
            self.notify('切换请求已过期或状态已变化，原操作保持不变；请发送 /backend 查看最新状态。')
        except Exception as error:
            reason={'source queue not drained':'仍有消息处理中，未切换','backend exclusivity check failed':'转发进程状态不一致，未切换','target exited':'目标进程启动后退出','command failed':'容器控制命令未成功'}.get(str(error),type(error).__name__)
            self.save(phase='failed',last_result='操作未完成：'+reason+'；未重发业务消息')
            self.notify('EFB 操作未完成：'+reason+'。当前保留 '+NAMES.get(self.state['active'],self.state['active'])+'，请使用 /backend 查看。')
        finally:
            path.replace(self.root/'processed'/path.name)
    def loop(self):
        for name in ['requests','processed','backups']:(self.root/name).mkdir(exist_ok=True)
        lock=open(self.root/'worker.lock','a+');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if self.state.get('phase') in {'switching','syncing'}:
            active=self.state.get('active','web');other='comwechat' if active=='web' else 'web'
            if self.running(other):self.run(['docker','stop','-t','15',self.config['containers'][other]],25)
            if not self.running(active):self.run(['docker','start',self.config['containers'][active]],20)
            self.save(phase='failed',last_result='恢复上次中断的切换，保留原方案')
        active=self.state.get('active','web');other='comwechat' if active=='web' else 'web'
        if self.running(other):self.run(['docker','stop','-t','20',self.config['containers'][other]],30)
        if active=='web':self.run(['docker','stop','-t','15',self.config['watchdog']],25)
        if not self.running(active):self.run(['docker','start',self.config['containers'][active]],20)
        last_cleanup=0
        last_sync=time.monotonic()
        while True:
            if self.state.get('phase')=='awaiting_login':
                active=self.state['active'];h=read_json(self.root/f'{active}-health.json')
                if time.time()-h.get('updated',0)<20 and h.get('ready') and h.get('wechat_online') is True:
                    self.save(phase='idle',last_result='扫码登录完成，当前方案已就绪')
                elif not self.running(active):
                    previous=self.state.get('previous','comwechat')
                    self.run(['docker','start',self.config['containers'][previous]],20)
                    self.save(active=previous,phase='failed',last_result='等待扫码期间目标退出，已恢复上一方案')
                    self.notify('目标在等待扫码期间退出，已恢复上一方案。请发送 /backend 查看。')
            if self.state.get('active')=='web' and time.monotonic()-last_cleanup>30:
                if read_json(self.root/'web-login.json').get('messages') and self.running('web'):
                    try:self.run(['docker','exec',self.config['containers']['web'],'python','/opt/efb-backend-control/qr_relay.py'],25)
                    except Exception:pass
                last_cleanup=time.monotonic()
            for path in sorted((self.root/'requests').glob('*.json')):self.process(path)
            if time.monotonic()-last_sync>120 and self.state.get('phase')=='idle':
                try:self.synchronize()
                except Exception as e:self.save(last_result='自动同步暂缓：'+type(e).__name__)
                last_sync=time.monotonic()
            time.sleep(2)

if __name__=='__main__':Worker(read_json(sys.argv[1])).loop()
