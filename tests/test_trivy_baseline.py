from datetime import date
from pathlib import Path


BASELINE = Path(__file__).parents[1] / ".trivyignore.yaml"


def test_trivy_baseline_is_precise_explained_and_time_limited():
    text = BASELINE.read_text(encoding="utf-8")
    entries = [line for line in text.splitlines() if line.strip().startswith("- id:")]

    assert len(entries) == 16
    assert text.count("statement:") == 16
    assert text.count("expired_at:") == 16
    assert "legacy python-telegram-bot 13 compatibility stack" in text
    for line in text.splitlines():
        if "expired_at:" not in line:
            continue
        expiry = date.fromisoformat(line.split(":", 1)[1].strip())
        assert expiry <= date(2026, 11, 30)
