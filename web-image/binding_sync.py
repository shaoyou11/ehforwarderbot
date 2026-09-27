"""Read-only binding export and offline, explicitly mapped web snapshots."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3

TABLES = ('chatassoc', 'topicassoc', 'msglog', 'slavechatinfo', 'imagefingerprint')


def export_bindings(path):
    con = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    try:
        con.execute('BEGIN')
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        schema = [r[0] for r in con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name IN (?,?,?,?,?)", TABLES)]
        schema += [r[0] for r in con.execute("SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL AND tbl_name IN (?,?,?,?,?)", TABLES)]
        chat = [dict(r) for r in con.execute('SELECT master_uid,slave_uid FROM chatassoc')] if 'chatassoc' in tables else []
        topic = [dict(r) for r in con.execute('SELECT topic_chat_id,message_thread_id,slave_uid FROM topicassoc')] if 'topicassoc' in tables else []
        catalog = []
        if 'slavechatinfo' in tables:
            catalog = [dict(r) for r in con.execute('SELECT slave_channel_id,slave_chat_uid,slave_chat_name,slave_chat_alias,slave_chat_type FROM slavechatinfo WHERE slave_chat_group_id IS NULL')]
        result = {'version':1, 'chat':chat, 'topic':topic, 'catalog':catalog, 'schema':schema}
        result['fingerprint'] = hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()
        return result
    finally:
        con.close()


def make_plan(snapshot, mapping, web_catalog):
    known = {c['uid'] for c in web_catalog}
    conversions, used = {}, set()
    for row in mapping:
        if row.get('confirmed') is not True:
            continue
        source, target = row['source'], row['target']
        if not source.startswith('honus.comwechat ') or not target.startswith('blueset.wechat '):
            raise ValueError('mapping must go from ComWechat to Web WeChat')
        if target.split(' ',1)[1] not in known:
            raise ValueError('target has not been observed in the web catalog')
        if source in conversions or target in used:
            raise ValueError('duplicate or many-to-one identity mapping')
        conversions[source] = target; used.add(target)
    result = {'chat':[], 'topic':[], 'pending':[], 'source_fingerprint':snapshot['fingerprint']}
    for kind in ('chat','topic'):
        for row in snapshot[kind]:
            if row['slave_uid'] not in conversions:
                result['pending'].append({'kind':kind, **row}); continue
            result[kind].append({**row, 'slave_uid':conversions[row['slave_uid']]})
    return result


def stage_database(snapshot, plan, destination):
    """Build a fresh candidate only. Never replace an active binding database."""
    p = Path(destination)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    con = sqlite3.connect(p)
    try:
        with con:
            for sql in snapshot['schema']:
                con.execute(sql)
            for row in plan['chat']:
                con.execute('INSERT INTO chatassoc(master_uid,slave_uid) VALUES (?,?)', (row['master_uid'],row['slave_uid']))
            for row in plan['topic']:
                con.execute('INSERT INTO topicassoc(topic_chat_id,message_thread_id,slave_uid) VALUES (?,?,?)', (row['topic_chat_id'],row['message_thread_id'],row['slave_uid']))
        if con.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('candidate database validation failed')
    finally:
        con.close()


def write_private(path, data):
    with open(path, 'x', encoding='utf-8') as f:
        os.chmod(path, 0o600)
        json.dump(data, f, ensure_ascii=False, indent=2)


def apply_web_plan(plan, destination, backup):
    """Merge confirmed bindings into web only; preserve history and unrelated rows."""
    destination = Path(destination).resolve()
    if destination.parent.name != 'blueset.telegram' or destination.parent.parent.name != 'web':
        raise ValueError('only an independent web profile may be modified')
    rows = plan['chat'] + plan['topic']
    if any(not r['slave_uid'].startswith('blueset.wechat ') for r in rows):
        raise ValueError('only web identities may be applied')
    backup = Path(backup)
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    con = sqlite3.connect(destination.as_uri() + '?mode=rw', uri=True, timeout=10)
    try:
        with sqlite3.connect(backup) as copy:
            con.backup(copy)
        con.execute('BEGIN IMMEDIATE')
        allowed = {}
        for row in plan['chat']:
            allowed.setdefault(row['master_uid'], set()).add(row['slave_uid'])
        for row in plan['chat']:
            existing = con.execute('SELECT slave_uid FROM chatassoc WHERE master_uid=?', (row['master_uid'],)).fetchall()
            if any(uid not in allowed[row['master_uid']] for (uid,) in existing):
                raise ValueError('existing chat destination conflicts with import')
            if (row['slave_uid'],) not in existing:
                con.execute('INSERT INTO chatassoc(master_uid,slave_uid) VALUES (?,?)', (row['master_uid'],row['slave_uid']))
        for row in plan['topic']:
            key = (str(row['topic_chat_id']), str(row['message_thread_id']))
            existing = con.execute('SELECT slave_uid FROM topicassoc WHERE topic_chat_id=? AND message_thread_id=?', key).fetchall()
            if any(uid != row['slave_uid'] for (uid,) in existing):
                raise ValueError('existing topic destination conflicts with import')
            if not existing:
                previous = con.execute('SELECT id FROM topicassoc WHERE topic_chat_id=? AND slave_uid=?', (key[0],row['slave_uid'])).fetchall()
                if len(previous) > 1:
                    raise ValueError('multiple active topics for one web identity')
                if previous:
                    con.execute('UPDATE topicassoc SET message_thread_id=? WHERE id=?', (key[1],previous[0][0]))
                else:
                    con.execute('INSERT INTO topicassoc(topic_chat_id,message_thread_id,slave_uid) VALUES (?,?,?)', (*key,row['slave_uid']))
        if con.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('web database check failed')
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-db', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--mapping')
    parser.add_argument('--web-catalog')
    args = parser.parse_args()
    os.umask(0o077)
    out = Path(args.output); out.mkdir(parents=True, exist_ok=False)
    snapshot = export_bindings(args.source_db)
    mapping = json.loads(Path(args.mapping).read_text()) if args.mapping else []
    catalog = json.loads(Path(args.web_catalog).read_text()) if args.web_catalog else []
    plan = make_plan(snapshot, mapping, catalog)
    write_private(out/'source-bindings.json',snapshot)
    write_private(out/'plan.json',plan)
    write_private(out/'mapping-template.json',[{'source':uid,'target':'','confirmed':False} for uid in sorted({r['slave_uid'] for r in snapshot['chat']+snapshot['topic'] if r['slave_uid'].startswith('honus.comwechat ')})])
    stage_database(snapshot, plan, out/'binding-candidate.db')
    report = {'source_chat_bindings':len(snapshot['chat']), 'source_topic_bindings':len(snapshot['topic']),
              'staged_chat_bindings':len(plan['chat']), 'staged_topic_bindings':len(plan['topic']),
              'pending_bindings':len(plan['pending']), 'production_modified':False,
              'message_history_copied':False, 'active_web_database_modified':False}
    write_private(out/'report.json',report)
    print(json.dumps(report))

if __name__ == '__main__': main()
