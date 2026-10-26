"""Install only the independent Mac worker. --check is read-only; no on/off RPC."""
import argparse
import datetime
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
from bridge import SSH,Store,DEFAULT_HOME,lock
from activate_images import workers

FILES=['bridge.py','image_api.py','image_transfer.py','ssh_images.exp','ssh_stdio.exp']

def check(config,status,current,expected):
    if config.get('validated_single_imessage_chat') is not True:raise ValueError('invalid_allowlist')
    if status.get('ready') is not True:raise ValueError('backend_not_ready')
    if current!=expected:raise ValueError('live_source_changed')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--activate',action='store_true');parser.add_argument('--home',type=Path,default=DEFAULT_HOME)
    args=parser.parse_args();home=args.home.resolve();source=Path(__file__).resolve().parent;live=home/'code/bridge.py'
    expected=json.loads((source/'MANAGED_BASE.json').read_text())['bridge_sha256']
    cfg=json.loads((home/'config.json').read_text());status=SSH(cfg).call('status')
    check(cfg,status,hashlib.sha256(live.read_bytes()).hexdigest(),expected)
    if not args.activate:print('Read-only checks passed; current worker unchanged.');return
    os.umask(0o077)
    with lock(home/'activation.lock'):
        cfg=json.loads((home/'config.json').read_text());check(cfg,SSH(cfg).call('status'),hashlib.sha256(live.read_bytes()).hexdigest(),expected)
        # Desired state is applied on NAS; never synthesize an on/off toggle.
        snap=SSH(cfg).call('control_sync');control=snap.get('control',{})
        if type(control.get('revision')) is not int or any(type(control.get(k)) is not bool for k in ['enabled','images_enabled']):raise ValueError('managed_protocol_unavailable')
        stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');backup=home/'backups'/('before-managed-'+stamp);backup.mkdir(parents=True,mode=0o700)
        for name in FILES:shutil.copyfile(home/'code'/name,backup/name);(backup/name).chmod(0o600)
        shutil.copyfile(home/'config.json',backup/'config.json');(backup/'config.json').chmod(0o600)
        (backup/'ROLLBACK.txt').write_text('Stop only this independent worker, restore code/config, then explicitly enable from now. Keep the current ledger; never restore older state.db. No Telegram/WeChat/EFB restart.\n')
        # Fence and terminate the exact independent process group, including its SSH children.
        with lock(home/'emission.lock'):
            pids=workers(live)
            if len(pids)>1:raise RuntimeError('duplicate_worker')
            store=Store(home);store.set(enabled=0,pending_off=0,off_requested=0,phase='paused_connection')
            with store.db() as db,sqlite3.connect(backup/'state-audit.db') as dest:db.backup(dest)
            (backup/'state-audit.db').chmod(0o600)
            for pid in pids:
                if os.getpgid(pid)!=pid:raise RuntimeError('unexpected_worker_group')
                os.killpg(pid,signal.SIGTERM)
            for _ in range(50):
                if not workers(live):break
                time.sleep(.1)
            else:raise RuntimeError('worker_stop_unconfirmed')
            for name in FILES:
                target=home/'code'/name;temp=target.with_suffix('.new');temp.write_bytes((source/name).read_bytes());temp.chmod(0o600);os.replace(temp,target)
            cfg['managed_control']=True;temp=home/'config.json.new';temp.write_text(json.dumps(cfg,ensure_ascii=True,indent=2)+'\n');temp.chmod(0o600);os.replace(temp,home/'config.json')
        subprocess.Popen([sys.executable,str(live),'--home',str(home),'worker'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        print('Independent Mac worker installed. Await a fresh actual-state acknowledgement; no messages generated.');print('Backup:',backup)

if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Managed activation stopped; keep paused and inspect the private backup. No automatic replay or service-wide restart.') from None
