"""Apply narrowly scoped patches; fail if the pinned source changes."""
from pathlib import Path
import importlib.util
root = Path(importlib.util.find_spec("efb_wechat_slave").origin).parent
p = root / "vendor/itchat/components/hotreload.py"
s = p.read_text()
start = s.index("    # Safe dump\n")
end = s.index("    logger.debug('Dump login status", start)
s = s[:start] + """    # Write and fsync beside the destination, then atomically replace it.
    import tempfile
    parent = os.path.dirname(os.path.abspath(fileDir))
    fd, temp_path = tempfile.mkstemp(prefix=".session-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as f:
            pickle.dump(status, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, fileDir)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)

""" + s[end:]
s = s.replace("    except FileNotFoundError:", "    except (FileNotFoundError, EOFError, pickle.UnpicklingError):", 1)
p.write_text(s)
p = root / "vendor/itchat/core.py"
s = p.read_text()
assert s.count("self.s = requests.Session()") == 1
s = s.replace("from . import storage", "from . import storage\nfrom .safe_session import BoundedSession")
s = s.replace("self.s = requests.Session()", "self.s = BoundedSession()")
p.write_text(s)
(root / "vendor/itchat/safe_session.py").write_text("""import requests
from .config import TIMEOUT

class BoundedSession(requests.Session):
    def request(self, method, url, **kwargs):
        if kwargs.get('timeout') is None:
            kwargs['timeout'] = TIMEOUT
        # No automatic business-operation replay, including POST uploads.
        return super().request(method, url, **kwargs)
""")

p = root / "chats.py"
s = p.read_text()
needle = "if chat_name == cached_obj.name and chat_alias == cached_obj.alias:"
assert s.count(needle) == 1
s = s.replace(needle, "if chat_name == cached_obj.name and chat_alias == cached_obj.alias and not isinstance(chat, wxpy.Group):")
s = s.replace("return self.MISSING_USER", "return self.MISSING_CHAT")
p.write_text(s)
# Preserve group/contact IDs across interrupted updates as well as login data.
p = root / "vendor/wxpy/utils/puid_map.py"
s = p.read_text()
s = s.replace("os.rename(self.path, bak_path)", "__import__('shutil').copy2(self.path, bak_path)")
p.write_text(s)
p = root / "slave_message.py"
s = p.read_text()
start = s.index("                # Retry mechanism for connection errors")
end = s.index("\n            def thread_wrapper", start)
s = s[:start] + '''                # Telegram's request layer owns safe connection retries.
                # An uncertain delivery must never replay the whole message here.
                try:
                    coordinator.send_message(efb_msg)
                finally:
                    if efb_msg.file:
                        efb_msg.file.close()
''' + s[end:]
p.write_text(s)
for name in ("__init__.py", "wizard.py"):
    p = root / name
    s = p.read_text().replace("from pkg_resources import resource_filename", "from importlib.resources import files\n\ndef resource_filename(package, resource):\n    return str(files(package).joinpath(resource))\n")
    p.write_text(s)

# Current EFB requires this capability even when the web API cannot provide it.
p = root / "__init__.py"
s = p.read_text()
needle = "    def get_chat_picture("
assert s.count(needle) == 1
s = s.replace(needle, "    def get_chat_member_picture(self, chat_member):\n        raise EFBOperationNotSupported()\n\n" + needle)
p.write_text(s)
