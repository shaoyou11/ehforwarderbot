"""Build a small local identity index outside the message delivery path."""
import sqlite3
import time
from pathlib import Path
from protocol import atomic_json
from topic_guard import normalized


def export_catalog(config, mappings):
    path = Path(config['profiles']['comwechat']) / 'blueset.telegram/tgdata.db'
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10) as db:
        db.execute('BEGIN')
        records = {}
        for uid, dest in db.execute('SELECT slave_uid,master_uid FROM chatassoc'):
            records.setdefault(uid, {'chats': [], 'topics': []})['chats'].append(dest)
        for uid, dest, thread in db.execute('SELECT slave_uid,topic_chat_id,message_thread_id FROM topicassoc'):
            records.setdefault(uid, {'chats': [], 'topics': []})['topics'].append([str(dest), str(thread)])
        names = {}
        for channel, uid, name, alias in db.execute('SELECT slave_channel_id,slave_chat_uid,slave_chat_name,slave_chat_alias FROM slavechatinfo WHERE slave_chat_group_id IS NULL'):
            identity = channel + ' ' + uid
            if identity not in records:
                continue
            records[identity]['name'] = alias or name or identity
            for value in (name, alias):
                key = normalized(value)
                if key:
                    names.setdefault(key, set()).add(identity)
    confirmed = {m['target']: {**records.get(m['source'], {'chats': [], 'topics': []}), 'source': m['source']}
                 for m in mappings if m.get('confirmed') is not False}
    atomic_json(Path(config['control_root']) / 'topic-guard-catalog.json',
                {'version': 1, 'updated': time.time(), 'names': {k: sorted(v) for k, v in names.items()}, 'confirmed': confirmed, 'records': records})
