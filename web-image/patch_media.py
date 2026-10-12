"""Translate before formatting; never retry lazy downloads during cleanup."""
from pathlib import Path


def patch_master(source):
    for template in ('Message editing is not supported.\\n\\n{exception!s}', 'Message is not sent.\\n\\n{exception!s}', 'Message is not sent.\\n\\n{exception!r}'):
        old='self._("'+template+'".format(exception=e))'
        assert old in source
        source=source.replace(old,'self._("'+template+'").format(exception=e)')
    old='                if m.file:\n                    m.file.close()'
    assert old in source
    return source.replace(old,'                m.close_loaded_file()')


def patch_message(source):
    needle='    def get_file(self) -> Optional[BinaryIO]:'
    assert needle in source
    return source.replace(needle,'''    def close_loaded_file(self):
        # Do not trigger a second download after an earlier download failure.
        if self.__file is not None:
            self.__file.close()

'''+needle)


if __name__ == '__main__':
    import sys
    root=Path(sys.argv[1])/'efb_telegram_master'
    for name,patch in [('master_message.py',patch_master),('message.py',patch_message)]:
        path=root/name
        path.write_text(patch(path.read_text()))
