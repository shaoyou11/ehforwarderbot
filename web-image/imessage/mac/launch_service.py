"""One user LaunchAgent. No credentials, app startup changes or independent respawns."""
import json
import os
from pathlib import Path
import re
import subprocess

LABEL='local.imessage-bridge'
REGISTRATION='launchd_service.json'


def target():return 'gui/'+str(os.getuid())+'/'+LABEL

def plist_path():return Path.home()/'Library/LaunchAgents'/(LABEL+'.plist')


def run_launchctl(*args):
    return subprocess.run(['/bin/launchctl',*args],capture_output=True,text=True,timeout=15)


def job_status():
    result=run_launchctl('print',target());text=result.stdout
    def field(name):
        match=re.search(r'^\s*'+re.escape(name)+r'\s*=\s*([^\r\n]+)',text,re.M)
        return match.group(1).strip() if match else None
    pid=field('pid')
    return {'registered':result.returncode==0,'state':field('state') if result.returncode==0 else 'unloaded',
            'pid':int(pid) if pid and pid.isdigit() else None,'last_exit_code':field('last exit code')}


def registration(home):
    path=Path(home)/REGISTRATION
    if not path.exists():return False
    value=json.loads(path.read_text())
    if value!={'schema':1,'label':LABEL}:raise ValueError('invalid_service_registration')
    return True


def ensure_worker(home):
    if not registration(home):return False
    if not plist_path().is_file():raise ConnectionError('service_plist_missing')
    status=job_status()
    if not status['registered']:
        result=run_launchctl('bootstrap','gui/'+str(os.getuid()),str(plist_path()))
        if result.returncode and not job_status()['registered']:raise ConnectionError('service_bootstrap_failed')
    elif not status['pid']:
        # No -k: a manual on/off never kills or duplicates an already-running worker.
        result=run_launchctl('kickstart',target())
        if result.returncode and not job_status()['pid']:raise ConnectionError('service_start_failed')
    return True


def render_plist(home,python):
    home=Path(home).resolve()
    return {'Label':LABEL,'ProgramArguments':[str(python),str(home/'code/bridge.py'),'--home',str(home),'--launchd-worker','worker'],
            'WorkingDirectory':str(home),'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':30,
            'LimitLoadToSessionType':'Aqua','ProcessType':'Background','Umask':63,
            'EnvironmentVariables':{'PATH':'/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin','PYTHONUNBUFFERED':'1','PYTHONDONTWRITEBYTECODE':'1'},
            'StandardOutPath':'/dev/null','StandardErrorPath':'/dev/null'}


def stop_job():
    if job_status()['registered']:
        result=run_launchctl('bootout',target())
        if result.returncode and job_status()['registered']:raise ConnectionError('service_stop_failed')
