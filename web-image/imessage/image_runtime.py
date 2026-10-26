"""Image sender hosted by EFB, reusing its existing logged-in slave.

Not auto-loaded. Start only after the transport and return-copy integration pass.
"""
import io
import json
from pathlib import Path
import threading
import time
from image_store import ImageStore
from bridge_control import permitted
from diagnostics import record


def verify_image(data, mime):
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(data)) as image:
        if image.format != {'image/png': 'PNG', 'image/jpeg': 'JPEG'}[mime]:
            raise ValueError('image_format_mismatch')
        if image.width*image.height > 25000000:
            raise ValueError('image_dimensions_exceeded')
        image.verify()
    # WeChat thumbnails may ignore EXIF while its original viewer honours it.
    # Bake orientation into pixels before upload; untouched photos keep their bytes.
    with Image.open(io.BytesIO(data)) as image:
        if image.getexif().get(274, 1) not in range(2, 9):
            return data
        corrected = ImageOps.exif_transpose(image)
        output = io.BytesIO()
        options = {}
        if image.info.get('icc_profile'):
            options['icc_profile'] = image.info['icc_profile']
        if mime == 'image/jpeg':
            options.update(quality=95, subsampling=0)
        # Do not carry stale EXIF thumbnails or orientation metadata into the result.
        corrected.save(output, format=image.format, **options)
        normalized = output.getvalue()
        from image_store import MAX_BYTES
        if len(normalized) > MAX_BYTES:
            raise ValueError('normalized_image_size_exceeded')
        return normalized


