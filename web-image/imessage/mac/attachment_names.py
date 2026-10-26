"""Shared NAS/Mac filename canonicalization. Labels only, never incoming paths."""
import re
import unicodedata

MAX_FILENAME_BYTES = 240


def filename(value, default='attachment.bin'):
    if not isinstance(value,str):return default
    name=unicodedata.normalize('NFC',value).replace('\\','/').rsplit('/',1)[-1]
    name=''.join(c for c in name if not unicodedata.category(c).startswith('C') and c!='"')
    name=name.strip(' .')[:180].strip(' .')
    if len(name.encode('utf-8'))>MAX_FILENAME_BYTES:
        match=re.search(r'\.[A-Za-z0-9]{1,16}$',name)
        suffix=match.group(0) if match else ''
        stem=name[:-len(suffix)] if suffix else name
        stem=stem.encode('utf-8')[:MAX_FILENAME_BYTES-len(suffix)].decode('utf-8','ignore').strip(' .')
        name=stem+suffix
    return name or default
