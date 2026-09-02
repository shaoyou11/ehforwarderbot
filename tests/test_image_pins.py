from pathlib import Path


DOCKERFILE = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")


def test_image_pins_merged_phase_two_revisions():
    assert "efb-telegram-master.git@623c1b749b2ca572cea6ce1cb132a89467d1f51a" in DOCKERFILE
    assert "python-comwechatrobot-http.git@687e2374dab5aa04c136c173d511ac8a8c89dbb5" in DOCKERFILE
    assert "efb-wechat-comwechat-slave.git@c08a867ff4acc6038476a6d57b2657069628d257" in DOCKERFILE
    assert 'ENV EFB_IMAGE_REVISION "623c1b7-c08a867-http687e237-' in DOCKERFILE
    assert "bridge-13d443a-watchdog-0b343fa" in DOCKERFILE
    assert 'ENV EFB_TELEGRAM_MASTER_REVISION "623c1b749b2ca572cea6ce1cb132a89467d1f51a"' in DOCKERFILE
    assert 'ENV EFB_COMWECHAT_SLAVE_REVISION "c08a867ff4acc6038476a6d57b2657069628d257"' in DOCKERFILE
