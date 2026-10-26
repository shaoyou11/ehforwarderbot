"""Bounded opaque attachments; filenames are labels, never filesystem paths."""
import re
import unicodedata
MAX_BYTES = 25 * 1024 * 1024

from attachment_names import filename

def valid_mime(value):
    return isinstance(value,str) and bool(re.fullmatch(r'[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+',value)) and len(value)<=100

def validate(data,mime):
    if not isinstance(data,bytes) or not 0<len(data)<=MAX_BYTES or not valid_mime(mime):raise ValueError('attachment_invalid')
    if mime=='video/mp4' and (len(data)<12 or data[4:8]!=b'ftyp'):raise ValueError('invalid_mp4')
