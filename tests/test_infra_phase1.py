from __future__ import annotations

from deployment_verification.orchestrator import run_verification


def test_infra_offline_verification_healthy():
    result = run_verification(
        input_path="fixtures/mock-infra-slp-dlp-output.json",
        reports_dir="reports",
        stub_path="fixtures/aws-stub-infra-healthy.json",
    )
    assert result["exit_code"] == 0
    assert result["verification"]["DeploymentHealthy"] is True
    assert result["verification"]["CoverageComplete"] is True
    # Phase2/Phase3 must not appear in the result
    assert "phase2" not in result
    assert "phase3" not in result
    assert "Phase2Required" not in result["verification"]
    # Each stack should now also report its inner resources
    stack_results = [
        s for s in result["verification"]["ServiceResults"]
        if s["service_type"] == "CFN_STACK"
    ]
    assert stack_results, "Expected CFN_STACK service results"
    for sr in stack_results:
        check_names = [c["check"] for c in sr["checks"]]
        assert "CloudFormation stack exists" in check_names
        assert "CloudFormation stack status healthy" in check_names
        # Every stack in the stub has resources, so this check must appear
        assert any("Stack resource exists" in n for n in check_names), (
            f"Stack {sr['name']} has no per-resource checks"
        )
