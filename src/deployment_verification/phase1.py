from __future__ import annotations

from typing import Any

from deployment_verification.adapter import expected_services_as_dicts
from deployment_verification.aws_facade import AwsFacade
from deployment_verification.models import (
    CheckResult,
    CheckStatus,
    DeploymentContext,
    DeploymentKind,
    ExpectedService,
    IMPLEMENTED_PHASE1_TYPES,
    Phase1Result,
    ServiceType,
)
from deployment_verification.phase1_ecs import run_ecs_checks
from deployment_verification.phase1_fe import run_fe_service_checks
from deployment_verification.phase1_infra import run_infra_service_checks
from deployment_verification.phase1_smoke import run_smoke_checks


def run_phase1(context: DeploymentContext, aws: AwsFacade, settings: dict[str, Any]) -> Phase1Result:
    all_checks: list[CheckResult] = []
    service_results: list[dict[str, Any]] = []

    for service in context.services:
        checks = _checks_for_service(
            service,
            aws,
            settings,
            context.verification_window.start,
            context.verification_window.end,
            context.deployment_kind,
        )
        all_checks.extend(checks)
        service_results.append(
            {
                "name": service.name,
                "service_type": service.service_type.value,
                "statuses": [check.status.value for check in checks],
                "checks": [check.to_dict() for check in checks],
            }
        )

    all_checks.extend(
        run_smoke_checks(
            context.smoke_tests,
            timeout_seconds=int(settings.get("smoke_timeout_seconds", 10)),
            retries=int(settings.get("smoke_retries", 2)),
        )
    )

    failed = [check for check in all_checks if check.status == CheckStatus.FAIL]
    missing = [check for check in all_checks if check.status == CheckStatus.CHECK_MISSING]
    skipped = [check for check in all_checks if check.status == CheckStatus.NOT_APPLICABLE]
    not_found = [check for check in all_checks if check.status == CheckStatus.RESOURCE_NOT_FOUND]
    performed = [
        check for check in all_checks if check.status in {CheckStatus.PASS, CheckStatus.FAIL}
    ]

    mandatory_failed = bool(failed or not_found)
    coverage_complete = not missing
    healthy = not mandatory_failed and coverage_complete

    return Phase1Result(
        DeploymentHealthy=healthy,
        ServiceResults=service_results,
        FailedChecks=[check.to_dict() for check in failed],
        MissingChecks=[check.to_dict() for check in missing],
        SkippedChecks=[check.to_dict() for check in skipped],
        ResourceNotFound=[check.to_dict() for check in not_found],
        ChecksPerformed=[check.to_dict() for check in performed],
        ExpectedServices=expected_services_as_dicts(context),
        CoverageComplete=coverage_complete,
        VerificationWindow={
            "start": context.verification_window.start,
            "end": context.verification_window.end,
        },
    )


def _checks_for_service(
    service: ExpectedService,
    aws: AwsFacade,
    settings: dict[str, Any],
    verification_start: str,
    verification_end: str,
    deployment_kind: DeploymentKind,
) -> list[CheckResult]:
    if service.service_type == ServiceType.ECS:
        return run_ecs_checks(service, aws, settings, verification_start, verification_end)

    if deployment_kind == DeploymentKind.FE or service.identifiers.extra.get("fe_assets"):
        fe_checks = run_fe_service_checks(service, aws, verification_start, verification_end)
        if fe_checks is not None:
            return fe_checks

    infra_checks = run_infra_service_checks(service, aws, verification_start, verification_end)
    if infra_checks is not None:
        return infra_checks

    if service.service_type == ServiceType.CLOUDFRONT:
        fe_checks = run_fe_service_checks(service, aws, verification_start, verification_end)
        if fe_checks is not None:
            return fe_checks

    if service.service_type == ServiceType.LAMBDA:
        described = aws.describe_lambda_function(service.identifiers.function_name or service.name)
        if described is None:
            return [
                CheckResult(
                    check="Lambda function exists",
                    status=CheckStatus.RESOURCE_NOT_FOUND,
                    resource=service.identifiers.function_name or service.name,
                    reason="GetFunction did not return the function",
                )
            ]
        return [
            CheckResult(
                check="Lambda function exists",
                status=CheckStatus.PASS,
                resource=service.identifiers.function_name or service.name,
                reason="Function was found",
                actual={"FunctionName": (described.get("Configuration") or {}).get("FunctionName")},
            )
        ]

    if service.service_type not in IMPLEMENTED_PHASE1_TYPES:
        return [
            CheckResult(
                check=f"{service.service_type.value} verification",
                status=CheckStatus.CHECK_MISSING,
                resource=service.name,
                reason=f"No deterministic check is implemented for {service.service_type.value}",
            )
        ]
    return []
