from pathlib import Path
import re

WORKFLOW = (Path(__file__).parents[1] / ".github" / "workflows" / "ci.yaml").read_text(
    encoding="utf-8"
)


def job_block(name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
        WORKFLOW,
    )
    assert match, f"missing job: {name}"
    return match.group(1)


def test_pr_canary_is_amd64_only_and_never_pushes():
    build = job_block("canary")

    assert "platforms: linux/amd64\n" in build
    assert "push: false\n" in build
    assert "load: true\n" in build


def test_release_is_dual_arch_with_sbom_and_provenance():
    build = job_block("deploy")

    assert "platforms: linux/amd64,linux/arm64\n" in build
    assert "push: true\n" in build
    assert "sbom: true\n" in build
    assert "provenance: mode=max\n" in build


def test_weekly_arm64_canary_builds_without_publishing():
    assert re.search(r"(?m)^  schedule:\n", WORKFLOW)
    job = job_block("arm64-canary")

    assert "if: github.event_name == 'schedule'\n" in job
    assert "platforms: linux/arm64\n" in job
    assert "push: false\n" in job
    assert "load: true\n" in job


def test_canaries_block_only_fixable_high_and_critical_vulnerabilities():
    for job_name in ("canary", "arm64-canary"):
        job = job_block(job_name)
        assert "uses: aquasecurity/trivy-action@" in job
        assert "ignore-unfixed: true\n" in job
        assert "severity: HIGH,CRITICAL\n" in job
        assert "exit-code: 1\n" in job


def test_dependency_audit_is_visible_but_non_blocking():
    job = job_block("dependency-audit")

    assert job.count("continue-on-error: true\n") == 2
    assert "pip-audit" in job
