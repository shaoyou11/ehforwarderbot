"""Serialize reauthentication without deleting cached credentials."""
import threading

def request_reauth(channel,command=False):
    if command and getattr(getattr(channel,'bot',None),'alive',False):
        return '网页版已经登录，无需重复扫码。'
    if not channel._reauth_lock.acquire(blocking=False):
        return '正在登录，请使用最新二维码并在手机确认。'
    def authenticate():
        try:
            channel.authenticate(channel.flag('qr_reload'))
        except Exception:
            channel.logger.warning('网页版重新登录失败，可稍后重试；登录缓存已保留')
        finally:channel._reauth_lock.release()
    try:threading.Thread(target=authenticate,name='EWS reauth thread',daemon=True).start()
    except BaseException:
        channel._reauth_lock.release();raise
    return '正在获取网页版登录二维码，请稍候。'
