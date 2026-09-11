from __future__ import annotations

from typing import Any

from deployment_verification.aws_facade import AwsFacade
from deployment_verification.models import (
    CheckStatus,
    DeploymentContext,
    Phase1Result,
    Phase2Result,
    ServiceType,
)


def run_phase2(
    context: DeploymentContext,
    phase1: Phase1Result,
    aws: AwsFacade,
    settings: dict[str, Any],
) -> Phase2Result:
    evidence: dict[str, Any] = {"ecs": [], "alb": [], "logs": [], "metrics": [], "dynamic": []}
    dynamic_results: list[dict[str, Any]] = []
    affected: list[str] = []

    for service in context.services:
        failed_for_service = [
            check
            for check in phase1.FailedChecks + phase1.ResourceNotFound + phase1.MissingChecks
            if service.name in (check.get("resource") or "") or service.identifiers.service_name and service.identifiers.service_name in (check.get("resource") or "")
            or check.get("resource") == service.name
        ]
        resource_token = f"{service.identifiers.cluster}/{service.identifiers.service_name}" if service.identifiers.cluster else service.name
        related = [
            check
            for check in phase1.FailedChecks + phase1.ResourceNotFound + phase1.MissingChecks
            if service.name in str(check) or resource_token in str(check.get("resource") or "")
        ]
        if not related and not failed_for_service:
            # Investigate ECS service if any ECS check failed globally for this resource
            related = [
                check
                for check in phase1.FailedChecks + phase1.ResourceNotFound
                if resource_token in str(check.get("resource") or "")
            ]
        if not related and service.service_type != ServiceType.ECS:
            missing = [check for check in phase1.MissingChecks if check.get("resource") == service.name]
            if not missing:
                continue
            related = missing

        if service.service_type == ServiceType.ECS:
            affected.append(service.name)
            cluster = service.identifiers.cluster or ""
            name = service.identifiers.service_name or ""
            described = aws.describe_ecs_service(cluster, name) or {}
            service_deployments = aws.list_ecs_service_deployments(
                cluster,
                name,
                context.verification_window.start,
                context.verification_window.end,
            )
            tasks = aws.list_ecs_tasks(cluster, name)
            stopped = [task for task in tasks if task.get("lastStatus") == "STOPPED"]
            evidence["ecs"].append(
                {
                    "service": service.name,
                    "service_deployments": service_deployments,
                    "events": described.get("events") or [],
                    "deployments": described.get("deployments") or [],
                    "task_states": [
                        {
                            "taskArn": task.get("taskArn"),
                            "lastStatus": task.get("lastStatus"),
                            "healthStatus": task.get("healthStatus"),
                            "stoppedReason": task.get("stoppedReason"),
                            "stopCode": task.get("stopCode"),
                            "containers": task.get("containers"),
                        }
                        for task in tasks
                    ],
                    "container_exit_reasons": [
                        {
                            "taskArn": task.get("taskArn"),
                            "container": container.get("name"),
                            "exitCode": container.get("exitCode"),
                            "reason": container.get("reason") or task.get("stoppedReason"),
                        }
                        for task in stopped
                        for container in (task.get("containers") or [])
                    ],
                }
            )
            load_balancers = described.get("loadBalancers") or []
            for lb in load_balancers:
                tg = lb.get("targetGroupArn")
                if tg:
                    targets = aws.describe_target_health(tg)
                    evidence["alb"].append({"targetGroupArn": tg, "targets": targets})
            if service.identifiers.log_group:
                evidence["logs"].append(
                    {
                        "log_group": service.identifiers.log_group,
                        "events": aws.get_log_events(
                            service.identifiers.log_group,
                            context.verification_window.start,
                            context.verification_window.end,
                            int(settings.get("cloudwatch_log_limit", 50)),
                        ),
                    }
                )
            evidence["metrics"].append(
                {
                    "service": service.name,
                    "metrics": aws.get_service_metrics(cluster, name),
                }
            )

        missing_for_type = [
            check
            for check in phase1.MissingChecks
            if check.get("resource") == service.name or service.service_type.value in str(check.get("check") or "")
        ]
        if missing_for_type:
            plan, result = _dynamic_check(service, aws)
            evidence["dynamic"].append({"plan": plan, "result": result})
            dynamic_results.append(result)
            if service.name not in affected:
                affected.append(service.name)

    if not affected:
        for check in phase1.FailedChecks + phase1.ResourceNotFound + phase1.MissingChecks:
            affected.append(str(check.get("resource")))

    summary = _summarize(phase1, evidence, dynamic_results)
    return Phase2Result(
        InvestigationCompleted=True,
        DynamicCheckResults=dynamic_results,
        Evidence=evidence,
        InvestigationSummary=summary,
        AffectedServices=list(dict.fromkeys(affected)),
    )


