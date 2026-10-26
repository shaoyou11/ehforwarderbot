"""Staged BlueBubbles ordinary-mode image adapter; not enabled by the worker."""
import contextlib
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import urllib.parse
import urllib.request
import uuid

MAX_BYTES = 25 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('image_redirect_refused')


def image_kind(data):
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_BYTES:
        raise ValueError('image_size_invalid')
    # Restrict transport formats; the receiving image decoder still validates content.
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png', 'image/png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'jpg', 'image/jpeg'
    raise ValueError('image_format_unsupported')


def multipart(chat, event, data):
    extension, mime = image_kind(data)
    name = hashlib.sha256(event.encode()).hexdigest() + '.' + extension
    fields = {'chatGuid': chat, 'tempGuid': 'imessage-image-' + str(uuid.uuid5(uuid.NAMESPACE_URL, event)),
              'name': name, 'method': 'apple-script'}
    boundary = 'efb-' + uuid.uuid4().hex
    parts = []
    for key, value in fields.items():
        parts.append(('--' + boundary + '\r\nContent-Disposition: form-data; name="' + key +
                      '"\r\n\r\n' + value + '\r\n').encode())
    parts.append(('--' + boundary + '\r\nContent-Disposition: form-data; name="attachment"; filename="' +
                  name + '"\r\nContent-Type: ' + mime + '\r\n\r\n').encode())
    parts.extend([data, ('\r\n--' + boundary + '--\r\n').encode()])
    return b''.join(parts), 'multipart/form-data; boundary=' + boundary


class Images:
    def __init__(self, config, opener=None):
        self.config = config
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def url(self, path, query=None):
        db_path = Path(self.config.get('bluebubbles_db', str(Path.home() /
                       'Library/Application Support/bluebubbles-server/config.db'))).resolve()
        with contextlib.closing(sqlite3.connect(db_path.as_uri()+'?mode=ro', uri=True)) as db:
            cfg = dict(db.execute("SELECT name,value FROM config WHERE name IN ('password','socket_port')"))
        port = int(cfg['socket_port'])
        if not 1 <= port <= 65535:
            raise ValueError('invalid_local_port')
        params = dict(query or {}, password=cfg['password'])
        return 'http://127.0.0.1:' + str(port) + '/api/v1/' + path + '?' + urllib.parse.urlencode(params)

    def download(self, message, guid):
        # Only an attachment in an incoming, exact-chat query result may be fetched.
        if message.get('isFromMe') is not False or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', guid):
            raise ValueError('attachment_not_allowed')
        matches = [a for a in message.get('attachments', []) if a.get('guid') == guid]
        if len(matches) != 1 or not str(matches[0].get('mimeType', '')).startswith('image/'):
            raise ValueError('attachment_not_allowed')
        # Conversion permits HEIC -> ordinary image, no forced/private API download.
        request = urllib.request.Request(self.url('attachment/' + guid + '/download',
                                                  {'original': 'false', 'force': 'false'}))
        with self.opener.open(request, timeout=20) as response:
            data = response.read(MAX_BYTES + 1)
        image_kind(data)
        return data

    def send(self, event, data):
        # Caller must durably mark intent before calling; never retry uncertain sends.
        payload, content_type = multipart(self.config['chat_guid'], event, data)
        request = urllib.request.Request(self.url('message/attachment'), data=payload,
                                          headers={'Content-Type': content_type})
        with self.opener.open(request, timeout=20) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('image_response_too_large')
        result = json.loads(raw)
        if result.get('status') != 200:
            raise ConnectionError('image_send_unconfirmed')
        return result.get('data')
