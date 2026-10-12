"""Signed explicit binding changes; worker serialization prevents sync races."""
import hashlib,hmac,json,sqlite3,time
from pathlib import Path
from protocol import atomic_json,read_json

def sign(secret,data):
    return hmac.new(secret,json.dumps(data,sort_keys=True,ensure_ascii=False).encode(),hashlib.sha256).hexdigest()

def selected_location(data, record=None):
    # An explicit /link in a group/topic is the user's chosen destination.
    if data.get('dest'):
        if data.get('thread'):
            return {'chats': [], 'topics': [[str(data['dest']), str(data['thread'])]]}
        return {'chats': ['blueset.telegram '+str(data['dest'])], 'topics': []}
    if record is None:
        raise ValueError('missing destination')
    return record


def apply_review(config,request):
    root=Path(config['control_root']);d=request['payload']
    if not hmac.compare_digest(request['mac'],sign((root/'secret').read_bytes(),d)):raise ValueError('invalid signature')
    if d['actor'] not in config['admins'] or not 0<=time.time()-d['created']<600:raise ValueError('expired review')
    target=d['target']
    if not target.startswith('blueset.wechat '):raise ValueError('invalid target')
    catalog=read_json(root/'topic-guard-catalog.json');old=read_json(root/'mapping.json',[]);mappings=old[:];source=d.get('source');prior=[]
    if source:
        record=catalog.get('records',{}).get(source)
        if not record or record!=d['record']:raise ValueError('source changed')
        if any(m['target']==target and m['source']!=source for m in mappings):raise ValueError('identity conflict')
        prior=[m['target'] for m in mappings if m['source']==source]
        if prior!=d.get('prior',[]):raise ValueError('mapping changed')
        mappings=[m for m in mappings if m['source']!=source]+[{'source':source,'target':target,'confirmed':True,'evidence':'explicit Telegram admin review','name':d['name']}]
        loc=selected_location(d,record)
    else:
        loc=selected_location(d)
    path=Path(config['profiles']['web'])/'blueset.telegram/tgdata.db';backup=root/'backups'/('binding-review-'+d['nonce']);backup.mkdir(mode=0o700)
    with sqlite3.connect(path,timeout=15) as db:
        with sqlite3.connect(backup/'web.db') as dst:db.backup(dst)
        (backup/'web.db').chmod(0o600);atomic_json(backup/'mapping.json',old,0o600);db.execute('BEGIN IMMEDIATE');owners=[]
        for dest,thread in loc['topics']:owners.extend(r[0] for r in db.execute('SELECT slave_uid FROM topicassoc WHERE topic_chat_id=? AND message_thread_id=?',(str(dest),str(thread))))
        parents={'blueset.telegram '+str(x[0]) for x in loc['topics']}
        for dest in set(loc['chats'])-parents:owners.extend(r[0] for r in db.execute('SELECT slave_uid FROM chatassoc WHERE master_uid=?',(dest,)))
        if sorted(set(owners))!=d['owners']:raise ValueError('destination changed')
        allowed=set(prior+[target])
        if any(o not in allowed for o in owners):raise ValueError('destination occupied')
        for uid in allowed:
            db.execute('DELETE FROM topicassoc WHERE slave_uid=?',(uid,));db.execute('DELETE FROM chatassoc WHERE slave_uid=?',(uid,))
        for dest,thread in loc['topics']:db.execute('INSERT INTO topicassoc(topic_chat_id,message_thread_id,slave_uid) VALUES (?,?,?)',(str(dest),str(thread),target))
        routes=list(dict.fromkeys(loc['chats'] or ['blueset.telegram '+str(d) for d,t in loc['topics']]))
        for dest in routes:db.execute('INSERT INTO chatassoc(master_uid,slave_uid) VALUES (?,?)',(dest,target))
        try:
            if source:atomic_json(root/'mapping.json',mappings,0o600)
            db.commit()
        except BaseException:
            if source:atomic_json(root/'mapping.json',old,0o600)
            raise
    # The worker refreshes the catalog on the next sync; DB commit is authoritative.
    return '绑定已确认：'+d['name']+'。后续消息使用所选位置；旧消息未重发。'