def _dynamic_check(service, aws: AwsFacade) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = {
        "service": service.name,
        "service_type": service.service_type.value,
        "strategy": "bounded read-only AWS describe",
        "writes": False,
    }
    if service.service_type == ServiceType.RDS:
        db_id = (service.identifiers.extra or {}).get("db_instance_identifier")
        plan["api"] = "rds.describe_db_instances"
        if not db_id:
            result = {
                "check": "RDS dynamic verification",
                "status": CheckStatus.UNSUPPORTED.value,
                "resource": service.name,
                "reason": "No db_instance_identifier supplied; cannot invent a PASS",
            }
            return plan, result
        instance = aws.describe_db_instance(str(db_id))
        if instance is None:
            result = {
                "check": "RDS dynamic verification",
                "status": CheckStatus.FAIL.value,
                "resource": str(db_id),
                "reason": "DescribeDBInstances did not return the instance",
                "evidence": None,
            }
        else:
            status = instance.get("DBInstanceStatus")
            result = {
                "check": "RDS dynamic verification",
                "status": CheckStatus.PASS.value if status == "available" else CheckStatus.FAIL.value,
                "resource": str(db_id),
                "reason": f"DBInstanceStatus={status}",
                "evidence": {"status": status},
            }
        return plan, result

    if service.service_type == ServiceType.LAMBDA:
        name = service.identifiers.function_name or service.name
        plan["api"] = "lambda.get_function"
        fn = aws.describe_lambda_function(name)
        if fn is None:
            result = {
                "check": "Lambda dynamic verification",
                "status": CheckStatus.INCONCLUSIVE.value,
                "resource": name,
                "reason": "Could not read Lambda function; not converting absence of evidence into PASS",
            }
        else:
            result = {
                "check": "Lambda dynamic verification",
                "status": CheckStatus.PASS.value,
                "resource": name,
                "reason": "Lambda function was readable via GetFunction",
                "evidence": {"state": (fn.get("Configuration") or {}).get("State")},
            }
        return plan, result

    plan["api"] = None
    result = {
        "check": f"{service.service_type.value} dynamic verification",
        "status": CheckStatus.UNSUPPORTED.value,
        "resource": service.name,
        "reason": f"No approved read-only dynamic check for {service.service_type.value}",
    }
    return plan, result


def _summarize(phase1: Phase1Result, evidence: dict[str, Any], dynamic_results: list[dict[str, Any]]) -> str:
    facts = []
    if phase1.FailedChecks:
        facts.append(f"{len(phase1.FailedChecks)} Phase 1 check(s) failed.")
    if phase1.ResourceNotFound:
        facts.append(f"{len(phase1.ResourceNotFound)} expected resource(s) were not found.")
    if phase1.MissingChecks:
        facts.append(f"{len(phase1.MissingChecks)} existing service(s) had no Phase 1 check.")
    exits = []
    for block in evidence.get("ecs") or []:
        exits.extend(block.get("container_exit_reasons") or [])
        for event in (block.get("events") or [])[:3]:
            facts.append(f"ECS event: {event.get('message')}")
    if exits:
        facts.append(
            "Container exit: "
            + "; ".join(f"{item.get('container')} exitCode={item.get('exitCode')} ({item.get('reason')})" for item in exits[:3])
        )
    for log_block in evidence.get("logs") or []:
        for event in (log_block.get("events") or [])[:3]:
            facts.append(f"Log: {event.get('message')}")
    for item in dynamic_results:
        facts.append(f"Dynamic check {item.get('check')}: {item.get('status')} — {item.get('reason')}")
    facts.append("This summary records observed symptoms only. RCA and rollback decisions are produced in Phase 3.")
    return " ".join(facts)
