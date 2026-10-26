"""SSH stdio gateway. No listener, Telegram polling, or EFB restart required."""
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo
from bridge_control import control_for, permitted


def atomic(path, value):
    previous = path.stat() if path.exists() else None
    tmp = path.with_suffix('.tmp')
    with open(tmp, 'w') as f:
        os.chmod(tmp, (previous.st_mode & 0o777) if previous else 0o600)
        json.dump(value, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    if previous is not None and os.geteuid()==0:
        os.chown(tmp, previous.st_uid, previous.st_gid)
        os.chmod(tmp, previous.st_mode & 0o777)
    os.replace(tmp, path)


def images_ready(config):
    if config.get('images_enabled') is not True or not permitted(config,images=True):return False
    try:
        heartbeat=json.loads((Path(config.get('image_media_dir',config['state_dir']))/'image-runtime.json').read_text())
        return heartbeat.get('ready') is True and 0<=time.time()-heartbeat['updated']<15
    except (OSError,ValueError,KeyError,TypeError):return False


class Gateway:
    def __init__(self, config, enable_mirror=True):
        self.config = config
        self.root = Path(config['state_dir'])
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        sys.path.insert(0, config['control_code'])
        from notification_queue import Store, binding
        self.binding = binding
        self.store = Store(config['notification_root'], config['bindings_db'])
        self.mirror=None
        if enable_mirror and config.get('mirror_to_telegram') is True:
            from mirror import Mirror
            self.mirror=Mirror(config)

    def db(self):
        return sqlite3.connect(Path(self.config['bindings_db']).resolve().as_uri()+'?mode=ro', uri=True, timeout=.2)

    def highwater(self):
        with self.db() as db:
            return db.execute('SELECT coalesce(max(rowid),0) FROM msglog').fetchone()[0]

    def ready(self):
        state = json.loads(Path(self.config['backend_state']).read_text())
        return (state.get('active') == 'web' and state.get('phase') == 'idle'
                and self.binding(self.config['bindings_db'], self.config['chat_id']) == self.config['target'])

    def sync_control(self):
        ctl=control_for(self.config);desired=ctl.desired()
        applied=ctl.read('applied.json',{})
        if applied.get('revision')!=desired['revision']:
            status=self.run({'op':'status'})
            current=json.loads((self.root/'state.json').read_text())
            if (desired['enabled'] and not status['enabled']) or (not desired['enabled'] and current['enabled']):
                result=self.run({'op':'on' if desired['enabled'] else 'off','managed':True})
                if result.get('error'):return dict(status,control=desired,error=result['error'])
            from image_store import ImageStore
            from image_outbound import Outbound
            if not desired['enabled'] or not desired['images_enabled']:
                ImageStore(Path(self.config.get('image_media_dir',self.root/'media'))).cancel()
                Outbound(self.config).cancel()
            with ctl.lock():
                if ctl.desired()['revision']==desired['revision']:ctl.write('applied.json',{'revision':desired['revision']})
        return dict(self.run({'op':'status'}),control=ctl.desired())

    def run(self, request):
        op=request.get('op')
        managed=self.config.get('remote_control_enabled') is True
        if managed and op=='control_sync':return self.sync_control()
        if managed and op in ('on','off') and not request.get('managed'):
            control_for(self.config).set(enabled=op=='on')
        with open(self.root/'lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self.root/'state.json'
            state = json.loads(path.read_text()) if path.exists() else {'enabled':False, 'cursor':self.highwater(), 'epoch':0}
            op = request.get('op')
            if op in ('on', 'off'):
                enabled = op == 'on'
                if enabled and not self.ready():
                    return {'error':'backend_or_binding_unavailable'}
                with open(self.store.root/'policy.lock','a') as policy_lock:
                    fcntl.flock(policy_lock,fcntl.LOCK_EX)
                    policy = self.store.policy()
                    route = policy.get('routes', {}).get(self.config['route'])
                    if not route or route.get('target') != self.config['target']:
                        return {'error':'route_not_configured'}
                    # Preserve HA route and revision; cancel only our source.
                    route['enabled'] = enabled
                    atomic(self.store.root/'policy.json', policy)
                with self.store.connect() as db:
                    db.execute("UPDATE events SET status='cancelled',text='',reason='imessage_toggle',updated=? WHERE source=? AND status='pending'", (time.time(), self.config['source']))
                if self.mirror:self.mirror.cancel()
                if self.config.get('images_enabled') is True:
                    from image_store import ImageStore
                    from image_outbound import Outbound
                    ImageStore(Path(self.config.get('image_media_dir',self.root/'media'))).cancel();Outbound(self.config).cancel()
                state.update(enabled=enabled, cursor=self.highwater(), epoch=state['epoch']+1, last_poll=time.time())
                atomic(path, state)
                return {'enabled':enabled, 'epoch':state['epoch'], 'note':'inflight_may_finish'}
            policy=self.store.policy()
            allowed=policy.get('enabled') is True and policy.get('routes',{}).get(self.config['route'],{}).get('enabled') is True
            if managed and op=='control_ack':
                try:control_for(self.config).acknowledge({k:v for k,v in request.items() if k!='op'},state['epoch'])
                except ValueError as e:return {'error':str(e)}
                return {'acknowledged':True}
            if op == 'status':
                return {'enabled':state['enabled'] and allowed, 'epoch':state['epoch'], 'ready':self.ready(), 'images_ready':images_ready(self.config)}
            if not state['enabled'] or not allowed or not self.ready() or not permitted(self.config):
                state['cursor'] = self.highwater()
                atomic(path, state)
                return {'enabled':False, 'epoch':state['epoch']}
            if op == 'submit':
                if request.get('epoch') != state['epoch']:
                    return {'error':'stale_epoch'}
                with self.store.connect() as db:
                    pending=db.execute("SELECT count(*) FROM events WHERE source=? AND status IN ('pending','intent')",(self.config['source'],)).fetchone()[0]
                if pending>=5:
                    return {'error':'imessage_queue_full'}
                # Caller supplies no destination. The sole route is private config.
                result = self.store.submit({'source':self.config['source'], 'route':self.config['route'],
                    'origin':'external', 'event_id':request['event_id'], 'text':request['text'], 'ttl':120}, self.config['source_token'])
                if self.mirror and not result.get('duplicate'):
                    self.mirror.stage(request['event_id'],state['epoch'],request['text'])
                return result
            if op == 'poll':
                if self.mirror:self.mirror.tick(self.store,state['epoch'])
                now = time.time()
                if now-state.get('last_poll',0)>30:
                    state.update(cursor=self.highwater(), last_poll=now)
                    atomic(path,state)
                    return {'enabled':True,'epoch':state['epoch'],'message':None}
                state['last_poll'] = now
                with self.db() as db:
                    high = self.highwater()
                    if high < state['cursor']:
                        return {'error':'database_replaced'}
                    since = (datetime.datetime.now(ZoneInfo(self.config.get('log_timezone', 'UTC')))-datetime.timedelta(seconds=120)).replace(tzinfo=None).isoformat(' ')
                    include_images=request.get('images') is True and images_ready(self.config)
                    include_attachments=include_images and request.get('attachments') is True and self.config.get('attachments_enabled') is True
                    columns={r[1] for r in db.execute('PRAGMA table_info(msglog)')}
                    mime_column=',mime' if include_attachments and 'mime' in columns else ',NULL'
                    rows = db.execute("SELECT rowid,slave_message_id,text,msg_type,time,sent_to"+(',file_id' if include_images else ',NULL')+mime_column+" FROM msglog WHERE rowid>? AND rowid<=? AND slave_origin_uid=? AND (sent_to='blueset.telegram' OR (? AND sent_to='blueset.wechat' AND time>=? AND substr(slave_message_id,1,8)<>'__fail__')) AND (msg_type='Text' OR (? AND msg_type='Image') OR (? AND msg_type IN ('File','Video','Audio','Voice'))) AND time>=? ORDER BY rowid LIMIT 1", (state['cursor'], high, self.config['target'], self.config.get('mirror_from_telegram') is True, self.config.get('mirror_since',''), include_images, include_attachments, since)).fetchall()
                # Commit before exposing text: an uncertain send is never replayed.
                state['cursor'] = rows[0][0] if rows else high
                atomic(path, state)
                if not rows:
                    return {'enabled':True, 'epoch':state['epoch'], 'message':None}
                _, uid, text, msg_type, logged_at, direction, file_id, mime = rows[0]
                created = datetime.datetime.fromisoformat(logged_at).replace(tzinfo=ZoneInfo(self.config.get('log_timezone', 'UTC'))).timestamp()
                with sqlite3.connect(self.root/'delivery.sqlite', timeout=.2) as ledger:
                    ledger.execute('CREATE TABLE IF NOT EXISTS delivered(id TEXT PRIMARY KEY)')
                    key = hashlib.sha256((uid if direction=='blueset.telegram' else 'telegram-out:'+uid).encode()).hexdigest()
                    if ledger.execute('SELECT 1 FROM delivered WHERE id=?',(key,)).fetchone():
                        return {'enabled':True,'epoch':state['epoch'],'message':None}
                    # Retain compact deduplication IDs permanently; no lifetime message quota.
                    ledger.execute('INSERT INTO delivered VALUES (?)',(key,))
                if msg_type in ('Image','File','Video','Audio','Voice'):
                    if managed and created<control_for(self.config).desired().get('images_since',0):
                        return {'enabled':True,'epoch':state['epoch'],'message':None}
                    from image_outbound import Outbound
                    try:
                        if msg_type=='Image':message=Outbound(self.config).grant(key,state['epoch'],file_id,created)
                        else:
                            if created<self.config.get('attachments_since',float('inf')):raise ValueError('attachment_before_enable')
                            import mimetypes
                            from media_types import filename as safe_filename
                            ext=mimetypes.guess_extension(mime or '') or '.bin'
                            name=safe_filename(text if text and '\n' not in text and len(text)<180 else 'attachment'+ext)
                            message=Outbound(self.config).grant(key,state['epoch'],file_id,created,filename=name,mime=mime,voice=msg_type=='Voice')
                    except Exception as error:
                        from diagnostics import record
                        record(self.config,'outbound_media',error)
                        message=None
                    return {'enabled':True,'epoch':state['epoch'],'message':message}
                if not text or len(text)>4000:
                    return {'enabled':True, 'epoch':state['epoch'], 'message':None}
                if direction=='blueset.wechat' and len(text)<=3980:text='[Telegram 发出]\n'+text
                return {'enabled':True, 'epoch':state['epoch'], 'message':{'id':key, 'text':text, 'created':created}}
            return {'error':'invalid_operation'}


def main():
    os.umask(0o077)
    try:
        config = json.loads(Path(sys.argv[1]).read_text())
        raw = sys.stdin.buffer.readline(20001)
        if len(raw)>20000:
            raise ValueError('too large')
        result = Gateway(config).run(json.loads(raw))
    except Exception:
        result = {'error':'temporarily_unavailable'}
    print(json.dumps(result, ensure_ascii=False))

if __name__ == '__main__':
    main()
