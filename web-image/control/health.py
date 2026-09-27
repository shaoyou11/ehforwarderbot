"""Bounded, real Bot API health check independent of PTB async adapters."""
from pathlib import Path
import requests,yaml

def bot_api_status(config_path='/data/profiles/web/blueset.telegram/config.yaml'):
    try:
        c=yaml.safe_load(Path(config_path).read_text())
        response=requests.post(c['flags']['api_base_url']+c['token']+'/getMe',timeout=(2,5))
        response.raise_for_status();data=response.json()
        if data.get('ok') is True and data.get('result',{}).get('is_bot') is True:return '可访问'
    except (OSError,KeyError,TypeError,ValueError,yaml.YAMLError,requests.RequestException):pass
    return '检查失败（不等于微信掉线）'
