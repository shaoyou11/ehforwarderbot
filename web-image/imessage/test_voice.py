import base64
import hashlib
import io
import json
import sqlite3
import subprocess
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock,patch
from audio_codec import convert,input_format
from image_outbound import Outbound
import test_gateway as fixtures
from test_image_runtime import ImageRuntimeTests


def wav_sample():
    out=io.BytesIO()
    with wave.open(out,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\0\0'*16000)
    return out.getvalue()


class VoiceCodecTests(unittest.TestCase):
    def test_real_ogg_caf_to_m4a(self):
        for fmt,codec in [('ogg','libopus'),('caf','pcm_s16le')]:
            result=subprocess.run(['ffmpeg','-v','error','-f','wav','-i','pipe:0','-c:a',codec,'-f',fmt,'pipe:1'],input=wav_sample(),capture_output=True,timeout=5)
            self.assertEqual(result.returncode,0)
            output=convert(result.stdout);self.assertEqual(input_format(output),'mov')
            with tempfile.NamedTemporaryFile(suffix='.m4a') as f:
                f.write(output);f.flush()
                check=subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_name,codec_type','-of','json',f.name],capture_output=True,timeout=3)
                streams=json.loads(check.stdout)['streams'];self.assertEqual(streams[0]['codec_name'],'aac');self.assertEqual(streams[0]['codec_type'],'audio')
    def test_bad_input_and_timeout_fail_closed(self):
        with self.assertRaises(ValueError):convert(b'#EXTM3U\nhttp://example.invalid/file')
        with patch('audio_codec.subprocess.run',side_effect=subprocess.TimeoutExpired('probe',3)):
            with self.assertRaises(subprocess.TimeoutExpired):convert(wav_sample())
    def test_long_audio_rejected_not_silently_truncated(self):
        probe=Mock(returncode=0,stdout=json.dumps({'streams':[{'codec_type':'audio'}],'format':{'duration':'601'}}).encode())
        with patch('audio_codec.subprocess.run',return_value=probe) as run:
            with self.assertRaisesRegex(ValueError,'duration'):convert(wav_sample())
            self.assertEqual(run.call_count,1)


class VoiceGatewayTests(unittest.TestCase):
    def setUp(self):fixtures.GatewayTests.setUp(self);self.epoch=self.g.run({'op':'on'})['epoch'];self.cfg.update(images_enabled=True,attachments_enabled=True,attachments_since=0)
    def test_actual_voice_type_is_polled_and_renamed(self):
        import datetime
        with sqlite3.connect(self.cfg['bindings_db']) as db:
            db.execute('ALTER TABLE msglog ADD COLUMN file_id TEXT');db.execute('ALTER TABLE msglog ADD COLUMN mime TEXT')
            db.execute('INSERT INTO msglog VALUES (?,?,?,?,?,?,?,?)',('voice','[Voice]','Voice',self.cfg['target'],'blueset.telegram',str(datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)),'opaque','audio/ogg'))
        with patch('gateway.images_ready',return_value=True),patch.object(Outbound,'fetch_file',return_value=b'OggSdata'),patch('audio_codec.convert_in_runtime',return_value=b'\0\0\0\x18ftypM4A audio') as codec:
            message=self.g.run({'op':'poll','images':True,'attachments':True})['message']
            self.assertEqual(message['filename'],'语音.m4a');self.assertEqual(message['mime'],'audio/mp4');codec.assert_called_once()
            self.assertIsNone(self.g.run({'op':'poll','images':True,'attachments':True})['message'])
    def test_conversion_failure_does_not_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Outbound({'state_dir':tmp},fetch=lambda _:b'OggSbad')
            with patch('audio_codec.convert_in_runtime',side_effect=ValueError()):
                with self.assertRaises(ValueError):store.grant('a'*64,1,'f',time.time(),filename='voice',mime='audio/ogg',voice=True)
            with store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM grants').fetchone()[0],0)


class VoiceRuntimeTests(ImageRuntimeTests):
    def test_caf_normalized_once_before_wechat_and_telegram_copy(self):
        self.sender.images.cancel();data=b'caffmock';self.g.config.update(attachments_enabled=True,mirror_to_telegram=True)
        self.key=self.sender.images.begin('voice',1,len(data),hashlib.sha256(data).hexdigest(),'audio/x-caf','录音.caf')['id']
        self.sender.images.append(self.key,1,0,base64.b64encode(data).decode());self.sender.images.commit(self.key,1)
        output=b'\0\0\0\x18ftypM4A mock';sent=[]
        self.slave._bot_send_file.side_effect=lambda c,n,f:sent.append((n,f.read())) or True
        self.sender.mirror=Mock()
        with patch('audio_codec.convert',return_value=output) as converter:
            self.sender.tick(self.slave);self.finish();self.sender.tick(self.slave);converter.assert_called_once()
        self.assertEqual(sent,[('录音.m4a',output)]);self.assertEqual(self.status(),'sent')
        self.assertEqual(self.sender.mirror.call_args.args[0]['data'],output)
