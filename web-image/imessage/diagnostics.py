"""Bounded, content-free diagnostics. Recording failures never blocks delivery."""
import json
import os
from pathlib import Path
import tempfile
import time

STAGES={'wechat_send':'发送到微信','telegram_copy':'同步到 Telegram','outbound_media':'读取发往 iMessage 的附件','runtime':'附件服务'}
CODES={
 'image_expired':'附件已过期','attachment_before_enable':'早于附件开启时间',
 'image_queue_full':'附件队列已满','image_size_invalid':'附件大小超限',
 'invalid_cache_file':'缓存文件不可用或超限','telegram_file_unavailable':'Telegram 文件不可用',
 'unsupported_image':'不支持的图片格式','image_dimensions_exceeded':'图片像素超限',
 'normalized_image_size_exceeded':'方向修正后图片超限','voice_size_invalid':'音频大小超限',
 'unsupported_voice_format':'不支持的音频格式','voice_probe_failed':'无法识别音频',
 'voice_not_audio_only':'音频含其他媒体流','voice_duration_invalid':'音频时长无效或超过 10 分钟',
 'voice_conversion_failed':'音频转换失败','voice_conversion_unavailable':'音频转换服务不可用',
 'voice_output_invalid':'音频转换结果无效','binding_unavailable':'绑定目标不可用',
 'send_unconfirmed':'接口未确认发送成功','image_copy_unconfirmed':'Telegram 未确认副本成功',
 'timeout':'操作超时，未自动重试','permission':'文件权限不足','other':'处理失败，未自动重试'}

def record(config,stage,error):
    if stage not in STAGES:return
    try:
        # Never persist exception repr: transport exceptions can contain tokens or URLs.
        raw=str(error) if isinstance(error,ValueError) else ''
        code=raw if raw in CODES else 'timeout' if isinstance(error,TimeoutError) or type(error).__name__ in ('TimeoutExpired','ReadTimeout','ConnectTimeout') else 'permission' if isinstance(error,PermissionError) else 'other'
        root=Path(config.get('image_media_dir',config['state_dir']))
        path=root/('diagnostic-'+stage+'.json')
        old=json.loads(path.read_text()) if path.exists() else {}
        value={'time':time.time(),'code':code,'count':int(old.get('count',0))+1}
        with tempfile.NamedTemporaryFile(mode='w',dir=root,prefix='.diagnostic-',delete=False) as f:
            name=f.name
            try:
                json.dump(value,f);f.flush();os.fsync(f.fileno())
                if os.geteuid()==0:os.fchown(f.fileno(),101,101)
                os.fchmod(f.fileno(),0o640)
                os.replace(name,path)
            finally:
                if os.path.exists(name):os.unlink(name)
    except Exception:pass

def summary(config):
    root=Path(config.get('image_media_dir',config['state_dir']));items=[]
    for stage,label in STAGES.items():
        try:
            d=json.loads((root/('diagnostic-'+stage+'.json')).read_text())
            age=time.time()-float(d['time'])
            if 0<=age<=86400 and d['code'] in CODES:items.append((d['time'],label+'：'+CODES[d['code']]))
        except (OSError,ValueError,KeyError,TypeError):pass
    return '\n'.join(text for _,text in sorted(items,reverse=True)) or '最近 24 小时无已记录异常（不代表全部消息已送达）'
