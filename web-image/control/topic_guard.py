"""Local-only guard, called only after both existing-topic lookups miss."""
import json
import os
import threading
import time
import unicodedata
from pathlib import Path


class TopicRouteOverride(Exception):
    def __init__(self, chat_id, thread_id=None, notice=''):
        self.chat_id = int(chat_id)
        self.thread_id = int(thread_id) if thread_id else None
        self.notice = notice
        super().__init__('topic creation redirected')


def normalized(value):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKC', value or '')
                            if unicodedata.category(c) != 'Cf').casefold().split())


class Catalog:
    def __init__(self):
        self.signature = None
        self.data = None
        self.lock = threading.Lock()

    def read(self, path):
        with self.lock:
            stat = path.stat()
            signature = (str(path), stat.st_mtime_ns, stat.st_size)
            if signature != self.signature:
                if stat.st_size > 4 * 1024 * 1024:
                    raise ValueError('catalog too large')
                data = json.loads(path.read_text())
                if data.get('version') != 1 or not isinstance(data.get('names'), dict) or not isinstance(data.get('confirmed'), dict):
                    raise ValueError('invalid catalog')
                self.data, self.signature = data, signature
            if not 0 <= time.time() - self.data['updated'] < 900:
                raise ValueError('stale catalog')
            return self.data


_catalog = Catalog()


def check_topic(binding, slave_uid, telegram_chat_id, chat):
    # The patch is web-image-only; retain existing behavior outside Web control.
    root = os.environ.get('EFB_BACKEND_CONTROL')
    if not root or os.environ.get('EFB_BACKEND') != 'web' or not str(slave_uid).startswith('blueset.wechat '):
        return
    root = Path(root)
    def hold(reason):
        # Original media/text follow the normal ETM send/log path to private chat.
        # No extra send, automatic replay, or second media spool is introduced.
        raise TopicRouteOverride(binding.channel.config['admins'][0], notice=(
            '【绑定待核对】' + reason + '\n此消息暂存于机器人私聊，未新建话题。'
            '请在原话题使用 /link 确认绑定；旧消息不会自动重复推送。\n\n'))
    try:
        data = _catalog.read(root / 'topic-guard-catalog.json')
    except (OSError, ValueError, TypeError, KeyError):
        hold('原绑定索引暂不可用。')
    known = data['confirmed'].get(str(slave_uid))
    if known:
        topics, chats = known['topics'], known['chats']
        if len(topics) == 1 and not chats:
            dest, thread = map(int, topics[0])
            owner = binding.db.get_topic_slave(topic_chat_id=dest, message_thread_id=thread)
            if owner and owner != slave_uid:
                hold('原话题已被其他会话占用。')
            binding.db.add_topic_assoc(topic_chat_id=dest, message_thread_id=thread, slave_uid=slave_uid)
            if dest == int(telegram_chat_id):
                return thread
            raise TopicRouteOverride(dest, thread)
        if len(chats) == 1 and not topics:
            dest = chats[0]
            owners = binding.db.get_chat_assoc(master_uid=dest)
            if any(owner != slave_uid for owner in owners):
                hold('原群已有其他绑定。')
            binding.db.add_chat_assoc(master_uid=dest, slave_uid=slave_uid, multiple_slave=True)
            raise TopicRouteOverride(dest.split(' ', 1)[1])
        hold('已确认会话的原位置需要核对。')
    candidates = set()
    for value in (getattr(chat, 'name', ''), getattr(chat, 'alias', '')):
        candidates.update(data['names'].get(normalized(value), []))
    if candidates:
        hold('发现与原会话相同的名称，暂未确认身份。')
