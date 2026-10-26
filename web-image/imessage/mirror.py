"""At-most-once Telegram copies of confirmed external WeChat sends."""
import json
from pathlib import Path
import sqlite3
import time


class Mirror:
    def __init__(self, config, clock=time.time, send=None):
        self.config, self.clock = config, clock
        self.path=Path(config['state_dir'])/'mirrors.sqlite'
        self.send=send or self.telegram_send
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS copies (
                event_id TEXT PRIMARY KEY, epoch INTEGER, text TEXT, created REAL,
                status TEXT, message_id TEXT DEFAULT '')''')
        self.path.chmod(0o600)

    def db(self):
        return sqlite3.connect(self.path,timeout=.2)

    def stage(self,event_id,epoch,text):
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO copies(event_id,epoch,text,created,status) VALUES(?,?,?,?,?)',
                       (event_id,epoch,text,self.clock(),'waiting'))

    def cancel(self):
        with self.db() as db:
            db.execute("UPDATE copies SET status='cancelled',text='' WHERE status='waiting'")

    def tick(self,store,epoch):
        with self.db() as db:
            db.execute("UPDATE copies SET status='expired',text='' WHERE status='waiting' AND (epoch<>? OR created<?)",
                       (epoch,self.clock()-300))
            rows=db.execute("SELECT event_id,text FROM copies WHERE status='waiting' ORDER BY created LIMIT 5").fetchall()
        for event_id,text in rows:
            with store.connect() as source:
                row=source.execute('SELECT status FROM events WHERE source=? AND event_id=?',
                                   (self.config['source'],event_id)).fetchone()
            if not row or row[0] in ('pending','intent'):continue
            if row[0]!='sent':
                with self.db() as db:db.execute("UPDATE copies SET status='cancelled',text='' WHERE event_id=?",(event_id,))
                continue
            # The caller holds the bridge lock and has checked binding and enable state.
            with self.db() as db:
                changed=db.execute("UPDATE copies SET status='intent',text='' WHERE event_id=? AND status='waiting'",(event_id,)).rowcount
            if not changed:continue
            status='unknown';message_id=''
            try:
                message_id=str(self.send(text+'（iMessage）'));status='sent'
            except Exception:
                pass  # An uncertain Bot API response must never trigger another copy.
            with self.db() as db:db.execute('UPDATE copies SET status=?,message_id=? WHERE event_id=?',(status,message_id,event_id))
            return

    def telegram_send(self,text):
        import requests
        import yaml
        cfg=yaml.safe_load(Path(self.config['telegram_config']).read_text())
        base=cfg['flags']['api_base_url']+cfg['token']
        response=requests.post(base+'/sendMessage',json={'chat_id':self.config['chat_id'],
            'text':text,'disable_notification':True,'link_preview_options':{'is_disabled':True}},timeout=(3,5))
        response.raise_for_status();result=response.json()
        if result.get('ok') is not True:raise RuntimeError('mirror_not_confirmed')
        return result['result']['message_id']
