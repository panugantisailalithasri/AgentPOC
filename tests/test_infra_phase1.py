from __future__ import annotations

from deployment_verification.orchestrator import run_verification


def test_infra_offline_verification_healthy():
    result = run_verification(
        input_path="fixtures/mock-infra-slp-dlp-output.json",
        reports_dir="reports",
        stub_path="fixtures/aws-stub-infra-healthy.json",
    )
    assert result["exit_code"] == 0
    assert result["phase1"]["DeploymentHealthy"] is True
    assert result["phase1"]["CoverageComplete"] is True
