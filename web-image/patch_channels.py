"""Prefer a verified playable clip, otherwise keep upstream Channels cards."""
from pathlib import Path
import sys

root=Path(sys.argv[1])/'efb_wechat_slave'
p=root/'slave_message.py';s=p.read_text()
needle='''        source = self._("WeChat Channels")
        return self.wechat_raw_link_msg(msg, title, description, image, url=url, suffix=self._("Via {source}").format(source=source))'''
assert s.count(needle)==1
s=s.replace(needle,'''        source = self._("WeChat Channels")
        if __import__('os').environ.get('EFB_WEB_CHANNELS_VIDEO') == '1':
            __import__('sys').path.insert(0, '/opt/efb-backend-control')
            from channels_video import download
            video = download(url)
            if video is not None:
                result = Message(type=MsgType.Video)
                result.path, result.file = Path(video.name), video
                result.filename, result.mime = '视频号.mp4', 'video/mp4'
                result.text = title + '\\n' + description
                return result
        return self.wechat_raw_link_msg(msg, title, description, image, url=url, suffix=self._("Via {source}").format(source=source))''')
p.write_text(s)
