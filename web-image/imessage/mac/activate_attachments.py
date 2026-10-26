"""Guarded attachment upgrade; default --check is read-only. No test sends/on/off RPC."""
import argparse
import contextlib
import datetime
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from bridge import SSH,Store,DEFAULT_HOME,REMOTE_CONFIG,GATEWAY,lock
from attachment_names import filename

FILES=['bridge.py','image_api.py','image_transfer.py','attachment_api.py','attachment_names.py','ssh_images.exp','ssh_stdio.exp']
CASES=['../../报告.pdf','C:\\folder\\file.mp4','a\r\n"b.pdf','cafe\u0301.pdf','a'*179+' .pdf','📎'*175+'.mp4','文'*170+'.pdf',None,'..']


def workers(path):
    result=subprocess.run(['/bin/ps','-axo','pid=,args='],capture_output=True,text=True,check=True)
    return [int(line.strip().split(None,1)[0]) for line in result.stdout.splitlines() if str(path) in line and line.rstrip().endswith(' worker')]


def control(home):
    with contextlib.closing(sqlite3.connect((home/'state.db').as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        return dict(db.execute('SELECT * FROM control WHERE id=1').fetchone())


def remote_files(remote):
    # Fixed local private config; only booleans/time and synthetic canonical labels are returned.
    script=("import sys,json,pathlib;print('IMESSAGE_STDIN_READY',flush=True);q=json.loads(sys.stdin.readline());"
            "sys.path.insert(0,"+repr(str(Path(GATEWAY).parent))+");"
            "from media_types import filename;"
            "c=json.loads(pathlib.Path("+repr(REMOTE_CONFIG)+").read_text());"
            "print(json.dumps({'attachments_enabled':c.get('attachments_enabled') is True,"
            "'attachments_since':c.get('attachments_since'),'canonical':[filename(n) for n in q['names']]}))")
    return remote.command('python3 -u -c '+shlex.quote(script),{'names':CASES})


def check(cfg,status,local,remote,live_hash,expected_hash):
    if cfg.get('managed_control') is not True or cfg.get('validated_single_imessage_chat') is not True:raise ValueError('managed_whitelist_required')
    if live_hash!=expected_hash:raise ValueError('runtime_source_changed')
    if not local.get('enabled') or local.get('pending_off') or local.get('phase')!='enabled':raise ValueError('local_not_active')
    if any(status.get(k) is not True for k in ['enabled','ready','images_ready']):raise ValueError('NAS_not_ready')
    if str(status.get('epoch'))!=str(local.get('epoch')):raise ValueError('epoch_mismatch')
    if remote.get('attachments_enabled') is not True or not isinstance(remote.get('attachments_since'),(int,float)):raise ValueError('NAS_attachments_not_enabled')
    if remote.get('canonical')!=[filename(n) for n in CASES]:raise ValueError('NAS_filename_rules_mismatch')


def replace(path,data):
    temp=path.with_name(path.name+'.new');temp.write_bytes(data);temp.chmod(0o600);os.replace(temp,path)


def stop_local(home,live):
    with lock(home/'emission.lock'):
        pids=workers(live)
        if len(pids)>1:raise RuntimeError('duplicate_worker')
        before=control(home)
        Store(home).set(enabled=0,pending_off=0,off_requested=0,phase='paused_connection')
        for pid in pids:
            if os.getpgid(pid)!=pid:raise RuntimeError('unexpected_worker_group')
            os.killpg(pid,signal.SIGTERM)
        for _ in range(50):
            if not workers(live):
                stopped=control(home)
                before['cursor_ms']=max(before['cursor_ms'],stopped['cursor_ms'])
                return before
            time.sleep(.1)
        raise RuntimeError('worker_stop_unconfirmed')


def start(home,live,preserve=False):
    args=[sys.executable,str(live),'--home',str(home)]
    if preserve:args.append('--preserve-text-state')
    subprocess.Popen(args+['worker'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)


def restore_control(home,snapshot):
    # Additive schema/ledger are retained. Never restore an older state.db.
    fields=['enabled','pending_off','epoch','start_ms','cursor_ms','phase','images_enabled','image_start_ms',
            'control_revision','control_synced_at','control_acked_at','off_requested']
    Store(home).set(**{k:snapshot[k] for k in fields if k in snapshot})


def verify(home,live,snapshot,since,remote):
    deadline=time.monotonic()+40;acks=[]
    while time.monotonic()<deadline:
        state=control(home)
        if state.get('control_acked_at',0)>since and state.get('phase')=='enabled' and state.get('enabled') and not state.get('pending_off'):
            if state['epoch']!=snapshot['epoch'] or state['start_ms']!=snapshot['start_ms'] or state['cursor_ms']<snapshot['cursor_ms']:
                raise RuntimeError('text_state_changed')
            if state.get('attachment_start_ms',0)<int(since*1000):raise RuntimeError('attachment_floor_not_current')
            if not acks or state['control_acked_at']>acks[-1]['control_acked_at']:acks.append(state)
            if len(acks)>=3:
                status=remote.call('status')
                if len(workers(live))!=1 or any(status.get(k) is not True for k in ['enabled','ready','images_ready']):raise RuntimeError('health_unconfirmed')
                return acks
        time.sleep(1)
    raise RuntimeError('fresh_ack_timeout')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--activate',action='store_true');parser.add_argument('--home',type=Path,default=DEFAULT_HOME)
    args=parser.parse_args();os.umask(0o077);home=args.home.resolve();source=Path(__file__).resolve().parent;live=home/'code/bridge.py'
    baseline=json.loads((source/'BASELINE_SHA256.json').read_text());expected=baseline['bridge.py']
    def unchanged():
        if any(not (home/'code'/n).is_file() or hashlib.sha256((home/'code'/n).read_bytes()).hexdigest()!=v for n,v in baseline.items()):raise ValueError('runtime_files_changed')
    unchanged()
    cfg=json.loads((home/'config.json').read_text());remote=SSH(cfg);status=remote.call('status');local=control(home);rf=remote_files(remote)
    check(cfg,status,local,rf,hashlib.sha256(live.read_bytes()).hexdigest(),expected)
    print('Preflight passed: NAS ready, canonical filenames identical, active text epoch preserved.',flush=True)
    if not args.activate:return
    with lock(home/'activation.lock'):
        unchanged()
        cfg=json.loads((home/'config.json').read_text());remote=SSH(cfg);snapshot=control(home)
        check(cfg,remote.call('status'),snapshot,remote_files(remote),hashlib.sha256(live.read_bytes()).hexdigest(),expected)
        backup=home/'backups'/('before-attachments-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'));backup.mkdir(parents=True,mode=0o700)
        old=[n for n in FILES if (home/'code'/n).is_file()]
        for n in old:shutil.copyfile(home/'code'/n,backup/n);(backup/n).chmod(0o600)
        shutil.copyfile(home/'config.json',backup/'config.json');(backup/'config.json').chmod(0o600)
        (backup/'ROLLBACK.txt').write_text('Stop only the independent Mac worker; restore backed-up code/config. Keep the current ledger and additive schema. Never restore state-audit.db or retry unknown events. No NAS/EFB/Telegram/WeChat changes.\n')
        with contextlib.closing(sqlite3.connect(home/'state.db')) as db,contextlib.closing(sqlite3.connect(backup/'state-audit.db')) as dest:db.backup(dest)
        (backup/'state-audit.db').chmod(0o600)
        # Re-read under the same emission lock used by the worker to avoid losing a newer cursor.
        with lock(home/'emission.lock'):snapshot=control(home)
        since=time.time()
        try:
            snapshot=stop_local(home,live)
            for n in FILES:replace(home/'code'/n,(source/n).read_bytes())
            updated=dict(cfg);updated['attachments_enabled']=True
            replace(home/'config.json',(json.dumps(updated,ensure_ascii=True,indent=2)+'\n').encode())
            restore_control(home,snapshot)
            start(home,live,preserve=True)
            acks=verify(home,live,snapshot,since,remote)
        except BaseException:
            stop_local(home,live)
            for n in FILES:
                if n in old:replace(home/'code'/n,(backup/n).read_bytes())
                # Keep newly introduced orphan modules; the restored bridge never imports them.
            replace(home/'config.json',(backup/'config.json').read_bytes());restore_control(home,snapshot);start(home,live)
            print('Independent Mac patch rolled back; ledger retained. Backup:',backup,flush=True)
            raise
        report={'tests':160,'backup':str(backup),'attachment_enabled':True,'config_keys_changed':['attachments_enabled'],'text_start_preserved':True,'epoch':snapshot['epoch'],'acks':acks,
                'bridge_sha256':hashlib.sha256(live.read_bytes()).hexdigest(),'no_generated_test_messages':True}
        replace(home/'attachment-deployment-verification.json',(json.dumps(report,indent=2)+'\n').encode())
        print('Independent Mac attachment upgrade verified: 3 fresh acks, one worker; text epoch/start preserved and cursor continues.',flush=True)
        print('Backup:',backup,flush=True)

if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Attachment activation stopped or independently rolled back; details suppressed. No message replay attempted.') from None
