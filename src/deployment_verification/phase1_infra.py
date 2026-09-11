from __future__ import annotations

from typing import Any

from deployment_verification.aws_facade import AwsFacade
from deployment_verification.models import CheckResult, CheckStatus, ExpectedService


def run_cfn_stack_checks(service: ExpectedService, aws: AwsFacade) -> list[CheckResult]:
    stack_name = service.identifiers.extra.get("stack_name") or service.name
    resource = f"cfn://{stack_name}"
    described = aws.describe_cloudformation_stack(stack_name)
    if described is None:
        return [
            CheckResult(
                check="CloudFormation stack exists",
                status=CheckStatus.RESOURCE_NOT_FOUND,
                resource=resource,
                reason="DescribeStacks did not return the stack",
                expected=stack_name,
                actual=None,
            )
        ]
    status = str(described.get("StackStatus") or "")
    ok = status.endswith("_COMPLETE") and not status.startswith("ROLLBACK") and "FAILED" not in status
    outputs = described.get("Outputs") or []
    return [
        CheckResult(
            check="CloudFormation stack exists",
            status=CheckStatus.PASS,
            resource=resource,
            reason="Stack was found",
            expected=stack_name,
            actual={"StackStatus": status},
        ),
        CheckResult(
            check="CloudFormation stack status healthy",
            status=CheckStatus.PASS if ok else CheckStatus.FAIL,
            resource=resource,
            reason=f"Stack status is {status}",
            expected="*_COMPLETE (non-rollback)",
            actual=status,
            evidence={"output_count": len(outputs)},
        ),
        CheckResult(
            check="CloudFormation stack has outputs",
            status=CheckStatus.PASS if outputs else CheckStatus.INCONCLUSIVE,
            resource=resource,
            reason=(
                f"Stack exposes {len(outputs)} output(s)"
                if outputs
                else "Stack has no outputs (may be expected for some stacks)"
            ),
            expected=">= 0 outputs",
            actual={"Outputs": outputs[:20]},
        ),
    ]


def run_ecs_cluster_checks(service: ExpectedService, aws: AwsFacade) -> list[CheckResult]:
    cluster = service.identifiers.cluster or service.name
    resource = f"ecs-cluster://{cluster}"
    described = aws.describe_ecs_cluster(cluster)
    if described is None:
        return [
            CheckResult(
                check="ECS cluster exists",
                status=CheckStatus.RESOURCE_NOT_FOUND,
                resource=resource,
                reason="DescribeClusters did not return the cluster",
                expected=cluster,
                actual=None,
            )
        ]
    status = described.get("status")
    active_services = int(described.get("activeServicesCount") or 0)
    running_tasks = int(described.get("runningTasksCount") or 0)
    return [
        CheckResult(
            check="ECS cluster exists",
            status=CheckStatus.PASS,
            resource=resource,
            reason="Cluster was found",
            expected=cluster,
            actual={"status": status, "activeServicesCount": active_services, "runningTasksCount": running_tasks},
        ),
        CheckResult(
            check="ECS cluster status is ACTIVE",
            status=CheckStatus.PASS if status == "ACTIVE" else CheckStatus.FAIL,
            resource=resource,
            reason=f"Cluster status is {status}",
            expected="ACTIVE",
            actual=status,
        ),
        CheckResult(
            check="ECS cluster has active services",
            status=CheckStatus.PASS if active_services > 0 else CheckStatus.FAIL,
            resource=resource,
            reason=f"activeServicesCount={active_services}",
            expected="> 0",
            actual=active_services,
        ),
    ]


def run_ecr_checks(service: ExpectedService, aws: AwsFacade) -> list[CheckResult]:
    repo = (
        service.identifiers.extra.get("repository_name")
        or service.expected_artifact.image_repository
        or service.name
    )
    resource = f"ecr://{repo}"
    described = aws.describe_ecr_repository(repo)
    if described is None:
        return [
            CheckResult(
                check="ECR repository exists",
                status=CheckStatus.RESOURCE_NOT_FOUND,
                resource=resource,
                reason="DescribeRepositories did not return the repository",
                expected=repo,
                actual=None,
            )
        ]
    images = aws.list_ecr_images(repo, max_results=5)
    return [
        CheckResult(
            check="ECR repository exists",
            status=CheckStatus.PASS,
            resource=resource,
            reason="Repository was found",
            expected=repo,
            actual={
                "repositoryName": described.get("repositoryName"),
                "repositoryUri": described.get("repositoryUri"),
            },
        ),
        CheckResult(
            check="ECR repository contains images",
            status=CheckStatus.PASS if images else CheckStatus.FAIL,
            resource=resource,
            reason=(
                f"Found {len(images)} recent image(s)"
                if images
                else "Repository exists but no images were returned"
            ),
            expected=">= 1 image",
            actual={"images": images},
        ),
    ]


