from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from deployment_verification.aws_facade import AwsFacade
from deployment_verification.models import CheckResult, CheckStatus, ExpectedService


def _normalize_digest(image_digest: str | None) -> str | None:
    if not image_digest:
        return None
    return image_digest if image_digest.startswith("sha256:") else f"sha256:{image_digest}"


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _in_verification_window(item: dict[str, Any], start: str, end: str) -> bool:
    created = _parse_timestamp(item.get("createdAt") or item.get("startedAt"))
    window_start = _parse_timestamp(start)
    window_end = _parse_timestamp(end)
    return bool(created and window_start and window_end and window_start <= created <= window_end)


def run_ecs_checks(
    service: ExpectedService,
    aws: AwsFacade,
    settings: dict[str, Any],
    verification_start: str,
    verification_end: str,
) -> list[CheckResult]:
    cluster = service.identifiers.cluster or ""
    service_name = service.identifiers.service_name or ""
    resource = f"ecs://{cluster}/{service_name}"
    described = aws.describe_ecs_service(cluster, service_name)

    if described is None:
        detail = getattr(aws, "last_error", None) or "DescribeServices did not return the expected ECS service"
        return [
            CheckResult(
                check="ECS cluster/service exists",
                status=CheckStatus.RESOURCE_NOT_FOUND,
                resource=resource,
                reason=detail,
                expected={"cluster": cluster, "service_name": service_name},
                actual=None,
            )
        ]

    results: list[CheckResult] = [
        CheckResult(
            check="ECS cluster/service exists",
            status=CheckStatus.PASS,
            resource=resource,
            reason="ECS service was found",
            expected={"cluster": cluster, "service_name": service_name},
            actual={"status": described.get("status")},
        )
    ]

    status = described.get("status")
    results.append(
        CheckResult(
            check="ECS service status is ACTIVE",
            status=CheckStatus.PASS if status == "ACTIVE" else CheckStatus.FAIL,
            resource=resource,
            reason="Service status is ACTIVE" if status == "ACTIVE" else f"Service status is {status}",
            expected="ACTIVE",
            actual=status,
        )
    )

    deployments = described.get("deployments") or []
    primary = next(
        (item for item in deployments if item.get("status") == "PRIMARY"),
        deployments[0] if deployments else {},
    )
    service_deployments = aws.list_ecs_service_deployments(
        cluster,
        service_name,
        verification_start,
        verification_end,
    )
    if service_deployments is not None:
        matching_deployments = [
            item
            for item in service_deployments
            if _in_verification_window(item, verification_start, verification_end)
        ]
        latest = max(
            matching_deployments,
            key=lambda item: _parse_timestamp(item.get("createdAt") or item.get("startedAt"))
            or datetime.min.replace(tzinfo=timezone.utc),
            default=None,
        )
        deployment_status = str((latest or {}).get("status") or "NOT_FOUND").upper()
        rollout_status = CheckStatus.PASS if deployment_status == "SUCCESSFUL" else CheckStatus.FAIL
        if latest is None:
            rollout_reason = "No ECS service deployment was created within the verification window"
        elif deployment_status == "SUCCESSFUL":
            rollout_reason = "ECS service deployment in the verification window is SUCCESSFUL"
        else:
            rollout_reason = f"ECS service deployment status={deployment_status}"
        rollout_expected = "SUCCESSFUL within verification window"
        rollout_actual: Any = {
            "verificationWindow": {"start": verification_start, "end": verification_end},
            "serviceDeployment": latest,
        }
    else:
        rollout = str(primary.get("rolloutState") or "").upper()
        stable = (
            rollout in {"COMPLETED", "STEADY_STATE", ""}
            and int(described.get("runningCount") or 0)
            == int(described.get("desiredCount") or 0)
        )
        if rollout == "COMPLETED" or (not rollout and stable):
            rollout_status = CheckStatus.PASS
            rollout_reason = "Primary deployment is complete/stable (legacy DescribeServices fallback)"
        else:
            rollout_status = CheckStatus.FAIL
            rollout_reason = f"Primary deployment rolloutState={rollout or 'UNKNOWN'}"
        rollout_expected = "COMPLETED"
        rollout_actual = primary
    results.append(
        CheckResult(
            check="ECS service deployment completed successfully",
            status=rollout_status,
            resource=resource,
            reason=rollout_reason,
            expected=rollout_expected,
            actual=rollout_actual,
        )
    )

    desired = int(described.get("desiredCount") or 0)
    running = int(described.get("runningCount") or 0)
    results.append(
        CheckResult(
            check="ECS desired count equals running count",
            status=CheckStatus.PASS if desired == running else CheckStatus.FAIL,
            resource=resource,
            reason="Running task count matches desired count" if desired == running else "Running task count is below desired count",
            expected=desired,
            actual=running,
        )
    )

    pending = int(described.get("pendingCount") or 0)
    allow_pending = bool(settings.get("allow_pending_tasks"))
    pending_ok = pending == 0 or allow_pending
    results.append(
        CheckResult(
            check="ECS pending task count is zero",
            status=CheckStatus.PASS if pending_ok else CheckStatus.FAIL,
            resource=resource,
            reason="No pending tasks" if pending == 0 else f"Pending count is {pending}",
            expected=0,
            actual=pending,
        )
    )

    failed_tasks = int(primary.get("failedTasks") or 0)
    failure_events = [
        event for event in (described.get("events") or [])
        if event.get("message") and ("unable to place" in event["message"].lower() or "unhealthy" in event["message"].lower() or "failed" in event["message"].lower())
    ]
    no_failures = failed_tasks == 0 and not failure_events
    results.append(
        CheckResult(
            check="ECS no deployment failures",
            status=CheckStatus.PASS if no_failures else CheckStatus.FAIL,
            resource=resource,
            reason="No failed tasks or failure events" if no_failures else "Deployment failures were reported",
            expected=0,
            actual={"failedTasks": failed_tasks, "failure_events": failure_events[:5]},
        )
    )

    tasks = aws.list_ecs_tasks(cluster, service_name)
    running_tasks = [task for task in tasks if task.get("lastStatus") == "RUNNING"]
    stopped_tasks = [task for task in tasks if task.get("lastStatus") == "STOPPED"]

    current_td = described.get("taskDefinition")
    td = aws.describe_task_definition(current_td) if current_td else None
    expected_revision = service.identifiers.task_definition_revision
    actual_revision = str(td.get("revision")) if td and td.get("revision") is not None else None
    expected_digest = service.expected_artifact.image_digest
    actual_images = []
    if td:
        actual_images = [container.get("image") for container in td.get("containerDefinitions") or []]
    running_containers = []
    for task in running_tasks:
        for container in task.get("containers") or []:
            running_containers.append(
                {
                    "taskArn": task.get("taskArn"),
                    "container": container.get("name"),
                    "image": container.get("image"),
                    "imageDigest": _normalize_digest(container.get("imageDigest")),
                }
            )
    runtime_digests = [
        item["imageDigest"]
        for item in running_containers
        if item.get("imageDigest")
    ]
    if not expected_digest:
        if (service.deployment_action or {}).get("mode") in {"infra_inventory", "fe_deploy"}:
            image_status = CheckStatus.NOT_APPLICABLE
            image_reason = (
                "IaC variables provide cluster/service/task revision but not sha256 digests; "
                "digest match skipped for infra inventory"
            )
        elif (service.deployment_action or {}).get("resolve_digest_from_runtime") and runtime_digests:
            # BE path without DA digest: prove tasks expose digests (actual pin comes from DA/CI).
            image_status = CheckStatus.PASS
            image_reason = (
                "No expected digest in IaC/DA; running tasks expose imageDigest "
                f"{runtime_digests[0]} (Deployment Agent should prefer CI sha256 when available)"
            )
        else:
            image_status = CheckStatus.CHECK_MISSING
            image_reason = (
                "Deployment Agent did not provide expected_artifact.image_digest "
                "(not available from ADO IaC variable groups)"
            )
    elif not runtime_digests:
        image_status = CheckStatus.CHECK_MISSING
        image_reason = "AWS did not return imageDigest for any running container"
    elif expected_digest in runtime_digests:
        image_status = CheckStatus.PASS
        image_reason = "A running container imageDigest matches the expected ECR digest"
    else:
        image_status = CheckStatus.FAIL
        image_reason = "Running container imageDigest does not match the expected ECR digest"

    results.append(
        CheckResult(
            check="ECS expected task definition revision exists",
            status=CheckStatus.PASS if (td is not None and (not expected_revision or actual_revision == expected_revision)) else CheckStatus.FAIL,
            resource=resource,
            reason=(
                "Current task definition revision matches expected revision"
                if (td is not None and (not expected_revision or actual_revision == expected_revision))
                else "Current task definition revision does not match expected revision"
            ),
            expected=expected_revision,
            actual={"taskDefinition": current_td, "revision": actual_revision},
        )
    )

    results.append(
        CheckResult(
            check="ECS running container image digest matches expected ECR digest",
            status=image_status,
            resource=resource,
            reason=image_reason,
            expected=expected_digest,
            actual={
                "taskDefinition": current_td,
                "taskDefinitionImages": actual_images,
                "revision": actual_revision,
                "runningContainers": running_containers,
            },
        )
    )

    deployment_started = _parse_timestamp(verification_start)
    tasks_started_after = [
        task for task in running_tasks
        if _parse_timestamp(task.get("startedAt")) and deployment_started
        and _parse_timestamp(task.get("startedAt")) >= deployment_started
    ]
    tasks_with_start = [task for task in running_tasks if task.get("startedAt")]
    if not tasks_with_start:
        task_start_status = CheckStatus.CHECK_MISSING
        task_start_reason = "Running tasks do not report startedAt; cannot verify task launch time"
    elif tasks_started_after:
        task_start_status = CheckStatus.PASS
        task_start_reason = f"{len(tasks_started_after)} of {len(running_tasks)} running task(s) started after deployment began"
    else:
        task_start_status = CheckStatus.FAIL
        task_start_reason = "No running tasks started on or after the deployment start time; pre-deployment tasks may still be serving traffic"
    results.append(
        CheckResult(
            check="ECS running tasks started after deployment began",
            status=task_start_status,
            resource=resource,
            reason=task_start_reason,
            expected=f">= {verification_start}",
            actual=[
                {"taskArn": task.get("taskArn"), "startedAt": task.get("startedAt")}
                for task in running_tasks
            ],
        )
    )

    running_use_td = True
    if current_td:
        running_use_td = all(task.get("taskDefinitionArn") == current_td for task in running_tasks) if running_tasks else False
    results.append(
        CheckResult(
            check="ECS running tasks use the deployed task definition",
            status=CheckStatus.PASS if running_use_td and running_tasks else CheckStatus.FAIL,
            resource=resource,
            reason="All running tasks use the current task definition" if running_use_td and running_tasks else "Running tasks are missing or not on the current task definition",
            expected=current_td if current_td else expected_revision,
            actual=[task.get("taskDefinitionArn") for task in running_tasks],
        )
    )

    containers_running = bool(running_tasks) and all(
        container.get("lastStatus") == "RUNNING"
        for task in running_tasks
        for container in (task.get("containers") or [])
    )
    results.append(
        CheckResult(
            check="ECS required containers are running",
            status=CheckStatus.PASS if containers_running else CheckStatus.FAIL,
            resource=resource,
            reason="Required containers are running" if containers_running else "One or more required containers are not running",
            expected="RUNNING",
            actual=[
                {"task": task.get("taskArn"), "containers": [container.get("lastStatus") for container in task.get("containers") or []]}
                for task in running_tasks
            ],
        )
    )

    unexpected_stopped = [
        task for task in stopped_tasks
        if task.get("stoppedReason") or task.get("stopCode")
    ]
    results.append(
        CheckResult(
            check="ECS no unexpected stopped/failed tasks",
            status=CheckStatus.PASS if not unexpected_stopped else CheckStatus.FAIL,
            resource=resource,
            reason="No unexpected stopped tasks for this deployment" if not unexpected_stopped else "Stopped/failed tasks were found for this deployment",
            expected=0,
            actual=[
                {
                    "taskArn": task.get("taskArn"),
                    "stoppedReason": task.get("stoppedReason"),
                    "stopCode": task.get("stopCode"),
                    "containers": task.get("containers"),
                }
                for task in unexpected_stopped
            ],
        )
    )

    health_values = [task.get("healthStatus") for task in running_tasks if task.get("healthStatus")]
    if not health_values:
        health_status = CheckStatus.NOT_APPLICABLE
        health_reason = "No ECS task healthStatus is configured"
    elif all(value in {"HEALTHY", "UNKNOWN"} for value in health_values) and any(value == "HEALTHY" for value in health_values):
        health_status = CheckStatus.PASS
        health_reason = "Running tasks report HEALTHY"
    elif all(value == "UNKNOWN" for value in health_values):
        health_status = CheckStatus.NOT_APPLICABLE
        health_reason = "Task health checks are not configured (UNKNOWN)"
    else:
        health_status = CheckStatus.FAIL
        health_reason = "One or more running tasks are not HEALTHY"
    results.append(
        CheckResult(
            check="ECS task/container health",
            status=health_status,
            resource=resource,
            reason=health_reason,
            expected="HEALTHY",
            actual=health_values,
        )
    )

    load_balancers = described.get("loadBalancers") or []
    target_group_arn = service.identifiers.target_group_arn or (
        load_balancers[0].get("targetGroupArn") if load_balancers else None
    )
    if not target_group_arn:
        results.append(
            CheckResult(
                check="ALB target health",
                status=CheckStatus.NOT_APPLICABLE,
                resource=resource,
                reason="No target group is attached to the ECS service",
                expected=None,
                actual=None,
            )
        )
    else:
        targets = aws.describe_target_health(target_group_arn)
        states = [item.get("state") for item in targets]
        unhealthy = [item for item in targets if str(item.get("state", "")).lower() != "healthy"]
        registered = bool(targets)
        results.append(
            CheckResult(
                check="ALB target group exists and targets registered",
                status=CheckStatus.PASS if registered else CheckStatus.FAIL,
                resource=target_group_arn,
                reason="Expected targets are registered" if registered else "No targets are registered",
                expected="registered targets",
                actual=states,
            )
        )
        results.append(
            CheckResult(
                check="ALB target health",
                status=CheckStatus.PASS if registered and not unhealthy else CheckStatus.FAIL,
                resource=target_group_arn,
                reason="All registered targets are healthy" if registered and not unhealthy else "Unhealthy or missing targets",
                expected="healthy",
                actual=targets,
            )
        )

    return results
