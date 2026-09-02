from pathlib import Path


DOCKERFILE = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")


def test_image_pins_merged_phase_two_revisions():
    assert "efb-telegram-master.git@3113d343c023da5f00d36c37bfa1f29316271c1f" in DOCKERFILE
    assert "python-comwechatrobot-http.git@687e2374dab5aa04c136c173d511ac8a8c89dbb5" in DOCKERFILE
    assert "efb-wechat-comwechat-slave.git@c08a867ff4acc6038476a6d57b2657069628d257" in DOCKERFILE
    assert 'ENV EFB_IMAGE_REVISION "3113d34-c08a867-http687e237-' in DOCKERFILE
    assert "bridge-13d443a-watchdog-0b343fa" in DOCKERFILE
    assert 'ENV EFB_TELEGRAM_MASTER_REVISION "3113d343c023da5f00d36c37bfa1f29316271c1f"' in DOCKERFILE
    assert 'ENV EFB_COMWECHAT_SLAVE_REVISION "c08a867ff4acc6038476a6d57b2657069628d257"' in DOCKERFILE
