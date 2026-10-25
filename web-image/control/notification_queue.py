"""Opt-in external text notifications. No Telegram polling or automatic retries."""
import hashlib
import hmac
import json
import re
import sqlite3
import time
from pathlib import Path

from protocol import read_json

MAX_PENDING = 100
MAX_HISTORY = 10000
MAX_TEXT = 4000
ID = re.compile(r'^[A-Za-z0-9_.:-]{1,128}$')

class Rejected(ValueError):
    def __init__(self, reason, status=409):
        self.reason, self.status = reason, status
        super().__init__(reason)


def binding(db_path, chat, thread=None):
    with sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro', uri=True, timeout=.2) as db:
        if thread:
            rows = db.execute('SELECT slave_uid FROM topicassoc WHERE topic_chat_id=? AND message_thread_id=?', (str(chat), str(thread))).fetchall()
        else:
            rows = db.execute('SELECT slave_uid FROM chatassoc WHERE master_uid=?', ('blueset.telegram '+str(chat),)).fetchall()
    values = sorted(set(r[0] for r in rows))
    if len(values) != 1 or not values[0].startswith('blueset.wechat '):
        raise Rejected('binding_not_unique')
    return values[0]


class Store:
    def __init__(self, root, db_path, clock=time.time):
        self.root, self.db_path, self.clock = Path(root), Path(db_path), clock
        self.path = self.root/'queue.sqlite'

    def connect(self):
        db = sqlite3.connect(self.path, timeout=.2)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        return db

    def initialize(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS events (
                source TEXT, event_id TEXT, digest TEXT, route TEXT, target TEXT,
                text TEXT, created REAL, expires REAL, revision TEXT,
                status TEXT, updated REAL, reason TEXT DEFAULT '',
                PRIMARY KEY(source,event_id))''')
        self.path.chmod(0o600)

    def policy(self):
        return read_json(self.root/'policy.json')

    def route(self, policy, name, source):
        if policy.get('enabled') is not True:
            raise Rejected('disabled', 503)
        route = policy.get('routes', {}).get(name, {})
        if route.get('enabled') is not True or source not in route.get('sources', []):
            raise Rejected('route_not_allowed', 403)
        return route

    def submit(self, data, token):
        policy = self.policy()
        source = data.get('source', '')
        if not isinstance(source,str) or not ID.fullmatch(source):raise Rejected('invalid_source',400)
        expected = read_json(self.root/'sources.json').get(source, {}).get('token', '')
        if not expected or not hmac.compare_digest(str(expected), str(token)):
            raise Rejected('unauthorized', 401)
        # Explicit external origin; never accept Telegram updates or EFB objects.
        if set(data)-{'source','event_id','route','text','ttl','origin'} or data.get('origin') != 'external':
            raise Rejected('invalid_payload', 400)
        event_id, text, name = data.get('event_id',''), data.get('text',''), data.get('route','')
        if not isinstance(event_id,str) or not ID.fullmatch(event_id) or not isinstance(text,str) or not text.strip() or len(text)>MAX_TEXT:
            raise Rejected('invalid_payload', 400)
        ttl = data.get('ttl', 120)
        if not isinstance(ttl,int) or isinstance(ttl,bool) or not 1<=ttl<=300:
            raise Rejected('invalid_ttl', 400)
        if not isinstance(name,str):raise Rejected('invalid_route',400)
        route = self.route(policy, name, source)
        target = binding(self.db_path, route['chat_id'], route.get('thread_id'))
        if target != route.get('target'):
            raise Rejected('binding_changed')
        digest = hashlib.sha256(json.dumps([name,text,ttl],ensure_ascii=False).encode()).hexdigest()
        now = self.clock()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT digest,status FROM events WHERE source=? AND event_id=?', (source,event_id)).fetchone()
            if old:
                if old['digest'] != digest:raise Rejected('event_id_conflict')
                return {'status':old['status'],'duplicate':True}
            db.execute("UPDATE events SET status='expired',text='',updated=? WHERE status='pending' AND expires<=?", (now,now))
            if db.execute("SELECT count(*) FROM events WHERE status IN ('pending','intent')").fetchone()[0]>=MAX_PENDING:
                raise Rejected('queue_full',429)
            if db.execute('SELECT count(*) FROM events').fetchone()[0]>=MAX_HISTORY:
                raise Rejected('history_full',507)
            db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       (source,event_id,digest,name,target,text,now,now+ttl,str(policy.get('revision','')), 'pending',now,''))
        return {'status':'pending','duplicate':False}

    def finish(self, event, status, reason=''):
        with self.connect() as db:
            db.execute("UPDATE events SET status=?,reason=?,text='',updated=? WHERE source=? AND event_id=? AND status IN ('pending','intent')", (status,reason,self.clock(),event['source'],event['event_id']))

    def recover(self):
        # A crash after send but before receipt must never cause a resend.
        with self.connect() as db:
            db.execute("UPDATE events SET status='unknown',reason='process_restarted',text='',updated=? WHERE status='intent'",(self.clock(),))
            db.execute("UPDATE events SET status='cancelled',reason='runtime_restarted',text='',updated=? WHERE status='pending'",(self.clock(),))

    def claim(self, idle, backend_ready):
        now=self.clock();policy=self.policy()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE events SET status='expired',text='',updated=? WHERE status='pending' AND expires<=?",(now,now))
            if not policy.get('enabled'):
                db.execute("UPDATE events SET status='cancelled',text='',reason='disabled',updated=? WHERE status='pending'",(now,));return None
            db.execute("UPDATE events SET status='cancelled',text='',reason='policy_changed',updated=? WHERE status='pending' AND revision<>?",(now,str(policy.get('revision',''))))
            if not backend_ready or not idle:return None
            # Global rate cap, including unknown outcomes and across restarts.
            if db.execute("SELECT 1 FROM events WHERE status IN ('intent','sent','unknown') AND updated>? LIMIT 1",(now-10,)).fetchone():return None
            candidates=db.execute("SELECT * FROM events WHERE status='pending' ORDER BY created LIMIT 100").fetchall()
            for row in candidates:
                event=dict(row)
                try:
                    route=self.route(policy,event['route'],event['source'])
                    if binding(self.db_path,route['chat_id'],route.get('thread_id'))!=event['target'] or route.get('target')!=event['target']:raise Rejected('binding_changed')
                except (Rejected,KeyError):
                    db.execute("UPDATE events SET status='blocked',text='',reason='binding_or_policy_changed',updated=? WHERE source=? AND event_id=?",(now,event['source'],event['event_id']));continue
                if db.execute("SELECT 1 FROM events WHERE route=? AND status IN ('intent','sent','unknown') AND updated>? LIMIT 1",(event['route'],now-30)).fetchone():continue
                db.execute("UPDATE events SET status='intent',updated=? WHERE source=? AND event_id=?",(now,event['source'],event['event_id']))
                return event
        return None

    def counts(self):
        with self.connect() as db:return dict(db.execute('SELECT status,count(*) FROM events GROUP BY status').fetchall())
