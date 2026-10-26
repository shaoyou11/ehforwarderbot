"""Own user LaunchAgent only. No remote on/off, preferences, login, SIP or sleep changes."""
import argparse
import contextlib
import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import time

RUNTIME=['bridge.py','launch_service.py','service_worker.py','bridge_diagnostics.py','install_launch_agent.py']


def atomic(path,data):
    temp=path.with_name(path.name+'.new');temp.write_bytes(data);temp.chmod(0o600);os.replace(temp,path)


def desired(remote):
    import shlex
    from bridge import REMOTE_CONFIG
    script=("import sys,json,pathlib;print('IMESSAGE_STDIN_READY',flush=True);sys.stdin.readline();"
            "c=json.loads(pathlib.Path("+repr(REMOTE_CONFIG)+").read_text());"
            "p=pathlib.Path(c.get('image_media_dir',c['state_dir']));v=json.loads((p/'control.json').read_text());"
            "print(json.dumps({k:v[k] for k in ['revision','enabled','images_enabled']}))")
    return remote.command('python3 -u -c '+shlex.quote(script),{})


def read_control(home):
    with contextlib.closing(sqlite3.connect((home/'state.db').as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row;return dict(db.execute('SELECT * FROM control WHERE id=1').fetchone())


def worker_pids(home):
    text=subprocess.run(['/bin/ps','-axo','pid=,args='],capture_output=True,text=True,check=True).stdout
    path=str(home/'code/bridge.py')
    return [int(line.strip().split(None,1)[0]) for line in text.splitlines() if path in line and line.rstrip().endswith(' worker')]


def fence_stop_agent(home):
    from bridge import Store,lock
    import launch_service as service
    with lock(home/'emission.lock'):
        Store(home).set(enabled=0,pending_off=0,phase='paused_connection')
        service.stop_job()
        for _ in range(50):
            if not service.job_status()['pid']:return
            time.sleep(.1)
        raise RuntimeError('service_stop_timeout')


def health(home,since,remote,count=3):
    import launch_service as service
    deadline=time.monotonic()+65;acks=[]
    while time.monotonic()<deadline:
        state=read_control(home);job=service.job_status()
        if job['registered'] and job['pid'] and worker_pids(home)==[job['pid']] and state['control_acked_at']>since:
            if not acks or state['control_acked_at']>acks[-1]['control_acked_at']:acks.append(state)
            if len(acks)>=count:
                d=desired(remote)
                if state['control_revision']!=json.dumps(d['revision'],separators=(',',':')):time.sleep(1);continue
                if state['phase'] not in ['enabled','closed','paused_backend']:raise RuntimeError('service_not_stable')
                if not d['enabled'] and (state['enabled'] or state['phase']!='closed'):raise RuntimeError('closed_desired_mismatch')
                return {'job':job,'desired':d,'actual':state,'acks':acks}
        time.sleep(1)
    raise RuntimeError('service_ack_timeout')


def move_service_files(home,backup):
    import launch_service as service
    backup.mkdir(parents=True,exist_ok=True,mode=0o700)
    for path in [service.plist_path(),home/service.REGISTRATION]:
        if path.exists():os.replace(path,backup/path.name)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('operation',choices=['check','install','status','stop','start','uninstall']);parser.add_argument('--home',type=Path,default=Path.home()/'Library/Application Support/imessage-text-bridge')
    parser.add_argument('--python',default=sys.executable);parser.add_argument('--baseline',type=Path);args=parser.parse_args();os.umask(0o077);home=args.home.resolve();source=Path(__file__).resolve().parent
    sys.path.insert(0,str(source));from bridge import SSH,Store,lock
    import launch_service as service
    if args.operation=='status':
        state=read_control(home);print(json.dumps({'job':service.job_status(),'registered_by_bridge':service.registration(home),
            'enabled':bool(state['enabled']),'phase':state['phase'],'ack_age':round(time.time()-state['control_acked_at'],1),'workers':len(worker_pids(home))}));return
    if args.operation in ['stop','start','uninstall']:
        if not service.registration(home):raise ValueError('service_not_registered_by_bridge')
        if args.operation in ['stop','uninstall']:fence_stop_agent(home)
        if args.operation=='start':service.ensure_worker(home)
        if args.operation=='uninstall':
            archive=home/'backups'/('uninstalled-launch-agent-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'));move_service_files(home,archive)
            print('Only own LaunchAgent unloaded and archived; config/ledger/modules retained. No remote switch changed.')
        else:print(json.dumps(service.job_status()))
        return
    cfg=json.loads((home/'config.json').read_text())
    if cfg.get('managed_control') is not True or cfg.get('validated_single_imessage_chat') is not True:raise ValueError('managed_whitelist_required')
    if not Path(args.python).is_file() or not os.access(args.python,os.X_OK):raise ValueError('python_not_available')
    baseline_path=args.baseline or source/'BASELINE_SHA256.json'
    baseline=json.loads(baseline_path.read_text())
    if any(hashlib.sha256((home/'code'/n).read_bytes()).hexdigest()!=v for n,v in baseline.items()):raise ValueError('runtime_changed')
    if service.plist_path().exists() or service.job_status()['registered'] or (home/service.REGISTRATION).exists():raise ValueError('service_already_present_requires_review')
    plist=plistlib.dumps(service.render_plist(home,args.python));remote=SSH(cfg);d_before=desired(remote)
    print('Preflight: managed whitelist, existing Python and desired state read; only own login agent will be installed.',flush=True)
    if args.operation=='check':return
    with lock(home/'activation.lock'),lock(home/'control.lock'):
        config_bytes=(home/'config.json').read_bytes();stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');backup=home/'backups'/('before-launch-agent-'+stamp);backup.mkdir(parents=True,mode=0o700)
        original=[n for n in RUNTIME if (home/'code'/n).exists()]
        for n in original:shutil.copyfile(home/'code'/n,backup/n);(backup/n).chmod(0o600)
        (backup/'config.json').write_bytes(config_bytes);(backup/'config.json').chmod(0o600)
        with contextlib.closing(sqlite3.connect(home/'state.db')) as db,contextlib.closing(sqlite3.connect(backup/'state-audit.db')) as dest:db.backup(dest)
        (backup/'state-audit.db').chmod(0o600)
        (backup/'ROLLBACK.txt').write_text('Boot out only local.imessage-bridge, archive its plist/registration, restore backed-up bridge code only. Keep config/current ledger and unused modules. Never restore state-audit.db or delete event records. Resume old manual managed worker only if it was running. No NAS/Telegram/WeChat/HA/BlueBubbles preference changes.\n')
        pids=worker_pids(home)
        if len(pids)>1:raise RuntimeError('multiple_workers_before_install')
        from activate_attachments import stop_local,start
        snapshot=read_control(home);since=time.time()
        try:
            if pids:snapshot=stop_local(home,home/'code/bridge.py')
            for n in RUNTIME:atomic(home/'code'/n,(source/n).read_bytes())
            service.plist_path().parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            atomic(service.plist_path(),plist)
            result=subprocess.run(['/usr/bin/plutil','-lint',str(service.plist_path())],capture_output=True,text=True)
            if result.returncode:raise ValueError('invalid_plist')
            atomic(home/service.REGISTRATION,(json.dumps({'schema':1,'label':service.LABEL})+'\n').encode())
            result=service.run_launchctl('enable',service.target())
            if result.returncode:raise RuntimeError('own_service_enable_failed')
            service.ensure_worker(home);started=health(home,since,remote)
            # One real own-job stop/read/start cycle; never changes the NAS desired switch.
            fence_stop_agent(home);stopped={'job':service.job_status(),'workers':len(worker_pids(home)),'enabled':bool(read_control(home)['enabled'])}
            if stopped['job']['registered'] or stopped['workers'] or stopped['enabled']:raise RuntimeError('stop_not_confirmed')
            restart=time.time();service.ensure_worker(home);restarted=health(home,restart,remote)
            if (home/'config.json').read_bytes()!=config_bytes:raise RuntimeError('config_changed')
            d_after=desired(remote)
        except BaseException:
            fence_stop_agent(home);move_service_files(home,backup/'failed-service')
            for n in original:atomic(home/'code'/n,(backup/n).read_bytes())
            # New isolated modules remain unused; no file deletion and no old ledger restore.
            if pids:start(home,home/'code/bridge.py')
            print('Own service rolled back; config/ledger and unrelated services retained. Backup:',backup,flush=True)
            raise
        report={'backup':str(backup),'label':service.LABEL,'python':str(args.python),'config_unchanged':True,
                'desired_before':d_before,'desired_after':d_after,'desired_unchanged':d_before==d_after,'started':started,'stopped':stopped,'restarted':restarted,
                'no_generated_messages':True,'ledger_retained':True,'requires_graphical_login':True}
        atomic(home/'launch_agent_verification.json',(json.dumps(report,indent=2)+'\n').encode())
        atomic(home/'launch_agent_installation.json',(json.dumps({'schema':1,'label':service.LABEL,'backup':str(backup)})+'\n').encode())
        print('Own user LaunchAgent installed and stop/start verified: one worker, three fresh acknowledgements each, desired/config unchanged.',flush=True)
        print('Backup:',backup,flush=True)

if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Own LaunchAgent operation stopped or rolled back safely; no remote switch/test message/ledger replay attempted.') from None
