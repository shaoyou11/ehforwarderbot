"""Backend-specific Telegram menus and a shared control panel."""
import os
import sqlite3
import shutil
import importlib.metadata
import threading
import time
from pathlib import Path
from telegram import InlineKeyboardButton,InlineKeyboardMarkup,BotCommand,BotCommandScopeAllPrivateChats,BotCommandScopeAllGroupChats,BotCommandScopeChat
from telegram.error import BadRequest
from telegram.ext import CommandHandler,CallbackQueryHandler,ApplicationHandlerStop
from ehforwarderbot import coordinator
from protocol import atomic_json,read_json,button,verify
from health import bot_api_status

NAMES={'web':'微信网页版','comwechat':'ComWechat'}
COMMON=[('status','查看当前微信方案的综合状态'),('login','登录当前微信方案'),('backend','切换微信方案'),('sync','同步绑定与个性化设置'),('link','管理会话绑定'),('help','查看命令说明')]
NATIVE_ONLY={'wechat','bridge','watchdog','filetest','security','backup_info','health','issues','trace'}


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
        dispatcher.add_handler(CommandHandler(['backend','status','sync','web_status','web_login','cw_login','login','cw_status','help',*sorted(NATIVE_ONLY)],self.command),group=-2)
        dispatcher.add_handler(CallbackQueryHandler(self.callback,pattern=r'^efbctl:'),group=-2)
        dispatcher.add_handler(CallbackQueryHandler(self.view_callback,pattern=r'^efbview:'),group=-2)
        if self.backend=='web':
            dispatcher.add_handler(CallbackQueryHandler(self.disabled_callback,pattern=r'^(ops:|watchdog:|wechat:|bridge:|bridgeq:)'),group=-2)
        self.original_menus=channel.watchdog_control.update_command_menu
        channel.watchdog_control.update_command_menu=self.menus
        if self.backend=='web':channel.watchdog_control.refresh_group_menu=lambda *a,**k:self.menus()
        self.menus()
        threading.Thread(target=self.heartbeat,daemon=True,name='efb-backend-status').start()

    def authorized(self,update):
        return bool(update.effective_user and update.effective_user.id in self.admins and update.effective_chat and update.effective_chat.type=='private')

    def text(self):
        state=read_json(self.root/'state.json');health=read_json(self.root/f'{self.backend}-health.json')
        online=health.get('wechat_online') if time.time()-health.get('updated',0)<20 else None;login='已登录' if online is True else '未登录' if online is False else '待确认'
        sync=state.get('sync',{})
        return ('EFB 方案与状态\n\n当前方案：'+NAMES[self.backend]+f'\n微信状态：{login}\n控制状态：'+{'idle':'就绪','switching':'切换中','failed':'操作失败','syncing':'同步中','awaiting_login':'等待登录'}.get(state.get('phase'),'准备中')+f"\n已映射绑定：{sync.get('mapped',311)}\n待核对绑定：{sync.get('pending',152)}"+'\n\n两套转发互斥运行；登录凭据和待发送队列不参与同步。'+ ('\n文件：网页版普通文件和视频保护上限 25 MiB。' if self.backend=='web' else '')+ ('\n最近结果：'+state['last_result'] if state.get('last_result') else ''))

    def overview(self):
        state=read_json(self.root/'state.json');sync=state.get('sync',{})
        prefix='当前方案：'+NAMES[self.backend]+'\n绑定同步：'+str(sync.get('mapped','未知'))+' 条已映射，'+str(sync.get('pending','未知'))+' 条待核对\n'
        if self.backend=='comwechat':
            return prefix+'详情 /cw_status；切换 /backend\n\n'+self.channel.operations_ui.health_text()
        health=read_json(self.root/'web-health.json');fresh=time.time()-health.get('updated',0)<20
        online='已登录' if fresh and health.get('wechat_online') else '未登录' if fresh else '状态过期，待确认'
        flags=[]
        for label,package in [('EFB','ehforwarderbot'),('Telegram','efb-telegram-master'),('微信网页版','efb-wechat-slave')]:
            try:flags.append(label+' '+importlib.metadata.version(package))
            except importlib.metadata.PackageNotFoundError:flags.append(label+' 版本未知')
        db=Path('/data/profiles/web/blueset.telegram/tgdata.db');counts='暂不可读'
        try:
            with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True,timeout=2) as conn:
                normal=conn.execute('SELECT count(*) FROM chatassoc').fetchone()[0];topics=conn.execute('SELECT count(*) FROM topicassoc').fetchone()[0]
                counts=f'会话绑定 {normal} 条；话题绑定 {topics} 条'
        except sqlite3.Error:pass
        try:space=f'{shutil.disk_usage("/data").free/(2**30):.1f} GiB'
        except OSError:space='未知'
        api=bot_api_status()
        spoiler=getattr(getattr(self.channel,'author_name_spoiler_store',None),'enabled',None)
        sync_time=time.strftime('%m-%d %H:%M:%S',time.localtime(sync['updated'])) if sync.get('updated') else '未记录'
        return ('EFB 综合状态\n\n'+prefix+
                '\n【微信网页版】\n微信连接：'+online+'\n状态心跳：'+('正常' if fresh else '过期')+
                '\n处理中消息：'+str(health.get('inflight','未知'))+'\nTelegram Bot API：'+api+
                '\n普通文件/视频：当前保护上限 25 MiB\n视频号：使用网页版原生处理'+
                '\n\n【绑定与个性化】\n'+counts+'\n最近同步：'+sync_time+
                '\n群成员姓名隐藏：'+('开启' if spoiler is True else '关闭' if spoiler is False else '未知')+
                '\n接收策略 /filter；姓名隐藏 /namespoiler；图片复用 /imageperception'+
                '\n\n【运行环境】\n'+ '\n'.join(flags)+'\n数据盘剩余：'+space+
                ('\n网络：网页版与 Bot API 独立运行；网关 192.168.12.2' if state.get('network_independent') else '\n网络：尚未完成独立化')+
                '\n\n【功能区分】\n通用：/link /chat /info /cleanup /version /sync'+
                '\n网页版：/web_status /web_login'+
                '\nComWechat：/cw_status /cw_login；专用 /wechat /bridge /watchdog /trace /issues'+
                '\n当前网页模式不执行原生自动恢复和 Bridge 操作。\n切换方案 /backend')

    def panel(self,update):
        actor=update.effective_user.id;other='comwechat' if self.backend=='web' else 'web'
        keyboard=InlineKeyboardMarkup([[InlineKeyboardButton('切换到 '+NAMES[other],callback_data=button(self.secret,other,actor))],[InlineKeyboardButton('立即同步',callback_data=button(self.secret,'sync',actor))],*self.navigation().inline_keyboard])
        update.effective_message.reply_text(self.text(),reply_markup=keyboard)

    def command(self,update,context):
        command=(update.effective_message.text or '').split()[0].split('@')[0].lstrip('/')
        if command in {'help','cw_status'} and self.backend=='comwechat':
            if command=='cw_status':self.channel.operations_ui.status(update,context);raise ApplicationHandlerStop
            return
        if command=='help' and update.effective_user and update.effective_user.id in self.admins:
            update.effective_message.reply_text('网页版命令\n/backend 切换方案\n/status 综合状态\n/web_status 网页微信连接\n/sync 同步绑定与设置\n/web_login 离线重新登录\n/link 绑定会话\n/chat 会话入口\n/info 当前绑定\n/filter 接收策略\n/namespoiler 姓名隐藏\n/imageperception 图片复用\n/cleanup 存储占用\n/version 组件版本\n/extra 更多当前渠道功能\n/login 登录当前方案；/cw_login 登录 ComWechat（需先切换）',reply_markup=self.navigation());raise ApplicationHandlerStop
        if not self.authorized(update):
            if update.effective_user and update.effective_user.id in self.admins:update.effective_message.reply_text('请在机器人私聊中管理方案和状态。')
            raise ApplicationHandlerStop
        if command in NATIVE_ONLY:
            if self.backend=='comwechat':return
            update.effective_message.reply_text('此命令仅适用于 ComWechat。当前是微信网页版，请使用 /backend 或 /web_status。')
        elif command=='cw_status':
            update.effective_message.reply_text('ComWechat 当前未启用，请先切换方案。' if self.backend=='web' else self.text(),reply_markup=self.navigation())
        elif command in {'cw_login','login'} and (command=='cw_login' or self.backend=='comwechat'):
            if self.backend=='comwechat':self.channel.wechat_control.login(update,context)
            else:update.effective_message.reply_text('当前是网页版。请先通过“切换微信方案”选择 ComWechat，再扫码登录。',reply_markup=self.navigation())
        elif command in {'web_login','login'}:
            slave=coordinator.slaves.get('blueset.wechat')
            if self.backend!='web':update.effective_message.reply_text('请先通过 /backend 切换到微信网页版。',reply_markup=self.navigation())
            elif slave is not None and slave.bot.alive:update.effective_message.reply_text('网页版已经登录，无需重复扫码。',reply_markup=self.navigation())
            elif slave is not None and getattr(slave.bot.core,'isLogging',False):update.effective_message.reply_text('正在登录，请使用最近的二维码。',reply_markup=self.navigation())
            elif slave is None:update.effective_message.reply_text('微信渠道尚未就绪，请稍后重试。',reply_markup=self.navigation())
            else:update.effective_message.reply_text(slave.reauth(command=True),reply_markup=self.navigation())
        elif command=='backend':self.panel(update)
        elif command=='sync':
            actor=update.effective_user.id
            update.effective_message.reply_text('同步已确认的绑定与个性化设置。未核对绑定、登录凭据和消息队列不覆盖。',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('立即同步',callback_data=button(self.secret,'sync',actor))],*self.navigation().inline_keyboard]))
        elif command=='web_status':
            if self.backend!='web':
                update.effective_message.reply_text('网页版当前未启用，请先切换方案。',reply_markup=self.navigation());raise ApplicationHandlerStop
            h=read_json(self.root/'web-health.json');fresh=time.time()-h.get('updated',0)<20
            update.effective_message.reply_text('网页版微信连接\n登录状态：'+('已登录' if fresh and h.get('wechat_online') else '未登录或状态过期')+'\n状态心跳：'+('正常' if fresh else '过期')+'\n处理中消息：'+str(h.get('inflight','未知'))+'\n说明：登录状态不等于端到端投递成功。',reply_markup=self.navigation())
        elif command=='status':
            update.effective_message.reply_text(self.overview(),reply_markup=self.status_markup())
        raise ApplicationHandlerStop

    @staticmethod
    def navigation():
        return InlineKeyboardMarkup([[InlineKeyboardButton('返回综合状态',callback_data='efbview:status'),InlineKeyboardButton('关闭',callback_data='efbview:close')]])

    def status_markup(self):
        rows=[[InlineKeyboardButton('刷新综合状态',callback_data='efbview:status'),InlineKeyboardButton('切换与同步',callback_data='efbview:backend')]]
        if self.backend=='comwechat':
            rows.extend(self.channel.operations_ui.markup('status',include_bridge=True).inline_keyboard)
        else:
            rows.append([InlineKeyboardButton('网页版功能说明',callback_data='efbview:help')])
        rows.append([InlineKeyboardButton('关闭',callback_data='efbview:close')])
        return InlineKeyboardMarkup(rows)

    def view_callback(self,update,context):
        q=update.callback_query
        action=q.data.split(':',1)[1]
        can_close=action=='close' and update.effective_user and update.effective_user.id in self.admins
        if not self.authorized(update) and not can_close:q.answer('仅管理员私聊可操作。');raise ApplicationHandlerStop
        q.answer()
        if action=='close':
            try:q.delete_message()
            except BadRequest:q.edit_message_reply_markup(reply_markup=None)
        elif action=='status':
            try:q.edit_message_text(self.overview(),reply_markup=self.status_markup())
            except BadRequest as error:
                if 'message is not modified' not in str(error).lower():raise
        elif action=='backend':self.panel(update)
        else:q.edit_message_text('通用：绑定 /link、会话 /chat、筛选 /filter、姓名隐藏 /namespoiler、图片复用 /imageperception、存储 /cleanup、版本 /version。\n网页版：/web_status、/web_login。\nComWechat：/cw_status、/cw_login、/wechat、/bridge、/watchdog、/trace、/issues。\n只有当前方案的专属操作可用。',reply_markup=self.navigation())
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
        extra=[('web_status','网页版：连接详情'),('web_login','网页版：扫码登录'+('' if self.backend=='web' else '（需先切换）')),('cw_status','ComWechat：运行详情'),('cw_login','ComWechat：扫码登录'+('' if self.backend=='comwechat' else '（需先切换）'))]
        base=[(k,('' if k not in NATIVE_ONLY else 'ComWechat：')+v) for k,v in PRIVATE_COMMANDS if k not in dict(COMMON) and (self.backend=='comwechat' or k not in NATIVE_ONLY)]
        private=list(dict(COMMON+extra+base+[('version','查看组件版本'),('extra','查看当前方案的扩展命令')]).items())
        group=list(LINKED_GROUP_COMMANDS)
        try:
            if self.backend=='comwechat':self.original_menus()
            self.bot.set_my_commands([BotCommand(*r) for r in private],scope=BotCommandScopeAllPrivateChats())
            if self.backend=='comwechat':return
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
