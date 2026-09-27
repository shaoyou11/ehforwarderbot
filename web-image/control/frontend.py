"""Backend-specific Telegram menus and a shared control panel."""
import os
import threading
import time
from pathlib import Path
from telegram import InlineKeyboardButton,InlineKeyboardMarkup,BotCommand,BotCommandScopeAllPrivateChats,BotCommandScopeAllGroupChats,BotCommandScopeChat
from telegram.ext import CommandHandler,CallbackQueryHandler,ApplicationHandlerStop
from ehforwarderbot import coordinator
from protocol import atomic_json,read_json,button,verify

NAMES={'web':'微信网页版','comwechat':'ComWechat'}
COMMON=[('backend','通用｜切换方案与同步设置'),('status','通用｜当前方案、登录与同步状态'),('sync','通用｜同步已确认的绑定和个性化'),('link','通用｜管理会话绑定'),('help','通用｜查看当前方案说明')]
NATIVE_ONLY={'wechat','login','bridge','watchdog','filetest','security','backup_info','health','issues','trace'}

class Frontend:
    def __init__(self,channel):
        self.channel=channel;self.root=Path(os.environ['EFB_BACKEND_CONTROL']);self.backend=os.environ['EFB_BACKEND']
        self.secret=(self.root/'secret').read_bytes();self.admins=channel.bot_manager.admins
        self.bot=channel.bot_manager.updater.bot
        self.inflight=0;self.activity_lock=threading.Lock();self.last_activity=0
        for owner,name in [(channel.master_messages,'process_telegram_message'),(channel,'send_message')]:
            original=getattr(owner,name)
            def guarded(*args,_original=original,**kwargs):
                with self.activity_lock:self.inflight+=1;self.last_activity=time.time()
                try:return _original(*args,**kwargs)
                finally:
                    with self.activity_lock:self.inflight-=1;self.last_activity=time.time()
            setattr(owner,name,guarded)
        dispatcher=channel.bot_manager.dispatcher
        dispatcher.add_handler(CommandHandler(['backend','status','sync','web_status','web_login','cw_status','help',*sorted(NATIVE_ONLY)],self.command),group=-2)
        dispatcher.add_handler(CallbackQueryHandler(self.callback,pattern=r'^efbctl:'),group=-2)
        if self.backend=='web':
            dispatcher.add_handler(CallbackQueryHandler(self.disabled_callback,pattern=r'^(ops:|watchdog:|wechat:|bridge:|bridgeq:)'),group=-2)
        channel.watchdog_control.update_command_menu=self.menus
        channel.watchdog_control.refresh_group_menu=lambda *a,**k:self.menus()
        self.menus()
        threading.Thread(target=self.heartbeat,daemon=True,name='efb-backend-status').start()

    def authorized(self,update):
        return bool(update.effective_user and update.effective_user.id in self.admins and update.effective_chat and update.effective_chat.type=='private')

    def text(self):
        state=read_json(self.root/'state.json');health=read_json(self.root/f'{self.backend}-health.json')
        online=health.get('wechat_online') if time.time()-health.get('updated',0)<20 else None;login='已登录' if online is True else '未登录' if online is False else '待确认'
        sync=state.get('sync',{})
        return ('EFB 方案与状态\n\n当前方案：'+NAMES[self.backend]+f'\n微信状态：{login}\n控制状态：'+{'idle':'就绪','switching':'切换中','failed':'操作失败','syncing':'同步中','awaiting_login':'等待登录'}.get(state.get('phase'),'准备中')+f"\n已映射绑定：{sync.get('mapped',311)}\n待核对绑定：{sync.get('pending',152)}"+'\n\n两套转发互斥运行；登录凭据和待发送队列不参与同步。'+ ('\n文件：网页版当前上限 25 MB，大文件支持仍待验证。' if self.backend=='web' else '')+ ('\n最近结果：'+state['last_result'] if state.get('last_result') else ''))

    def panel(self,update):
        actor=update.effective_user.id;other='comwechat' if self.backend=='web' else 'web'
        keyboard=InlineKeyboardMarkup([[InlineKeyboardButton('切换到 '+NAMES[other],callback_data=button(self.secret,other,actor))],[InlineKeyboardButton('立即同步',callback_data=button(self.secret,'sync',actor))]])
        update.effective_message.reply_text(self.text(),reply_markup=keyboard)

    def command(self,update,context):
        command=(update.effective_message.text or '').split()[0].split('@')[0].lstrip('/')
        if command in {'help','status','cw_status'} and self.backend=='comwechat':
            if command=='cw_status':self.channel.operations_ui.status(update,context);raise ApplicationHandlerStop
            return
        if command=='help' and update.effective_user and update.effective_user.id in self.admins:
            update.effective_message.reply_text('网页版命令\n/backend 切换方案\n/status 综合状态\n/web_status 网页微信连接\n/sync 同步绑定与设置\n/web_login 离线重新登录\n/link 绑定会话\n/chat 会话入口\n/info 当前绑定\n/filter 接收策略\n/namespoiler 姓名隐藏\n/imageperception 图片复用\n/cleanup 存储占用\n/version 组件版本\n/extra 更多当前渠道功能');raise ApplicationHandlerStop
        if not self.authorized(update):
            if update.effective_user and update.effective_user.id in self.admins:update.effective_message.reply_text('请在机器人私聊中管理方案和状态。')
            raise ApplicationHandlerStop
        if command in NATIVE_ONLY:
            if self.backend=='comwechat':return
            update.effective_message.reply_text('此命令仅适用于 ComWechat。当前是微信网页版，请使用 /backend 或 /web_status。')
        elif command=='cw_status':
            update.effective_message.reply_text('ComWechat 当前未启用。' if self.backend=='web' else self.text())
        elif command=='web_login':
            slave=coordinator.slaves.get('blueset.wechat')
            if self.backend!='web':update.effective_message.reply_text('请先通过 /backend 切换到微信网页版。')
            elif slave.bot.alive:update.effective_message.reply_text('网页版已经登录，无需重复扫码。')
            elif getattr(slave.bot.core,'isLogging',False):update.effective_message.reply_text('正在登录，请使用最近的二维码。')
            else:update.effective_message.reply_text(slave.reauth(command=True))
        elif command=='backend':self.panel(update)
        elif command=='sync':
            actor=update.effective_user.id
            update.effective_message.reply_text('同步已确认的绑定与个性化设置。未核对绑定、登录凭据和消息队列不覆盖。',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('立即同步',callback_data=button(self.secret,'sync',actor))]]))
        elif command=='web_status':
            h=read_json(self.root/'web-health.json');fresh=time.time()-h.get('updated',0)<20
            update.effective_message.reply_text('网页版微信连接\n登录状态：'+('已登录' if fresh and h.get('wechat_online') else '未登录或状态过期')+'\n状态心跳：'+('正常' if fresh else '过期')+'\n处理中消息：'+str(h.get('inflight','未知'))+'\n说明：登录状态不等于端到端投递成功。')
        elif command=='status':
            h=read_json(self.root/f'{self.backend}-health.json')
            update.effective_message.reply_text(self.text().replace('EFB 方案与状态','EFB 综合状态')+'\n处理中消息：'+str(h.get('inflight','未知'))+'\n方案切换请使用 /backend。')
        raise ApplicationHandlerStop

    def callback(self,update,context):
        q=update.callback_query
        if not self.authorized(update):q.answer('仅管理员私聊可操作。');raise ApplicationHandlerStop
        try:request=verify(self.secret,q.data,update.effective_user.id,self.admins)
        except ValueError:q.answer('操作已过期，请重新发送 /backend。');raise ApplicationHandlerStop
        if request['action']==self.backend:q.answer('当前已经是该方案。');raise ApplicationHandlerStop
        request['requested_backend']=self.backend
        path=self.root/'requests'/(request['nonce']+'.json')
        if path.exists() or (self.root/'processed'/path.name).exists():q.answer('请求已提交，请勿重复点击。');raise ApplicationHandlerStop
        atomic_json(path,request,0o600);q.answer('已提交')
        q.edit_message_text('正在同步配置并处理请求，请稍候。不会自动重发旧消息。')
        raise ApplicationHandlerStop

    def disabled_callback(self,update,context):
        update.callback_query.answer('这是 ComWechat 的旧按钮。当前请使用 /backend。')
        raise ApplicationHandlerStop

    def menus(self):
        from efb_telegram_master.watchdog_control import PRIVATE_COMMANDS, LINKED_GROUP_COMMANDS
        extra=[('web_status','网页版｜微信连接详情'),('web_login','网页版｜离线重新登录')] if self.backend=='web' else [('cw_status','ComWechat｜原生综合状态')]
        base=[(k,('通用｜' if k not in NATIVE_ONLY else 'ComWechat｜')+v) for k,v in PRIVATE_COMMANDS if k not in dict(COMMON) and (self.backend=='comwechat' or k not in NATIVE_ONLY)]
        private=COMMON+base+extra+[('version','通用｜组件版本'),('extra','通用｜当前渠道扩展命令')]
        group=list(LINKED_GROUP_COMMANDS)
        try:
            self.bot.set_my_commands([BotCommand(*r) for r in private],scope=BotCommandScopeAllPrivateChats())
            self.bot.set_my_commands([BotCommand(*r) for r in group],scope=BotCommandScopeAllGroupChats())
            for chat in read_json(self.root/'managed-chats.json',[]):
                self.bot.set_my_commands([BotCommand(*r) for r in group],scope=BotCommandScopeChat(chat))
        except Exception:
            # No credentials or request URLs in diagnostics.
            self.channel.logger.warning('方案命令菜单更新失败，稍后重试')

    def heartbeat(self):
        while True:
            online=None
            if self.backend=='web':
                slave=coordinator.slaves.get('blueset.wechat');online=bool(slave and slave.bot.alive)
            worker=getattr(self.channel,'master_messages',None)
            queue=getattr(worker,'message_queue',None)
            atomic_json(self.root/f'{self.backend}-health.json',{'updated':time.time(),'pid':os.getpid(),'backend':self.backend,'wechat_online':online,'queue_size':queue.qsize() if queue is not None else None,'ready':bool(coordinator.slaves.get('blueset.wechat' if self.backend=='web' else 'honus.comwechat')),'inflight':self.inflight,'last_activity':self.last_activity})
            time.sleep(5)


def install(channel):
    if os.environ.get('EFB_BACKEND_CONTROL'):
        channel.backend_frontend=Frontend(channel)
