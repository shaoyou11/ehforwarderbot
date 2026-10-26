"""Synthetic voice metadata only; no live chat lookup, audio playback or sends."""
import tempfile
import unittest
from pathlib import Path
from bridge import Bridge,Store,incoming_attachments,incoming_images,incoming_text
from test_bridge import Blue
from test_bridge_images import Images,MediaRemote
from test_bridge_attachments import Files,Transfers,file_message
from image_api import MAX_BYTES
from attachment_api import attachment_metadata,multipart_file
from unittest.mock import Mock
from test_attachment_api import API,Response


def voice(**changes):
    return file_message()|{'isAudioMessage':True,'hasPayloadData':True,'balloonBundleId':None,
      'attachments':[{'guid':'mock-caf','mimeType':'audio/x-caf','transferName':'Audio Message.caf','totalBytes':100}]}|changes

def altered(**changes):
    v=voice();v['attachments'][0].update(changes);return v

class Tests(unittest.TestCase):
    def accepted(self,message):return bool(incoming_attachments(message,1002000,1000000))
    def test_plain_native_audio_still_passes_without_payload_exception(self):self.assertTrue(self.accepted(voice(hasPayloadData=False)))
    def test_explicit_native_single_caf_payload_passes(self):self.assertTrue(self.accepted(voice()))
    def test_payload_needs_strict_native_flag(self):
        for value in [False,None,1,'true']:
            self.assertFalse(self.accepted(voice(isAudioMessage=value)))
    def test_app_balloon_always_rejected_even_native_flag(self):
        for value in ['com.apple.DigitalTouchBalloonProvider','com.example.red-packet','com.example.extension']:
            self.assertFalse(self.accepted(voice(balloonBundleId=value)))
    def test_matching_m4a_mp3_audio_pairs(self):
        for mime,name in [('audio/mp4','audio.m4a'),('audio/x-m4a','audio.m4a'),('audio/mpeg','audio.mp3'),('audio/aac','audio.aac')]:
            self.assertTrue(self.accepted(altered(mimeType=mime,transferName=name)))
    def test_mismatched_unknown_mime_or_document_payload_stays_rejected(self):
        for mime,name in [('application/octet-stream','doc.pdf'),('application/pdf','doc.pdf'),('video/mp4','video.mp4'),('audio/x-caf','doc.pdf'),('image/png','image.png'),(None,'unknown.bin')]:
            self.assertFalse(self.accepted(altered(mimeType=mime,transferName=name)))
    def test_native_recording_name_fallback_from_known_audio_mime(self):
        v=altered(transferName=None);self.assertTrue(self.accepted(v))
        self.assertEqual(attachment_metadata(v,v['attachments'][0]),('Audio Message.caf','audio/x-caf'))
    def test_native_recording_mime_fallback_from_known_audio_extension(self):
        for mime in [None,'','application/octet-stream']:
            v=altered(mimeType=mime);self.assertTrue(self.accepted(v))
            self.assertEqual(attachment_metadata(v,v['attachments'][0]),('Audio Message.caf','audio/x-caf'))
    def test_missing_both_or_malformed_mime_never_unlocks_payload(self):
        self.assertFalse(self.accepted(altered(mimeType=None,transferName=None)))
        self.assertFalse(self.accepted(altered(mimeType='audio/x-caf\r\nInjected: yes')))
    def test_fallbacks_do_not_retype_other_files_or_app_balloons(self):
        for v in [altered(mimeType=None)|{'isAudioMessage':False},altered(transferName=None)|{'isAudioMessage':False},altered(mimeType=None)|{'balloonBundleId':'com.example.app'}]:
            name,mime=attachment_metadata(v,v['attachments'][0])
            if v['attachments'][0]['mimeType'] is None:self.assertEqual(mime,'application/octet-stream')
            else:self.assertEqual(name,'attachment.bin')
    def test_single_attachment_size_and_flags_are_required(self):
        for change in [{'totalBytes':None},{'totalBytes':True},{'totalBytes':0},{'totalBytes':MAX_BYTES+1},{'guid':''},{'isSticker':True},{'isOutgoing':True}]:
            self.assertFalse(self.accepted(altered(**change)))
        two=voice();two['attachments']*=2;self.assertFalse(self.accepted(two))
        self.assertFalse(self.accepted(voice(attachments=[])))
    def test_system_reaction_outgoing_stale_future_guards_unchanged(self):
        for change in [{'isFromMe':True},{'associatedMessageGuid':'mock-reaction'},{'associatedMessageType':2000},{'itemType':1},{'groupActionType':1},{'dateCreated':900000},{'dateCreated':1003000}]:
            self.assertFalse(self.accepted(voice(**change)))
    def test_no_payload_relaxation_for_text_or_images(self):
        v=voice();self.assertIsNone(incoming_text(v,1002000,1000000));self.assertFalse(incoming_images(v,1002000,1000000))
        self.assertFalse(self.accepted(voice(isAudioMessage=False,attachments=[{'guid':'doc','mimeType':'application/pdf','transferName':'file.pdf','totalBytes':100}])))
    def test_native_audio_original_download_uses_known_mime_name_fallback(self):
        data=b'caff\x00\x01\x00\x00mock';opener=Mock();opener.open.return_value=Response(data)
        api=API({'chat_guid':'fixed'},opener);v=altered(transferName=None)
        self.assertEqual(api.download(v,'mock-caf'),data);self.assertEqual(api.query,{'original':'true','force':'false'})
        self.assertEqual(opener.open.call_count,1)
    def test_reverse_m4a_is_ordinary_audio_file_without_native_flag_or_transcoding(self):
        data=b'\x00\x00\x00\x18ftypM4A mock';body,_=multipart_file('fixed-chat','event',data,'voice.m4a','audio/mp4')
        self.assertIn(data,body);self.assertIn(b'audio/mp4',body);self.assertIn(b'voice.m4a',body)
        self.assertIn(b'apple-script',body);self.assertNotIn(b'isAudioMessage',body);self.assertNotIn(b'private-api',body)
    def test_audio_uses_existing_file_protocol_and_unknown_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory));remote=MediaRemote();blue=Blue();files=Files();files.data=b'caff\x00\x01\x00\x00mock';transfer=Transfers();transfer.fail_put=True
            now=[1000];cfg={'chat_guid':'mock','validated_single_imessage_chat':True,'images_enabled':True,'attachments_enabled':True}
            bridge=Bridge(store,cfg,remote,blue,lambda:now[0],Images(),transfer,files);bridge.on();now[0]=1002;blue.items=[voice()]
            bridge.tick();bridge.tick();self.assertEqual(len(transfer.puts),1);args,kw=transfer.puts[0]
            self.assertEqual(kw['mime'],'audio/x-caf');self.assertEqual(kw['filename'],'Audio Message.caf');self.assertEqual(args[3],files.data)
            self.assertTrue(store.get()['enabled']);self.assertFalse(blue.sent)
            with store.db() as db:self.assertEqual(db.execute("SELECT status FROM ledger WHERE direction='attachment_to_wechat'").fetchone()[0],'unknown')

if __name__=='__main__':unittest.main()
