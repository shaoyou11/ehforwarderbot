"""Host-side disable/rollback. Rollback changes code image, never message DBs."""
import argparse
import fcntl
import json
import secrets
import sqlite3
import subprocess
import time
from pathlib import Path
import yaml
from protocol import atomic_json


def disable(root):
    root=Path(root)/'controller-state/notifications'
    with open(root/'policy.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        policy=json.loads((root/'policy.json').read_text());policy.update(enabled=False,revision=secrets.token_hex(12))
        atomic_json(root/'policy.json',policy,0o600)
    if (root/'queue.sqlite').exists():
        with sqlite3.connect(root/'queue.sqlite') as db:
            db.execute("UPDATE events SET status='cancelled',text='',reason='disabled',updated=? WHERE status='pending'",(time.time(),))


def rollback(root,backup,apply=False):
    root=Path(root);backup=Path(backup)
    manifest=json.loads((backup/'manifest.json').read_text())
    image=manifest['image']
    if not image.startswith('ghcr.io/shaoyou11/efb-web-standby@sha256:'):raise ValueError('unexpected baseline image')
    paths=[root/'docker-compose.yaml']
    if (root/'login-test.compose.json').exists():paths.append(root/'login-test.compose.json')
    if not apply:return {'action':'rollback','image':image,'preserves':'current profiles, bindings, sessions and message history'}
    state=json.loads((root/'controller-state/state.json').read_text())
    if state.get('active')!='web' or state.get('phase')!='idle':raise ValueError('web must be active and idle')
    subprocess.run(['docker','image','inspect',image],check=True,stdout=subprocess.DEVNULL)
    disable(root)
    # Preserve the current deployment too; no automatic database restoration.
    snapshot=root/'controller-state/backups'/('before-notification-rollback-'+str(time.time_ns()));snapshot.mkdir(mode=0o700)
    for p in paths:
        (snapshot/p.name).write_bytes(p.read_bytes());(snapshot/p.name).chmod(0o600)
    subprocess.run(['systemctl','stop','efb-backend-control.service'],check=True)
    try:
        h=json.loads((root/'controller-state/web-health.json').read_text())
        if h.get('inflight') or h.get('queue_size'):raise ValueError('ordinary messages busy; retry later')
        for p in paths:
            data=json.loads(p.read_text()) if p.suffix=='.json' else yaml.safe_load(p.read_text())
            data['services']['web-standby']['image']=image
            p.write_text(json.dumps(data) if p.suffix=='.json' else yaml.safe_dump(data,allow_unicode=True,sort_keys=False))
        started=time.time()
        subprocess.run(['docker','compose','-f',str(paths[0]),'up','-d','--no-deps','--pull','never','web-standby'],check=True,timeout=120)
        deadline=time.time()+150
        while time.time()<deadline:
            time.sleep(3)
            h=json.loads((root/'controller-state/web-health.json').read_text())
            if h.get('wechat_online') is True and h.get('ready') and h.get('updated',0)>started:
                return {'action':'rollback_verified_online','image':image}
        raise RuntimeError('fresh login heartbeat timeout')
    except Exception:
        for p in paths:p.write_bytes((snapshot/p.name).read_bytes())
        subprocess.run(['docker','compose','-f',str(paths[0]),'up','-d','--no-deps','--pull','never','web-standby'],check=True,timeout=120)
        raise
    finally:subprocess.run(['systemctl','start','efb-backend-control.service'],check=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['off','rollback']);p.add_argument('--root',required=True);p.add_argument('--backup');p.add_argument('--apply',action='store_true');args=p.parse_args()
    if args.action=='off':disable(args.root);print('通知转发已关闭，普通聊天不变。')
    else:print(json.dumps(rollback(args.root,args.backup,args.apply),ensure_ascii=False))
