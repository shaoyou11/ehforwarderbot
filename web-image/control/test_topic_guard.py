import ast
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from topic_guard import Catalog, TopicRouteOverride, check_topic, normalized
from topic_catalog import export_catalog


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.uid='blueset.wechat abc'
        self.env=patch.dict(os.environ,{'EFB_BACKEND_CONTROL':str(self.root),'EFB_BACKEND':'web'});self.env.start();self.addCleanup(self.env.stop)
        self.db=Mock();self.db.get_topic_slave.return_value=None;self.db.get_chat_assoc.return_value=[]
        self.binding=SimpleNamespace(db=self.db,channel=SimpleNamespace(config={'admins':[123]}))
        self.chat=SimpleNamespace(name='原群',alias='')
        self.data={'version':1,'updated':time.time(),'names':{},'confirmed':{}}
        self.save()
    def save(self):
        self.path=self.root/'topic-guard-catalog.json';self.path.write_text(json.dumps(self.data))
    def call(self):return check_topic(self.binding,self.uid,-100,self.chat)
    def held(self):
        with self.assertRaises(TopicRouteOverride) as ctx:self.call()
        self.assertEqual(ctx.exception.chat_id,123);self.assertTrue(ctx.exception.notice)
        self.db.add_topic_assoc.assert_not_called();self.db.add_chat_assoc.assert_not_called()
    def test_genuinely_new_chat_can_create(self):self.assertIsNone(self.call())
    def test_same_name_never_auto_binds(self):
        self.data['names']['原群']=['honus.comwechat one'];self.save();self.held()
    def test_alias_and_unicode_normalization(self):
        self.chat.name='other';self.chat.alias=' Ａ\u200bＢＣ（群） '
        self.data['names']['abc(群)']=['honus.comwechat one'];self.save();self.held()
    def test_ambiguous_names_hold(self):
        self.data['names']['原群']=['a','b'];self.save();self.held()
    def test_confirmed_topic_reused(self):
        self.data['confirmed'][self.uid]={'topics':[['-100','42']],'chats':[]};self.save()
        self.assertEqual(self.call(),42);self.db.add_topic_assoc.assert_called_once()
    def test_confirmed_other_forum_rerouted(self):
        self.data['confirmed'][self.uid]={'topics':[['-200','42']],'chats':[]};self.save()
        with self.assertRaises(TopicRouteOverride) as ctx:self.call()
        self.assertEqual((ctx.exception.chat_id,ctx.exception.thread_id,ctx.exception.notice),(-200,42,''))
    def test_occupied_topic_not_overwritten(self):
        self.data['confirmed'][self.uid]={'topics':[['-100','42']],'chats':[]};self.db.get_topic_slave.return_value='other';self.save();self.held()
    def test_confirmed_standalone_group(self):
        self.data['confirmed'][self.uid]={'topics':[],'chats':['blueset.telegram -222']};self.save()
        with self.assertRaises(TopicRouteOverride) as ctx:self.call()
        self.assertEqual(ctx.exception.chat_id,-222);self.db.add_chat_assoc.assert_called_once_with(master_uid='blueset.telegram -222',slave_uid=self.uid,multiple_slave=True)
    def test_occupied_standalone_not_overwritten(self):
        self.data['confirmed'][self.uid]={'topics':[],'chats':['blueset.telegram -222']};self.db.get_chat_assoc.return_value=['other'];self.save();self.held()
    def test_stale_catalog_holds_new_topic(self):
        self.data['updated']=time.time()-901;self.save();self.held()
    def test_missing_catalog_holds(self):self.path.unlink();self.held()
    def test_broken_catalog_holds(self):self.path.write_text('{');self.held()
    def test_empty_confirmed_destination_holds(self):
        self.data['confirmed'][self.uid]={'topics':[],'chats':[]};self.save();self.held()
    def test_native_backend_unchanged(self):
        self.path.unlink()
        with patch.dict(os.environ,{'EFB_BACKEND':'comwechat'}):self.assertIsNone(self.call())
    def test_cache_does_not_reread_unchanged_file(self):
        cache=Catalog();cache.read(self.path)
        with patch.object(Path,'read_text',side_effect=AssertionError('unexpected disk read')):
            for _ in range(1000):cache.read(self.path)
    def test_atomic_catalog_replacement_invalidates_cache(self):
        cache=Catalog();cache.read(self.path);self.data['names']['new']=['old'];tmp=self.path.with_suffix('.tmp');tmp.write_text(json.dumps(self.data));tmp.replace(self.path)
        self.assertIn('new',cache.read(self.path)['names'])


