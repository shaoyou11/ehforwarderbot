import json
from pathlib import Path
import plistlib
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from launch_service import LABEL,REGISTRATION,ensure_worker,render_plist,job_status
from bridge import Store,launch

class Tests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def registered(self): (self.home/REGISTRATION).write_text(json.dumps({'schema':1,'label':LABEL}))
    def test_plist_single_managed_worker_no_secrets_or_auto_on(self):
        value=render_plist(self.home,'/usr/local/opt/python@3.13/bin/python3.13');plistlib.loads(plistlib.dumps(value))
        self.assertEqual(value['Label'],LABEL);self.assertTrue(value['KeepAlive']);self.assertTrue(value['RunAtLoad'])
        self.assertEqual(value['ProgramArguments'][-2:],['--launchd-worker','worker']);self.assertNotIn('on',value['ProgramArguments'])
        self.assertEqual(value['StandardErrorPath'],'/dev/null');self.assertEqual(value['ThrottleInterval'],30)
        self.assertFalse(any('PASSWORD' in key or 'TOKEN' in key for key in value['EnvironmentVariables']))
    def test_missing_registration_retains_legacy(self):self.assertFalse(ensure_worker(self.home))
    def test_invalid_registration_does_not_bootstrap(self):
        (self.home/REGISTRATION).write_text(json.dumps({'label':'other'}))
        with patch('launch_service.run_launchctl') as call:
            with self.assertRaises(ValueError):ensure_worker(self.home)
            call.assert_not_called()
    def test_running_service_not_killed_or_duplicated(self):
        self.registered()
        with patch('launch_service.plist_path',return_value=Path(__file__)),patch('launch_service.job_status',return_value={'registered':True,'pid':123}),patch('launch_service.run_launchctl') as call:
            self.assertTrue(ensure_worker(self.home));call.assert_not_called()
    def test_idle_job_kickstart_never_uses_kill_flag(self):
        self.registered()
        with patch('launch_service.plist_path',return_value=Path(__file__)),patch('launch_service.job_status',return_value={'registered':True,'pid':None}),patch('launch_service.run_launchctl',return_value=SimpleNamespace(returncode=0)) as call:
            self.assertTrue(ensure_worker(self.home));self.assertEqual(call.call_args.args[0],'kickstart');self.assertNotIn('-k',call.call_args.args)
    def test_unloaded_job_bootstraps_only_own_plist(self):
        self.registered()
        with patch('launch_service.plist_path',return_value=Path(__file__)),patch('launch_service.job_status',return_value={'registered':False,'pid':None}),patch('launch_service.run_launchctl',return_value=SimpleNamespace(returncode=0)) as call:
            self.assertTrue(ensure_worker(self.home));self.assertEqual(call.call_args.args[0],'bootstrap');self.assertEqual(call.call_args.args[-1],str(Path(__file__)))
    def test_launch_delegates_without_independent_popen(self):
        self.registered();Store(self.home)
        with patch('launch_service.ensure_worker',return_value=True) as service,patch('bridge.subprocess.Popen') as popen:
            launch(self.home);service.assert_called_once_with(self.home);popen.assert_not_called()
    def test_launch_failure_does_not_fall_back_to_duplicate(self):
        self.registered();Store(self.home)
        with patch('launch_service.ensure_worker',side_effect=ConnectionError),patch('bridge.subprocess.Popen') as popen:
            with self.assertRaises(ConnectionError):launch(self.home)
            popen.assert_not_called()
    def test_status_parses_only_safe_fields(self):
        output='state = running\n pid = 123\n last exit code = 0\n environment = secret-not-returned\n'
        with patch('launch_service.run_launchctl',return_value=SimpleNamespace(returncode=0,stdout=output)):
            status=job_status();self.assertEqual(status['pid'],123);self.assertNotIn('secret',json.dumps(status))

if __name__=='__main__':unittest.main()
