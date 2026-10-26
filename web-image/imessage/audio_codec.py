"""Bounded voice compatibility conversion; never fetch external media or send messages."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from media_types import MAX_BYTES


def input_format(data):
    if data.startswith(b'caff'):return 'caf'
    if data.startswith(b'OggS'):return 'ogg'
    if data.startswith(b'#!AMR'):return 'amr'
    if data.startswith(b'RIFF') and data[8:12]==b'WAVE':return 'wav'
    if len(data)>=12 and data[4:8]==b'ftyp':return 'mov'
    if data.startswith(b'ID3') or (len(data)>1 and data[0]==255 and data[1]&224==224):return 'mp3'
    raise ValueError('unsupported_voice_format')


def convert(data):
    if not isinstance(data,bytes) or not 0<len(data)<=MAX_BYTES:raise ValueError('voice_size_invalid')
    fmt=input_format(data)
    # Two named files; cleanup removes only these exact temporary files.
    with tempfile.NamedTemporaryFile(suffix='.'+fmt) as source, tempfile.NamedTemporaryFile(suffix='.m4a') as target:
        source.write(data);source.flush()
        probe=subprocess.run(['ffprobe','-v','error','-protocol_whitelist','file,pipe','-f',fmt,
                              '-show_entries','stream=codec_type:format=duration','-of','json',source.name],
                             capture_output=True,timeout=3)
        if probe.returncode:raise ValueError('voice_probe_failed')
        info=json.loads(probe.stdout);streams=info.get('streams',[])
        if not streams or any(s.get('codec_type')!='audio' for s in streams):raise ValueError('voice_not_audio_only')
        duration=float(info.get('format',{}).get('duration',0))
        if not 0<duration<=600:raise ValueError('voice_duration_invalid')
        result=subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-protocol_whitelist','file,pipe','-f',fmt,
                               '-i',source.name,'-map','0:a:0','-vn','-sn','-dn','-map_metadata','-1',
                               '-threads','1','-filter_threads','1','-c:a','aac','-b:a','96k','-ac','1',
                               '-movflags','+faststart','-fs',str(MAX_BYTES+1),target.name],
                              stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=10)
        if result.returncode:raise ValueError('voice_conversion_failed')
        size=os.fstat(target.fileno()).st_size
        if not 0<size<=MAX_BYTES:raise ValueError('voice_output_invalid')
        target.seek(0);return target.read(MAX_BYTES+1)


def convert_in_runtime(data):
    """Host gateway delegates only conversion to the existing, unprivileged EFB runtime."""
    result=subprocess.run(['docker','exec','-i','--user','101:101','efb-web-lab-standby',
                           'python3','/opt/efb-imessage/audio_codec.py'],input=data,
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=18)
    if result.returncode or not 0<len(result.stdout)<=MAX_BYTES:raise ValueError('voice_conversion_unavailable')
    if input_format(result.stdout)!='mov':raise ValueError('voice_output_invalid')
    return result.stdout


if __name__=='__main__':
    try:sys.stdout.buffer.write(convert(sys.stdin.buffer.read(MAX_BYTES+1)))
    except Exception:sys.exit(1)
