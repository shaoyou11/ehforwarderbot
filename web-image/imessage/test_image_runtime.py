import base64
import hashlib
import json
from pathlib import Path
import queue
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock
from image_runtime import ImageSender

class ImageRuntimeTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.root.joinpath('state.json').write_text(json.dumps({'enabled':True,'epoch':1}))
        self.front=NS(activity_lock=threading.Lock(),inflight=0,last_activity=0,channel=NS(master_messages=NS(message_queue=queue.Queue())))
        self.g=NS(config={'state_dir':str(self.root),'images_enabled':True,'route':'only','target':'blueset.wechat test'},ready=lambda:True,
                  store=NS(policy=lambda:{'enabled':True,'routes':{'only':{'enabled':True}}}))
        self.sender=ImageSender(self.front,self.g,clock=lambda:100,verifier=lambda *_:None)
        self.slave=Mock();self.slave.bot.alive=True;self.slave.bot.search.return_value=['chat'];self.slave._bot_send_image.return_value=object()
        data=b'\xff\xd8\xffdata';self.key=self.sender.images.begin('one',1,len(data),hashlib.sha256(data).hexdigest(),'image/jpeg')['id']
        self.sender.images.append(self.key,1,0,base64.b64encode(data).decode());self.sender.images.commit(self.key,1)
    def status(self):return self.sender.images.commit(self.key,1)['status']
    def finish(self):
        if self.sender.active:self.sender.active.join(2)
        self.assertEqual(self.front.inflight,0)
    def test_shared_chat_priority(self):
        self.front.inflight=1;self.sender.tick(self.slave);self.slave._bot_send_image.assert_not_called()
        self.assertEqual(self.status(),'pending')
    def test_reuses_existing_transport_once(self):
        self.sender.tick(self.slave);self.finish();self.sender.tick(self.slave)
        self.slave._bot_send_image.assert_called_once();self.assertEqual(self.status(),'sent')
    def test_uncertain_send_not_retried(self):
        self.slave._bot_send_image.side_effect=TimeoutError()
        self.sender.tick(self.slave);self.finish();self.sender.tick(self.slave)
        self.slave._bot_send_image.assert_called_once();self.assertEqual(self.status(),'unknown')
    def test_disable_cancels(self):
        self.g.config['images_enabled']=False;self.sender.tick(self.slave)
        self.slave._bot_send_image.assert_not_called();self.assertEqual(self.status(),'cancelled')
    def test_changed_binding_blocks(self):
        self.g.ready=lambda:False;self.sender.tick(self.slave)
        self.slave._bot_send_image.assert_not_called();self.assertEqual(self.status(),'cancelled')
    def test_invalid_image_never_sent(self):
        self.sender.verifier=Mock(side_effect=ValueError())
        self.sender.tick(self.slave);self.finish()
        self.slave._bot_send_image.assert_not_called();self.assertEqual(self.status(),'blocked')

if __name__=='__main__':unittest.main()
