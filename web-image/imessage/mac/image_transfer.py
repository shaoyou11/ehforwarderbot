"""Single-SSH bounded image transport; no image data in argv/env/stdout logs."""
import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import runpy
import shlex
import signal
import subprocess
import tempfile
import time
from image_api import image_kind, MAX_BYTES
from attachment_api import clean_filename, safe_mime, validate_file
from bridge import DEFAULT_HOME, REMOTE_CONFIG, GATEWAY

CHUNK_BYTES = 8192
IMAGE_PIPE = str(Path(GATEWAY).with_name('image_pipe.py'))


def upload_lines(file, data):
    for offset in range(0,len(data),CHUNK_BYTES):
        file.write(json.dumps({'offset':offset,'data':base64.b64encode(data[offset:offset+CHUNK_BYTES]).decode('ascii')},separators=(',',':'))+'\n')
    file.write('{"op":"commit"}\n')


def read_download(file, expected):
    first=file.readline(19901)
    if len(first)>19900:raise ValueError('image_line_too_large')
    header=json.loads(first)
    for key in ['size','digest','mime','created']:
        if header.get(key)!=expected.get(key):raise ValueError('image_metadata_changed')
    kind=expected.get('kind','image')
    if kind not in ('image','attachment') or header.get('kind')!=kind:raise ValueError('image_header_invalid')
    if type(expected.get('size')) is not int or not 0<expected['size']<=MAX_BYTES:raise ValueError('image_size_invalid')
    if kind=='attachment' and header.get('filename')!=expected.get('filename'):raise ValueError('attachment_filename_changed')
    data=bytearray();complete=False
    for _ in range(MAX_BYTES//CHUNK_BYTES+2):
        line=file.readline(19901)
        if not line:break
        if len(line)>19900:raise ValueError('image_line_too_large')
        value=json.loads(line)
        if value.get('status')=='complete':complete=True;break
        if value.get('offset')!=len(data) or not isinstance(value.get('data'),str) or len(value['data'])>10924:
            raise ValueError('image_chunk_invalid')
        chunk=base64.b64decode(value['data'],validate=True)
        if not 0<len(chunk)<=CHUNK_BYTES or len(data)+len(chunk)>expected['size']:
            raise ValueError('image_chunk_size_invalid')
        data.extend(chunk)
    if not complete or file.read(1) or len(data)!=expected['size'] or hashlib.sha256(data).hexdigest()!=expected['digest']:
        raise ValueError('image_transfer_incomplete')
    data=bytes(data)
    if kind=='image':
        if image_kind(data)[1]!=expected['mime']:raise ValueError('image_mime_changed')
    else:validate_file(data,expected['mime'],expected['filename'])
    return data


class ImageTransfer:
    def __init__(self, config, cache=None, clock=time.time):
        self.config=config;self.clock=clock
        self.cache=Path(cache or DEFAULT_HOME/'image-tmp')

    def _credentials(self):
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            helper=runpy.run_path(self.config['credential_helper'],run_name='image_credentials')
            host,user,password=helper['extract'](self.config['ssh_label'])
        if hashlib.sha256(host.strip().encode()).hexdigest()!=self.config['expected_host_digest']:
            raise ConnectionError('credential_target_changed')
        return host,user,password

    def _command(self,mode):
        return 'python3 -u '+shlex.quote(IMAGE_PIPE)+' '+shlex.quote(REMOTE_CONFIG)+' '+mode

    def _execute(self,mode,header,input_path,output_path,active=None):
        host,user,password=self._credentials()
        raw=json.dumps(header,ensure_ascii=False,separators=(',',':'))
        if len(raw.encode('utf-8'))>2048:raise ValueError('image_header_too_large')
        command=self._command(mode)
        remote='stty -echo; exec sudo -S -- sh -c '+shlex.quote('stty -icanon -echo min 1 time 0; exec '+command)
        env=os.environ.copy()
        env.update(CODEX_REMOTE_HOST=host,CODEX_REMOTE_USER=user,CODEX_REMOTE_PASSWORD=password,
                   IMESSAGE_REMOTE_COMMAND=remote,IMESSAGE_IMAGE_HEADER=raw,
                   IMESSAGE_IMAGE_HEADER_HEX=raw.encode('utf-8').hex(),
                   IMESSAGE_IMAGE_INPUT=str(input_path),IMESSAGE_IMAGE_OUTPUT=str(output_path),IMESSAGE_IMAGE_MODE=mode)
        # Header and paths only. Never put image bytes/Base64 in the environment.
        process=subprocess.Popen(['/usr/bin/expect',str(Path(__file__).with_name('ssh_images.exp'))],
            env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
        deadline=time.monotonic()+75
        try:
            while True:
                if active is not None and not active():raise ConnectionError('image_cancelled')
                if time.monotonic()>deadline:raise TimeoutError('image_transport_timeout')
                try:
                    stdout,_=process.communicate(timeout=.25);break
                except subprocess.TimeoutExpired:continue
            if process.returncode or json.loads(stdout).get('finished') is not True:
                raise ConnectionError('image_transport_unconfirmed')
        finally:
            if process.poll() is None:
                with contextlib.suppress(ProcessLookupError):os.killpg(process.pid,signal.SIGTERM)
                try:process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(ProcessLookupError):os.killpg(process.pid,signal.SIGKILL)
                    process.communicate()

    def _workspace(self):
        self.cache.mkdir(parents=True,exist_ok=True,mode=0o700);self.cache.chmod(0o700)
        return tempfile.TemporaryDirectory(prefix='transfer-',dir=self.cache)

    def put(self,event_id,epoch,created,data,active=None,filename=None,mime=None):
        if not isinstance(event_id,str) or not re.fullmatch('[A-Za-z0-9_.:-]{1,128}',event_id):raise ValueError('invalid_image_event')
        if not 0<=self.clock()-created<=120:raise ValueError('image_expired')
        if filename is None:
            _,mime=image_kind(data)
        else:
            filename=clean_filename(filename);mime=safe_mime(mime);validate_file(data,mime,filename)
        header={'epoch':int(epoch),'event_id':event_id,'created':created,'size':len(data),'digest':hashlib.sha256(data).hexdigest(),'mime':mime}
        if filename is not None:header['filename']=filename
        with self._workspace() as temp:
            input_path=Path(temp)/'request.jsonl';output_path=Path(temp)/'response.jsonl'
            with open(input_path,'w',encoding='ascii') as file:upload_lines(file,data)
            input_path.chmod(0o600);output_path.touch(mode=0o600)
            self._execute('put',header,input_path,output_path,active)
            if output_path.stat().st_size>19900:raise ValueError('image_reply_too_large')
            lines=output_path.read_text(encoding='ascii').splitlines()
            if len(lines)!=1:raise ValueError('image_put_reply_invalid')
            result=json.loads(lines[0])
            if result.get('status') not in ('pending','duplicate') or result.get('error'):
                raise ConnectionError('image_put_unconfirmed')
            return result

    def get(self,item,epoch,active=None):
        if not isinstance(item.get('image_id'),str) or not re.fullmatch('[a-f0-9]{64}',item['image_id']):raise ValueError('image_id_invalid')
        if type(item.get('size')) is not int or not 0<item['size']<=MAX_BYTES:raise ValueError('image_size_invalid')
        kind=item.get('kind','image')
        if kind=='image':
            if item.get('mime') not in ('image/jpeg','image/png'):raise ValueError('image_metadata_invalid')
        elif kind=='attachment':
            name=item.get('filename');mime=item.get('mime')
            if not isinstance(name,str) or name!=clean_filename(name) or mime!=safe_mime(mime) or mime.startswith('image/'):
                raise ValueError('attachment_metadata_invalid')
        else:raise ValueError('attachment_kind_invalid')
        if not isinstance(item.get('digest'),str) or not re.fullmatch('[a-f0-9]{64}',item['digest']):raise ValueError('image_metadata_invalid')
        if not isinstance(item.get('created'),(int,float)) or not 0<=self.clock()-item['created']<=120:raise ValueError('image_expired')
        with self._workspace() as temp:
            input_path=Path(temp)/'request.jsonl';output_path=Path(temp)/'response.jsonl'
            input_path.touch(mode=0o600);output_path.touch(mode=0o600)
            self._execute('get',{'epoch':int(epoch),'image_id':item['image_id']},input_path,output_path,active)
            if output_path.stat().st_size>MAX_BYTES*2:raise ValueError('image_reply_too_large')
            with open(output_path,encoding='ascii') as file:data=read_download(file,item)
            if not 0<=self.clock()-item['created']<=120:raise ValueError('image_expired')
            return data
