import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from binding_sync import export_bindings, make_plan, stage_database, apply_web_plan

class BindingSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.db=self.root/'source.db'
        con=sqlite3.connect(self.db)
        con.executescript('CREATE TABLE chatassoc(id INTEGER PRIMARY KEY,master_uid TEXT,slave_uid TEXT); CREATE TABLE topicassoc(id INTEGER PRIMARY KEY,topic_chat_id TEXT,message_thread_id TEXT,slave_uid TEXT); CREATE TABLE msglog(id INTEGER PRIMARY KEY,text TEXT);')
        con.execute('INSERT INTO chatassoc(master_uid,slave_uid) VALUES (?,?)',('tg-group','honus.comwechat group-a'))
        con.execute('INSERT INTO topicassoc(topic_chat_id,message_thread_id,slave_uid) VALUES (?,?,?)',('tg-forum','42','honus.comwechat group-b'))
        con.execute('INSERT INTO msglog(text) VALUES (?)',('private-history',));con.commit();con.close()
        self.before=self.db.read_bytes();self.snapshot=export_bindings(self.db)

    def test_unconfirmed_never_maps_by_name(self):
        plan=make_plan(self.snapshot,[{'source':'honus.comwechat group-a','target':'blueset.wechat one','confirmed':False}],[{'uid':'one','name':'同名群'}])
        self.assertEqual(len(plan['pending']),2);self.assertEqual(plan['chat'],[])

    def test_groups_and_topics_preserve_destinations_without_history(self):
        mapping=[{'source':'honus.comwechat group-'+s,'target':'blueset.wechat '+t,'confirmed':True} for s,t in [('a','one'),('b','two')]]
        plan=make_plan(self.snapshot,mapping,[{'uid':'one'},{'uid':'two'}])
        dst=self.root/'candidate.db';stage_database(self.snapshot,plan,dst)
        with sqlite3.connect(dst) as c:
            self.assertEqual(c.execute('SELECT master_uid,slave_uid FROM chatassoc').fetchone(),('tg-group','blueset.wechat one'))
            self.assertEqual(c.execute('SELECT topic_chat_id,message_thread_id,slave_uid FROM topicassoc').fetchone(),('tg-forum','42','blueset.wechat two'))
            self.assertEqual(c.execute('SELECT count(*) FROM msglog').fetchone()[0],0)
        self.assertEqual(self.db.read_bytes(),self.before)
        with self.assertRaises(FileExistsError):stage_database(self.snapshot,plan,dst)

    def test_unknown_and_duplicate_targets_rejected(self):
        first={'source':'honus.comwechat group-a','target':'blueset.wechat one','confirmed':True}
        with self.assertRaises(ValueError):make_plan(self.snapshot,[first],[])
        with self.assertRaises(ValueError):make_plan(self.snapshot,[first,{**first,'source':'honus.comwechat group-b'}],[{'uid':'one'}])

    def test_repeat_export_is_stable_and_source_is_read_only(self):
        self.assertEqual(export_bindings(self.db)['fingerprint'],self.snapshot['fingerprint'])
        self.assertEqual(self.db.read_bytes(),self.before)

    def web_database(self):
        dst=self.root/'profiles/web/blueset.telegram/tgdata.db';dst.parent.mkdir(parents=True)
        stage_database(self.snapshot, {'chat':[], 'topic':[]}, dst)
        return dst

    def test_merge_restores_original_topic_preserves_history_and_is_idempotent(self):
        dst=self.web_database()
        with sqlite3.connect(dst) as c:
            c.execute("INSERT INTO topicassoc VALUES (1,'tg-forum','999','blueset.wechat two')")
            c.execute("INSERT INTO msglog VALUES (1,'keep-web-history')")
        plan={'chat':[], 'topic':[{'topic_chat_id':'tg-forum','message_thread_id':'42','slave_uid':'blueset.wechat two'}]}
        for i in range(2):apply_web_plan(plan,dst,self.root/f'backup-{i}.db')
        with sqlite3.connect(dst) as c:
            self.assertEqual(c.execute('SELECT message_thread_id FROM topicassoc').fetchall(),[('42',)])
            self.assertEqual(c.execute('SELECT text FROM msglog').fetchone()[0],'keep-web-history')
        self.assertEqual(self.db.read_bytes(),self.before)

    def test_conflict_rolls_back_entire_import(self):
        dst=self.web_database()
        with sqlite3.connect(dst) as c:c.execute("INSERT INTO topicassoc VALUES (1,'tg-forum','42','blueset.wechat unrelated')")
        plan={'chat':[{'master_uid':'new-chat','slave_uid':'blueset.wechat one'}], 'topic':[{'topic_chat_id':'tg-forum','message_thread_id':'42','slave_uid':'blueset.wechat two'}]}
        with self.assertRaises(ValueError):apply_web_plan(plan,dst,self.root/'backup.db')
        with sqlite3.connect(dst) as c:self.assertEqual(c.execute('SELECT count(*) FROM chatassoc').fetchone()[0],0)

    def test_apply_refuses_original_profile(self):
        with self.assertRaises(ValueError):apply_web_plan({'chat':[],'topic':[]},self.db,self.root/'backup.db')

if __name__=='__main__':unittest.main(verbosity=2)
