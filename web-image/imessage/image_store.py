"""Bounded image staging for the independent bridge; caller checks route and epoch."""
import base64
import contextlib
import hashlib
from media_types import filename as safe_filename, valid_mime
from pathlib import Path
import re
import sqlite3
import time

from media_types import MAX_BYTES
CHUNK_BYTES = 8192
MAX_PENDING = 5


class ImageStore:
    def __init__(self, root, clock=time.time):
        self.root = Path(root); self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.root/'images.sqlite'; self.clock = clock
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS images (
                id TEXT PRIMARY KEY, epoch INTEGER, size INTEGER, digest TEXT,
                mime TEXT, created REAL, expires REAL, status TEXT);
                CREATE TABLE IF NOT EXISTS chunks (
                id TEXT, offset INTEGER, data BLOB, PRIMARY KEY(id,offset));''')
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS media_names(id TEXT PRIMARY KEY,filename TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS images_active ON images(status,expires)')
        self.path.chmod(0o660)

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=.5)
        try:
            with db: yield db
        finally: db.close()

    def expire(self, db):
        db.execute("UPDATE images SET status='expired' WHERE status IN ('uploading','pending') AND expires<=?", (self.clock(),))
        db.execute("DELETE FROM chunks WHERE EXISTS (SELECT 1 FROM images WHERE images.id=chunks.id AND images.status NOT IN ('uploading','pending'))")

    def begin(self, event_id, epoch, size, digest, mime, filename=None):
        if (not isinstance(event_id,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',event_id)
            or type(epoch) is not int or epoch<0 or type(size) is not int or not 0<size<=MAX_BYTES
            or not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest)
            or not valid_mime(mime) or (mime not in ('image/jpeg','image/png') and not filename)):
            raise ValueError('image_metadata_invalid')
        key=hashlib.sha256(event_id.encode()).hexdigest()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE'); self.expire(db)
            old=db.execute('SELECT epoch,size,digest,mime,status FROM images WHERE id=?',(key,)).fetchone()
            if old:
                if old[:4]!=(epoch,size,digest,mime): raise ValueError('image_id_conflict')
                name=db.execute('SELECT filename FROM media_names WHERE id=?',(key,)).fetchone()
                if (name[0] if name else None)!=(safe_filename(filename) if filename else None):raise ValueError('image_id_conflict')
                return {'id':key,'status':old[4],'duplicate':True}
            if db.execute("SELECT count(*) FROM images WHERE status IN ('uploading','pending','intent')").fetchone()[0]>=MAX_PENDING:
                raise ValueError('image_queue_full')
            # Historical IDs remain for deduplication; only active payloads are bounded.
            now=self.clock()
            db.execute('INSERT INTO images VALUES (?,?,?,?,?,?,?,?)',(key,epoch,size,digest,mime,now,now+120,'uploading'))
            if filename:db.execute('INSERT INTO media_names VALUES (?,?)',(key,safe_filename(filename)))
        return {'id':key,'status':'uploading','duplicate':False}

    def append(self, key, epoch, offset, encoded):
        if type(offset) is not int or offset<0 or offset%CHUNK_BYTES or not isinstance(encoded,str) or len(encoded)>10924:
            raise ValueError('invalid_chunk')
        data=base64.b64decode(encoded,validate=True)
        if not 0<len(data)<=CHUNK_BYTES: raise ValueError('invalid_chunk')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE'); self.expire(db)
            row=db.execute('SELECT epoch,size,status FROM images WHERE id=?',(key,)).fetchone()
            if not row or row[0]!=epoch or row[2]!='uploading': raise ValueError('image_not_uploading')
            if offset+len(data)>row[1] or len(data)!=min(CHUNK_BYTES,row[1]-offset):raise ValueError('invalid_chunk_length')
            old=db.execute('SELECT data FROM chunks WHERE id=? AND offset=?',(key,offset)).fetchone()
            if old and old[0]!=data: raise ValueError('chunk_conflict')
            db.execute('INSERT OR IGNORE INTO chunks VALUES (?,?,?)',(key,offset,data))
        return {'received':len(data)}

    def commit(self,key,epoch):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE'); self.expire(db)
            row=db.execute('SELECT epoch,size,digest,mime,status FROM images WHERE id=?',(key,)).fetchone()
            if not row or row[0]!=epoch:raise ValueError('invalid_image_epoch')
            if row[4]!='uploading':return {'status':row[4],'duplicate':True}
            data=b''.join(r[0] for r in db.execute('SELECT data FROM chunks WHERE id=? ORDER BY offset',(key,)))
            if len(data)!=row[1] or hashlib.sha256(data).hexdigest()!=row[2]:raise ValueError('image_incomplete')
            signature=b'\x89PNG\r\n\x1a\n' if row[3]=='image/png' else b'\xff\xd8\xff'
            if row[3] in ('image/png','image/jpeg') and not data.startswith(signature):raise ValueError('image_format_mismatch')
            db.execute("UPDATE images SET status='pending' WHERE id=?",(key,))
        return {'status':'pending','duplicate':False}

    def cancel(self):
        with self.db() as db:
            db.execute("UPDATE images SET status='cancelled' WHERE status IN ('uploading','pending')")
            self.expire(db)

    def recover(self):
        with self.db() as db:
            db.execute("UPDATE images SET status='unknown' WHERE status='intent'")
        self.cancel()

    def claim(self,epoch):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');self.expire(db)
            db.execute("UPDATE images SET status='cancelled' WHERE epoch<>? AND status IN ('uploading','pending')",(epoch,))
            self.expire(db)
            row=db.execute("SELECT id,mime,expires FROM images WHERE epoch=? AND status='pending' ORDER BY created LIMIT 1",(epoch,)).fetchone()
            if not row:return None
            data=b''.join(r[0] for r in db.execute('SELECT data FROM chunks WHERE id=? ORDER BY offset',(row[0],)))
            db.execute("UPDATE images SET status='intent' WHERE id=?",(row[0],))
            db.execute('DELETE FROM chunks WHERE id=?',(row[0],))
        with self.db() as db:name=db.execute('SELECT filename FROM media_names WHERE id=?',(row[0],)).fetchone()
        return dict({'id':row[0],'mime':row[1],'data':data,'expires':row[2]},**({'filename':name[0]} if name else {}))

    def finish(self,key,status):
        if status not in ('sent','unknown','blocked'):raise ValueError('invalid_outcome')
        with self.db() as db:db.execute("UPDATE images SET status=? WHERE id=? AND status='intent'",(status,key))
