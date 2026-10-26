"""Opt-in ordinary-mode files. No transcoding, automatic retries or public listener."""
import hashlib
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request
import uuid
from image_api import Images, MAX_BYTES
from attachment_names import filename as clean_filename


def safe_mime(value):
    if not isinstance(value,str):return 'application/octet-stream'
    value=value.lower().strip()
    if len(value)<=100 and re.fullmatch(r'[a-z0-9!#$&^_.+\-]+/[a-z0-9!#$&^_.+\-]+',value):return value
    return 'application/octet-stream'



AUDIO_MIME_PAIRS={'.caf':{'audio/caf','audio/x-caf','audio/x-apple-caf'},
                  '.m4a':{'audio/mp4','audio/m4a','audio/x-m4a'},
                  '.mp4':{'audio/mp4'},'.mp3':{'audio/mpeg','audio/mp3'},
                  '.aac':{'audio/aac','audio/x-aac'}}
AUDIO_DEFAULT_MIME={'.caf':'audio/x-caf','.m4a':'audio/mp4','.mp4':'audio/mp4',
                    '.mp3':'audio/mpeg','.aac':'audio/aac'}


def attachment_metadata(message,attachment):
    raw_name=attachment.get('transferName');raw_mime=attachment.get('mimeType')
    name=clean_filename(raw_name);mime=safe_mime(raw_mime)
    # Only explicit native recordings get audio fallbacks; other files are unchanged.
    if message.get('isAudioMessage') is True and not message.get('balloonBundleId'):
        if not isinstance(raw_name,str) or not raw_name.strip():
            extension=next((ext for ext,mimes in AUDIO_MIME_PAIRS.items() if mime in mimes),None)
            if extension:name='Audio Message'+extension
        extension=Path(name).suffix.lower()
        if raw_mime is None or (isinstance(raw_mime,str) and raw_mime.strip().lower() in ('','application/octet-stream')):
            mime=AUDIO_DEFAULT_MIME.get(extension,mime)
    return name,mime


def validate_file(data,mime,filename):
    if not isinstance(data,bytes) or not 0<len(data)<=MAX_BYTES:raise ValueError('attachment_size_invalid')
    if mime!=safe_mime(mime) or filename!=clean_filename(filename):raise ValueError('attachment_metadata_invalid')
    if mime.startswith('image/'):raise ValueError('use_image_protocol')
    if (mime=='video/mp4' or filename.lower().endswith('.mp4')) and (len(data)<12 or data[4:8]!=b'ftyp'):
        raise ValueError('attachment_mp4_invalid')


def multipart_file(chat,event,data,filename,mime):
    filename=clean_filename(filename);mime=safe_mime(mime);validate_file(data,mime,filename)
    boundary='efb-'+uuid.uuid4().hex
    fields={'chatGuid':chat,'tempGuid':'imessage-file-'+str(uuid.uuid5(uuid.NAMESPACE_URL,event)),
            'name':filename,'method':'apple-script'}
    parts=[]
    for key,value in fields.items():
        parts.append(('--'+boundary+'\r\nContent-Disposition: form-data; name="'+key+'"\r\n\r\n'+value+'\r\n').encode('utf-8'))
    # 'name' preserves the display name. ASCII fallback + RFC 5987 prevent header injection.
    fallback=filename.encode('ascii','replace').decode().replace('"','_')
    disposition=('Content-Disposition: form-data; name="attachment"; filename="'+fallback+'"; filename*=UTF-8\'\''+
                 urllib.parse.quote(filename,safe=''))
    parts.append(('--'+boundary+'\r\n'+disposition+'\r\nContent-Type: '+mime+'\r\n\r\n').encode('ascii'))
    parts.extend([data,('\r\n--'+boundary+'--\r\n').encode()])
    return b''.join(parts),'multipart/form-data; boundary='+boundary


class Attachments(Images):
    def download(self,message,guid):
        if message.get('isFromMe') is not False or not isinstance(guid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',guid):
            raise ValueError('attachment_not_allowed')
        if message.get('chatGuid',self.config['chat_guid'])!=self.config['chat_guid']:raise ValueError('attachment_wrong_chat')
        matches=[a for a in message.get('attachments',[]) if isinstance(a,dict) and a.get('guid')==guid]
        if len(matches)!=1 or matches[0].get('isSticker') or matches[0].get('isOutgoing'):raise ValueError('attachment_not_allowed')
        a=matches[0];name,mime=attachment_metadata(message,a)
        if mime.startswith('image/'):raise ValueError('use_image_protocol')
        size=a.get('totalBytes')
        if isinstance(size,(int,float)) and (size<0 or size>MAX_BYTES):raise ValueError('attachment_size_invalid')
        request=urllib.request.Request(self.url('attachment/'+guid+'/download',{'original':'true','force':'false'}))
        with self.opener.open(request,timeout=20) as response:data=response.read(MAX_BYTES+1)
        validate_file(data,mime,name)
        return data

    def send(self,event,data,filename,mime):
        payload,content_type=multipart_file(self.config['chat_guid'],event,data,filename,mime)
        request=urllib.request.Request(self.url('message/attachment'),data=payload,headers={'Content-Type':content_type})
        with self.opener.open(request,timeout=20) as response:raw=response.read(1024*1024+1)
        if len(raw)>1024*1024:raise ValueError('attachment_response_too_large')
        result=json.loads(raw)
        if result.get('status')!=200:raise ConnectionError('attachment_send_unconfirmed')
        return result.get('data')
