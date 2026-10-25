"""Passive HA service-event adapter; no Telegram polling, history or send retries."""
import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path

from notification_queue import Store, Rejected
from protocol import atomic_json, read_json


def notification(event, entity, now):
    if event.get('event_type') != 'call_service' or event.get('origin') != 'LOCAL':
        return None
    data = event.get('data', {})
    if data.get('domain') != 'notify' or data.get('service') != 'send_message':
        return None
    args = data.get('service_data', {})
    targets = args.get('entity_id')
    if targets != entity and targets != [entity]:
        return None
    # Reject mixed/indirect destinations rather than accidentally forwarding them.
    if any(args.get(k) for k in ('area_id', 'device_id', 'label_id')):
        return None
    text = args.get('message')
    if not isinstance(text, str) or not text.strip():
        return None
    title = args.get('title')
    if isinstance(title, str) and title.strip():
        text = title + '\n' + text
    if len(text) > 4000:
        return None
    try:
        stamp = datetime.fromisoformat(event['time_fired'].replace('Z', '+00:00')).timestamp()
    except (KeyError, TypeError, ValueError):
        return None
    if not -5 <= now - stamp <= 30:
        return None
    identity = [event.get('context', {}).get('id'), event['time_fired'], entity, text]
    event_id = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
    return dict(source='ha', event_id=event_id, route='ha_ying', origin='external', text=text, ttl=120)


def run(root, db_path):
    import websocket
    import fcntl
    lock = open(root/'ha-source.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    config = read_json(root/'ha-source.json')
    store = Store(root, db_path)
    status = {'connected': False, 'accepted': 0, 'rejected': 0}
    def report(**values):
        status.update(values)
        status['updated'] = time.time()
        atomic_json(root/'ha-status.json', status, mode=0o600)
    delay = 2
    while True:
        ws = None
        try:
            url = config['url'].rstrip('/')
            url = ('wss://' + url[8:]) if url.startswith('https://') else ('ws://' + url[7:])
            ws = websocket.create_connection(url+'/api/websocket', timeout=15, suppress_origin=True)
            assert json.loads(ws.recv())['type'] == 'auth_required'
            ws.send(json.dumps({'type':'auth', 'access_token':config['token']}))
            assert json.loads(ws.recv())['type'] == 'auth_ok'
            ws.send(json.dumps({'id':1, 'type':'subscribe_events', 'event_type':'call_service'}))
            ack = json.loads(ws.recv())
            assert ack.get('id') == 1 and ack.get('success') is True
            ws.settimeout(5)
            report(connected=True, reason='subscribed')
            delay = 2
            last_ping = last_pong = time.monotonic()
            seq = 1
            while True:
                now = time.monotonic()
                if now-last_pong > 60:
                    raise TimeoutError()
                if now-last_ping >= 20:
                    seq += 1
                    ws.send(json.dumps({'id':seq, 'type':'ping'}))
                    last_ping = now
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if not raw or len(raw) > 2000000:
                    raise ValueError()
                msg = json.loads(raw)
                if msg.get('type') == 'pong':
                    last_pong = time.monotonic()
                    report(connected=True)
                if msg.get('type') != 'event' or msg.get('id') != 1:
                    continue
                payload = notification(msg.get('event', {}), config['entity'], time.time())
                if payload is None:
                    continue
                try:
                    result = store.submit(payload, read_json(root/'sources.json').get('ha',{}).get('token',''))
                    report(accepted=status['accepted']+int(not result['duplicate']), last_result=result['status'])
                except Rejected as error:
                    report(rejected=status['rejected']+1, last_result=error.reason)
                except Exception:
                    # An uncertain enqueue is never retried. Persistent dedup remains authoritative.
                    report(rejected=status['rejected']+1, last_result='enqueue_unknown')
        except Exception:
            report(connected=False, reason='connection_unavailable')
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
        time.sleep(delay)
        delay = min(delay*2, 60)


if __name__ == '__main__':
    os.umask(0o077)
    run(Path(os.environ['EFB_NOTICE_ROOT']), os.environ['EFB_NOTICE_BINDINGS'])
