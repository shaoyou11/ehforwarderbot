from pathlib import Path


DOCKERFILE = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")


def test_image_pins_merged_telegram_and_http_revisions():
    assert "efb-telegram-master.git@1cd2f62d3b308e9f8848cfb0be36036de28134cf" in DOCKERFILE
    assert "python-comwechatrobot-http.git@65c4833b32ae33e63d59a1ad710a910d842d66c9" in DOCKERFILE
    assert 'ENV EFB_IMAGE_REVISION "1cd2f62-bb5ad0c-http65c4833-' in DOCKERFILE
