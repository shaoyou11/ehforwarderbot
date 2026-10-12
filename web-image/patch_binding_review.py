"""Patch only pinned Web paths; abort a build if call sites drift."""
from pathlib import Path
import sys

def patch(root):
    root=Path(root)
    p=root/'efb_wechat_slave/chats.py';s=p.read_text()
    old='                return wxpy.Chat(wxpy.utils.wrap_user_name(uid), self.bot)'
    assert s.count(old)==1
    s=s.replace(old,"                raise EFBChatNotFound('当前微信联系人列表中已找不到原绑定 ID。请核对绑定后再发送；本条未自动重发。')")
    p.write_text(s)
    p=root/'efb_wechat_slave/__init__.py';s=p.read_text()
    needle='    def substitute_known_error_reason(self, err: wxpy.ResponseError) -> wxpy.ResponseError:\n'
    assert s.count(needle)==1
    s=s.replace(needle,needle+"        if err.err_code == -1 and not err.err_msg:\n            err.err_msg = '微信接口未接受本次发送，原因未明确。请先核对收件人绑定及登录状态；本条未自动重发。'\n            return err\n")
    p.write_text(s)
    p=root/'efb_telegram_master/slave_message.py';s=p.read_text()
    needle='        reactions = self.build_reactions_footer(msg.reactions)'
    assert s.count(needle)==1
    s=s.replace(needle,"""        if msg_template.startswith('【绑定待核对】') and hasattr(self.channel, 'binding_ui'):
            review_markup = self.channel.binding_ui.held_markup(msg)
            reply_markup = InlineKeyboardMarkup(list(reply_markup.inline_keyboard) + list(review_markup.inline_keyboard)) if reply_markup else review_markup

"""+needle)
    p.write_text(s)
    p=root/'efb_telegram_master/master_message.py';s=p.read_text()
    needle='            self.bot.reply_error(update, e.args[0] or self._("Chat is not found."))'
    assert s.count(needle)==1
    s=s.replace(needle,"""            ui = getattr(self.channel, 'binding_ui', None)
            if ui:
                ui.send_failure(update, m, str(e) or '微信会话不可用，请核对绑定。')
            else:
    """+needle)
    p.write_text(s)

if __name__=='__main__':patch(sys.argv[1])
