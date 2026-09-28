"""Synchronize confirmed identities only; never copy sessions, tokens or history."""
import base64
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
import yaml
from protocol import atomic_json,read_json

FLAGS={'send_image_as_file','prevent_message_removal','network_error_prompt_interval','animated_stickers'}
CONFIGS=['QQ_War.keyword_reply/config.yaml','jiz4oh.keyword_replace/config.yaml','blueset.telegram/author-name-spoiler.json']


def rows(path):
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=10) as c:
        c.execute('BEGIN')
        return {'chat':[dict(zip(('master_uid','slave_uid'),r)) for r in c.execute('SELECT master_uid,slave_uid FROM chatassoc')],
                'topic':[dict(zip(('topic_chat_id','message_thread_id','slave_uid'),r)) for r in c.execute('SELECT topic_chat_id,message_thread_id,slave_uid FROM topicassoc')]}


def synchronize(config,active,force=False):
    root=Path(config['control_root']);other='comwechat' if active=='web' else 'web'
    source=Path(config['profiles'][active]);target=Path(config['profiles'][other])
    mappings=json.loads((root/'mapping.json').read_text())
    if not isinstance(mappings,list) or not mappings:raise ValueError('missing identity mapping')
    sources=set();targets=set()
    for row in mappings:
        if not isinstance(row,dict):raise ValueError('invalid identity mapping')
        source_id=row.get('source');target_id=row.get('target')
        if not isinstance(source_id,str) or not isinstance(target_id,str) or not source_id.startswith('honus.comwechat ') or not target_id.startswith('blueset.wechat ') or row.get('confirmed') is False:raise ValueError('invalid identity mapping')
        if source_id in sources or target_id in targets:raise ValueError('ambiguous identity mapping')
        sources.add(source_id);targets.add(target_id)
    table={r['target']:r['source'] for r in mappings} if active=='web' else {r['source']:r['target'] for r in mappings}
    original=rows(source/'blueset.telegram/tgdata.db')
    plan={kind:[{**r,'slave_uid':table[r['slave_uid']]} for r in original[kind] if r['slave_uid'] in table] for kind in ('chat','topic')}
    changes={};pending_config=[]
    for name in CONFIGS:
        p=source/name
        if not p.exists():continue
        content=p.read_bytes()
        # Rules tied to channel IDs must remain pending until separately translated.
        if b'honus.comwechat' in content or b'blueset.wechat' in content or b'@chatroom' in content:
            pending_config.append(name);continue
        if not (target/name).exists() or (target/name).read_bytes()!=content:changes[name]=content
    for name in ['blueset.telegram/config.yaml','QQ_War.message_merge/config.yaml','blueset.telegram/delivery-policies.json']:
        p=source/name;q=target/name
        if not p.exists() or not q.exists():continue
        parser=json.loads if name.endswith('.json') else yaml.safe_load
        src=parser(p.read_text()) or {};dst=parser(q.read_text()) or {};before=json.dumps(dst,sort_keys=True)
        if name=='blueset.telegram/config.yaml':
            for key in FLAGS:
                if key in src.get('flags',{}):dst.setdefault('flags',{})[key]=src['flags'][key]
        elif name.endswith('delivery-policies.json'):
            for key,value in src.items():
                if key!='rules':dst[key]=value
            rules={k:v for k,v in dst.get('rules',{}).items() if k not in set(table.values())}
            rules.update({table[k]:v for k,v in src.get('rules',{}).items() if k in table})
            dst['rules']=rules
            if any(k not in table for k in src.get('rules',{})):pending_config.append(name+':unmapped-rules')
        else:
            for key,value in src.items():
                if key not in {'comwechatretrive','samemessagegroup','samemessageprivate'}:dst[key]=value
            if src.get('samemessagegroup') or src.get('samemessageprivate'):pending_config.append(name+':chat-rules')
        if json.dumps(dst,sort_keys=True)!=before:changes[name]=(json.dumps(dst,ensure_ascii=False,indent=2) if name.endswith('.json') else yaml.safe_dump(dst,allow_unicode=True)).encode()
    current=rows(target/'blueset.telegram/tgdata.db');managed=set(table.values())
    normalized=lambda d:{k:sorted(json.dumps(r,sort_keys=True) for r in v if r['slave_uid'] in managed) for k,v in d.items()}
    binding_changed=normalized(current)!=normalized(plan)
    report={'mapped':len(mappings),'pending':config.get('pending_bindings',152),'direction':active+' → '+other,'pending_config_sections':pending_config,'updated':time.time(),'changed':binding_changed or bool(changes)}
    if not report['changed']:return report
    backup=root/'backups'/str(time.time_ns());backup.mkdir(parents=True,mode=0o700)
    db=target/'blueset.telegram/tgdata.db'
    saved={}
    for name in changes:
        p=target/name
        saved[name]={'data':base64.b64encode(p.read_bytes()).decode() if p.exists() else None,'uid':p.stat().st_uid if p.exists() else target.stat().st_uid,'gid':p.stat().st_gid if p.exists() else target.stat().st_gid,'mode':p.stat().st_mode&0o777 if p.exists() else 0o600}
    atomic_json(backup/'files.json',saved,0o600)
    c=sqlite3.connect(db,timeout=10)
    try:
        with sqlite3.connect(backup/'bindings.db') as copy:c.backup(copy)
        os.chmod(backup/'bindings.db',0o600)
        c.execute('BEGIN IMMEDIATE')
        for r in plan['topic']:
            conflict=c.execute('SELECT slave_uid FROM topicassoc WHERE topic_chat_id=? AND message_thread_id=?',(str(r['topic_chat_id']),str(r['message_thread_id']))).fetchall()
            if any(uid not in managed for uid, in conflict):raise ValueError('unmapped topic conflict')
        for uid in managed:
            c.execute('DELETE FROM chatassoc WHERE slave_uid=?',(uid,));c.execute('DELETE FROM topicassoc WHERE slave_uid=?',(uid,))
        for r in plan['chat']:c.execute('INSERT INTO chatassoc(master_uid,slave_uid) VALUES (?,?)',(r['master_uid'],r['slave_uid']))
        for r in plan['topic']:c.execute('INSERT INTO topicassoc(topic_chat_id,message_thread_id,slave_uid) VALUES (?,?,?)',(str(r['topic_chat_id']),str(r['message_thread_id']),r['slave_uid']))
        for name,data in changes.items():
            p=target/name;metadata=saved[name];p.parent.mkdir(parents=True,exist_ok=True)
            tmp=p.with_name('.'+p.name+'.sync');fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,metadata['mode'])
            with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
            os.chown(tmp,metadata['uid'],metadata['gid']);os.replace(tmp,p)
        if c.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('target database validation failed')
        c.commit()
    except BaseException:
        c.rollback()
        for name,metadata in saved.items():
            if metadata['data'] is not None:
                p=target/name;p.write_bytes(base64.b64decode(metadata['data']));os.chmod(p,metadata['mode']);os.chown(p,metadata['uid'],metadata['gid'])
        raise
    finally:c.close()
    report['backup']=backup.name
    return report
