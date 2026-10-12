import json,sqlite3,tempfile,time,unittest
from pathlib import Path
from binding_review import apply_review,sign
from protocol import atomic_json

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        (self.root/'backups').mkdir();(self.root/'secret').write_bytes(b'test-secret')
        self.profile=self.root/'web';(self.profile/'blueset.telegram').mkdir(parents=True);self.dbpath=self.profile/'blueset.telegram/tgdata.db'
        with sqlite3.connect(self.dbpath) as db:db.executescript('CREATE TABLE chatassoc(master_uid TEXT,slave_uid TEXT);CREATE TABLE topicassoc(topic_chat_id TEXT,message_thread_id TEXT,slave_uid TEXT);')
        self.config={'control_root':str(self.root),'profiles':{'web':str(self.profile)},'admins':[1]}
        self.record={'name':'测试群','chats':['blueset.telegram -100'],'topics':[['-100','42']]}
        atomic_json(self.root/'mapping.json',[{'source':'honus.comwechat one','target':'blueset.wechat old'}])
        atomic_json(self.root/'topic-guard-catalog.json',{'records':{'honus.comwechat one':self.record}})
        self.data={'nonce':'abc','actor':1,'created':time.time(),'target':'blueset.wechat new','source':'honus.comwechat one','record':self.record,'prior':['blueset.wechat old'],'owners':[],'name':'测试群'}
    def apply(self):return apply_review(self.config,{'payload':self.data,'mac':sign(b'test-secret',self.data)})
    def rows(self):
        with sqlite3.connect(self.dbpath) as db:return list(db.execute('SELECT * FROM topicassoc'))
    def test_original_topic_preserved_and_mapping_replaced(self):
        self.apply();self.assertEqual(self.rows(),[('-100','42','blueset.wechat new')]);self.assertEqual(json.loads((self.root/'mapping.json').read_text())[0]['target'],'blueset.wechat new')
    def test_shared_parent_is_not_a_conflict_or_removed(self):
        with sqlite3.connect(self.dbpath) as db:db.execute('INSERT INTO chatassoc VALUES (?,?)',('blueset.telegram -100','blueset.wechat other'))
        self.apply()
        with sqlite3.connect(self.dbpath) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM chatassoc').fetchone()[0],2)
    def test_occupied_topic_rejected_without_changes(self):
        with sqlite3.connect(self.dbpath) as db:db.execute('INSERT INTO topicassoc VALUES (?,?,?)',('-100','42','blueset.wechat other'))
        self.data['owners']=['blueset.wechat other']
        with self.assertRaises(ValueError):self.apply()
        self.assertEqual(self.rows(),[('-100','42','blueset.wechat other')])
    def test_existing_direct_group_route_is_preserved(self):
        self.record['chats']=['blueset.telegram -999']
        atomic_json(self.root/'topic-guard-catalog.json',{'records':{'honus.comwechat one':self.record}})
        self.apply()
        with sqlite3.connect(self.dbpath) as db:self.assertEqual(list(db.execute('SELECT master_uid FROM chatassoc')),[('blueset.telegram -999',)])
    def test_tampered_payload_rejected(self):
        mac=sign(b'test-secret',self.data);self.data['target']='blueset.wechat injected'
        with self.assertRaises(ValueError):apply_review(self.config,{'payload':self.data,'mac':mac})
        self.assertEqual(self.rows(),[])
    def test_expired_and_unauthorized_rejected(self):
        self.data['created']-=601
        with self.assertRaises(ValueError):self.apply()
        self.data['created']=time.time();self.data['actor']=2
        with self.assertRaises(ValueError):self.apply()
    def test_source_changed_rejected(self):
        self.data['record']={**self.record,'topics':[['-100','99']]}
        with self.assertRaises(ValueError):self.apply()
    def test_new_topic_binding_uses_exact_thread(self):
        for k in ('source','record','prior'):self.data.pop(k)
        self.data.update(dest=-200,thread=77);self.apply();self.assertEqual(self.rows(),[('-200','77','blueset.wechat new')])
    def test_source_mapping_drift_rejected(self):
        self.data['prior']=['blueset.wechat changed']
        with self.assertRaises(ValueError):self.apply()

class RoutingTests(unittest.TestCase):
    def test_missing_puid_fails_before_creating_fake_recipient(self):
        from efb_wechat_slave.chats import ChatManager
        from ehforwarderbot.exceptions import EFBChatNotFound
        from types import SimpleNamespace
        from unittest.mock import Mock
        bot=Mock();bot.search.return_value=[]
        with self.assertRaises(EFBChatNotFound):ChatManager.get_wxpy_chat_by_uid(SimpleNamespace(bot=bot),'obsolete-puid')
        bot.chats.assert_called_once_with(update=True)
    def test_existing_recipient_is_fast_path(self):
        from efb_wechat_slave.chats import ChatManager
        from types import SimpleNamespace
        from unittest.mock import Mock
        bot=Mock();recipient=object();bot.search.return_value=[recipient]
        self.assertIs(ChatManager.get_wxpy_chat_by_uid(SimpleNamespace(bot=bot),'valid'),recipient)
        bot.chats.assert_not_called()
    def test_topic_context_is_preserved_by_picker(self):
        from binding_ui import BindingUI
        from types import SimpleNamespace as NS
        from unittest.mock import Mock,patch
        from telegram.ext import ApplicationHandlerStop
        ui=object.__new__(BindingUI);ui.sessions={};ui.f=NS(admins=[1])
        m=NS(chat=NS(id=-100,type='supergroup'),message_thread_id=42,reply_text=Mock())
        update=NS(effective_user=NS(id=1),effective_message=m)
        slave=Mock();slave.get_chats.return_value=[NS(uid='abc',name='测试',alias=None)]
        with patch('binding_ui.coordinator.slaves',{'blueset.wechat':slave}):
            with self.assertRaises(ApplicationHandlerStop):ui.command(update,NS(args=[]))
        session=next(iter(ui.sessions.values()));self.assertEqual((session['dest'],session['thread']),(-100,42))
        m.reply_text.assert_called_once()
    def test_pending_button_survives_new_ui_instance(self):
        from binding_ui import BindingUI
        from types import SimpleNamespace as NS
        with tempfile.TemporaryDirectory() as tmp:
            ui=object.__new__(BindingUI);ui.f=NS(root=Path(tmp),admins=[1]);ui.sessions={}
            msg=NS(chat=NS(uid='abc',name='测试',alias=None));button=ui.held_markup(msg).inline_keyboard[0][0]
            token=button.callback_data.split(':')[-1]
            self.assertEqual(json.loads((Path(tmp)/'binding-pending'/(token+'.json')).read_text())['target'],'blueset.wechat abc')
            self.assertEqual(ui.sessions,{})
    def test_nonadmin_cannot_open_link_picker(self):
        from binding_ui import BindingUI
        from types import SimpleNamespace as NS
        from telegram.ext import ApplicationHandlerStop
        ui=object.__new__(BindingUI);ui.f=NS(admins=[1])
        with self.assertRaises(ApplicationHandlerStop):ui.command(NS(effective_user=NS(id=2)),None)

if __name__=='__main__':unittest.main()
