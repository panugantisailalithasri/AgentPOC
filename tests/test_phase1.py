from pathlib import Path

from deployment_verification.adapter import load_deployment_agent_output
from deployment_verification.aws_facade import StubAwsFacade
from deployment_verification.phase1 import run_phase1
from deployment_verification.settings import load_settings

ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "fixtures" / "mock-deployment-agent-output.json"
HEALTHY = ROOT / "fixtures" / "aws-stub-healthy.json"
UNHEALTHY = ROOT / "fixtures" / "aws-stub-unhealthy.json"


def test_healthy_ecs_passes_and_skips_phase2():
    context = load_deployment_agent_output(MOCK)
    result = run_phase1(context, StubAwsFacade(HEALTHY), load_settings())
    assert result.DeploymentHealthy is True
    assert result.Phase2Required is False
    assert result.CoverageComplete is True
    assert result.FailedChecks == []
    deployment_check = next(
        check
        for check in result.ChecksPerformed
        if check["check"] == "ECS service deployment completed successfully"
    )
    assert deployment_check["actual"]["serviceDeployment"]["status"] == "SUCCESSFUL"
    image_check = next(
        check
        for check in result.ChecksPerformed
        if check["check"] == "ECS running container image digest matches expected ECR digest"
    )
    assert image_check["status"] == "PASS"
    assert image_check["actual"]["taskDefinitionImages"][0].endswith(":latest")
    assert image_check["actual"]["runningContainers"][0]["imageDigest"] == image_check["expected"]
    assert not any("smoke" in check["check"].lower() for check in result.SkippedChecks)


def test_unhealthy_ecs_fails_desired_count():
    context = load_deployment_agent_output(MOCK)
    result = run_phase1(context, StubAwsFacade(UNHEALTHY), load_settings())
    assert result.DeploymentHealthy is False
    assert result.Phase2Required is True
    names = [check["check"] for check in result.FailedChecks]
    assert "ECS service deployment completed successfully" in names
    assert "ECS desired count equals running count" in names
    assert "ECS no unexpected stopped/failed tasks" in names


def test_successful_service_deployment_outside_window_does_not_pass():
    context = load_deployment_agent_output(MOCK)
    aws = StubAwsFacade(HEALTHY)
    key = "ff-cont-sccm-devsecops-cluster|ff-cont-sccm-devsecops-api-service"
    aws.data["ecs_service_deployments"][key][0]["createdAt"] = "2026-08-14T08:09:09Z"

    result = run_phase1(context, aws, load_settings())

    failed = next(
        check
        for check in result.FailedChecks
        if check["check"] == "ECS service deployment completed successfully"
    )
    assert failed["actual"]["serviceDeployment"] is None
    assert "verification window" in failed["reason"]


def test_legacy_rollout_state_is_used_when_service_deployment_api_is_unavailable():
    context = load_deployment_agent_output(MOCK)
    aws = StubAwsFacade(HEALTHY)
    del aws.data["ecs_service_deployments"]

    result = run_phase1(context, aws, load_settings())

    deployment_check = next(
        check
        for check in result.ChecksPerformed
        if check["check"] == "ECS service deployment completed successfully"
    )
    assert deployment_check["status"] == "PASS"
    assert "legacy DescribeServices fallback" in deployment_check["reason"]


def test_running_container_digest_mismatch_fails_artifact_check():
    context = load_deployment_agent_output(MOCK)
    aws = StubAwsFacade(HEALTHY)
    key = "ff-cont-sccm-devsecops-cluster|ff-cont-sccm-devsecops-api-service"
    for task in aws.data["ecs_tasks"][key]:
        for container in task["containers"]:
            container["imageDigest"] = "sha256:" + ("b" * 64)

    result = run_phase1(context, aws, load_settings())

    failed = next(
        check
        for check in result.FailedChecks
        if check["check"] == "ECS running container image digest matches expected ECR digest"
    )
    assert "does not match" in failed["reason"]


def test_missing_runtime_digest_is_missing_coverage_not_a_false_match():
    context = load_deployment_agent_output(MOCK)
    aws = StubAwsFacade(HEALTHY)
    key = "ff-cont-sccm-devsecops-cluster|ff-cont-sccm-devsecops-api-service"
    for task in aws.data["ecs_tasks"][key]:
        for container in task["containers"]:
            container.pop("imageDigest")

    result = run_phase1(context, aws, load_settings())

    missing = next(
        check
        for check in result.MissingChecks
        if check["check"] == "ECS running container image digest matches expected ECR digest"
    )
    assert "did not return imageDigest" in missing["reason"]
    assert result.CoverageComplete is False


def test_missing_service_is_resource_not_found():
    context = load_deployment_agent_output(MOCK)
    context.services[0].identifiers.service_name = "does-not-exist"
    result = run_phase1(context, StubAwsFacade(HEALTHY), load_settings())
    assert result.ResourceNotFound
    assert result.Phase2Required is True
    assert result.DeploymentHealthy is False


def test_unknown_service_type_is_check_missing():
    context = load_deployment_agent_output(MOCK)
    from deployment_verification.models import ExpectedService, ResourceIdentifiers, ServiceType

    context.services.append(
        ExpectedService(
            service_type=ServiceType.RDS,
            name="docs-postgres",
            identifiers=ResourceIdentifiers(extra={"db_instance_identifier": "ff-docs-docs-devsecops-docsapi-rds"}),
        )
    )
    result = run_phase1(context, StubAwsFacade(HEALTHY), load_settings())
    assert result.CoverageComplete is False
    assert result.Phase2Required is True
    assert any(check["status"] == "CHECK_MISSING" for check in result.MissingChecks)
    # ECS itself is still healthy
    assert not result.FailedChecks
