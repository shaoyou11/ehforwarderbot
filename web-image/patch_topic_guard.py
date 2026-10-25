"""Fail closed at build time if pinned routing code no longer matches."""
from pathlib import Path
import sys


def patch(root):
    p = Path(root) / 'efb_telegram_master/chat_binding.py'
    s = p.read_text()
    needle = '                    try:\n                        topic = self.bot.create_forum_topic('
    assert s.count(needle) == 1
    s = s.replace(needle, '''                    from .topic_guard import check_topic
                    recovered = check_topic(self, slave_uid, telegram_chat_id, chat)
                    if recovered:
                        return recovered
''' + needle)
    p.write_text(s)
    p = Path(root) / 'efb_telegram_master/slave_message.py'
    s = p.read_text()
    needle = '                    thread_id = self.channel.chat_binding.create_topic(slave_uid=chat_uid, telegram_chat_id=tg_dest)'
    assert s.count(needle) == 1
    s = s.replace(needle, '''                    from .topic_guard import TopicRouteOverride
                    try:
''' + '    ' + needle + '''
                    except TopicRouteOverride as route:
                        tg_dest, thread_id = route.chat_id, route.thread_id
                        review_notice = route.notice
''')
    needle = '        # Generate chat text template & Decide type target'
    assert s.count(needle) == 1
    s = s.replace(needle, "        review_notice = ''\n" + needle)
    needle = '        msg_template = self.generate_message_template(msg, singly_linked)'
    assert s.count(needle) == 1
    s = s.replace(needle, '        msg_template = review_notice + self.generate_message_template(msg, singly_linked)')
    p.write_text(s)


if __name__ == '__main__':
    patch(sys.argv[1])