def run_alb_checks(service: ExpectedService, aws: AwsFacade) -> list[CheckResult]:
    arn = service.identifiers.extra.get("load_balancer_arn")
    dns = service.identifiers.extra.get("dns_name")
    resource = f"alb://{dns or arn or service.name}"
    described = aws.describe_load_balancer(arn=arn, dns_name=dns)
    if described is None:
        return [
            CheckResult(
                check="ALB exists",
                status=CheckStatus.RESOURCE_NOT_FOUND,
                resource=resource,
                reason="DescribeLoadBalancers did not return the load balancer",
                expected={"arn": arn, "dns_name": dns},
                actual=None,
            )
        ]
    state = ((described.get("State") or {}) if isinstance(described.get("State"), dict) else {}).get("Code")
    if state is None and isinstance(described.get("State"), str):
        state = described.get("State")
    lb_arn = described.get("LoadBalancerArn") or arn
    listeners = aws.describe_alb_listeners(lb_arn) if lb_arn else []
    target_groups = aws.describe_alb_target_groups(lb_arn) if lb_arn else []

    results = [
        CheckResult(
            check="ALB exists",
            status=CheckStatus.PASS,
            resource=resource,
            reason="Load balancer was found",
            expected={"arn": arn, "dns_name": dns},
            actual={
                "LoadBalancerArn": described.get("LoadBalancerArn"),
                "DNSName": described.get("DNSName"),
                "State": state,
            },
        ),
        CheckResult(
            check="ALB state is active",
            status=CheckStatus.PASS if str(state).lower() == "active" else CheckStatus.FAIL,
            resource=resource,
            reason=f"ALB state is {state}",
            expected="active",
            actual=state,
        ),
        CheckResult(
            check="ALB has listeners",
            status=CheckStatus.PASS if listeners else CheckStatus.FAIL,
            resource=resource,
            reason=f"Found {len(listeners)} listener(s)" if listeners else "No listeners on ALB",
            expected=">= 1 listener",
            actual={"listeners": listeners},
        ),
        CheckResult(
            check="ALB has target groups",
            status=CheckStatus.PASS if target_groups else CheckStatus.FAIL,
            resource=resource,
            reason=(
                f"Found {len(target_groups)} target group(s)"
                if target_groups
                else "No target groups attached to ALB"
            ),
            expected=">= 1 target group",
            actual={"target_groups": target_groups},
        ),
    ]

    # Deeper: target health on first TG (same depth idea as ECS TG checks)
    if target_groups:
        tg_arn = target_groups[0].get("TargetGroupArn")
        if tg_arn:
            health = aws.describe_target_health(tg_arn)
            healthy = [
                item
                for item in health
                if str((item.get("TargetHealth") or {}).get("State") or item.get("state") or "").lower()
                == "healthy"
            ]
            results.append(
                CheckResult(
                    check="ALB target group has healthy targets",
                    status=CheckStatus.PASS if healthy else CheckStatus.FAIL,
                    resource=tg_arn,
                    reason=(
                        f"{len(healthy)}/{len(health)} targets healthy"
                        if health
                        else "No targets registered"
                    ),
                    expected=">= 1 healthy target",
                    actual={"targets": health},
                )
            )
    return results


def run_s3_infra_checks(
    service: ExpectedService,
    aws: AwsFacade,
    verification_start: str | None = None,
    verification_end: str | None = None,
    require_recent_objects: bool = False,
) -> list[CheckResult]:
    bucket = service.identifiers.extra.get("bucket_name") or service.name
    resource = f"s3://{bucket}"
    exists = aws.head_s3_bucket(bucket)
    if not exists:
        return [
            CheckResult(
                check="S3 bucket exists",
                status=CheckStatus.RESOURCE_NOT_FOUND,
                resource=resource,
                reason="HeadBucket failed or bucket was not found",
                expected=bucket,
                actual=None,
            )
        ]
    results = [
        CheckResult(
            check="S3 bucket exists",
            status=CheckStatus.PASS,
            resource=resource,
            reason="Bucket was found",
            expected=bucket,
            actual={"bucket": bucket},
        )
    ]
    # Always sample whether the bucket has any objects (infra storage should usually not be empty
    # after a successful provision+seed; FE requires windowed uploads).
    if verification_start and verification_end:
        recent = aws.list_s3_objects_updated_between(bucket, verification_start, verification_end)
        if require_recent_objects:
            results.append(
                CheckResult(
                    check="S3 objects uploaded in verification window",
                    status=CheckStatus.PASS if recent else CheckStatus.FAIL,
                    resource=resource,
                    reason=(
                        f"Found {len(recent)} object(s) updated in verification window"
                        if recent
                        else "No objects updated in verification window"
                    ),
                    expected={"window_start": verification_start, "window_end": verification_end},
                    actual={
                        "recent_object_count": len(recent),
                        "sample_keys": [item.get("Key") for item in recent[:5]],
                    },
                )
            )
        else:
            results.append(
                CheckResult(
                    check="S3 bucket object activity in verification window",
                    status=CheckStatus.PASS if recent else CheckStatus.INCONCLUSIVE,
                    resource=resource,
                    reason=(
                        f"Found {len(recent)} object update(s) in window"
                        if recent
                        else "No object updates in window (acceptable for empty/data buckets)"
                    ),
                    expected="optional for infra data buckets",
                    actual={"recent_object_count": len(recent)},
                )
            )
    return results


def run_infra_service_checks(
    service: ExpectedService,
    aws: AwsFacade,
    verification_start: str,
    verification_end: str,
) -> list[CheckResult] | None:
    """Return checks for infra-oriented types, or None if type is not handled here."""
    from deployment_verification.models import ServiceType

    if service.service_type == ServiceType.CFN_STACK:
        return run_cfn_stack_checks(service, aws)
    if service.service_type == ServiceType.ECS_CLUSTER:
        return run_ecs_cluster_checks(service, aws)
    if service.service_type == ServiceType.ECR:
        return run_ecr_checks(service, aws)
    if service.service_type == ServiceType.ALB:
        if service.identifiers.target_group_arn:
            return []
        return run_alb_checks(service, aws)
    if service.service_type == ServiceType.S3:
        return run_s3_infra_checks(
            service, aws, verification_start, verification_end, require_recent_objects=False
        )
    return None
