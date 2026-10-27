"""Administrator-only Telegram controls, with explicit remote acknowledgement."""
import fcntl
import secrets
import time
from telegram import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup
from telegram.error import BadRequest
from telegram.ext import CommandHandler,CallbackQueryHandler,ApplicationHandlerStop
from bridge_control import control_for
from gateway import atomic
from image_store import ImageStore
from diagnostics import summary

class BridgeUI:
    def __init__(self,frontend,gateway):
        self.f,self.g,self.sessions=frontend,gateway,{}
        self.control=control_for(gateway.config)
        d=frontend.channel.bot_manager.dispatcher
        d.add_handler(CommandHandler('imessage',self.command),group=-3)
        d.add_handler(CallbackQueryHandler(self.callback,pattern=r'^efbim:'),group=-3)
    def render(self,actor):
        now=time.time();self.sessions={k:v for k,v in self.sessions.items() if now-v['time']<300}
        desired,status=self.control.summary();nonce=secrets.token_hex(6)
        heartbeat=self.control.read('image-runtime.json',{})
        image_ready=heartbeat.get('ready') is True and 0<=now-heartbeat.get('updated',0)<15
        self.sessions[nonce]={'actor':actor,'time':now,'revision':desired['revision'],'receive_revision':desired.get('receive_revision',0)}
        button=lambda text,action:Button(text.replace('图片同步','图片与附件同步') if self.g.config.get('attachments_enabled') is True else text,callback_data=f'efbim:{nonce}:{action}')
        rows=[[button('关闭 iMessage 桥接' if desired['enabled'] else '开启 iMessage 桥接','bridge_off' if desired['enabled'] else 'bridge_on')],
              [button('关闭图片同步' if desired['images_enabled'] else '开启图片同步','images_off' if desired['images_enabled'] else 'images_on')],
              [button('iMessage仅接收文字：'+('已开启（点击关闭）' if desired.get('receive_text_only') else '已关闭（点击开启）'),'receive_all' if desired.get('receive_text_only') else 'receive_text')],
              [button('刷新 iMessage 状态','refresh')]]
        text=('iMessage 桥接\n桥接开关：'+('开启' if desired['enabled'] else '关闭')+
              '\n图片同步：'+('开启' if desired['images_enabled'] else '关闭')+'\n实际状态：'+status+
              ('\n图片服务：'+('就绪' if image_ready else '暂未就绪') if desired['enabled'] and desired['images_enabled'] else '')+
              '\n仅控制 iMessage；Telegram 与微信原有转发保持运行。\n已在发送的消息可能完成；关闭期间不补发。')
        if self.g.config.get('attachments_enabled') is True:
            text=text.replace('图片同步','图片与附件同步').replace('图片服务','附件服务')+'\n图片/文件/视频上限：25 MiB；MP4 按视频发送，其他附件按文件发送。'
        text+='\niMessage仅接收文字：'+('开启' if desired.get('receive_text_only') else '关闭')
        text+='\n此开关由 NAS 执行：开启后不向 iMessage 传入图片、视频、文件及语音；文字照常。\niMessage 发出的文字与支持的附件仍按现有规则同步到微信和 Telegram。关闭此限制也不补发已跳过的附件。'
        text+='\n\n最近诊断：\n'+summary(self.g.config)
        return text,rows
    def stop_pending(self,images_only=False):
        # This process owns only the configured iMessage route, never HA/global policy.
        if not images_only:
            store=self.g.store
            with open(store.root/'policy.lock','a') as guard:
                fcntl.flock(guard,fcntl.LOCK_EX);policy=store.policy();route=policy.get('routes',{}).get(self.g.config['route'])
                if not route or route.get('target')!=self.g.config['target']:raise ValueError('binding_changed')
                route['enabled']=False;atomic(store.root/'policy.json',policy)
            with store.connect() as db:
                db.execute("UPDATE events SET status='cancelled',text='',reason='imessage_control_off',updated=? WHERE source=? AND status='pending'",(time.time(),self.g.config['source']))
        ImageStore(self.control.root).cancel()
    def command(self,u,context):
        if not self.f.authorized(u):raise ApplicationHandlerStop
        text,rows=self.render(u.effective_user.id)
        u.effective_message.reply_text(text,reply_markup=Markup(rows+list(self.f.navigation().inline_keyboard)))
        raise ApplicationHandlerStop
    def callback(self,u,context):
        q=u.callback_query
        if not self.f.authorized(u):q.answer('仅管理员私聊可操作。');raise ApplicationHandlerStop
        try:
            _,nonce,action=q.data.split(':');session=self.sessions[nonce]
            if session['actor']!=u.effective_user.id or time.time()-session['time']>=300:raise ValueError('expired')
            actions={'bridge_on':{'enabled':True},'bridge_off':{'enabled':False},'images_on':{'images_enabled':True},'images_off':{'images_enabled':False},'receive_text':{'receive_text_only':True},'receive_all':{'receive_text_only':False}}
            if action!='refresh':
                self.control.set(expected=session['revision'],expected_receive=session['receive_revision'],**actions[action])
                if action in ('bridge_off','images_off'):self.stop_pending(images_only=action=='images_off')
                q.answer('已生效；已开始传输的附件可能完成，不补发旧附件。' if action in ('receive_text','receive_all') else '已提交，正在等待 Mac 确认。')
            else:q.answer()
            text,rows=self.render(u.effective_user.id)
            try:q.edit_message_text(text,reply_markup=Markup(rows+list(self.f.navigation().inline_keyboard)))
            except BadRequest as e:
                if 'message is not modified' not in str(e).lower():raise
        except (KeyError,ValueError):q.answer('操作已过期，请重新打开 /backend 或 /imessage。')
        raise ApplicationHandlerStop
