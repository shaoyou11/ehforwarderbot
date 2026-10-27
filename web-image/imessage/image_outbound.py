"""Short-lived, exact-event image grants for the fixed Telegram binding."""
import base64
import contextlib
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from image_store import MAX_BYTES
from bridge_control import receive_media_allowed
from media_types import filename as safe_filename, valid_mime, validate

class Outbound:
    def __init__(self,config,clock=time.time,fetch=None):
        self.cfg=config;self.clock=clock;self.fetch=fetch or self.fetch_file
        self.path=Path(config['state_dir'])/'outbound-images.sqlite'
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS grants(id TEXT PRIMARY KEY,epoch INTEGER,created REAL,mime TEXT,digest TEXT,size INTEGER,data BLOB,status TEXT)')
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS media_names(id TEXT PRIMARY KEY,filename TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS grants_active ON grants(status,created)')
        self.path.chmod(0o600)
    @contextlib.contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=.5)
        try:
            with db:yield db
        finally:db.close()
    def cancel(self):
        with self.db() as db:db.execute("UPDATE grants SET status='cancelled',data=NULL WHERE status='ready'")
    def grant(self,key,epoch,file_id,created,filename=None,mime=None,voice=False):
        if not receive_media_allowed(self.cfg,created):raise ValueError('receive_media_disabled')
        if not 0<=self.clock()-created<=120:raise ValueError('image_expired')
        with self.db() as db:
            db.execute("UPDATE grants SET status='expired',data=NULL WHERE status='ready' AND created<?",(self.clock()-120,))
            if db.execute('SELECT 1 FROM grants WHERE id=?',(key,)).fetchone():raise ValueError('image_already_claimed')
            if db.execute("SELECT count(*) FROM grants WHERE status='ready'").fetchone()[0]>=5:raise ValueError('image_queue_full')
            # Keep claimed IDs; reaching a historical count must not stop new attachments.
        data=self.fetch(file_id)
        if not 0<len(data)<=MAX_BYTES:raise ValueError('image_size_invalid')
        if voice:
            from audio_codec import convert_in_runtime
            data=convert_in_runtime(data);mime='audio/mp4';filename='语音.m4a'
        attachment=filename is not None
        if attachment:
            mime=mime if valid_mime(mime) else 'application/octet-stream'
            validate(data,mime)
            filename=safe_filename(filename)
        else:mime='image/png' if data.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if data.startswith(b'\xff\xd8\xff') else None
        if not mime:raise ValueError('unsupported_image')
        if not receive_media_allowed(self.cfg,created):raise ValueError('receive_media_disabled')
        digest=hashlib.sha256(data).hexdigest()
        with self.db() as db:db.execute('INSERT INTO grants VALUES (?,?,?,?,?,?,?,?)',(key,epoch,created,mime,digest,len(data),data,'ready'))
        if attachment:
            with self.db() as db:db.execute('INSERT INTO media_names VALUES (?,?)',(key,filename))
        return dict({'id':key,'kind':'attachment' if attachment else 'image','image_id':key,'mime':mime,'size':len(data),'digest':digest,'created':created},**({'filename':filename} if attachment else {}))
    def take(self,key,epoch):
        if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('invalid_image_id')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT created,mime,digest,size,data FROM grants WHERE id=? AND epoch=? AND status='ready'",(key,epoch)).fetchone()
            if not row or not 0<=self.clock()-row[0]<=120:raise ValueError('image_not_available')
            if not receive_media_allowed(self.cfg,row[0]):raise ValueError('receive_media_disabled')
            db.execute("UPDATE grants SET status='taken',data=NULL WHERE id=?",(key,))
        with self.db() as db:name=db.execute('SELECT filename FROM media_names WHERE id=?',(key,)).fetchone()
        metadata=dict(zip(['created','mime','digest','size'],row[:4]))
        if name:metadata['filename']=name[0]
        return metadata,row[4]
    def fetch_file(self,file_id):
        import requests,yaml
        cfg=yaml.safe_load(Path(self.cfg['telegram_config']).read_text())
        response=requests.post(cfg['flags']['api_base_url']+cfg['token']+'/getFile',json={'file_id':file_id},timeout=(3,8),allow_redirects=False)
        response.raise_for_status();result=response.json()
        if result.get('ok') is not True:raise ValueError('telegram_file_unavailable')
        remote=Path(result['result']['file_path']);prefix=Path('/var/lib/telegram-bot-api')
        relative=remote.relative_to(prefix)
        root=Path(self.cfg['telegram_cache_root']).resolve();path=(root/relative).resolve()
        if not path.is_relative_to(root) or not path.is_file() or not 0<path.stat().st_size<=MAX_BYTES:raise ValueError('invalid_cache_file')
        with open(path,'rb') as f:return f.read(MAX_BYTES+1)
