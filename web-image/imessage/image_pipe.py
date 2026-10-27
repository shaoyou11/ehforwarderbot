"""Single authenticated SSH stream; no listening socket or caller-provided paths."""
import base64
import fcntl
import json
import math
import os
from pathlib import Path
import sys
import time
from gateway import Gateway, images_ready
from bridge_control import control_for, receive_media_allowed
from image_store import ImageStore, CHUNK_BYTES, MAX_BYTES


def line(stream, limit=19900):
    raw=stream.readline(limit+1)
    if len(raw)>limit or not raw.endswith(b'\n'):raise ValueError('invalid_image_line')
    value=json.loads(raw)
    if not isinstance(value,dict):raise ValueError('invalid_image_line')
    return value


def check(g,epoch):
    cfg=g.config
    state=json.loads((g.root/'state.json').read_text())
    policy=g.store.policy()
    if (type(epoch) is not int or epoch!=state['epoch'] or not state['enabled'] or not images_ready(cfg)
        or not g.ready() or policy.get('enabled') is not True
        or policy.get('routes',{}).get(cfg['route'],{}).get('enabled') is not True):
        raise ValueError('images_not_ready')


def transfer(g,mode,source,emit):
    header=line(source,2048);epoch=header.get('epoch');check(g,epoch)
    if mode=='get':
        if set(header)!={'epoch','image_id'}:raise ValueError('invalid_image_header')
        from image_outbound import Outbound
        item,data=Outbound(g.config).take(header['image_id'],epoch)
        if not receive_media_allowed(g.config,item['created']):raise ValueError('receive_media_disabled')
        emit(dict(item,kind='attachment' if 'filename' in item else 'image'))
        for offset in range(0,len(data),CHUNK_BYTES):
            if not receive_media_allowed(g.config,item['created']):raise ValueError('receive_media_disabled')
            emit({'offset':offset,'data':base64.b64encode(data[offset:offset+CHUNK_BYTES]).decode()})
        emit({'status':'complete'});return
    if mode!='put' or set(header) not in ({'epoch','event_id','size','digest','mime','created'}, {'epoch','event_id','size','digest','mime','created','filename'}):raise ValueError('invalid_image_header')
    if 'filename' in header and g.config.get('attachments_enabled') is not True:raise ValueError('attachments_disabled')
    created=header['created']
    if 'filename' in header and (not isinstance(created,(int,float)) or created<g.config.get('attachments_since',float('inf'))):raise ValueError('attachment_before_enable')
    if type(created) not in (int,float) or not math.isfinite(created) or not 0<=time.time()-created<=120:raise ValueError('image_expired')
    if g.config.get('remote_control_enabled') is True and created<control_for(g.config).desired().get('images_since',0):raise ValueError('image_before_control_change')
    store=ImageStore(Path(g.config.get('image_media_dir',g.root/'media')))
    result=store.begin(header['event_id'],epoch,header['size'],header['digest'],header['mime'],header.get('filename'))
    with store.db() as db:
        db.execute('UPDATE images SET expires=min(expires,?) WHERE id=?',(created+120,result['id']))
    if result['duplicate']:
        emit({'status':'duplicate','id':result['id']});return
    emit('IMESSAGE_IMAGE_BODY_READY');offset=0
    try:
        for _ in range(MAX_BYTES//CHUNK_BYTES+1):
            chunk=line(source)
            if chunk=={'op':'commit'}:
                if offset!=header['size']:raise ValueError('image_incomplete')
                with open(g.root/'lock','a') as guard:
                    fcntl.flock(guard,fcntl.LOCK_EX);check(g,epoch)
                    if not 0<=time.time()-created<=120:raise ValueError('image_expired')
                    result2=store.commit(result['id'],epoch)
                emit(dict(result2,id=result['id']));return
            if set(chunk)!={'offset','data'} or chunk['offset']!=offset:raise ValueError('image_chunk_order')
            check(g,epoch)
            reply=store.append(result['id'],epoch,offset,chunk['data']);offset+=reply['received']
        raise ValueError('image_incomplete')
    except Exception:
        # Cancel only this upload; other images/text retain their state.
        with store.db() as db:
            db.execute("UPDATE images SET status='cancelled' WHERE id=? AND status='uploading'",(result['id'],))
            store.expire(db)
        raise


def main():
    os.umask(0o077)
    # Bound interrupted/malicious clients without blocking the ordinary gateway.
    import signal
    signal.alarm(80)
    def emit(value):
        print(value if isinstance(value,str) else json.dumps(value,ensure_ascii=True,separators=(',',':')),flush=True)
    try:
        g=Gateway(json.loads(Path(sys.argv[1]).read_text()))
        emit('IMESSAGE_STDIN_READY');transfer(g,sys.argv[2],sys.stdin.buffer,emit)
    except Exception:
        emit({'error':'image_transfer_unavailable'})

if __name__=='__main__':main()
