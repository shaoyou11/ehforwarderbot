"""Admin-only existing-topic picker; name matches always need confirmation."""
import hashlib,secrets,time
from telegram import InlineKeyboardButton as Button,InlineKeyboardMarkup as Markup
from telegram.ext import CommandHandler,CallbackQueryHandler,ApplicationHandlerStop
from ehforwarderbot import coordinator
from protocol import atomic_json,read_json
from binding_review import sign

class BindingUI:
    def __init__(self,f):
        self.f=f;self.channel=f.channel;self.sessions={};self.channel.binding_ui=self
        dispatcher=self.channel.bot_manager.dispatcher
        dispatcher.add_handler(CommandHandler(['link','bindings'],self.command),group=-3)
        dispatcher.add_handler(CallbackQueryHandler(self.callback,pattern=r'^efblink:'),group=-3)
    def authorized(self,u):return bool(u.effective_user and u.effective_user.id in self.f.admins)
    def session(self,**data):
        self.sessions={k:v for k,v in self.sessions.items() if time.time()-v['created']<600}
        token=secrets.token_hex(6);self.sessions[token]={'created':time.time(),**data};return token
    def button(self,label,token,action):return Button(label,callback_data=f'efblink:{token}:{action}')
    def held_markup(self,msg):
        target='blueset.wechat '+str(msg.chat.uid);token=hashlib.sha256(target.encode()).hexdigest()[:16]
        path=self.f.root/'binding-pending'/(token+'.json')
        if not path.exists():atomic_json(path,{'target':target,'name':msg.chat.alias or msg.chat.name},0o600)
        return Markup([[Button('核对原绑定 / 选择已有话题',callback_data='efblink:pending:'+token)]])
    def send_failure(self,update,msg,text):
        catalog=read_json(self.f.root/'topic-guard-catalog.json')
        known=catalog.get('confirmed',{}).get('blueset.wechat '+str(msg.chat.uid),{})
        token=self.session(actor=update.effective_user.id,source=known.get('source'),name=msg.chat.alias or msg.chat.name,held=True)
        update.effective_message.reply_text('消息未发送。\n'+text,reply_markup=Markup([[self.button('核对收件人绑定',token,'fix')]]))

    def command(self,u,context):
        if not self.authorized(u):raise ApplicationHandlerStop
        m=u.effective_message;query=' '.join(context.args or []).casefold();chats=coordinator.slaves['blueset.wechat'].get_chats()
        items=[{'uid':'blueset.wechat '+str(c.uid),'name':c.alias or c.name} for c in chats if not query or query in ((c.name or '')+' '+(c.alias or '')).casefold()]
        token=self.session(actor=u.effective_user.id,items=items,dest=m.chat.id if m.chat.type!='private' else None,thread=m.message_thread_id if m.chat.type!='private' else None)
        text,markup=self.chat_page(token,0);m.reply_text(text,reply_markup=markup);raise ApplicationHandlerStop
    def pages(self,token,prefix,page,count):
        row=[]
        if page>0:row.append(self.button('上一页',token,prefix+str(page-1)))
        if (page+1)*8<count:row.append(self.button('下一页',token,prefix+str(page+1)))
        return ([row] if row else [])+[[self.button('关闭',token,'close')]]
    def chat_page(self,token,page):
        s=self.sessions[token];rows=[[self.button(c['name'][:55],token,'chat'+str(i))] for i,c in enumerate(s['items']) if page*8<=i<(page+1)*8]
        return '请选择微信会话'+('，将关联当前话题。' if s.get('thread') else '，下一步确认绑定位置。')+'\n可使用 /link 名称 搜索。',Markup(rows+self.pages(token,'page',page,len(s['items'])))
    def originals(self,token,page):
        s=self.sessions[token];c=read_json(self.f.root/'topic-guard-catalog.json')
        if time.time()-c.get('updated',0)>900:return '原绑定索引暂不可用，请稍后重试。',Markup([[self.button('关闭',token,'close')]])
        records=c.get('records',{})
        if 'sources' not in s:
            from topic_guard import normalized
            candidates=set(c.get('names',{}).get(normalized(s['name']),[]))
            s['sources']=sorted(records,key=lambda k:(k not in candidates,records[k].get('name','')))
        rows=[[self.button(records[k].get('name',k)[:50]+' · 原绑定',token,'source'+str(i))] for i,k in enumerate(s['sources']) if page*8<=i<(page+1)*8 and k in records]
        return '当前微信会话：'+s['name']+'\n请选择对应的原会话（群组或话题），核实身份后确认。\n也可在目标话题发送 /link 名称，直接绑定该话题。',Markup(rows+self.pages(token,'orig',page,len(s['sources'])))
    def confirmation(self,token):
        s=self.sessions[token];db=self.channel.chat_binding.db;c=read_json(self.f.root/'topic-guard-catalog.json')
        if s.get('source'):
            s['record']=c['records'][s['source']];s['prior']=[uid for uid,r in c.get('confirmed',{}).items() if r.get('source')==s['source']];loc=s['record']
        else:loc={'topics':[[s['dest'],s['thread']]] if s.get('thread') else [],'chats':[] if s.get('thread') else ['blueset.telegram '+str(s['dest'])]}
        owners=[]
        for d,t in loc['topics']:
            owner=db.get_topic_slave(topic_chat_id=d,message_thread_id=t)
            if owner:owners.append(owner)
        parents={'blueset.telegram '+str(x[0]) for x in loc['topics']}
        for d in set(loc['chats'])-parents:owners.extend(db.get_chat_assoc(master_uid=d))
        s['owners']=sorted(set(owners))
        if set(owners)-set(s.get('prior',[])+[s['target']]):return '所选位置已绑定其他会话，未覆盖。请在私聊核对原绑定，或选择空话题。',Markup([[self.button('关闭',token,'close')]])
        label='原会话「'+s['record'].get('name','')+'」的已有位置' if s.get('source') else ('当前话题' if s.get('thread') else '当前群组')
        return '确认将微信会话「'+s['name']+'」绑定到'+label+'？\n该微信会话的旧位置会替换；不新建话题，不重发旧消息。',Markup([[self.button('确认绑定',token,'apply')],[self.button('关闭',token,'close')]])
    def callback(self,u,context):
        q=u.callback_query
        if not self.authorized(u):q.answer('仅管理员可操作。');raise ApplicationHandlerStop
        try:
            _,token,action=q.data.split(':')
            if token=='pending':
                if len(action)!=16 or any(c not in '0123456789abcdef' for c in action):raise ValueError()
                saved=read_json(self.f.root/'binding-pending'/(action+'.json'))
                token=self.session(**saved,actor=u.effective_user.id,held=True);action='orig0'
            s=self.sessions[token]
            if s['actor']!=u.effective_user.id or time.time()-s['created']>600:raise KeyError()
            q.answer()
            if action=='close':q.edit_message_reply_markup(reply_markup=None);self.sessions.pop(token,None);raise ApplicationHandlerStop
            if action=='fix':
                chats=coordinator.slaves['blueset.wechat'].get_chats()
                s['items']=[{'uid':'blueset.wechat '+str(c.uid),'name':c.alias or c.name} for c in chats]
                s['items'].sort(key=lambda c:(c['name']!=s['name'],c['name']))
                text,markup=self.chat_page(token,0)
            elif action.startswith('page'):text,markup=self.chat_page(token,int(action[4:]))
            elif action.startswith('chat'):
                c=s['items'][int(action[4:])];s.update(target=c['uid'],name=c['name'])
                if s.get('dest'):
                    db=self.channel.chat_binding.db
                    old=[db.get_topic_slave(topic_chat_id=s['dest'],message_thread_id=s['thread'])] if s.get('thread') else db.get_chat_assoc(master_uid='blueset.telegram '+str(s['dest']))
                    catalog=read_json(self.f.root/'topic-guard-catalog.json')
                    if len(old)==1 and old[0]!=s['target']:
                        s['source']=catalog.get('confirmed',{}).get(old[0],{}).get('source')
                text,markup=self.confirmation(token) if s.get('dest') or s.get('source') else self.originals(token,0)
            elif action.startswith('orig'):text,markup=self.originals(token,int(action[4:]))
            elif action.startswith('source'):s['source']=s['sources'][int(action[6:])];text,markup=self.confirmation(token)
            elif action=='apply':
                if 'owners' not in s:raise ValueError()
                payload={k:v for k,v in s.items() if k in {'actor','target','name','dest','thread','source','record','prior','owners'}};payload.update(created=time.time(),nonce=token)
                path=self.f.root/'binding-requests'/(token+'.json')
                if path.exists() or (self.f.root/'binding-processed'/path.name).exists():raise ValueError()
                atomic_json(path,{'payload':payload,'mac':sign(self.f.secret,payload)},0o600)
                text='已提交绑定核对，结果会在私聊反馈。旧消息不会重发。';markup=None;self.sessions.pop(token,None)
            else:raise ValueError()
            if s.pop('held',False):q.message.reply_text(text,reply_markup=markup)
            else:q.edit_message_text(text,reply_markup=markup)
        except (KeyError,ValueError,IndexError):q.message.reply_text('操作已过期或绑定已变化，请重新使用 /link 或 /bindings。')
        raise ApplicationHandlerStop