class IntegrationTests(unittest.TestCase):
    def test_existing_topic_fast_path_does_not_call_guard(self):
        # Execute the actual patched method in isolation, with a trap chat manager.
        import importlib.util
        spec=importlib.util.find_spec('efb_telegram_master')
        if spec is None:self.skipTest('requires image dependencies')
        source=Path(spec.origin).parent/'chat_binding.py'
        tree=ast.parse(source.read_text());node=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='create_topic')
        node.returns=None
        for arg in node.args.args:arg.annotation=None
        ns={};exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),ns)
        db=Mock();db.get_topic_thread_id.return_value=42
        manager=Mock();manager.get_chat.side_effect=AssertionError('new-topic path entered')
        obj=SimpleNamespace(db=db,chat_manager=manager,_topic_mutex=threading.Lock())
        for _ in range(1000):self.assertEqual(ns['create_topic'](obj,'blueset.wechat abc',-100),42)
        manager.get_chat.assert_not_called()
    def load_method(self, filename, method, namespace):
        import importlib.util
        spec=importlib.util.find_spec('efb_telegram_master')
        if spec is None:self.skipTest('requires image dependencies')
        source=Path(spec.origin).parent/filename
        tree=ast.parse(source.read_text());node=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==method)
        node.returns=None
        for arg in node.args.args:arg.annotation=None
        namespace['__package__']='efb_telegram_master'
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),namespace)
        return namespace[method]
    def test_concurrent_new_topic_creates_once(self):
        from concurrent.futures import ThreadPoolExecutor
        namespace={'utils':SimpleNamespace(chat_id_str_to_id=lambda uid:('blueset.wechat','abc',None))}
        method=self.load_method('chat_binding.py','create_topic',namespace)
        state={'thread':None};db=Mock();db.get_topic_thread_id.side_effect=lambda **kw:state['thread']
        def save(**kw):state['thread']=kw['message_thread_id']
        db.add_topic_assoc.side_effect=save
        bot=Mock()
        def create(**kw):time.sleep(.01);return SimpleNamespace(message_thread_id=42)
        bot.create_forum_topic.side_effect=create
        obj=SimpleNamespace(db=db,bot=bot,chat_manager=Mock(),_topic_mutex=threading.Lock(),channel=SimpleNamespace(),logger=Mock())
        with patch('efb_telegram_master.topic_guard.check_topic',return_value=None):
            with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(lambda _:method(obj,'blueset.wechat abc',-100),range(24)))
        self.assertEqual(results,[42]*24);bot.create_forum_topic.assert_called_once()
    def test_held_message_uses_normal_private_delivery_destination(self):
        from efb_telegram_master.topic_guard import TopicRouteOverride as Route
        namespace={'utils':SimpleNamespace(chat_id_to_str=lambda **kw:'blueset.wechat abc'),'TelegramChatID':int,'SystemChat':type('SystemChat',(),{})}
        method=self.load_method('slave_message.py','get_slave_msg_dest',namespace)
        chat=SimpleNamespace();msg=SimpleNamespace(uid='one',chat=chat,author=SimpleNamespace())
        db=Mock();db.get_chat_assoc.return_value=[]
        manager=Mock();manager.update_chat_obj.return_value=chat
        bot=Mock();bot.get_chat_info.return_value=SimpleNamespace(is_forum=True)
        binding=Mock();binding.create_topic.side_effect=Route(123,notice='待核对\n')
        obj=SimpleNamespace(db=db,chat_manager=manager,bot=bot,logger=Mock(),channel=SimpleNamespace(config={'admins':[123]},topic_group=-100,chat_binding=binding),generate_message_template=lambda *a:'原消息头',chat_dest_cache=Mock())
        self.assertEqual(method(obj,msg),('待核对\n原消息头',(123,None)))
    def test_catalog_contains_only_bound_identities_and_confirmed_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'native/blueset.telegram';p.mkdir(parents=True)
            with sqlite3.connect(p/'tgdata.db') as db:
                db.executescript('CREATE TABLE chatassoc(slave_uid TEXT,master_uid TEXT);CREATE TABLE topicassoc(slave_uid TEXT,topic_chat_id TEXT,message_thread_id TEXT);CREATE TABLE slavechatinfo(slave_channel_id TEXT,slave_chat_uid TEXT,slave_chat_name TEXT,slave_chat_alias TEXT,slave_chat_group_id TEXT);')
                db.execute('INSERT INTO topicassoc VALUES (?,?,?)',('honus.comwechat one','-100','42'))
                db.executemany('INSERT INTO slavechatinfo VALUES (?,?,?,?,?)',[('honus.comwechat','one','原群','别名',None),('honus.comwechat','two','未绑定','',None)])
            export_catalog({'profiles':{'comwechat':str(root/'native')},'control_root':str(root)},[{'source':'honus.comwechat one','target':'blueset.wechat abc','confirmed':True}])
            d=json.loads((root/'topic-guard-catalog.json').read_text())
            self.assertIn('原群',d['names']);self.assertIn('别名',d['names']);self.assertNotIn('未绑定',d['names']);self.assertEqual(d['confirmed']['blueset.wechat abc']['topics'],[['-100','42']])


if __name__=='__main__':unittest.main()
