"""Admin controls. Producer credentials are never displayed in Telegram."""
import fcntl
import secrets
import time
from telegram import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup
from telegram.ext import CommandHandler, CallbackQueryHandler, ApplicationHandlerStop
from protocol import atomic_json

class NotificationUI:
    def __init__(self, frontend, service):
        self.f,self.store,self.sessions=frontend,service.store,{}
        dispatcher=frontend.channel.bot_manager.dispatcher
        dispatcher.add_handler(CommandHandler('notify',self.command),group=-3)
        dispatcher.add_handler(CallbackQueryHandler(self.callback,pattern=r'^efbnotice:'),group=-3)

    def panel(self, actor):
        policy=self.store.policy();enabled=policy.get('enabled') is True
        self.sessions={k:v for k,v in self.sessions.items() if time.time()-v['time']<300}
        nonce=secrets.token_hex(6);routes=list(policy.get('routes',{}))
        self.sessions[nonce]={'actor':actor,'time':time.time(),'revision':policy.get('revision'),'routes':routes}
        buttons=[[Button('关闭通知转发' if enabled else '开启通知转发',callback_data=f'efbnotice:{nonce}:toggle')]]
        for i,name in enumerate(routes):
            route=policy['routes'][name]
            buttons.append([Button(('✅ 已开启：' if route.get('enabled') else '已关闭：')+route.get('label',name)[:40],callback_data=f'efbnotice:{nonce}:route{i}')])
        buttons.append([Button('刷新',callback_data=f'efbnotice:{nonce}:refresh'),Button('关闭面板',callback_data=f'efbnotice:{nonce}:close')])
        counts=self.store.counts()
        text=('外部程序通知\n\n总开关：'+('开启' if enabled else '关闭（保持原有转发行为）')+
              '\n范围：仅已配置来源和绑定；仅文字通知。\n普通聊天优先；通知最多保留 5 分钟。'+
              '\n等待：'+str(counts.get('pending',0))+'；已发送：'+str(counts.get('sent',0))+
              '\n结果不明：'+str(counts.get('unknown',0))+'（不会自动重发）'+
              '\n过期或取消：'+str(sum(counts.get(k,0) for k in ['expired','cancelled','blocked']))+
              '\n关闭开关会停止后续投递；已在发送的通知无法撤回。')
        return text,Markup(buttons)

    def command(self,u,context):
        if not self.f.authorized(u):raise ApplicationHandlerStop
        text,markup=self.panel(u.effective_user.id);u.effective_message.reply_text(text,reply_markup=markup)
        raise ApplicationHandlerStop

    def callback(self,u,context):
        if not self.f.authorized(u):raise ApplicationHandlerStop
        q=u.callback_query
        try:
            _,nonce,action=q.data.split(':');session=self.sessions[nonce]
            if session['actor']!=u.effective_user.id or time.time()-session['time']>300:raise ValueError()
            if action=='close':q.answer();q.edit_message_reply_markup(reply_markup=None);raise ApplicationHandlerStop
            if action!='refresh':
                with open(self.store.root/'policy.lock','a') as lock:
                    fcntl.flock(lock,fcntl.LOCK_EX)
                    policy=self.store.policy()
                    if policy.get('revision')!=session['revision']:raise ValueError()
                    if action=='toggle':policy['enabled']=not policy.get('enabled',False)
                    elif action.startswith('route'):
                        route=policy['routes'][session['routes'][int(action[5:])]];route['enabled']=not route.get('enabled',False)
                    else:raise ValueError()
                    policy['revision']=secrets.token_hex(12);atomic_json(self.store.root/'policy.json',policy,0o600)
                # Cancellation is durable even if re-enabled before the next poll.
                with self.store.connect() as db:
                    db.execute("UPDATE events SET status='cancelled',text='',reason='policy_changed',updated=? WHERE status='pending'",(time.time(),))
            q.answer();text,markup=self.panel(u.effective_user.id);q.edit_message_text(text,reply_markup=markup)
        except (KeyError,ValueError,IndexError):q.answer('操作已过期，请重新发送 /notify。')
        raise ApplicationHandlerStop
