import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from bridge import Bridge,Store
from test_managed_control import Remote
from test_bridge import Blue
from test_bridge_images import Images,Transfers
from service_worker import GatedRemote,BlueStarter,BlueProbe
from bridge_diagnostics import snapshot

class Tests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name);self.store=Store(self.home);self.remote=Remote();self.probe=Mock(return_value=True);self.starter=Mock(return_value='requested');self.now=1000
    def tearDown(self):self.tmp.cleanup()
    def bridge(self):
        gate=GatedRemote(self.remote,self.home,self.probe,clock=lambda:self.now,starter=self.starter)
        worker=Bridge(self.store,{'managed_control':True,'chat_guid':'mock','validated_single_imessage_chat':True},gate,Blue(),lambda:self.now,Images(),Transfers());return worker,gate
    def test_closed_desired_stays_closed_and_idle_without_opening_app(self):
        self.remote.enabled=False;worker,gate=self.bridge();self.assertTrue(worker.tick());self.assertFalse(self.store.get()['enabled'])
        self.probe.assert_not_called();self.starter.assert_not_called();self.assertEqual([op for op,kw in self.remote.calls],['control_sync','control_ack']);self.assertEqual(self.remote.calls[-1][1]['phase'],'closed')
    def test_open_desired_uses_existing_managed_flow_no_on_off(self):
        worker,gate=self.bridge();worker.tick();self.assertTrue(self.store.get()['enabled']);self.assertFalse([op for op,kw in self.remote.calls if op in ['on','off']]);self.starter.assert_not_called()
    def test_missing_bluebubbles_blocks_poll_and_truthfully_acks_false(self):
        self.probe.return_value=False;worker,gate=self.bridge();worker.tick()
        self.assertFalse(self.store.get()['enabled']);self.assertEqual(self.remote.calls[-1][1]['phase'],'paused_backend');self.assertFalse(self.remote.calls[-1][1]['enabled'])
        self.assertFalse([op for op,kw in self.remote.calls if op=='poll']);self.starter.assert_called_once()
    def test_recovery_starts_now_and_never_changes_desired(self):
        self.probe.return_value=False;worker,gate=self.bridge();worker.tick();self.now=1010;self.probe.return_value=True;worker.tick()
        self.assertTrue(self.store.get()['enabled']);self.assertEqual(self.store.get()['start_ms'],1010000);self.assertTrue(self.remote.enabled)
    def test_nas_offline_does_not_open_app_or_guess_desired(self):
        self.remote.fail='control_sync';worker,gate=self.bridge();worker.tick();self.starter.assert_not_called();self.probe.assert_not_called();self.assertFalse(self.store.get()['enabled'])
    def test_diagnostic_record_contains_no_payload_or_exception(self):
        gate=GatedRemote(self.remote,self.home,self.probe);self.remote.fail='submit'
        with self.assertRaises(ConnectionError):gate.call('submit',text='PRIVATE_BODY',event_id='PRIVATE_ID')
        text=(self.home/'service_status.json').read_text();self.assertNotIn('PRIVATE_BODY',text);self.assertNotIn('PRIVATE_ID',text);self.assertEqual((self.home/'service_status.json').stat().st_mode&0o777,0o600)
    def test_app_open_once_across_worker_restarts_same_login(self):
        with patch('service_worker.subprocess.run',return_value=SimpleNamespace(returncode=0)) as call:
            for _ in range(3):BlueStarter(self.home,key=lambda:'session-one',running=lambda:False)()
            self.assertEqual(call.call_count,1);self.assertEqual(call.call_args.args[0],['/usr/bin/open','-gj','-a','/Applications/BlueBubbles.app'])
            BlueStarter(self.home,key=lambda:'session-two',running=lambda:False)();self.assertEqual(call.call_count,2)
    def test_running_app_is_not_reopened(self):
        with patch('service_worker.subprocess.run') as call:
            self.assertEqual(BlueStarter(self.home,key=lambda:'same',running=lambda:True)(),'already_running');call.assert_not_called()
    def test_failed_open_does_not_loop(self):
        with patch('service_worker.subprocess.run',return_value=SimpleNamespace(returncode=1)) as call:
            starter=BlueStarter(self.home,key=lambda:'same',running=lambda:False);self.assertEqual(starter(),'failed');self.assertEqual(starter(),'already_attempted');self.assertEqual(call.call_count,1)
    def test_read_only_diagnostics_preserves_unknown_and_hides_ids(self):
        self.store.intent('to_imessage','PRIVATE_ID');self.store.recover();before=(self.home/'state.db').read_bytes();data=snapshot(self.home)
        self.assertEqual(data['ledger']['status_counts']['unknown'],1);self.assertNotIn('PRIVATE_ID',json.dumps(data));self.assertEqual((self.home/'state.db').read_bytes(),before)

if __name__=='__main__':unittest.main()
