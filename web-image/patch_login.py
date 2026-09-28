"""Pinned reauthentication patch: retain sessions and allow one login attempt."""
from pathlib import Path
import sys
p=Path(sys.argv[1])/'efb_wechat_slave/__init__.py'
s=p.read_text()
if 'from login_guard import request_reauth' not in s:
    needle="        self.qr_uuid: Tuple[str, int] = ('', 0)"
    assert s.count(needle)==1
    s=s.replace(needle,needle+'\n        self._reauth_lock = threading.Lock()')
    start=s.index('    def reauth(self, command=False):')
    end=s.index('    # endregion',start)
    s=s[:start]+'''    def reauth(self, command=False):
        __import__('sys').path.insert(0, '/opt/efb-backend-control')
        from login_guard import request_reauth
        return request_reauth(self, command)

'''+s[end:]
    p.write_text(s)
