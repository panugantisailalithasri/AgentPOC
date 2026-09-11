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
    # Phase2/Phase3 must NOT appear anywhere in the result
    assert "phase2" not in result
    assert "phase3" not in result
    assert "Phase2Required" not in result["verification"]
    # Every CFN stack gets exactly two checks: exists + status healthy
    stack_results = [
        s for s in result["verification"]["ServiceResults"]
        if s["service_type"] == "CFN_STACK"
    ]
    assert stack_results, "Expected CFN_STACK service results"
    for sr in stack_results:
        check_names = [c["check"] for c in sr["checks"]]
        assert "CloudFormation stack exists" in check_names
        assert "CloudFormation stack status healthy" in check_names
    # Resources from infra_export.variables are verified separately
    # (ECS cluster, ECS services, ECR repos, ALB, S3 bucket)
    checked_types = {
        s["service_type"]
        for s in result["verification"]["ServiceResults"]
    }
    assert "ECS_CLUSTER" in checked_types
    assert "ECR" in checked_types
    assert "ALB" in checked_types
    assert "S3" in checked_types
