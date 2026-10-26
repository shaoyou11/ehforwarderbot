import json
import unittest
import test_gateway as fixtures
from bridge_control import Control,permitted

class RemoteControls(unittest.TestCase):
    message=fixtures.GatewayTests.message
    def setUp(self):
        fixtures.GatewayTests.setUp(self)
        self.epoch=self.g.run({'op':'on'})['epoch']
        self.cfg['remote_control_enabled']=True
        self.cfg['image_media_dir']=str(self.g.root/'media');self.g.root.joinpath('media').mkdir()
        self.control=Control(self.cfg['image_media_dir'])
        self.control.set(enabled=True,images_enabled=True)
    def test_fast_image_toggle_records_new_floor(self):
        self.control.clock=lambda:100
        self.control.set(images_enabled=False)
        self.control.clock=lambda:101
        self.control.set(images_enabled=True)
        self.assertEqual(self.control.desired()['images_since'],101)
        self.assertTrue(self.control.desired()['enabled'])

    def test_off_on_skips_history_and_preserves_ha(self):
        self.g.run({'op':'control_sync'})
        self.control.set(enabled=False)
        off=self.g.run({'op':'control_sync'});self.assertFalse(off['enabled'])
        self.message('while-off');self.control.set(enabled=True)
        on=self.g.run({'op':'control_sync'});self.assertTrue(on['enabled']);self.assertGreater(on['epoch'],off['epoch'])
        self.assertIsNone(self.g.run({'op':'poll'})['message'])
        self.assertEqual(self.store.policy()['routes']['ha_test'],self.policy['routes']['ha_test'])
        self.assertEqual(self.store.policy()['revision'],'original')
    def test_image_toggle_does_not_reset_text_epoch(self):
        before=self.g.run({'op':'control_sync'});self.control.set(images_enabled=False)
        after=self.g.run({'op':'control_sync'});self.assertEqual(before['epoch'],after['epoch']);self.assertTrue(after['enabled'])
        self.assertFalse(permitted(self.cfg,images=True))
        self.message('text');self.assertEqual(self.g.run({'op':'poll'})['message']['text'],'text')
    def test_control_blocks_new_submit_before_mac_ack(self):
        self.control.set(enabled=False)
        reply=self.g.run({'op':'submit','epoch':self.epoch,'event_id':'blocked','text':'test'})
        self.assertFalse(reply['enabled']);self.assertEqual(self.store.counts(),{})
    def test_offline_never_reported_as_success(self):
        desired,status=self.control.summary();self.assertIn('等待连接',status)
        self.g.run({'op':'control_sync'})
        reply=self.g.run({'op':'control_ack','revision':desired['revision'],'epoch':self.epoch,'enabled':True,'images_enabled':True,'phase':'enabled'})
        self.assertTrue(reply['acknowledged']);self.assertEqual(self.control.summary()[1],'运行中')
        self.control.clock=lambda:__import__('time').time()+46
        self.assertIn('等待连接',self.control.summary()[1])
    def test_stale_ack_and_stale_buttons_rejected(self):
        desired=self.control.desired();self.control.set(images_enabled=False)
        with self.assertRaises(ValueError):self.control.set(expected=desired['revision'],enabled=False)
        reply=self.g.run({'op':'control_ack','revision':desired['revision'],'epoch':self.epoch,'enabled':True,'images_enabled':True,'phase':'enabled'})
        self.assertIn('error',reply)
    def test_desktop_off_updates_desired_and_unchanged_sync_keeps_epoch(self):
        self.g.run({'op':'off'});self.assertFalse(self.control.desired()['enabled'])
        a=self.g.run({'op':'control_sync'});b=self.g.run({'op':'control_sync'});self.assertEqual(a['epoch'],b['epoch'])

if __name__=='__main__':unittest.main()