class ImageSender:
    def __init__(self, frontend, gateway, clock=time.time, verifier=verify_image):
        self.frontend=frontend;self.gateway=gateway;self.clock=clock;self.verifier=verifier
        self.images=ImageStore(Path(gateway.config.get('image_media_dir',Path(gateway.config['state_dir'])/'media')),clock=clock)
        self.active=None;self.last_attempt=0

    def mirror(self,item):
        import requests,yaml
        cfg=yaml.safe_load(Path(self.gateway.config['telegram_config']).read_text())
        attachment='filename' in item
        method='sendAudio' if attachment and item['mime'] in ('audio/mp4','audio/mpeg') else 'sendVideo' if attachment and item['mime']=='video/mp4' else 'sendDocument' if attachment or len(item['data'])>10*1024*1024 else 'sendPhoto'
        field={'sendAudio':'audio','sendVideo':'video','sendDocument':'document','sendPhoto':'photo'}[method]
        response=requests.post(cfg['flags']['api_base_url']+cfg['token']+'/'+method,
            data={'chat_id':self.gateway.config['chat_id'],'caption':'（iMessage）','disable_notification':'true'},
            files={field:(item.get('filename') or ('image.png' if item['mime']=='image/png' else 'image.jpg'),item['data'],item['mime'])},
            timeout=(3,15),allow_redirects=False)
        response.raise_for_status()
        if response.json().get('ok') is not True:raise ValueError('image_copy_unconfirmed')

    def allowed(self):
        cfg=self.gateway.config
        state=json.loads((Path(cfg['state_dir'])/'state.json').read_text())
        policy=self.gateway.store.policy()
        if (not permitted(cfg,images=True) or cfg.get('images_enabled') is not True or not state.get('enabled')
            or not self.gateway.ready() or policy.get('enabled') is not True
            or policy.get('routes',{}).get(cfg['route'],{}).get('enabled') is not True):
            return None
        return state['epoch']

    def tick(self, slave):
        if self.active and self.active.is_alive():return
        epoch=self.allowed()
        if epoch is None:
            self.images.cancel();return
        f=self.frontend
        queue=getattr(f.channel.master_messages,'message_queue',None)
        with f.activity_lock:
            if (not slave or not slave.bot.alive or f.inflight or (queue is not None and not queue.empty())
                or self.clock()-f.last_activity<5 or self.clock()-self.last_attempt<30):return
            # Reserve the same drain/priority guard used by normal EFB and HA sends.
            item=self.images.claim(epoch)
            if not item:return
            f.inflight+=1;self.last_attempt=self.clock()
        def send():
            nonlocal item
            outcome='blocked'
            try:
                if self.allowed()!=epoch or self.clock()>=item['expires']:return
                attachment='filename' in item
                if attachment:
                    if self.gateway.config.get('attachments_enabled') is not True:return
                    from media_types import validate
                    validate(item['data'],item['mime'])
                    if item['data'].startswith(b'caff'):
                        from audio_codec import convert
                        from media_types import filename as safe_filename
                        name=safe_filename(item.get('filename'),'语音')
                        stem=name.rsplit('.',1)[0] if '.' in name else name
                        item=dict(item,data=convert(item['data']),mime='audio/mp4',filename=safe_filename(stem+'.m4a'))
                    elif item['mime'] in ('audio/x-m4a','audio/m4a') and item['data'][4:8]==b'ftyp':
                        item=dict(item,mime='audio/mp4')
                    elif item['mime']=='audio/mp3':
                        item=dict(item,mime='audio/mpeg')
                    normalized=None
                else:normalized=self.verifier(item['data'],item['mime'])
                if normalized is not None:item=dict(item,data=normalized)
                if self.allowed()!=epoch or self.clock()>=item['expires']:return
                chats=slave.bot.search(puid=self.gateway.config['target'].split(' ',1)[1])
                if len(chats)!=1:raise ValueError('binding_unavailable')
                # No second login/client, Telegram injection or automatic retry.
                from notification_runtime import bounded_transport
                extension='png' if item['mime']=='image/png' else 'jpg'
                outcome='unknown'
                with bounded_transport(slave.bot), io.BytesIO(item['data']) as file:
                    send_method=(slave._bot_send_video if item['mime']=='video/mp4' else slave._bot_send_file) if attachment else slave._bot_send_image
                    result=send_method(chats[0],item.get('filename') or item['id']+'.'+extension,file)
                if result:outcome='sent'
                else:record(self.gateway.config,'wechat_send',ValueError('send_unconfirmed'))
            except Exception as error:
                record(self.gateway.config,'wechat_send',error)
            finally:
                self.images.finish(item['id'],outcome)
                if outcome=='sent' and self.gateway.config.get('mirror_to_telegram') is True:
                    # The image send is already committed. A copy failure never retries WeChat.
                    try:self.mirror(item)
                    except Exception as error:record(self.gateway.config,'telegram_copy',error)
                with f.activity_lock:f.inflight-=1
        self.active=threading.Thread(target=send,daemon=True,name='imessage-image-send')
        self.active.start()


def start(frontend):
    """Explicit opt-in startup, isolated failures never stop ordinary EFB."""
    path=frontend.root/'imessage/image-runtime-config.json'
    if not path.is_file():return None
    from gateway import Gateway, atomic
    import fcntl
    cfg=json.loads(path.read_text())
    if cfg.get('images_enabled') is not True:return None
    sender=ImageSender(frontend,Gateway(cfg,enable_mirror=False))
    if cfg.get('remote_control_enabled') is True:
        from bridge_ui import BridgeUI
        frontend.imessage_controls=BridgeUI(frontend,sender.gateway)
    def loop():
        root=Path(cfg.get('image_media_dir',cfg['state_dir']));guard=open(root/'image-consumer.lock','a')
        try:fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:guard.close();return
        sender.images.recover()
        while True:
            try:
                from ehforwarderbot import coordinator
                slave=coordinator.slaves.get('blueset.wechat')
                atomic(root/'image-runtime.json',{'ready':bool(slave and slave.bot.alive),'updated':time.time()})
                sender.tick(slave)
            except Exception as error:
                record(cfg,'runtime',error)
                atomic(root/'image-runtime.json',{'ready':False,'updated':time.time()})
            time.sleep(2)
    threading.Thread(target=loop,daemon=True,name='imessage-images').start()
    return sender
