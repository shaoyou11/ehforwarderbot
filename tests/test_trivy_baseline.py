from datetime import date
from pathlib import Path


BASELINE = Path(__file__).parents[1] / ".trivyignore.yaml"


def test_trivy_baseline_is_precise_explained_and_time_limited():
    text = BASELINE.read_text(encoding="utf-8")
    entries = [line for line in text.splitlines() if line.strip().startswith("- id:")]

    assert len(entries) == 15
    assert text.count("statement:") == 15
    assert text.count("expired_at:") == 15
    assert "legacy python-telegram-bot 13 compatibility stack" in text
    for line in text.splitlines():
        if "expired_at:" not in line:
            continue
        expiry = date.fromisoformat(line.split(":", 1)[1].strip())
        assert expiry <= date(2026, 11, 30)


def test_ptb22_canary_uses_the_audited_urllib3_release():
    root = Path(__file__).parents[1]
    constraints = (root / "constraints.lock").read_text(encoding="utf-8")
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")

    assert "urllib3==2.7.0" in constraints
    assert "urllib3==2.7.0" in dockerfile
    assert "CVE-2023-43804" not in BASELINE.read_text(encoding="utf-8")
