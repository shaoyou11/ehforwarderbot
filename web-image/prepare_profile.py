"""Copy portable preferences without production credentials or chat identities."""
import json
import os
from pathlib import Path
import sys
import yaml

PORTABLE_FLAGS = {'retry_on_error', 'auto_locale', 'animated_stickers', 'topic_group',
                  'network_error_prompt_interval', 'send_image_as_file', 'prevent_message_removal'}


def prepare(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError('source and destination must be separate')
    if target.exists():
        raise ValueError('destination already exists; refusing overwrite')
    target.mkdir(parents=True, mode=0o700)
    pending = {}
    def load(name):
        path = source / name
        return yaml.safe_load(path.read_text()) if path.exists() else {}
    def save(name, data):
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        path.chmod(0o600)
    active = ['QQ_War.keyword_reply', 'QQ_War.message_merge', 'jiz4oh.keyword_replace', 'jiz4oh.map']
    old = load('config.yaml')
    active = [m for m in old.get('middlewares', []) if m in active]
    save('config.yaml', {'master_channel': 'blueset.telegram', 'slave_channels': ['blueset.wechat'], 'middlewares': active})
    telegram = load('blueset.telegram/config.yaml')
    flags = {k:v for k,v in telegram.get('flags', {}).items() if k in PORTABLE_FLAGS}
    save('blueset.telegram/config.yaml', {'token': '', 'admins': telegram.get('admins', []), 'flags': flags})
    save('blueset.wechat/config.yaml', {'flags': {'on_log_out': 'command', 'qr_reload': 'master_qr_code', 'puid_logs': False}})
    counts = {}
    for name in ['QQ_War.keyword_reply', 'jiz4oh.keyword_replace']:
        data = load(name + '/config.yaml') or {}
        # Rules naming a native channel/chat need explicit identity mapping.
        text = yaml.safe_dump(data)
        if 'honus.comwechat' in text or '@chatroom' in text:
            pending[name] = data
            data = {'keywords': {}}
        save(name + '/config.yaml', data)
        counts[name] = len(data.get('keywords', {}))
    merge = load('QQ_War.message_merge/config.yaml') or {}
    for key in ('samemessagegroup', 'samemessageprivate'):
        if merge.get(key): pending[key] = merge[key]
        merge[key] = []
    merge['comwechatretrive'] = False
    save('QQ_War.message_merge/config.yaml', merge)
    for name in ['author-name-spoiler.json', 'delivery-policies.json']:
        path = source / 'blueset.telegram' / name
        if not path.exists(): continue
        data = json.loads(path.read_text())
        if name == 'delivery-policies.json':
            if data.get('rules'): pending['delivery_rules'] = data['rules']
            data['rules'] = {}
        destination = target / 'blueset.telegram' / name
        destination.write_text(json.dumps(data, ensure_ascii=False, indent=2));destination.chmod(0o600)
    save('pending-mappings.yaml', pending)
    report = {'portable_flags': sorted(flags), 'keyword_counts': counts,
              'pending_mapping_sections': len(pending), 'production_token_copied': False,
              'login_enabled': False, 'binding_database_copied': False}
    (target / 'import-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report

if __name__ == '__main__':
    os.umask(0o077)
    print(json.dumps(prepare(sys.argv[1], sys.argv[2]), ensure_ascii=False))
