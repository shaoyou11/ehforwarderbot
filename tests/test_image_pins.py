from pathlib import Path


DOCKERFILE = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")


def test_image_pins_merged_phase_two_revisions():
    assert "efb-telegram-master.git@a95a18c1d2ba0f3c67bee4bf196f672b2bc8a6f0" in DOCKERFILE
    assert "python-comwechatrobot-http.git@687e2374dab5aa04c136c173d511ac8a8c89dbb5" in DOCKERFILE
    assert "efb-wechat-comwechat-slave.git@f208dbc0103c22c467a761407935e309b79d1ee5" in DOCKERFILE
    assert 'ENV EFB_IMAGE_REVISION "a95a18c-f208dbc-http687e237-' in DOCKERFILE
    assert "bridge-13d443a-watchdog-0b343fa" in DOCKERFILE
    assert 'ENV EFB_TELEGRAM_MASTER_REVISION "a95a18c1d2ba0f3c67bee4bf196f672b2bc8a6f0"' in DOCKERFILE
    assert 'ENV EFB_COMWECHAT_SLAVE_REVISION "f208dbc0103c22c467a761407935e309b79d1ee5"' in DOCKERFILE
