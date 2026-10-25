import threading,unittest
from types import SimpleNamespace
from unittest.mock import Mock
from login_guard import request_reauth

class LoginGuard(unittest.TestCase):
    def channel(self,authenticate):
        return SimpleNamespace(_reauth_lock=threading.Lock(),bot=SimpleNamespace(alive=False),flag=lambda _: 'console_qr_code',authenticate=authenticate,logger=Mock())
    def test_repeated_command_starts_only_one_authentication(self):
        started=threading.Event();finish=threading.Event();calls=[]
        def authenticate(mode):calls.append(mode);started.set();finish.wait(2)
        c=self.channel(authenticate)
        try:
            request_reauth(c,True);self.assertTrue(started.wait(1));text=request_reauth(c,True)
            self.assertEqual(len(calls),1);self.assertIn('正在登录',text)
        finally:finish.set()
    def test_failure_releases_guard_for_retry(self):
        complete=threading.Event()
        def authenticate(mode):complete.set();raise RuntimeError('offline')
        c=self.channel(authenticate);request_reauth(c,True);self.assertTrue(complete.wait(1))
        self.assertTrue(c._reauth_lock.acquire(timeout=1));c._reauth_lock.release()
    def test_online_command_does_not_restart_session(self):
        c=self.channel(Mock());c.bot.alive=True
        self.assertIn('已经登录',request_reauth(c,True));c.authenticate.assert_not_called()
if __name__=='__main__':unittest.main(verbosity=2)
