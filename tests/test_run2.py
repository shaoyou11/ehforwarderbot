import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "run2.py"
SPEC = importlib.util.spec_from_file_location("run2", MODULE_PATH)
run2 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run2)


class FinishedProcess:
    def __init__(self, return_code):
        self.return_code = return_code

    def poll(self):
        return self.return_code


def test_monitor_exits_when_wechat_process_stops():
    hook = object.__new__(run2.DockerWechatHook)
    hook.wechat = FinishedProcess(1)
    hook.reg_hook = FinishedProcess(None)

    with pytest.raises(RuntimeError, match="WeChat"):
        hook.monitor_children(poll_interval=0)


def test_monitor_exits_when_hook_process_stops():
    hook = object.__new__(run2.DockerWechatHook)
    hook.wechat = FinishedProcess(None)
    hook.reg_hook = FinishedProcess(2)

    with pytest.raises(RuntimeError, match="Hook"):
        hook.monitor_children(poll_interval=0)
