"""Host notifications through the independent Bot API namespace.

Credentials are read inside the child from the private profile, never argv.
"""
import json
import subprocess

CHILD = r'''
import json,sys,urllib.request,yaml
from pathlib import Path
request=json.load(sys.stdin)
c=yaml.safe_load((Path(request['profile'])/'blueset.telegram/config.yaml').read_text())
for admin in c['admins']:
    body={'chat_id':admin,'text':request['text']}
    if request.get('callback'):body['reply_markup']={'inline_keyboard':[[{'text':'查看综合状态','callback_data':request['callback']}]]}
    url=c['flags']['api_base_url']+c['token']+'/sendMessage'
    with urllib.request.urlopen(urllib.request.Request(url,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'}),timeout=15) as response:
        if not json.load(response).get('ok'):raise RuntimeError('notification failed')
'''

def send_notice(config,backend,text,callback=''):
    pid=subprocess.check_output(['docker','inspect','-f','{{.State.Pid}}',config['bot_api_container']],text=True,timeout=10).strip()
    if not pid.isdigit() or int(pid)<=0:raise RuntimeError('Bot API unavailable')
    payload={'profile':config['profiles'][backend],'text':text,'callback':callback}
    p=subprocess.run(['nsenter','-t',pid,'-n','/usr/bin/python3','-c',CHILD],input=json.dumps(payload),text=True,capture_output=True,timeout=40)
    if p.returncode:raise RuntimeError('notification failed')
