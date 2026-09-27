import importlib
import os
from pathlib import Path
import pickle
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import requests
from efb_wechat_slave.vendor.itchat.safe_session import BoundedSession
from efb_wechat_slave.vendor.itchat.components.hotreload import dump_login_status, load_login_status

class WebImageTests(unittest.TestCase):
    def session(self, directory):
        return SimpleNamespace(hotReloadDir=str(Path(directory)/"session.pkl"), loginInfo={"User": {}}, s=requests.Session(), storageClass=SimpleNamespace(dumps=lambda: {}))

    def test_component_imports(self):
        for module in ["efb_wechat_slave", "efb_telegram_master", "ehforwarderbot"]:
            importlib.import_module(module)

    def test_channel_implements_current_core_contract(self):
        import inspect
        from efb_wechat_slave import WeChatChannel
        from ehforwarderbot.exceptions import EFBOperationNotSupported
        self.assertFalse(inspect.isabstract(WeChatChannel))
        channel = object.__new__(WeChatChannel)
        with self.assertRaises(EFBOperationNotSupported):
            channel.get_chat_member_picture(None)

    def test_native_channel_absent(self):
        self.assertIsNone(importlib.util.find_spec("efb_wechat_comwechat_slave"))

    def test_session_roundtrip_and_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            core = self.session(directory)
            dump_login_status(core)
            self.assertEqual(pickle.loads(Path(core.hotReloadDir).read_bytes())["loginInfo"], core.loginInfo)
            self.assertEqual(os.stat(core.hotReloadDir).st_mode & 0o777, 0o600)
            dump_login_status(core)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_failed_replace_preserves_old_session(self):
        with tempfile.TemporaryDirectory() as directory:
            core = self.session(directory); dump_login_status(core)
            original = Path(core.hotReloadDir).read_bytes()
            core.loginInfo = {"changed": True}
            with patch("os.replace", side_effect=OSError("interrupted")):
                with self.assertRaises(OSError): dump_login_status(core)
            self.assertEqual(Path(core.hotReloadDir).read_bytes(), original)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_corrupt_cache_does_not_crash_or_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            core = self.session(directory); p = Path(core.hotReloadDir)
            for data in (b"", b"not-a-pickle"):
                p.write_bytes(data)
                result = load_login_status(core, str(p))
                self.assertEqual(result["BaseResponse"]["Ret"], -1002)
                self.assertEqual(p.read_bytes(), data)

    def test_default_and_explicit_timeouts(self):
        with patch.object(requests.Session, "request") as request:
            session = BoundedSession()
            session.get("https://offline.invalid")
            self.assertEqual(request.call_args.kwargs["timeout"], (10,60))
            session.get("https://offline.invalid", timeout=7)
            self.assertEqual(request.call_args.kwargs["timeout"], 7)
            session.post("https://offline.invalid", timeout=None)
            self.assertEqual(request.call_args.kwargs["timeout"], (10,60))

    def test_no_replay_after_uncertain_send(self):
        with patch.object(requests.Session, "request", side_effect=requests.ReadTimeout("uncertain")) as request:
            with self.assertRaises(requests.ReadTimeout):
                BoundedSession().post("https://offline.invalid", data={"message":"test"})
            self.assertEqual(request.call_count, 1)
        self.assertEqual(BoundedSession().get_adapter("https://").max_retries.total, 0)

    def test_groups_with_same_name_remain_distinct_and_members_refresh(self):
        from unittest.mock import Mock
        from efb_wechat_slave import chats
        from ehforwarderbot.chat import GroupChat
        from ehforwarderbot.channel import SlaveChannel
        channel = Mock(spec=SlaveChannel)
        channel.channel_id = "blueset.wechat"
        channel.channel_name = "Web WeChat"
        channel.channel_emoji = "W"
        channel._ = lambda text: text
        channel.flag = lambda key: []
        bot = SimpleNamespace(self=SimpleNamespace(user_name="self"))
        channel.bot = bot
        class FakeGroup:
            def __init__(self, uid):
                self.puid = uid
                self.nick_name = "同名群"
                self.display_name = self.remark_name = None
                self.bot = bot
                self.raw = {"UserName": "@@temporary", "ContactFlag": 2}
                self.members = [SimpleNamespace(puid="member1", nick_name="成员一", display_name="群昵称", remark_name=None, user_name="u1")]
        with patch.object(chats.wxpy, "Group", FakeGroup):
            manager = chats.ChatManager(channel)
            first, second = FakeGroup("group1"), FakeGroup("group2")
            one = manager.wxpy_chat_to_efb_chat(first)
            two = manager.wxpy_chat_to_efb_chat(second)
            self.assertIsInstance(one, GroupChat)
            self.assertNotEqual(one.uid, two.uid)
            first.members.append(SimpleNamespace(puid="member2", nick_name="成员二", display_name=None, remark_name=None, user_name="u2"))
            updated = manager.wxpy_chat_to_efb_chat(first)
            self.assertIs(updated, one)
            self.assertIn("member2", {member.uid for member in updated.members})
            first.nick_name = "改名后的群"
            self.assertEqual(manager.wxpy_chat_to_efb_chat(first).uid, "group1")
            self.assertIs(manager.wxpy_chat_to_efb_chat(None), manager.MISSING_CHAT)

    def test_group_sends_are_single_attempt(self):
        from unittest.mock import Mock
        from efb_wechat_slave import WeChatChannel
        chat = Mock()
        self.assertIs(WeChatChannel._bot_send_msg(None, chat, "群消息"), chat.send_msg.return_value)
        chat.send_msg.assert_called_once_with("群消息")
        chat.send_file.side_effect = requests.ReadTimeout("uncertain")
        with self.assertRaises(requests.ReadTimeout):
            WeChatChannel._bot_send_file(None, chat, "file.txt", None)
        chat.send_file.assert_called_once()

    def test_failed_group_id_update_preserves_existing_mapping(self):
        from efb_wechat_slave.vendor.wxpy.utils.puid_map import PuidMap
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "wxpy_puid.pkl"
            original = pickle.dumps(({}, {}, {}, {}))
            p.write_bytes(original)
            core = SimpleNamespace(path=str(p), user_names={}, wxids={}, remark_names={}, captions={}, _dump_task=None, log=lambda *args: None)
            with patch("os.replace", side_effect=OSError("interrupted")):
                with self.assertRaises(OSError): PuidMap.dump(core)
            self.assertEqual(p.read_bytes(), original)

    def test_received_group_message_is_not_replayed_after_connection_loss(self):
        from unittest.mock import Mock
        from efb_wechat_slave import slave_message
        manager = SimpleNamespace(channel=SimpleNamespace(_stop_polling_event=SimpleNamespace(is_set=lambda:False)))
        attachment = Mock()
        converted = SimpleNamespace(uid="", chat=True, author=True, file=attachment)
        wrapped = slave_message.SlaveMessageManager.Decorators.wechat_msg_meta(lambda *args: converted)
        with patch.object(slave_message.coordinator, "master", object(), create=True), patch.object(slave_message.coordinator, "send_message", side_effect=ConnectionError("Remote end closed connection")) as send:
            with self.assertRaises(ConnectionError):
                wrapped(manager, SimpleNamespace(id="group-message", raw={}))
            send.assert_called_once_with(converted)
            attachment.close.assert_called_once()

    def test_personalization_excludes_credentials_and_native_bindings(self):
        import yaml
        from prepare_profile import prepare
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source"; target = root / "web"
            (source / "blueset.telegram").mkdir(parents=True)
            (source / "config.yaml").write_text("middlewares: [jiz4oh.keyword_replace]\n")
            (source / "blueset.telegram/config.yaml").write_text(yaml.safe_dump({"token":"production-secret", "admins":[1], "flags":{"topic_group":True,"api_base_url":"private-endpoint"}}))
            (source / "QQ_War.message_merge").mkdir()
            (source / "QQ_War.message_merge/config.yaml").write_text(yaml.safe_dump({"comwechatretrive":True,"samemessagegroup":["native-group"]}))
            before = (source / "blueset.telegram/config.yaml").read_bytes()
            result = prepare(source, target)
            config = yaml.safe_load((target / "blueset.telegram/config.yaml").read_text())
            self.assertEqual(config['token'], '')
            self.assertTrue(config['flags']['topic_group'])
            self.assertNotIn('api_base_url', config['flags'])
            self.assertEqual(before, (source / "blueset.telegram/config.yaml").read_bytes())
            self.assertEqual(yaml.safe_load((target / "QQ_War.message_merge/config.yaml").read_text())['samemessagegroup'], [])
            self.assertFalse(result['login_enabled'])
            with self.assertRaises(ValueError): prepare(source, target)
            with self.assertRaises(ValueError): prepare(source, source)

if __name__ == "__main__":
    unittest.main(verbosity=2)
