"""Private local ingress for opted-in producers; not a Bot API proxy."""
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from notification_queue import Store, Rejected

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):pass  # Never log bodies, source keys or URLs.
    def do_POST(self):
        status=202
        try:
            if self.path!='/v1/notifications':raise Rejected('not_found',404)
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=20000:raise Rejected('invalid_size',413)
            self.connection.settimeout(3)
            data=json.loads(self.rfile.read(size))
            if not isinstance(data,dict):raise Rejected('invalid_payload',400)
            auth=self.headers.get('Authorization','')
            if not auth.startswith('Bearer '):raise Rejected('unauthorized',401)
            result=self.server.store.submit(data,auth[7:])
        except Rejected as e:status=e.status;result={'error':e.reason}
        except (ValueError,TypeError,KeyError):status=400;result={'error':'invalid_payload'}
        except Exception:status=503;result={'error':'temporarily_unavailable'}
        body=json.dumps(result).encode()
        self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers()
        try:self.wfile.write(body)
        except OSError:pass

if __name__=='__main__':
    os.umask(0o077)
    root=Path(os.environ.get('EFB_NOTICE_ROOT','/backend-control/notifications'))
    store=Store(root,os.environ.get('EFB_NOTICE_BINDINGS','/data/profiles/web/blueset.telegram/tgdata.db'));store.initialize()
    server=HTTPServer((os.environ.get('EFB_NOTICE_LISTEN','127.0.0.1'),int(os.environ.get('EFB_NOTICE_PORT','8768'))),Handler)
    server.timeout=3;server.store=store;server.serve_forever()
