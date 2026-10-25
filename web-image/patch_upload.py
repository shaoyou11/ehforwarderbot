"""Fail failed chunks immediately; never replay uploads or leak open files."""
from pathlib import Path
import sys
p=Path(sys.argv[1])/'efb_wechat_slave/vendor/itchat/components/messages.py'
s=p.read_text()
old='''    for chunk in range(chunks):
        r = upload_chunk_file(self, fileDir, fileSymbol, fileSize,
                              file_, chunk, chunks, uploadMediaRequest)
    file_.close()
    if isinstance(r, dict):
        return ReturnValue(r)
    return ReturnValue(rawResponse=r)'''
new='''    try:
        for chunk in range(chunks):
            response = upload_chunk_file(self, fileDir, fileSymbol, fileSize,
                                         file_, chunk, chunks, uploadMediaRequest)
            if isinstance(response, dict):
                r = ReturnValue(response)
            else:
                response.raise_for_status()
                r = ReturnValue(rawResponse=response)
            if not r:
                return r
        return r if isinstance(r, ReturnValue) else ReturnValue(r)
    finally:
        file_.close()'''
if new not in s:
    assert s.count(old)==1, 'pinned upload implementation changed'
    p.write_text(s.replace(old,new))
