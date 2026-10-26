#!/usr/bin/env python3
"""Independent text-only bridge. No message/credential logs, no public listener."""
import argparse
import base64
import contextlib
import fcntl
import io
import hashlib
import json
import os
from pathlib import Path
import runpy
import shlex
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid

DEFAULT_HOME = Path.home() / 'Library/Application Support/imessage-text-bridge'
GATEWAY = '/vol1/1000/docker/efb-web-lab-20260927/imessage-code/gateway.py'
REMOTE_CONFIG = '/vol1/1000/docker/efb-web-lab-20260927/controller-state/imessage/config.json'
MAX_AGE = 120

from attachment_api import Attachments, clean_filename, safe_mime, validate_file, attachment_metadata, AUDIO_MIME_PAIRS
from image_api import MAX_BYTES


@contextlib.contextmanager
def lock(path, nonblocking=False):
    with open(path, 'a', encoding='utf-8') as f:
        os.chmod(path, 0o600)
        fcntl.flock(f, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


class Store:
    def __init__(self, home):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.home, 0o700)
        self.path = self.home / 'state.db'
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS control
              (id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL DEFAULT 0,
               pending_off INTEGER NOT NULL DEFAULT 1, epoch TEXT NOT NULL DEFAULT '',
               start_ms INTEGER NOT NULL DEFAULT 0, cursor_ms INTEGER NOT NULL DEFAULT 0,
               phase TEXT NOT NULL DEFAULT 'closed');
              INSERT OR IGNORE INTO control(id) VALUES(1);
              CREATE TABLE IF NOT EXISTS ledger
              (direction TEXT NOT NULL,event_id TEXT NOT NULL,status TEXT NOT NULL,
               created REAL NOT NULL,PRIMARY KEY(direction,event_id));''')
            columns={row[1] for row in db.execute('PRAGMA table_info(control)')}
            additions={'images_enabled':'INTEGER NOT NULL DEFAULT 0','image_start_ms':'INTEGER NOT NULL DEFAULT 0',
                       'control_revision':"TEXT NOT NULL DEFAULT ''",'control_synced_at':'REAL NOT NULL DEFAULT 0',
                       'control_acked_at':'REAL NOT NULL DEFAULT 0','off_requested':'INTEGER NOT NULL DEFAULT 0',
                       'attachment_start_ms':'INTEGER NOT NULL DEFAULT 0'}
            for name,kind in additions.items():
                if name not in columns:db.execute('ALTER TABLE control ADD COLUMN '+name+' '+kind)
            if 'off_requested' not in columns:
                db.execute("UPDATE control SET off_requested=1 WHERE pending_off=1 AND phase='closed_remote_sync_pending'")
        os.chmod(self.path, 0o600)

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self):
        with self.db() as db:
            db.row_factory = sqlite3.Row
            return dict(db.execute('SELECT * FROM control WHERE id=1').fetchone())

    def set(self, **values):
        allowed = {'enabled','pending_off','epoch','start_ms','cursor_ms','phase','images_enabled','image_start_ms','control_revision','control_synced_at','control_acked_at','off_requested','attachment_start_ms'}
        assert values and set(values) <= allowed
        with self.db() as db:
            db.execute('UPDATE control SET '+','.join(k+'=?' for k in values)+' WHERE id=1', list(values.values()))

    def intent(self, direction, event_id):
        with self.db() as db:
            return db.execute('INSERT OR IGNORE INTO ledger VALUES(?,?,?,?)',
                              (direction,event_id,'intent',time.time())).rowcount == 1

    def seen(self, direction, event_id):
        with self.db() as db:
            return db.execute('SELECT 1 FROM ledger WHERE direction=? AND event_id=?',(direction,event_id)).fetchone() is not None

    def finish(self, direction, event_id, status):
        with self.db() as db:
            db.execute('UPDATE ledger SET status=? WHERE direction=? AND event_id=?', (status,direction,event_id))

    def recover(self):
        with self.db() as db:
            db.execute("UPDATE ledger SET status='unknown' WHERE status='intent'")


class SSH:
    def __init__(self, config):
        self.config = config

    def command(self, command, payload):
        # Import the existing credentials entrance. Never print its output/errors.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            helper = runpy.run_path(self.config['credential_helper'], run_name='bridge_credentials')
            host, user, password = helper['extract'](self.config['ssh_label'])
        # command is a fixed, caller-owned Python script; requests only use stdin.
        if hashlib.sha256(host.strip().encode()).hexdigest() != self.config['expected_host_digest']:
            raise ConnectionError('credential_target_changed')
        # Disable canonical buffering after sudo authentication, for this PTY only.
        normalize = "import sys,json\nfor line in sys.stdin:\n if line.strip()=='IMESSAGE_STDIN_READY': print(line.strip(),flush=True)\n else:\n  try: value=json.loads(line)\n  except Exception: continue\n  print(json.dumps(value,ensure_ascii=True),flush=True)"
        decode_input = "import sys,base64;raw=base64.b64decode(sys.stdin.buffer.readline().strip(),validate=True);sys.stdout.buffer.write(raw+b'\\n');sys.stdout.buffer.flush()"
        pipeline = "python3 -u -c " + shlex.quote(decode_input) + " | " + command + " | python3 -u -c " + shlex.quote(normalize)
        remote = "stty -echo; exec sudo -S -- sh -c " + shlex.quote("stty -icanon -echo min 1 time 0; " + pipeline)
        raw_request=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode('utf-8')
        if len(raw_request)>19900:raise ValueError('request_too_large')
        env = os.environ.copy()
        env.update(CODEX_REMOTE_HOST=host, CODEX_REMOTE_USER=user,
                   CODEX_REMOTE_PASSWORD=password, IMESSAGE_REMOTE_COMMAND=remote,
                   IMESSAGE_REQUEST=base64.b64encode(raw_request).decode('ascii'))
        result = subprocess.run(['/usr/bin/expect', str(Path(__file__).with_name('ssh_stdio.exp'))],
                                env=env, capture_output=True, text=True, timeout=25)
        if result.returncode:
            raise ConnectionError('ssh_unavailable')
        try:
            data = json.loads(result.stdout)
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, TypeError):
            raise ConnectionError('invalid_gateway_response') from None

    def call(self, op, **values):
        wrapper = ("import sys,runpy;print('IMESSAGE_STDIN_READY',flush=True);"
                   "sys.path.insert(0,"+repr(str(Path(GATEWAY).parent))+");sys.argv="+repr([GATEWAY,REMOTE_CONFIG])+";runpy.run_path("+repr(GATEWAY)+",run_name='__main__')")
        return self.command('python3 -u -c '+shlex.quote(wrapper), {'op':op,**values})


class BlueBubbles:
    def __init__(self, config):
        self.config = config
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path, body):
        cfg = Path(self.config.get('bluebubbles_db', str(Path.home()/'Library/Application Support/bluebubbles-server/config.db')))
        with contextlib.closing(sqlite3.connect(cfg.as_uri()+'?mode=ro', uri=True)) as db:
            settings = dict(db.execute("SELECT name,value FROM config WHERE name IN ('password','socket_port')"))
        url = 'http://127.0.0.1:'+str(int(settings['socket_port']))+'/api/v1/'+path+'?password='+urllib.parse.quote(settings['password'],safe='')
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={'Content-Type':'application/json'})
        with self.opener.open(req, timeout=12) as response:
            result = json.loads(response.read(4*1024*1024))
        if result.get('status') != 200:
            raise ConnectionError('api_failed')
        return result

    def messages(self, after, before):
        # chatGuid never comes from network events. Exact locally stored allowlist only.
        result = self.request('message/query', {'chatGuid':self.config['chat_guid'],
            'after':after,'before':before,'sort':'ASC','offset':0,'limit':100,
            'with':['attributedBody','attachments']})
        # Fail closed on pagination overflow: never advance and silently lose events.
        if result.get('metadata',{}).get('total',0)>100:
            raise ConnectionError('page_overflow')
        return result.get('data',[])

    def send(self, event_id, text):
        return self.request('message/text', {'chatGuid':self.config['chat_guid'],
            'tempGuid':'imessage-bridge-'+uuid.uuid5(uuid.NAMESPACE_URL,event_id).hex,
            'message':text,'method':'apple-script'})


def incoming_text(message, now_ms, start_ms):
    if message.get('isFromMe') not in (False,0):
        return None
    if any(message.get(k) not in (None,0,'') for k in ['itemType','groupActionType','associatedMessageType']):
        return None
    if message.get('associatedMessageGuid') or message.get('balloonBundleId') or message.get('hasPayloadData') or message.get('attachments'):
        return None
    date = message.get('dateCreated')
    text = message.get('text')
    if not isinstance(date,(int,float)) or not start_ms <= date <= now_ms or now_ms-date > MAX_AGE*1000:
        return None
    if not isinstance(text,str) or not text.strip() or len(text)>4000 or '\ufffc' in text:
        return None
    if not isinstance(message.get('guid'),str) or not message['guid']:
        return None
    return text


def incoming_images(message, now_ms, start_ms):
    if message.get('isFromMe') is not False or not isinstance(message.get('guid'),str):return []
    if any(message.get(k) not in (None,0,'') for k in ['itemType','groupActionType','associatedMessageType']):return []
    if message.get('associatedMessageGuid') or message.get('balloonBundleId') or message.get('hasPayloadData'):return []
    created=message.get('dateCreated')
    if not isinstance(created,(int,float)) or not start_ms<=created<=now_ms or now_ms-created>MAX_AGE*1000:return []
    attachments=message.get('attachments',[])
    if not isinstance(attachments,list) or len(attachments)>10:return []
    allowed=[]
    for attachment in attachments:
        if not isinstance(attachment,dict) or attachment.get('isSticker') or attachment.get('isOutgoing'):continue
        if attachment.get('mimeType') not in ['image/jpeg','image/png','image/heic','image/heif']:continue
        if not isinstance(attachment.get('guid'),str) or not attachment['guid']:continue
        size=attachment.get('totalBytes')
        if isinstance(size,(int,float)) and size>MAX_BYTES:continue
        allowed.append(attachment)
    return allowed



def native_audio_payload_allowed(message):
    # A native audio flag alone must never unlock arbitrary app/red-packet payloads.
    if message.get('isAudioMessage') is not True or message.get('balloonBundleId'):return False
    attachments=message.get('attachments')
    if not isinstance(attachments,list) or len(attachments)!=1:return False
    a=attachments[0]
    if not isinstance(a,dict) or a.get('isSticker') or a.get('isOutgoing'):return False
    if not isinstance(a.get('guid'),str) or not a['guid']:return False
    size=a.get('totalBytes')
    if type(size) is not int or not 0<size<=MAX_BYTES:return False
    name,mime=attachment_metadata(message,a)
    return mime in AUDIO_MIME_PAIRS.get(Path(name).suffix.lower(),set())


def incoming_attachments(message,now_ms,start_ms):
    # Called only on BlueBubbles' exact locally allowlisted chat query.
    if message.get('isFromMe') is not False or not isinstance(message.get('guid'),str) or not message['guid']:return []
    if any(message.get(k) not in (None,0,'') for k in ['itemType','groupActionType','associatedMessageType']):return []
    if message.get('associatedMessageGuid') or message.get('balloonBundleId'):return []
    if message.get('hasPayloadData') and not native_audio_payload_allowed(message):return []
    created=message.get('dateCreated')
    if not isinstance(created,(int,float)) or not start_ms<=created<=now_ms or now_ms-created>MAX_AGE*1000:return []
    attachments=message.get('attachments',[])
    if not isinstance(attachments,list) or len(attachments)>10:return []
    allowed=[]
    for a in attachments:
        if not isinstance(a,dict) or a.get('isSticker') or a.get('isOutgoing'):continue
        if safe_mime(a.get('mimeType')).startswith('image/'):continue
        if not isinstance(a.get('guid'),str) or not a['guid']:continue
        size=a.get('totalBytes')
        if isinstance(size,(int,float)) and not 0<size<=MAX_BYTES:continue
        allowed.append(a)
    return allowed


class Bridge:
    def __init__(self, store, config, remote=None, blue=None, clock=time.time, images=None, image_remote=None, attachments=None):
        self.store,self.config,self.clock=store,config,clock
        self.remote = remote or SSH(config)
        self.blue = blue or BlueBubbles(config)
        self.disconnected = False
        self.managed=config.get('managed_control') is True
        self.managed_disconnected=True
        self.images_checked=False
        self.image_floor_ms=None
        self.images=images
        self.image_remote=image_remote
        self.attachments=attachments
        # A newly opted-in worker always excludes pre-deployment attachments.
        self.attachment_floor_ms=int(clock()*1000)
        if config.get('attachments_enabled') is True and self.attachments is None:self.attachments=Attachments(config)
        if config.get('images_enabled') is True or self.managed or config.get('attachments_enabled') is True:
            if self.images is None:
                from image_api import Images
                self.images=Images(config)
            if self.image_remote is None:
                from image_transfer import ImageTransfer
                self.image_remote=ImageTransfer(config,cache=store.home/'image-tmp',clock=clock)

    def active(self, epoch):
        state = self.store.get()
        return bool(state['enabled'] and not state['pending_off'] and state['epoch']==str(epoch))

    def images_enabled(self):
        return bool(self.store.get()['images_enabled']) if self.managed else self.config.get('images_enabled') is True

    def image_active(self,epoch):
        return self.active(epoch) and self.images_enabled()

    def attachments_enabled(self):
        return self.config.get('attachments_enabled') is True and self.images_enabled()

    def attachment_active(self,epoch):
        return self.attachments_enabled() and self.active(epoch)

    def attachment_since(self):
        state=self.store.get()
        return max(state['start_ms'],state['image_start_ms'] if self.managed else 0,
                   self.image_floor_ms or 0,state['attachment_start_ms'],self.attachment_floor_ms)

    def off(self):
        # Local close commits before waiting for an in-flight request or SSH.
        self.store.set(enabled=0,pending_off=1,off_requested=1,epoch='',phase='closed_remote_sync_pending')
        with lock(self.store.home/'emission.lock'):
            try:
                reply=self.remote.call('off')
                if reply.get('enabled') is not False:
                    raise ConnectionError('off_not_confirmed')
                self.store.set(pending_off=0,off_requested=0,phase='closed')
                return True
            except Exception:
                return False

    def on(self):
        if not self.config.get('chat_guid') or self.config.get('validated_single_imessage_chat') is not True:
            raise ValueError('allowlist_not_validated')
        # Every new enable reconciles a prior off first, including failed remote closes.
        if not self.off():
            raise ConnectionError('off_sync_required')
        with lock(self.store.home/'emission.lock'):
            reply=self.remote.call('on')
            if reply.get('enabled') is not True or not reply.get('epoch') or reply.get('ready',True) is not True:
                self.store.set(pending_off=1,phase='enable_failed')
                raise ConnectionError('remote_not_ready')
            now=int(self.clock()*1000)
            self.store.set(enabled=1,pending_off=0,epoch=str(reply['epoch']),start_ms=now,cursor_ms=now,phase='enabled')

    def emit(self, direction, event_id, text, epoch, created=None):
        with lock(self.store.home/'emission.lock'):
            if created is not None and not 0<=self.clock()-created<=MAX_AGE:
                return
            if not self.active(epoch) or not self.store.intent(direction,event_id):
                return
            try:
                if not self.active(epoch):
                    self.store.finish(direction,event_id,'cancelled');return
                if direction=='to_wechat':
                    result=self.remote.call('submit',epoch=int(epoch),event_id=event_id,text=text)
                    if result.get('status') not in ('pending','duplicate') or result.get('error'):
                        raise ConnectionError('submit_rejected')
                else:
                    self.blue.send(event_id,text)
                self.store.finish(direction,event_id,'submitted')
            except Exception:
                self.store.finish(direction,event_id,'unknown')
                raise

    def image_upload(self, message, attachment, epoch):
        direction='image_to_wechat'
        event='image-'+hashlib.sha256((message['guid']+':'+attachment['guid']).encode()).hexdigest()
        if not self.image_active(epoch) or self.store.seen(direction,event):return
        # Read-only downloads may become available later during the overlap window.
        # Do not record a send intent until bytes exist; no outgoing operation here.
        try:data=self.images.download(message,attachment['guid'])
        except Exception:return
        with lock(self.store.home/'emission.lock'):
            created=message['dateCreated']/1000
            if not self.image_active(epoch) or not 0<=self.clock()-created<=MAX_AGE or not self.store.intent(direction,event):return
            try:
                self.image_remote.put(event,int(epoch),created,data,active=lambda:self.image_active(epoch))
                self.store.finish(direction,event,'submitted')
            except Exception:self.store.finish(direction,event,'unknown')

    def image_download(self, item, epoch):
        direction='image_to_imessage';event=item.get('id');created=item.get('created')
        if not isinstance(event,str) or not event or not isinstance(created,(int,float)) or not 0<=self.clock()-created<=MAX_AGE:return
        with lock(self.store.home/'emission.lock'):
            if not self.image_active(epoch) or not self.store.intent(direction,event):return
            try:
                data=self.image_remote.get(item,int(epoch),active=lambda:self.image_active(epoch))
                if not self.image_active(epoch) or not 0<=self.clock()-created<=MAX_AGE:
                    self.store.finish(direction,event,'cancelled');return
                self.images.send(event,data)
                self.store.finish(direction,event,'submitted')
            except Exception:self.store.finish(direction,event,'unknown')

    def attachment_upload(self,message,attachment,epoch):
        direction='attachment_to_wechat'
        event='attachment-'+hashlib.sha256((message['guid']+':'+attachment['guid']).encode()).hexdigest()
        created=message['dateCreated']/1000
        if not self.attachment_active(epoch) or not 0<=self.clock()-created<=MAX_AGE or created*1000<self.attachment_since() or self.store.seen(direction,event):return
        # Downloads are read-only and may be retried; a send intent is never retried.
        try:
            filename,mime=attachment_metadata(message,attachment)
            data=self.attachments.download(message,attachment['guid']);validate_file(data,mime,filename)
        except Exception:return
        with lock(self.store.home/'emission.lock'):
            if not self.attachment_active(epoch) or created*1000<self.attachment_since() or not 0<=self.clock()-created<=MAX_AGE or not self.store.intent(direction,event):return
            try:
                self.image_remote.put(event,int(epoch),created,data,active=lambda:self.attachment_active(epoch),filename=filename,mime=mime)
                self.store.finish(direction,event,'submitted')
            except Exception:self.store.finish(direction,event,'unknown')

    def attachment_download(self,item,epoch):
        direction='attachment_to_imessage';event=item.get('id');created=item.get('created')
        if not isinstance(event,str) or not event or not isinstance(created,(int,float)) or not 0<=self.clock()-created<=MAX_AGE or created*1000<self.attachment_since():return
        with lock(self.store.home/'emission.lock'):
            if not self.attachment_active(epoch) or not self.store.intent(direction,event):return
            try:
                data=self.image_remote.get(item,int(epoch),active=lambda:self.attachment_active(epoch))
                if not self.attachment_active(epoch) or created*1000<self.attachment_since() or not 0<=self.clock()-created<=MAX_AGE:
                    self.store.finish(direction,event,'cancelled');return
                # Never transcode, execute or inline-render received files.
                self.attachments.send(event,data,item['filename'],item['mime'])
                self.store.finish(direction,event,'submitted')
            except Exception:self.store.finish(direction,event,'unknown')

    def _reconcile_managed_close(self):
        # Only an explicit, unconfirmed desktop close may call legacy off here.
        # A remote desired=false snapshot never issues another off/on RPC.
        with lock(self.store.home/'emission.lock'):
            if not self.store.get()['off_requested']:return True
            try:
                reply=self.remote.call('off')
                if reply.get('enabled') is not False:return False
                self.store.set(enabled=0,pending_off=0,off_requested=0,phase='closed')
                return True
            except Exception:return False

    def managed_sync(self):
        if self.store.get()['off_requested'] and not self._reconcile_managed_close():
            self.managed_disconnected=True
            return None
        with lock(self.store.home/'emission.lock'):
            try:
                reply=self.remote.call('control_sync')
                control=reply.get('control')
                if not isinstance(control,dict):raise ValueError('invalid_control')
                revision=control.get('revision');epoch=reply.get('epoch')
                if type(revision) is not int or revision<0:
                    raise ValueError('invalid_revision')
                if type(epoch) is not int or epoch<0:raise ValueError('invalid_epoch')
                if any(type(control.get(k)) is not bool for k in ['enabled','images_enabled']):raise ValueError('invalid_control_flags')
                if any(type(reply.get(k)) is not bool for k in ['enabled','ready','images_ready']):raise ValueError('invalid_remote_flags')
                if not control['enabled'] and reply['enabled']:raise ValueError('control_not_applied_on_NAS')
                old=self.store.get()
                if old['off_requested']:return None  # Desktop close superseded this network snapshot.
                now=int(self.clock()*1000)
                enabled=control['enabled'] and reply['enabled'] and reply['ready']
                phase='enabled' if enabled else 'closed' if not control['enabled'] else 'paused_backend'
                if enabled and self.config.get('validated_single_imessage_chat') is not True:
                    enabled=False;phase='enable_failed'
                changed_epoch=old['epoch']!=str(epoch)
                reset=enabled and (not old['enabled'] or changed_epoch or self.managed_disconnected or self.disconnected)
                changed_revision=old['control_revision']!=json.dumps(revision,separators=(',',':'))
                image_reset=control['images_enabled'] and (not old['images_enabled'] or changed_epoch or reset or changed_revision)
                values={'enabled':int(enabled),'pending_off':0,'epoch':str(epoch),'images_enabled':int(control['images_enabled']),
                        'control_revision':json.dumps(revision,separators=(',',':')),'control_synced_at':self.clock(),'phase':phase}
                if reset:values.update(start_ms=now,cursor_ms=now)
                if image_reset:
                    values['image_start_ms']=now;self.image_floor_ms=now
                    self.attachment_floor_ms=now;values['attachment_start_ms']=now
                if not control['images_enabled']:
                    self.image_floor_ms=None;self.images_checked=True
                self.store.set(**values)
                actual=self.store.get()
                if actual['off_requested']:return None
                ack=self.remote.call('control_ack',revision=revision,epoch=epoch,enabled=bool(actual['enabled']),
                                     images_enabled=bool(actual['images_enabled']),phase=actual['phase'])
                if not isinstance(ack,dict) or ack.get('acknowledged') is not True:raise ConnectionError('control_ack_unconfirmed')
                if ('revision' in ack and (type(ack['revision']) is not type(revision) or ack['revision']!=revision)) or ('epoch' in ack and ack['epoch']!=epoch):
                    raise ConnectionError('stale_control_ack')
                current=self.store.get()
                if current['off_requested']:return None
                self.store.set(control_acked_at=self.clock())
                self.managed_disconnected=False;self.disconnected=False
                return reply
            except Exception:
                self.managed_disconnected=True;self.disconnected=True
                # Fence forwarding while offline, without changing the NAS desired state.
                if not self.store.get()['off_requested']:
                    self.store.set(enabled=0,phase='paused_connection')
                return None

    def tick(self):
        if not self.managed:return self.legacy_tick()
        remote=self.managed_sync()
        if remote is not None and self.store.get()['enabled']:
            self.legacy_tick(remote)
        return True  # Manual managed worker stays alive while closed/offline.

    def legacy_tick(self,remote_snapshot=None):
        state=self.store.get()
        if not state['enabled']:
            if state['pending_off']:
                with lock(self.store.home/'emission.lock'):
                    fresh=self.store.get()
                    if not fresh['enabled'] and fresh['pending_off']:
                        try:
                            reply=self.remote.call('off')
                            if reply.get('enabled') is False:self.store.set(pending_off=0,phase='closed')
                        except Exception:pass
            return bool(self.store.get()['enabled'])
        epoch=state['epoch']
        try:
            remote=self.remote.call('status') if remote_snapshot is None else remote_snapshot
            if not remote.get('enabled') or str(remote.get('epoch'))!=epoch:
                if self.active(epoch):self.store.set(enabled=0,pending_off=1,phase='remote_mismatch')
                return bool(self.store.get()['enabled'])
            if not remote.get('ready',True):
                self.disconnected=True
                if self.active(epoch):self.store.set(phase='paused_backend')
                return True
            now=int(self.clock()*1000)
            if self.disconnected:
                self.store.set(start_ms=now,cursor_ms=now);self.disconnected=False
            images_ready=self.images_enabled() and remote.get('images_ready') is True
            if images_ready and self.image_floor_ms is None:
                self.image_floor_ms=now if self.images_checked else self.store.get()['start_ms']
            if not images_ready:self.image_floor_ms=None
            self.images_checked=True
            attachments_ready=images_ready and self.attachments_enabled()
            options={'images':True} if images_ready else {}
            if attachments_ready:options['attachments']=True
            reply=self.remote.call('poll',**options)
            polled_at=self.clock()
            if str(reply.get('epoch'))!=epoch or not reply.get('enabled'):
                raise ConnectionError('poll_epoch_changed')
            item=reply.get('message')
            if item and item.get('kind')=='image':
                image_floor=max(self.store.get()['start_ms'],self.store.get()['image_start_ms'] if self.managed else 0,self.image_floor_ms or now)
                if images_ready and isinstance(item.get('created'),(int,float)) and item['created']*1000>=image_floor:self.image_download(item,epoch)
                item=None
            if item and item.get('kind')=='attachment':
                if attachments_ready:self.attachment_download(item,epoch)
                item=None
            if item:
                text=item.get('text');identifier=item.get('id')
                created=item.get('created')
                fresh=False
                if isinstance(created,(int,float)):
                    fresh=0<=(self.clock()-created if created<10**12 else self.clock()-created/1000)<=MAX_AGE
                    if self.managed:fresh=fresh and (created*1000 if created<10**12 else created)>=self.store.get()['start_ms']
                if isinstance(text,str) and text.strip() and len(text)<=4000 and isinstance(identifier,str) and fresh and self.clock()-polled_at<=MAX_AGE:
                    self.emit('to_imessage',identifier,text,epoch,created=created if created<10**12 else created/1000)
            # Timestamp order is not insertion order: iMessage may arrive late.
            # Re-read a bounded window; persistent GUID intents prevent duplicates.
            current=self.store.get()
            cursor=max(current['start_ms'],now-MAX_AGE*1000)
            for message in self.blue.messages(cursor,now):
                text=incoming_text(message,now,current['start_ms'])
                if text is not None:
                    self.emit('to_wechat',message['guid'],text,epoch,created=message['dateCreated']/1000)
                if images_ready:
                    for attachment in incoming_images(message,now,max(current['start_ms'],current['image_start_ms'] if self.managed else 0,self.image_floor_ms or now)):
                        self.image_upload(message,attachment,epoch)
                if attachments_ready:
                    for attachment in incoming_attachments(message,now,self.attachment_since()):
                        self.attachment_upload(message,attachment,epoch)
            if self.active(epoch):self.store.set(cursor_ms=now,phase='enabled')
            return True
        except Exception:
            self.disconnected=True
            # No local message queue: skip the disconnected interval on recovery.
            if self.active(epoch):self.store.set(cursor_ms=int(self.clock()*1000),phase='paused_connection')
            return True

    def run(self,preserve_text_state=False):
        # A previous worker may be releasing its lock during an off/on transition.
        deadline=time.monotonic()+5
        guard=None
        while guard is None:
            candidate=lock(self.store.home/'worker.lock',nonblocking=True)
            try:
                candidate.__enter__();guard=candidate
            except BlockingIOError:
                if time.monotonic()>=deadline:return
                time.sleep(0.1)
        try:
            self.store.recover()
            now=int(self.clock()*1000)
            self.attachment_floor_ms=now
            self.store.set(attachment_start_ms=now)
            if preserve_text_state:
                current=self.store.get()
                if not self.managed or not current['enabled'] or current['pending_off']:
                    raise ValueError('cannot_preserve_inactive_worker')
                # Explicit upgrade-only invocation; first NAS sync still validates epoch/readiness.
                self.managed_disconnected=False
            elif self.managed:
                self.store.set(enabled=0,images_enabled=0,start_ms=now,cursor_ms=now,image_start_ms=now,phase='paused_connection')
            elif self.store.get()['enabled']:
                self.store.set(start_ms=now,cursor_ms=now,phase='enabled')
            delay=5
            while self.tick():
                delay=min(30,max(15,delay*2)) if self.disconnected else 15 if self.managed and not self.store.get()['enabled'] else 5
                time.sleep(delay)
        finally:
            guard.__exit__(None,None,None)


def config_for(home):
    path=home/'config.json'
    if not path.is_file():raise ValueError('not_configured')
    if path.stat().st_mode & 0o077:raise ValueError('insecure_config_permissions')
    cfg=json.loads(path.read_text())
    if not isinstance(cfg.get('chat_guid'),str) or not cfg['chat_guid']:
        raise ValueError('missing_allowlist')
    return cfg


def launch(home):
    # Avoid even a temporary duplicate process when the manual worker is alive.
    try:
        with lock(home/'worker.lock',nonblocking=True):pass
    except BlockingIOError:return
    # Worker takes its own OS lock; duplicate launcher races cannot duplicate work.
    subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--home',str(home),'worker'],
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--home',type=Path,default=DEFAULT_HOME)
    parser.add_argument('--preserve-text-state',action='store_true')
    parser.add_argument('operation',choices=['on','off','status','worker'])
    args=parser.parse_args();os.umask(0o077)
    if args.preserve_text_state and args.operation!='worker':parser.error('--preserve-text-state is worker-only')
    store=Store(args.home)
    try:
        cfg=config_for(args.home)
        bridge=Bridge(store,cfg)
        if args.operation=='worker':
            bridge.run(preserve_text_state=args.preserve_text_state);return
        if args.operation=='status':
            local=store.get()
            print('本地：'+('开启' if local['enabled'] else '关闭'))
            print('远端关闭待同步：'+('是' if local['pending_off'] else '否'))
            print('运行状态：'+local['phase'])
            print('图片功能：'+('已开启' if bridge.images_enabled() else '关闭'))
            print('文件视频接入：'+('开启' if bridge.attachments_enabled() else '关闭'))
            print('托管控制：'+('开启' if bridge.managed else '关闭'))
            try:
                with lock(args.home/'worker.lock',nonblocking=True):
                    print('转发进程：未运行')
            except BlockingIOError:print('转发进程：运行中')
            try:
                remote=bridge.remote.call('status')
                print('NAS：'+('开启' if remote.get('enabled') else '关闭'))
                print('NAS就绪：'+('是' if remote.get('ready') else '否'))
                print('NAS图片就绪：'+('是' if remote.get('images_ready') else '否'))
            except Exception:print('NAS：暂时无法连接')
            return
        with lock(args.home/'control.lock'):
            if args.operation=='off':
                print('本地已关闭。'+('NAS已关闭。' if bridge.off() else 'NAS关闭待同步；重开前会先完成关闭。'))
                if bridge.managed:launch(args.home)
            else:
                bridge.on();launch(args.home);print('已提交开启，等待托管同步确认。' if bridge.managed else '已开启独立文字桥接。')
    except BlockingIOError:
        return
    except BaseException:
        if args.operation=='off':store.set(enabled=0,pending_off=1,off_requested=1,epoch='',phase='closed_remote_sync_pending')
        if args.operation in ['on','off'] and locals().get('cfg',{}).get('managed_control') is True:launch(args.home)
        if args.operation!='worker':
            print('桥接保持关闭或暂停；配置、白名单或连接尚未就绪。')
        raise SystemExit(1) from None


if __name__=='__main__':main()
