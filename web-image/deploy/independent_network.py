"""Prepare Feiniu network config after backups; never prints private YAML.

Usage: python independent_network.py NATIVE_ROOT WEB_ROOT WEB_IMAGE_DIGEST
Does not stop/start containers. Rollback uses the emitted backup directory.
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path
import yaml


def environment(service,changes):
    value=service.get('environment',{})
    if isinstance(value,list):value=dict(v.split('=',1) for v in value)
    value.update(changes);service['environment']=value


def prepare(native,web,image):
    if not image.startswith('ghcr.io/shaoyou11/efb-web-standby@sha256:'):raise ValueError('pinned published web image required')
    stamp=time.strftime('%Y%m%d-%H%M%S');backup=web/('network-backup-'+stamp);backup.mkdir(mode=0o700)
    paths=[native/'docker-compose.yaml',web/'docker-compose.yaml',web/'login-test.compose.json',web/'controller-config.json',native/'operations/telegram_notify.py']
    config=json.loads((web/'controller-config.json').read_text())
    paths += [Path(p)/'blueset.telegram/config.yaml' for p in config['profiles'].values()]
    video_compose=native.parent/'efb-video-lab/compose.cookie.yaml'
    if video_compose.exists():paths.append(video_compose)
    manifest={}
    for index,path in enumerate(paths):
        dest=backup/str(index);shutil.copy2(path,dest);manifest[str(path)]=str(dest)
    (backup/'manifest.json').write_text(json.dumps(manifest));os.chmod(backup/'manifest.json',0o600)
    if video_compose.exists():
        video=yaml.safe_load(video_compose.read_text())
        for service in video.get('services',{}).values():service['restart']='no'
        video_compose.write_text(yaml.safe_dump(video,allow_unicode=True,sort_keys=False))
    n=yaml.safe_load((native/'docker-compose.yaml').read_text());w=yaml.safe_load((web/'docker-compose.yaml').read_text())
    bot=n['services']['telegram-bot-api'];bot.pop('network_mode',None);bot.pop('depends_on',None)
    bot['networks']={'efb_lan':{'ipv4_address':'192.168.12.115'}};bot['dns']=['192.168.12.2'];bot['sysctls']={'net.ipv6.conf.all.disable_ipv6':'1'}
    environment(bot,{'TELEGRAM_HTTP_IP_ADDRESS':'0.0.0.0'})
    bot['image']='ghcr.io/shaoyou11/telegram-bot-api@sha256:3ef7ad38566ac54cca97d787ff22d293a7ffdc8ce7031791d3e59fa78c5de4d6'
    n['services']['efb']['image']='ghcr.io/shaoyou11/efb@sha256:6840ccb49aaf56ffc9b933ff78e4e71d278b47740aa42e88354e05f02a045fab'
    for key in ['efb','wechat-session-watchdog']:environment(n['services'][key],{'TELEGRAM_BOT_API':'http://192.168.12.115:8081'})
    # The host worker is the sole owner of native backend startup.
    for key in ['comwechat','efb','wechat-session-watchdog']:n['services'][key]['restart']='no'
    service=w['services']['web-standby'];service.pop('network_mode',None);service['image']=image
    service['networks']={'shared_lan':{'ipv4_address':'192.168.12.116'}};service['dns']=['192.168.12.2'];service['sysctls']={'net.ipv6.conf.all.disable_ipv6':'1'}
    service['volumes']=[str(web/'personalized-v2')+':/data',str(native/'telegram-bot-api')+':/var/lib/telegram-bot-api:ro',str(web/'controller-state')+':/backend-control']
    service['restart']='no';w['networks']={'shared_lan':{'external':True,'name':'efb2026_efb_lan'}}
    for path in config['profiles'].values():
        p=Path(path)/'blueset.telegram/config.yaml';c=yaml.safe_load(p.read_text());flags=c.setdefault('flags',{})
        flags.update(api_base_url='http://192.168.12.115:8081/bot',api_base_file_url='http://192.168.12.115:8081/file/bot',local_bot_api=True)
        p.write_text(yaml.safe_dump(c,allow_unicode=True,sort_keys=False))
    config.update(network_independent=True,native_container='efb2026-comwechat-1',bot_api_container='efb2026-telegram-bot-api-1',native_aux_unit='efb-video-lab.service',native_aux_container='efb-video-lab',controller_code=str(web/'controller-code'))
    (web/'controller-config.json').write_text(json.dumps(config));os.chmod(web/'controller-config.json',0o600)
    (native/'docker-compose.yaml').write_text(yaml.safe_dump(n,allow_unicode=True,sort_keys=False))
    (web/'docker-compose.yaml').write_text(yaml.safe_dump(w,allow_unicode=True,sort_keys=False))
    (web/'login-test.compose.json').write_text(json.dumps(w))
    p=native/'operations/telegram_notify.py';s=p.read_text();needle='    import requests\n    import yaml\n'
    if 'EFB_BACKEND_HOST_CONFIG' not in s:
        assert s.count(needle)==1
        s=s.replace(needle,'''    # Host jobs remain reachable while either frontend is stopped.
    host_config=Path(os.getenv('EFB_BACKEND_HOST_CONFIG',str(efb_root()/'operations/backend-controller.json')))
    if host_config.exists():
        config=json.loads(host_config.read_text())
        if config.get('network_independent'):
            import sys
            sys.path.insert(0,config['controller_code'])
            from transport import send_notice
            state=json.loads((Path(config['control_root'])/'state.json').read_text())
            backend=state['active']
            send_notice(config,backend,text,'efbview:status' if callback_data and backend=='web' else callback_data)
            return True
'''+needle);p.write_text(s)
    link=native/'operations/backend-controller.json'
    if not link.exists():link.symlink_to(web/'controller-config.json')
    return backup

if __name__=='__main__':print(prepare(Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3]))
