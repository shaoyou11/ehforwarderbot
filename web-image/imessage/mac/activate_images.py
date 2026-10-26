"""Guarded Mac-only activation. Default --check never changes a service/config."""
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from zoneinfo import ZoneInfo
from bridge import SSH,DEFAULT_HOME,lock

TEXT_BASE_SHA='94063e1430faf0646a51c23dbe404b1d2c489be87619877f0dec2bad19030373'
FILES=['bridge.py','image_api.py','image_transfer.py','ssh_images.exp','ssh_stdio.exp']


def check(config,status,live_hash,candidate_hash):
    if config.get('validated_single_imessage_chat') is not True:raise ValueError('whitelist_not_validated')
    if status.get('ready') is not True or status.get('images_ready') is not True:raise ValueError('NAS_images_not_ready')
    if live_hash not in [TEXT_BASE_SHA,candidate_hash]:raise ValueError('live_source_changed_requires_review')


def workers(path):
    text=subprocess.run(['/bin/ps','-axo','pid=,args='],capture_output=True,text=True,check=True).stdout
    return [int(line.strip().split(None,1)[0]) for line in text.splitlines() if str(path) in line and line.rstrip().endswith(' worker')]


def read_control(home):
    with contextlib.closing(sqlite3.connect((home/'state.db').as_uri()+'?mode=ro',uri=True)) as db:
        return db.execute('SELECT enabled,pending_off FROM control WHERE id=1').fetchone()


def run_control(path,home,operation):
    result=subprocess.run([sys.executable,str(path),'--home',str(home),operation],capture_output=True,text=True,timeout=65)
    if result.returncode:raise RuntimeError('independent_control_failed')


def stop(home,path):
    pids=workers(path)
    if len(pids)>1:raise RuntimeError('unexpected_duplicate_workers')
    run_control(path,home,'off')
    enabled,pending=read_control(home)
    if enabled or pending:raise RuntimeError('remote_close_unconfirmed')
    deadline=time.monotonic()+6
    while time.monotonic()<deadline:
        if not workers(path):return
        time.sleep(.2)
    # Kill only the exact independent worker already fenced locally/remotely.
    for pid in workers(path):
        if pid not in pids:raise RuntimeError('worker_changed_during_activation')
        os.kill(pid,signal.SIGTERM)
    for _ in range(10):
        if not workers(path):return
        time.sleep(.2)
    raise RuntimeError('worker_not_stopped')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--activate',action='store_true')
    parser.add_argument('--home',type=Path,default=DEFAULT_HOME)
    args=parser.parse_args();home=args.home.resolve();source=Path(__file__).resolve().parent
    config=json.loads((home/'config.json').read_text());status=SSH(config).call('status')
    live=home/'code/bridge.py'
    live_hash=hashlib.sha256(live.read_bytes()).hexdigest();candidate_hash=hashlib.sha256((source/'bridge.py').read_bytes()).hexdigest()
    print('NAS backend ready:',status.get('ready') is True)
    print('NAS image protocol ready:',status.get('images_ready') is True)
    print('Current private image flag:',config.get('images_enabled') is True)
    if not args.activate:
        print('Check only; nothing changed.');return
    check(config,status,live_hash,candidate_hash)
    if live_hash==candidate_hash and config.get('images_enabled') is True:
        print('This candidate is already installed; no restart performed.');return
    os.umask(0o077)
    with lock(home/'activation.lock'):
        # Check again under the local deployment lock before changing anything.
        config=json.loads((home/'config.json').read_text());status=SSH(config).call('status')
        check(config,status,hashlib.sha256(live.read_bytes()).hexdigest(),candidate_hash)
        stamp=datetime.datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d-%H%M%S')
        backup=home/'backups'/('before-images-'+stamp);backup.mkdir(parents=True,mode=0o700)
        for name in FILES:
            p=home/'code'/name
            if p.exists():shutil.copyfile(p,backup/name);(backup/name).chmod(0o600)
        shutil.copyfile(home/'config.json',backup/'config.json');(backup/'config.json').chmod(0o600)
        (backup/'ROLLBACK.txt').write_text('Close the independent bridge; restore code/config only; enable from now. Never restore an older ledger. No EFB/Telegram/WeChat process was restarted by this installer.\n')
        was_enabled=bool(read_control(home)[0])
        stop(home,live)
        for name in FILES:
            data=(source/name).read_bytes();target=home/'code'/name;stage=target.with_suffix(target.suffix+'.new')
            stage.write_bytes(data);stage.chmod(0o600);os.replace(stage,target)
        config['images_enabled']=True
        staging=home/'config.json.new';staging.write_text(json.dumps(config,ensure_ascii=True,indent=2)+'\n');staging.chmod(0o600);os.replace(staging,home/'config.json')
        if was_enabled:run_control(live,home,'on')
        print('Image candidate installed; only independent Mac bridge restarted from now. No test/history message sent.')
        print('Rollback backup:',backup)


if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Activation stopped safely; protocol/readiness/control check failed. No retry performed.') from None
