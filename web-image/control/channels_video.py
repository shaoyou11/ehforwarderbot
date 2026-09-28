"""Bounded direct-video adaptation; failures leave the upstream card intact."""
import ipaddress
import json
import socket
import subprocess
import tempfile
import time
from urllib.parse import urljoin, urlsplit
import requests

MAX_BYTES = 25 * 1024 * 1024
ALLOWED_HOSTS = ('qq.com',)


def allowed_url(url):
    p = urlsplit(url)
    host = (p.hostname or '').lower()
    if p.scheme not in ('http', 'https') or p.username or p.password or p.port not in (None, 80, 443):
        return False
    if not any(host == domain or host.endswith('.' + domain) for domain in ALLOWED_HOSTS):
        return False
    addresses = socket.getaddrinfo(host, p.port or (443 if p.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    return bool(addresses) and all(ipaddress.ip_address(a[4][0]).is_global for a in addresses)


def probe(path):
    p = subprocess.run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_entries',
                        'format=format_name,duration:stream=codec_type,codec_name,width,height', '-of', 'json', path],
                       capture_output=True, text=True, timeout=5)
    if p.returncode:
        return False
    result = json.loads(p.stdout)
    streams = result.get('streams', [])
    video = [s for s in streams if s.get('codec_type') == 'video']
    audio = [s for s in streams if s.get('codec_type') == 'audio']
    return ('mp4' in result.get('format', {}).get('format_name', '').split(',')
            and float(result['format'].get('duration', 0)) > 0
            and len(video) == 1 and video[0].get('codec_name') == 'h264'
            and all(s.get('codec_name') == 'aac' for s in audio))


def download(url, max_bytes=MAX_BYTES):
    """Return an owned temporary file or None. Never send or retry messages."""
    file = None
    try:
        started = time.monotonic()
        with requests.Session() as session:
            session.trust_env = False
            for redirect in range(4):
                if not allowed_url(url):
                    return None
                with session.get(url, stream=True, allow_redirects=False, timeout=(3, 5),
                                 headers={'Accept-Encoding': 'identity'}) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        url = urljoin(url, response.headers.get('Location', ''))
                        continue
                    response.raise_for_status()
                    if int(response.headers.get('Content-Length', 0)) > max_bytes:
                        return None
                    file = tempfile.NamedTemporaryFile(suffix='.mp4')
                    total = 0
                    for chunk in response.iter_content(64 * 1024):
                        total += len(chunk)
                        if total > max_bytes or time.monotonic() - started > 20:
                            file.close()
                            return None
                        file.write(chunk)
                    file.flush()
                    if not total or not probe(file.name):
                        file.close()
                        return None
                    file.seek(0)
                    return file
    except (requests.RequestException, OSError, ValueError, subprocess.SubprocessError):
        if file is not None:
            file.close()
    return None
