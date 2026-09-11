from __future__ import annotations

from deployment_verification.aws_facade import AwsFacade
from deployment_verification.models import CheckResult, CheckStatus, ExpectedService
from deployment_verification.phase1_infra import run_s3_infra_checks


def run_cloudfront_checks(service: ExpectedService, aws: AwsFacade) -> list[CheckResult]:
    distribution_id = service.identifiers.extra.get("distribution_id") or service.name
    resource = f"cloudfront://{distribution_id}"
    described = aws.describe_cloudfront_distribution(distribution_id)
    if described is None:
        return [
            CheckResult(
                check="CloudFront distribution exists",
                status=CheckStatus.RESOURCE_NOT_FOUND,
                resource=resource,
                reason="GetDistribution did not return the distribution",
                expected=distribution_id,
                actual=None,
            )
        ]

    status = str(described.get("Status") or "")
    enabled = described.get("Enabled")
    origins = described.get("Origins") or []
    origin_domains = [item.get("DomainName") for item in origins if item.get("DomainName")]
    domain_name = described.get("DomainName")

    expected_bucket = service.identifiers.extra.get("origin_bucket")
    origin_ok = True
    origin_reason = f"Origins: {origin_domains}"
    if expected_bucket:
        needle = str(expected_bucket).rstrip(".")
        origin_ok = any(needle in str(domain) for domain in origin_domains)
        origin_reason = (
            f"Found expected S3 origin for {expected_bucket}"
            if origin_ok
            else f"Expected S3 origin {expected_bucket} not found in {origin_domains}"
        )

    fe_mode = (service.deployment_action or {}).get("mode") == "fe_deploy"
    results = [
        CheckResult(
            check="CloudFront distribution exists",
            status=CheckStatus.PASS,
            resource=resource,
            reason="Distribution was found",
            expected=distribution_id,
            actual={"Status": status, "Enabled": enabled, "DomainName": domain_name},
        ),
        CheckResult(
            check="CloudFront distribution status is Deployed",
            status=CheckStatus.PASS if status.lower() == "deployed" else CheckStatus.FAIL,
            resource=resource,
            reason=f"Distribution Status is {status}",
            expected="Deployed",
            actual=status,
        ),
        CheckResult(
            check="CloudFront distribution enabled",
            status=CheckStatus.PASS if enabled else CheckStatus.FAIL,
            resource=resource,
            reason="Distribution Enabled flag is true" if enabled else "Distribution Enabled flag is false",
            expected=True,
            actual=enabled,
        ),
        CheckResult(
            check="CloudFront S3 origin configuration",
            status=CheckStatus.PASS if origin_ok else CheckStatus.FAIL,
            resource=resource,
            reason=origin_reason,
            expected=expected_bucket,
            actual={"origins": origin_domains},
        ),
    ]

    invalidations = aws.list_cloudfront_invalidations(distribution_id, max_items=5) or []
    if fe_mode:
        results.append(
            CheckResult(
                check="CloudFront cache invalidation after FE deploy",
                status=CheckStatus.PASS if invalidations else CheckStatus.FAIL,
                resource=resource,
                reason=(
                    f"Found {len(invalidations)} recent invalidation(s)"
                    if invalidations
                    else "No recent invalidations after FE deploy"
                ),
                expected=">= 1 recent invalidation",
                actual={"invalidations": invalidations[:5]},
            )
        )
    else:
        results.append(
            CheckResult(
                check="CloudFront recent invalidations present",
                status=CheckStatus.PASS if invalidations else CheckStatus.INCONCLUSIVE,
                resource=resource,
                reason=(
                    f"Found {len(invalidations)} recent invalidation(s)"
                    if invalidations
                    else "No recent invalidations returned"
                ),
                expected="optional outside FE deploys",
                actual={"invalidations": invalidations[:5]},
            )
        )

    smoke_url = service.identifiers.extra.get("smoke_url")
    if smoke_url:
        results.append(
            CheckResult(
                check="CloudFront smoke URL resolved from IaC",
                status=CheckStatus.PASS,
                resource=resource,
                reason=f"Smoke URL available: {smoke_url}",
                expected="https://{stgDomain}/ from Library/CD vars",
                actual={"smoke_url": smoke_url, "distributionDomain": domain_name},
            )
        )
    return results


def run_fe_service_checks(
    service: ExpectedService,
    aws: AwsFacade,
    verification_start: str,
    verification_end: str,
) -> list[CheckResult] | None:
    from deployment_verification.models import ServiceType

    if service.service_type == ServiceType.S3:
        return run_s3_infra_checks(service, aws, verification_start, verification_end)
    if service.service_type == ServiceType.CLOUDFRONT:
        return run_cloudfront_checks(service, aws)
    return None
