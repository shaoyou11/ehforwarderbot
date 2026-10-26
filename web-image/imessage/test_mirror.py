import unittest
import test_gateway as fixtures
from mirror import Mirror

class MirrorTests(unittest.TestCase):
    def setUp(self):
        fixtures.GatewayTests.setUp(self);self.calls=[]
        self.m=Mirror(self.cfg,send=lambda text:self.calls.append(text) or 42)
        self.epoch=self.g.run({'op':'on'})['epoch']
    def queued(self,event='one'):
        self.g.run({'op':'submit','epoch':self.epoch,'event_id':event,'text':'new'})
        self.m.stage(event,self.epoch,'new')
    def test_waits_for_confirmed_wechat_send_and_no_repeat(self):
        self.queued();self.m.tick(self.store,self.epoch);self.assertEqual(self.calls,[])
        self.store.finish({'source':'imessage','event_id':'one'},'sent')
        self.m.tick(self.store,self.epoch);self.m.tick(self.store,self.epoch)
        self.assertEqual(self.calls,['new（iMessage）'])
    def test_unknown_send_never_mirrored(self):
        self.queued();self.store.finish({'source':'imessage','event_id':'one'},'unknown')
        self.m.tick(self.store,self.epoch);self.assertEqual(self.calls,[])
    def test_uncertain_telegram_result_not_retried(self):
        self.queued();self.store.finish({'source':'imessage','event_id':'one'},'sent')
        def fail(text):self.calls.append(text);raise TimeoutError()
        self.m.send=fail
        self.m.tick(self.store,self.epoch);self.m.tick(self.store,self.epoch)
        self.assertEqual(len(self.calls),1)
    def test_off_or_new_epoch_cancels_copy(self):
        self.queued();self.store.finish({'source':'imessage','event_id':'one'},'sent')
        self.m.tick(self.store,self.epoch+1);self.assertEqual(self.calls,[])
        self.queued('two');self.m.cancel();self.store.finish({'source':'imessage','event_id':'two'},'sent')
        self.m.tick(self.store,self.epoch);self.assertEqual(self.calls,[])

if __name__=='__main__':unittest.main()
