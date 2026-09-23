from pathlib import Path


DOCKERFILE = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")


def test_image_pins_login_recovery_revisions_and_preserves_http_hook():
    assert "efb-telegram-master.git@9816e65fa50c93fd5272260aff045a23e4386ed7" in DOCKERFILE
    assert "python-comwechatrobot-http.git@662ec2f66c7aebbeee39507cce6c3862edb0ef7f" in DOCKERFILE
    assert "efb-wechat-comwechat-slave.git@235b66e9d61bbe00db4ee42b359e8bd615bb8439" in DOCKERFILE
    assert 'ENV EFB_IMAGE_REVISION "9816e65-235b66e-http662ec2f-' in DOCKERFILE
    assert "bridge-13d443a-watchdog-edde14a" in DOCKERFILE
    assert 'ENV EFB_TELEGRAM_MASTER_REVISION "9816e65fa50c93fd5272260aff045a23e4386ed7"' in DOCKERFILE
    assert 'ENV EFB_COMWECHAT_SLAVE_REVISION "235b66e9d61bbe00db4ee42b359e8bd615bb8439"' in DOCKERFILE
