from __future__ import annotations

from deployment_verification.adapter import load_deployment_agent_output, normalize_deployment_agent_output
from deployment_verification.models import DeploymentKind, ServiceType
from deployment_verification.resource_discovery import discover_services_from_payload, infer_deployment_kind


def test_infer_infra_kind_from_stack_names():
    kind = infer_deployment_kind(
        {
            "pipeline_name": "ML-SLP-DLP-Infra-Automation",
            "infra_export": {
                "stack_names": [
                    "ff-ml-slp-dlp-devsecops-internet-alb-stack",
                    "ff-ml-slp-dlp-devsecops-ecs-cluster-stack",
                    "ff-ml-slp-dlp-devsecops-dlpapi-ecr-stack",
                    "ff-ml-slp-dlp-devsecops-dlpapi-ecs-stack",
                ]
            },
        }
    )
    assert kind == DeploymentKind.INFRA


def test_infer_fe_kind_from_cloudfront_and_frontend_bucket_vars():
    kind = infer_deployment_kind(
        {
            "pipeline_name": "ff-urf-fe-admin-center-fe-CD",
            "infra_export": {
                "stack_names": [],
                "variables": {
                    "s3BucketDso": "ff-urf-ac-devsecops-admin-ui-frontend-assets-storage-bucket",
                    "cloudFrontDistributionIdDso": "E3TC6LIZ9DFXDO",
                    "stgDomainDso": "devsecops.freyafusion.com",
                },
            },
        }
    )
    assert kind == DeploymentKind.FE


def test_discover_resources_from_infra_export(tmp_path):
    payload = {
        "pipeline_name": "ML-SLP-DLP-Infra-Automation",
        "infra_export": {
            "stack_names": ["ff-ml-slp-dlp-devsecops-ecs-cluster-stack"],
            "variables": {
                "ff-ml-slp-dlp-devsecops-ecs-cluster-stack-ECSClusterName": "ff-ml-slp-dlp-devsecops-cluster",
                "ff-ml-slp-dlp-devsecops-dlpapi-ecs-stack-ClusterServiceName": "ff-ml-slp-dlp-devsecops-dlp-service",
                "ff-ml-slp-dlp-devsecops-dlpapi-ecs-stack-TaskDefinition": "arn:aws:ecs:us-east-1:1:task-definition/ff-ml-slp-dlp-devsecops-dlp-task-definition:6",
                "ff-ml-slp-dlp-devsecops-dlpapi-ecr-stack-RepoName": "ff-ml-slp-dlp-devsecops-dlp-repository",
                "ff-ml-slp-dlp-devsecops-internet-alb-stack-ALBDNS": "example.elb.amazonaws.com",
                "ff-ml-slp-dlp-devsecops-internet-alb-stack-ApplicationLoadBalancerARN": "arn:aws:elasticloadbalancing:us-east-1:1:loadbalancer/app/x/y",
                "ff-ml-slp-dlp-devsecops-dlpextractor-S3-bucket-stack-S3BucketName": "ff-ml-slp-dlp-devsecops-dlp-processor",
            },
        },
    }
    services = discover_services_from_payload(payload)
    types = {s.service_type for s in services}
    assert ServiceType.CFN_STACK in types
    assert ServiceType.ECS_CLUSTER in types
    assert ServiceType.ECS in types
    assert ServiceType.ECR in types
    assert ServiceType.ALB in types
    assert ServiceType.S3 in types


def test_schema_14_adapter_ignores_bogus_ecs_and_uses_infra_export():
    context = load_deployment_agent_output("fixtures/mock-infra-slp-dlp-output.json")
    assert context.deployment_kind == DeploymentKind.INFRA
    assert context.deployment_status == "SUCCEEDED"
    assert any("bogus ECS" in note for note in context.discovery_notes)
    names = {s.name for s in context.services}
    assert "ML-SLP-DLP-Infra-Automation" not in names
    assert "ff-ml-slp-dlp-devsecops-dlp-service" in names
    assert "ff-ml-slp-dlp-devsecops-cluster" in names


def test_legacy_schema_still_loads():
    context = load_deployment_agent_output("fixtures/mock-deployment-agent-output.json")
    assert context.raw["schema_version"] == "1.2"
    assert context.services
    assert context.services[0].service_type == ServiceType.ECS
