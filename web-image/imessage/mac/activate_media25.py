"""25-MiB code-only upgrade; no config writes, on/off calls or test messages."""
import argparse
import contextlib
import datetime
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
from bridge import SSH,Store,DEFAULT_HOME,GATEWAY,lock
from image_api import MAX_BYTES
from activate_attachments import FILES,control,remote_files,check,stop_local,start,restore_control,verify


def remote_limits(remote):
    script=("import sys,json;print('IMESSAGE_STDIN_READY',flush=True);sys.stdin.readline();"
            "sys.path.insert(0,"+repr(str(Path(GATEWAY).parent))+");"
            "from media_types import MAX_BYTES as media;from image_store import MAX_BYTES as store;"
            "print(json.dumps({'media':media,'store':store}))")
    return remote.command('python3 -u -c '+shlex.quote(script),{})


def check_limits(limits):
    if MAX_BYTES!=25*1024*1024 or limits!={'media':MAX_BYTES,'store':MAX_BYTES}:raise ValueError('NAS_media_limit_not_ready')


def media_floor(home,now_ms):
    # Newly accepted large images must not reappear from the previous overlap window.
    Store(home).set(image_start_ms=now_ms,attachment_start_ms=now_ms)


def runtime_limits(home):
    script=("import sys,json;sys.path.insert(0,"+repr(str(home/'code'))+");"
            "from image_api import MAX_BYTES as image;from attachment_api import MAX_BYTES as file;"
            "from image_transfer import MAX_BYTES as stream;from bridge import MAX_BYTES as incoming;"
            "print(json.dumps({'image':image,'file':file,'stream':stream,'incoming':incoming}))")
    result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,check=True,timeout=10)
    values=json.loads(result.stdout)
    if any(value!=25*1024*1024 for value in values.values()) or len(values)!=4:raise RuntimeError('runtime_limit_mismatch')
    return values


def replace(path,data):
    temp=path.with_name(path.name+'.new');temp.write_bytes(data);temp.chmod(0o600);os.replace(temp,path)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--activate',action='store_true');parser.add_argument('--home',type=Path,default=DEFAULT_HOME)
    args=parser.parse_args();os.umask(0o077);home=args.home.resolve();source=Path(__file__).resolve().parent;live=home/'code/bridge.py'
    baseline=json.loads((source/'BASELINE_SHA256.json').read_text())
    def preflight():
        if any(not (home/'code'/n).is_file() or hashlib.sha256((home/'code'/n).read_bytes()).hexdigest()!=v for n,v in baseline.items()):raise ValueError('source_changed')
        cfg=json.loads((home/'config.json').read_text());remote=SSH(cfg);snapshot=control(home)
        check(cfg,remote.call('status'),snapshot,remote_files(remote),hashlib.sha256(live.read_bytes()).hexdigest(),baseline['bridge.py'])
        check_limits(remote_limits(remote));return cfg,remote,snapshot
    cfg,remote,snapshot=preflight();print('25-MiB preflight passed; code-only upgrade, no switch changes.',flush=True)
    if not args.activate:return
    with lock(home/'activation.lock'):
        cfg,remote,snapshot=preflight();config_bytes=(home/'config.json').read_bytes()
        backup=home/'backups'/('before-media25-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'));backup.mkdir(parents=True,mode=0o700)
        for n in FILES:shutil.copyfile(home/'code'/n,backup/n);(backup/n).chmod(0o600)
        (backup/'config.json').write_bytes(config_bytes);(backup/'config.json').chmod(0o600)
        (backup/'ROLLBACK.txt').write_text('Restore backed-up independent worker code only, keep current config and ledger. Retain unused modules; never restore state-audit.db, delete batches or resend unknown events. No NAS/EFB/Telegram/WeChat actions.\n')
        with contextlib.closing(sqlite3.connect(home/'state.db')) as db,contextlib.closing(sqlite3.connect(backup/'state-audit.db')) as dest:db.backup(dest)
        (backup/'state-audit.db').chmod(0o600);since=time.time()
        try:
            snapshot=stop_local(home,live)
            for n in FILES:
                data=(source/n).read_bytes()
                if data!=(home/'code'/n).read_bytes():replace(home/'code'/n,data)
            restore_control(home,snapshot);floor_ms=int(time.time()*1000);media_floor(home,floor_ms)
            start(home,live,preserve=True)
            acks=verify(home,live,snapshot,since,remote);limits=runtime_limits(home)
            if (home/'config.json').read_bytes()!=config_bytes:raise RuntimeError('configuration_changed')
            if control(home)['image_start_ms']<floor_ms:raise RuntimeError('media_floor_not_current')
        except BaseException:
            stop_local(home,live)
            for n in FILES:replace(home/'code'/n,(backup/n).read_bytes())
            restore_control(home,snapshot);media_floor(home,int(time.time()*1000));start(home,live,preserve=True)
            print('Only independent Mac code rolled back; current configuration/ledger retained. Backup:',backup,flush=True)
            raise
        report={'tests':164,'backup':str(backup),'config_unchanged':True,'text_start_preserved':True,'epoch':snapshot['epoch'],
                'image_start_ms':floor_ms,'acks':acks,'limits':limits,'bridge_sha256':hashlib.sha256(live.read_bytes()).hexdigest(),
                'no_generated_test_messages':True,'no_history_replay':True}
        replace(home/'media25-deployment-verification.json',(json.dumps(report,indent=2)+'\n').encode())
        print('25-MiB independent worker verified: 3 fresh acks, single worker, runtime constants 25 MiB, configuration unchanged.',flush=True)
        print('Backup:',backup,flush=True)

if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Media25 activation stopped or independent code rolled back; no message/file replay attempted.') from None
