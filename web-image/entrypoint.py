"""Offline by default. Activation is a separate, explicit operation."""
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import yaml


def main():
    os.umask(0o077)
    if os.environ.get("EFB_WEB_ENABLE_LOGIN") != "1":
        return subprocess.call([sys.executable, "/opt/efb-web/test_web.py"])
    root = Path(os.environ.get("EFB_DATA_PATH", "/data"))
    profile = os.environ.get("EFB_PROFILE", "web")
    if profile != "web":
        raise SystemExit("网页版仅允许使用独立 web 配置档案")
    config = root / "profiles" / profile / "config.yaml"
    with config.open() as f:
        settings = yaml.safe_load(f)
    if settings.get("slave_channels") != ["blueset.wechat"]:
        raise SystemExit("配置必须仅启用网页版通道")
    if not (root / "ALLOW_WEB_LOGIN").is_file():
        raise SystemExit("缺少独立数据目录的登录启用标记")
    telegram_config = root / "profiles" / profile / "blueset.telegram" / "config.yaml"
    telegram = yaml.safe_load(telegram_config.read_text()) or {}
    if telegram.get("flags", {}).get("local_bot_api"):
        media = Path("/var/lib/telegram-bot-api")
        if not media.is_dir() or not os.access(media, os.R_OK | os.X_OK):
            raise SystemExit("无法读取 Telegram 附件目录，请匹配运行 UID/GID，并保持附件挂载只读")
    lock = open(root / ".web-instance.lock", "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("此网页版数据目录已有实例运行")
    os.set_inheritable(lock.fileno(), True)
    os.execvp("ehforwarderbot", ["ehforwarderbot", "--profile", profile])


if __name__ == "__main__":
    sys.exit(main())
