"""Explicit real-SSH compatibility check against staged NAS code, isolated data only."""
import contextlib
import hashlib
import json
from pathlib import Path
import shlex
from bridge import DEFAULT_HOME,SSH
from image_transfer import ImageTransfer

STAGED='/vol1/1000/docker/efb-web-lab-20260927/imessage-code/image-staging/nas-ready/imessage'
SETUP=r'''
import sys,json,sqlite3,time,tempfile,os
from pathlib import Path
print('IMESSAGE_STDIN_READY',flush=True);sys.stdin.readline();os.umask(0o077)
production=json.loads(Path('/vol1/1000/docker/efb-web-lab-20260927/controller-state/imessage/config.json').read_text())
root=Path(tempfile.mkdtemp(prefix='imessage-image-stream-test-',dir='/tmp'))
state=root/'state';state.mkdir(mode=0o700)
notice=root/'notice';notice.mkdir(mode=0o700)
db=root/'bindings.db'
with sqlite3.connect(db) as c:
 c.executescript('CREATE TABLE chatassoc(master_uid TEXT,slave_uid TEXT); CREATE TABLE msglog(slave_message_id TEXT,text TEXT,msg_type TEXT,slave_origin_uid TEXT,sent_to TEXT,time TEXT);')
 c.execute('INSERT INTO chatassoc VALUES (?,?)',('blueset.telegram 1','blueset.wechat mock'))
(state/'state.json').write_text(json.dumps({'enabled':True,'epoch':7,'cursor':0,'last_poll':time.time()}))
(state/'image-runtime.json').write_text(json.dumps({'ready':True,'updated':time.time()}))
(root/'backend.json').write_text(json.dumps({'active':'web','phase':'idle'}))
(notice/'policy.json').write_text(json.dumps({'enabled':True,'revision':'mock-only','routes':{'mock':{'enabled':True,'sources':['imessage-mock'],'chat_id':'1','target':'blueset.wechat mock'}}}))
(notice/'sources.json').write_text(json.dumps({'imessage-mock':{'token':'test-only'}}))
cfg={'transport_mock_only':True,'state_dir':str(state),'control_code':production['control_code'],'notification_root':str(notice),'bindings_db':str(db),'backend_state':str(root/'backend.json'),'chat_id':'1','target':'blueset.wechat mock','route':'mock','source':'imessage-mock','source_token':'test-only','images_enabled':True,'image_media_dir':str(root/'media')}
Path(cfg['image_media_dir']).mkdir(mode=0o700)
(Path(cfg['image_media_dir'])/'image-runtime.json').write_text(json.dumps({'ready':True,'updated':time.time()}))
(root/'config.json').write_text(json.dumps(cfg))
sys.path.insert(0,'/vol1/1000/docker/efb-web-lab-20260927/imessage-code/image-staging/nas-ready/imessage')
from image_outbound import Outbound
data=b'\x89PNG\r\n\x1a\n'+bytes(range(256))*8192
created=time.time()-2;item=Outbound(cfg,fetch=lambda _:data).grant('f'*64,7,'synthetic-file-only',created)
print(json.dumps({'config':str(root/'config.json'),'root':str(root),'item':item,'created':created}))
'''
WRAPPER=r'''
import sys,runpy,threading,time,json
from pathlib import Path
config=Path(sys.argv[1]);mode=sys.argv[2]
assert config.parent.parent==Path('/tmp') and config.parent.name.startswith('imessage-image-stream-test-')
cfg=json.loads(config.read_text());assert cfg.get('transport_mock_only') is True
heartbeat=Path(cfg.get('image_media_dir',cfg['state_dir']))/'image-runtime.json'
def refresh():
 while True:
  tmp=heartbeat.with_suffix('.new');tmp.write_text(json.dumps({'ready':True,'updated':time.time()}));tmp.replace(heartbeat);time.sleep(2)
threading.Thread(target=refresh,daemon=True).start()
root='/vol1/1000/docker/efb-web-lab-20260927/imessage-code/image-staging/nas-ready/imessage'
sys.path.insert(0,root);sys.argv=[root+'/image_pipe.py',str(config),mode]
runpy.run_path(root+'/image_pipe.py',run_name='__main__')
'''
CLEANUP=r'''
import sys,json,shutil
from pathlib import Path
print('IMESSAGE_STDIN_READY',flush=True);r=json.loads(sys.stdin.readline());root=Path(r['root']).resolve()
assert root.parent==Path('/tmp') and root.name.startswith('imessage-image-stream-test-')
assert json.loads((root/'config.json').read_text()).get('transport_mock_only') is True
shutil.rmtree(root);print(json.dumps({'removed':True}))
'''

class Staged(ImageTransfer):
    def __init__(self,config,test):super().__init__(config);self.test=test
    def _command(self,mode):return 'python3 -u -c '+shlex.quote(WRAPPER)+' '+shlex.quote(self.test['config'])+' '+mode


def main():
    config=json.loads((DEFAULT_HOME/'config.json').read_text());ssh=SSH(config);test=None
    try:
        test=ssh.command('python3 -u -c '+shlex.quote(SETUP),{})
        data=b'\x89PNG\r\n\x1a\n'+bytes(range(256))*8192
        transfer=Staged(config,test)
        result=transfer.put('isolated-protocol-upload',7,test['created'],data)
        assert result.get('status')=='pending'
        print('Formal NAS image_pipe put compatibility: passed; isolated 2 MiB only')
        received=transfer.get(test['item'],7)
        assert hashlib.sha256(received).digest()==hashlib.sha256(data).digest()
        print('Formal NAS image_pipe get compatibility: passed; exact digest')
    finally:
        if test is not None:
            removed=ssh.command('python3 -u -c '+shlex.quote(CLEANUP),{'root':test['root']})
            print('Isolated NAS mock files removed:',removed.get('removed') is True)
    print('No production config/process/queue or chat send endpoint changed')

if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Isolated NAS image protocol check failed; private details suppressed') from None
