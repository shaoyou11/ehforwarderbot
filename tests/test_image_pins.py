from pathlib import Path


DOCKERFILE = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")


def test_image_pins_merged_phase_two_revisions():
    assert "efb-telegram-master.git@34db342d7b921b46d9713d7eba5f761d5a103e9f" in DOCKERFILE
    assert "python-comwechatrobot-http.git@687e2374dab5aa04c136c173d511ac8a8c89dbb5" in DOCKERFILE
    assert "efb-wechat-comwechat-slave.git@3bd72b0f987abf894a60d09478d1d07d3a3ba348" in DOCKERFILE
    assert 'ENV EFB_IMAGE_REVISION "34db342-3bd72b0-http687e237-' in DOCKERFILE
    assert "bridge-13d443a-watchdog-0b343fa" in DOCKERFILE
    assert 'ENV EFB_TELEGRAM_MASTER_REVISION "34db342d7b921b46d9713d7eba5f761d5a103e9f"' in DOCKERFILE
    assert 'ENV EFB_COMWECHAT_SLAVE_REVISION "3bd72b0f987abf894a60d09478d1d07d3a3ba348"' in DOCKERFILE
