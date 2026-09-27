"""Compile the shipped Simplified Chinese PO without build-time dependencies."""
import ast
import struct
from pathlib import Path


def read_po(path):
    entries = {}
    for block in path.read_text().split('\n\n'):
        fields = {}; field = None
        if '#, fuzzy' in block:
            continue
        for line in block.splitlines():
            if line.startswith('#') or not line.strip():
                continue
            if line.startswith('"'):
                fields[field] += ast.literal_eval(line)
            else:
                field, value = line.split(' ', 1)
                fields[field] = ast.literal_eval(value)
        if 'msgid' not in fields:
            continue
        key = fields['msgid']
        if 'msgid_plural' in fields:
            key += '\0' + fields['msgid_plural']
            value = fields.get('msgstr[0]', '')
        else:
            value = fields.get('msgstr', '')
        if value:
            entries[key] = value
    return entries


def write_mo(entries, path):
    pairs = sorted((k.encode(),v.encode()) for k,v in entries.items())
    count=len(pairs); start=28+16*count
    keys=b''; values=b''; key_table=[]; value_table=[]
    for k,v in pairs:
        key_table.append((len(k),start+len(keys)));keys+=k+b'\0'
    for k,v in pairs:
        value_table.append((len(v),start+len(keys)+len(values)));values+=v+b'\0'
    header=struct.pack('<7I',0x950412de,0,count,28,28+8*count,0,0)
    tables=b''.join(struct.pack('<2I',*row) for row in key_table+value_table)
    path.write_bytes(header+tables+keys+values)


OVERRIDES = {
    'efb_telegram_master': {
        '(unsupported) [Edited]': '（不支持的消息类型）[已编辑]',
        'Failed to create topic for {name} in the group.\nPlease make sure the bot has the right.\nYou can send /init_topics to create again.': '无法为 {name} 创建话题。\n请确认机器人拥有管理话题权限。\n可发送 /init_topics 重试。',
        'Sent a video.': '发送了一段视频。',
        'The topic {topic_name} ({topic_id}) is linked to:': '话题 {topic_name}（{topic_id}）已绑定至：',
        'This chat is not managed by this bot. Update failed': '此会话不由本机器人管理，更新失败。',
    },
    'efb_wechat_slave': {
        'Your mobile WeChat client is offline for too long. Please ensure your mobileWeChat client is always online.': '手机微信离线时间过长，请保持手机微信在线。',
        'File size exceeds 25MB limit. File size: {size:.2f}MB': '文件超过 25 MB 上限，当前大小为 {size:.2f} MB。',
        'Replace the emoticon in WeChat to emoji. If disabled, the emoticon will be shown as text in square brackets. Enabled by default.': '将微信表情转换为 Emoji。关闭后以方括号内的文字显示，默认开启。',
        'WeChat Channels': '微信视频号',
        '[Hongbao image, please check your phone.]': '[红包图片，请在手机微信查看。]',
    },
}


def compile_web(root, output=None):
    for package, overrides in OVERRIDES.items():
        path=Path(root)/package/'locale/zh_CN/LC_MESSAGES'/f'{package}.po'
        entries=read_po(path)
        entries.update(overrides)
        destination=Path(output)/f'{package}.mo' if output else path.with_suffix('.mo')
        write_mo(entries,destination)

if __name__=='__main__':
    import sys
    compile_web(sys.argv[1],sys.argv[2] if len(sys.argv)>2 else None)
