from pathlib import Path


DOCKERFILE = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")


def test_image_pins_merged_phase_two_revisions():
    assert "efb-telegram-master.git@b39ab75d8625d04194b33f29a269aca30444cb75" in DOCKERFILE
    assert "python-comwechatrobot-http.git@65c4833b32ae33e63d59a1ad710a910d842d66c9" in DOCKERFILE
    assert "efb-wechat-comwechat-slave.git@e925989b491d4f485d668abe44a92b354a36d22d" in DOCKERFILE
    assert 'ENV EFB_IMAGE_REVISION "b39ab75-e925989-http65c4833-' in DOCKERFILE
    assert "bridge-13d443a-watchdog-0b343fa" in DOCKERFILE
    assert 'ENV EFB_TELEGRAM_MASTER_REVISION "b39ab75d8625d04194b33f29a269aca30444cb75"' in DOCKERFILE
    assert 'ENV EFB_COMWECHAT_SLAVE_REVISION "e925989b491d4f485d668abe44a92b354a36d22d"' in DOCKERFILE
