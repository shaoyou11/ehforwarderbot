"""Low-priority notification worker; isolated from ordinary message processing."""
from contextlib import contextmanager
import fcntl
import threading
import time
from pathlib import Path
from protocol import read_json
from notification_queue import Store, binding

@contextmanager
def bounded_transport(bot):
    # Only the notification thread receives tighter HTTP deadlines. Ordinary
    # requests keep their original kwargs and the active login session.
    session=bot.core.s;original=session.request;owner=threading.get_ident()
    def request(method,url,**kwargs):
        if threading.get_ident()==owner:kwargs['timeout']=(3,8)
        return original(method,url,**kwargs)
    session.request=request
    try:yield
    finally:
        if session.request is request:session.request=original


class Notifications:
    def __init__(self, frontend):
        self.frontend=frontend
        self.store=Store(frontend.root/'notifications','/data/profiles/web/blueset.telegram/tgdata.db')
        self.inflight=None
        # Missing/default-disabled deployment adds no thread to the EFB process.
        if not self.store.root.exists():return
        self.store.initialize()
        threading.Thread(target=self.loop,daemon=True,name='efb-notifications').start()

    def loop(self):
        lock=open(self.store.root/'consumer.lock','a')
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        self.store.recover()
        while True:
            try:self.tick()
            except Exception:
                # Notification faults must not terminate or restart EFB.
                pass
            time.sleep(2)

    def tick(self):
        f=self.frontend;now=time.time()
        state=read_json(f.root/'state.json')
        if state.get('active')!='web' or state.get('phase')!='idle':
            with self.store.connect() as db:
                db.execute("UPDATE events SET status='cancelled',text='',reason='backend_changed',updated=? WHERE status='pending'",(now,))
            return
        if self.inflight:
            event,thread,started=self.inflight
            if thread.is_alive():
                if now-started>20:self.store.finish(event,'unknown','deadline_exceeded')
                return  # One hung request cannot spawn more send threads.
            self.inflight=None
        from ehforwarderbot import coordinator
        slave=coordinator.slaves.get('blueset.wechat')
        healthy=bool(slave and slave.bot.alive)
        queue=getattr(f.channel.master_messages,'message_queue',None)
        with f.activity_lock:
            idle=f.inflight==0 and (queue is None or queue.empty()) and now-f.last_activity>=5
        event=self.store.claim(idle,healthy)
        if not event:return
        # Account in existing switch-drain guard before starting any network work.
        with f.activity_lock:
            if f.inflight or (queue is not None and not queue.empty()):
                self.store.finish(event,'cancelled','ordinary_chat_priority');return
            f.inflight+=1
        def send():
            try:
                policy=self.store.policy();route=self.store.route(policy,event['route'],event['source'])
                current=read_json(f.root/'state.json')
                if str(policy.get('revision',''))!=event['revision'] or current.get('active')!='web' or current.get('phase')!='idle' or time.time()>=event['expires']:
                    self.store.finish(event,'cancelled','state_changed');return
                if binding(self.store.db_path,route['chat_id'],route.get('thread_id'))!=event['target']:
                    self.store.finish(event,'blocked','binding_changed');return
                chats=slave.bot.search(puid=event['target'].split(' ',1)[1])
                if len(chats)!=1:
                    self.store.finish(event,'blocked','recipient_unavailable');return
                # Reuse the active EFB transport; no login, second client, mark-as-read,
                # Telegram resend, middleware reflection, or autonomous reconnect.
                with bounded_transport(slave.bot):
                    result=slave._bot_send_msg(chats[0],event['text'])
                self.store.finish(event,'sent' if result else 'unknown','' if result else 'no_receipt')
            except Exception:
                self.store.finish(event,'unknown','send_result_unknown')
            finally:
                with f.activity_lock:f.inflight-=1
        thread=threading.Thread(target=send,daemon=True,name='efb-notification-send')
        self.inflight=(event,thread,now);thread.start()
