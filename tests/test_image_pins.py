from pathlib import Path


DOCKERFILE = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")


def test_image_pins_merged_phase_two_revisions():
    assert "efb-telegram-master.git@9bbbd973c7e5130a060a0351de61d46529942a14" in DOCKERFILE
    assert "python-comwechatrobot-http.git@687e2374dab5aa04c136c173d511ac8a8c89dbb5" in DOCKERFILE
    assert "efb-wechat-comwechat-slave.git@31549fb93b665668bbde927922f7a34be2aea108" in DOCKERFILE
    assert 'ENV EFB_IMAGE_REVISION "9bbbd97-31549fb-http687e237-' in DOCKERFILE
    assert "bridge-13d443a-watchdog-0b343fa" in DOCKERFILE
    assert 'ENV EFB_TELEGRAM_MASTER_REVISION "9bbbd973c7e5130a060a0351de61d46529942a14"' in DOCKERFILE
    assert 'ENV EFB_COMWECHAT_SLAVE_REVISION "31549fb93b665668bbde927922f7a34be2aea108"' in DOCKERFILE
