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
    assert "github.ref == 'refs/heads/master'" in build


def test_ptb22_branch_canary_publishes_only_isolated_tag_after_scan():
    canary = job_block("canary")
    publish = job_block("publish-ptb22-canary")

    assert "refs/heads/migration/ptb22-canary" in canary
    assert "needs: canary\n" in publish
    assert "platforms: linux/amd64\n" in publish
    assert "push: true\n" in publish
    assert "efb:ptb22-canary\n" in publish
    assert "efb:latest" not in publish
    assert "telegram.__version__ == '22.8'" in canary
    assert "pip efb:canary check" in canary


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
        assert "trivyignores: .trivyignore.yaml\n" in job


def test_dependency_audit_is_visible_but_non_blocking():
    job = job_block("dependency-audit")

    assert job.count("continue-on-error: true\n") == 2
    assert "python -m pip_audit -r constraints.lock --disable-pip --no-deps\n" in job


def test_all_third_party_actions_are_pinned_to_commit_sha():
    action_refs = re.findall(
        r"(?m)^[ \t]*(?:-[ \t]+)?uses:[ \t]+([^\s#]+)", WORKFLOW
    )

    assert action_refs
    for action_ref in action_refs:
        assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", action_ref), action_ref
