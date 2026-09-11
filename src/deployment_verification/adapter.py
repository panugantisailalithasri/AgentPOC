from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from deployment_verification.models import (
    DeploymentContext,
    DeploymentKind,
    ExpectedArtifact,
    ExpectedService,
    ResourceIdentifiers,
    ServiceType,
    SmokeTest,
    VerificationWindow,
)
from deployment_verification.resource_discovery import (
    build_smoke_tests_from_inventory,
    discover_services_from_payload,
    infer_deployment_kind,
    is_bogus_pipeline_named_ecs,
    summarize_pipeline_deployment,
)

REQUIRED_ROOT_FIELDS_V12 = (
    "deployment_id",
    "deployment_status",
    "aws",
    "services",
    "verification_window",
)


def load_deployment_agent_output(path: str | Path) -> DeploymentContext:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return normalize_deployment_agent_output(payload)


def normalize_deployment_agent_output(payload: dict[str, Any]) -> DeploymentContext:
    payload = dict(payload)
    _ensure_compatibility_fields(payload)

    missing = [field for field in ("deployment_id", "aws", "verification_window") if field not in payload]
    if missing:
        raise ValueError(f"Deployment Agent output missing required fields: {missing}")

    aws = payload["aws"]
    if not aws.get("account_id") or not aws.get("region"):
        raise ValueError("Deployment Agent output requires aws.account_id and aws.region")

    window = payload["verification_window"]
    if not window.get("start") or not window.get("end"):
        raise ValueError("verification_window.start and verification_window.end are required")

    kind = infer_deployment_kind(payload)
    discovery_notes: list[str] = []
    discovered = discover_services_from_payload(payload)
    if discovered:
        discovery_notes.append(
            f"Discovered {len(discovered)} resources from infra_export/environment_contract stack outputs"
        )

    explicit = [_parse_service(item) for item in payload.get("services") or []]
    pipeline_name = payload.get("pipeline_name")
    filtered_explicit: list[ExpectedService] = []
    for service in explicit:
        if is_bogus_pipeline_named_ecs(service, pipeline_name):
            discovery_notes.append(
                f"Ignored bogus ECS target {service.identifiers.cluster}/{service.identifiers.service_name} "
                "(pipeline name used as ECS service; use infra_export ClusterServiceName values instead)"
            )
            continue
        filtered_explicit.append(service)

    # Prefer discovered inventory for INFRA; otherwise merge explicit + discovered.
    if kind == DeploymentKind.INFRA and discovered:
        services = discovered
        discovery_notes.append("Using infra_export-discovered resources as verification inventory")
    else:
        services = _merge_services(filtered_explicit, discovered)

    if not services:
        raise ValueError(
            "Deployment Agent output must include at least one expected service "
            "or discoverable infra_export/stack_outputs resources"
        )

    for service in services:
        _validate_service(service, deployment_kind=kind)

    smoke_tests = [
        SmokeTest(
            name=item.get("name") or item.get("url"),
            url=item["url"],
            expected_status=int(item.get("expected_status", 200)),
            expected_body_contains=item.get("expected_body_contains"),
        )
        for item in payload.get("smoke_tests") or []
        if item.get("url")
    ]
    existing_urls = {item.url for item in smoke_tests}
    # Only auto-add smoke for FE / explicit smoke_url on CloudFront; ALB HTTP may be private.
    if kind in {DeploymentKind.FE, DeploymentKind.MIXED}:
        for item in build_smoke_tests_from_inventory(services, existing_urls):
            if "cloudfront" in item["name"] or kind == DeploymentKind.FE:
                smoke_tests.append(
                    SmokeTest(name=item["name"], url=item["url"], expected_status=200)
                )

    stages = payload.get("stages") or []
    environment = (
        payload.get("environment")
        or (stages[0] if stages else None)
        or ((payload.get("releases") or [{}])[0].get("stage"))
        or ""
    )

    summary = summarize_pipeline_deployment(payload, kind)
    discovery_notes.extend(summary.get("notes") or [])
    discovery_notes.append(
        f"Pipeline `{summary.get('pipeline_name')}` classified as {kind.value}; "
        f"stacks={len(summary.get('stack_names') or [])}, "
        f"ecs_services={summary.get('resolved_from_iac', {}).get('ecs_services')}"
    )

    return DeploymentContext(
        deployment_id=str(payload["deployment_id"]),
        release_id=str(payload.get("release_id") or _first_release_id(payload) or payload["deployment_id"]),
        pipeline_run_id=str(
            payload.get("pipeline_run_id") or payload.get("master_build_id") or payload["deployment_id"]
        ),
        application=str(payload.get("application") or payload.get("title") or payload.get("pipeline_name") or ""),
        environment=str(environment),
        deployment_status=str(payload["deployment_status"]),
        started_at=str(payload.get("started_at") or window["start"]),
        completed_at=str(payload.get("completed_at") or window["end"]),
        verification_window=VerificationWindow(start=str(window["start"]), end=str(window["end"])),
        aws_account_id=str(aws["account_id"]),
        aws_region=str(aws["region"]),
        services=services,
        smoke_tests=smoke_tests,
        deployment_agent_checks=list(payload.get("deployment_agent_checks") or payload.get("verifications") or []),
        raw=payload,
        deployment_kind=kind,
        pipeline_name=str(pipeline_name) if pipeline_name else None,
        discovery_notes=discovery_notes,
        pipeline_summary=summary,
    )


def _ensure_compatibility_fields(payload: dict[str, Any]) -> None:
    """Map schema 1.4 Deployment Agent envelopes onto the verifier contract."""
    if "deployment_id" not in payload:
        work_item = payload.get("work_item_id")
        release = _first_release_id(payload)
        payload["deployment_id"] = (
            f"deploy:{work_item}:rel-{release}" if work_item or release else "unknown-deployment"
        )

    if "deployment_status" not in payload:
        releases = payload.get("releases") or []
        statuses = [str(item.get("status") or "").lower() for item in releases]
        if statuses and all(status == "succeeded" for status in statuses):
            payload["deployment_status"] = "SUCCEEDED"
        elif any(status in {"failed", "rejected", "canceled"} for status in statuses):
            payload["deployment_status"] = "FAILED"
        else:
            outcome = payload.get("outcome") or {}
            gate = payload.get("qualification_gate") or {}
            if gate.get("release_succeeded") or outcome.get("deployed"):
                payload["deployment_status"] = "SUCCEEDED"
            else:
                payload["deployment_status"] = "UNKNOWN"

    if "verification_window" not in payload:
        releases = payload.get("releases") or []
        starts = [item.get("started_at") for item in releases if item.get("started_at")]
        ends = [item.get("finished_at") for item in releases if item.get("finished_at")]
        written = payload.get("written_at")
        if starts and ends:
            payload["verification_window"] = {"start": min(starts), "end": max(ends)}
        elif written:
            payload["verification_window"] = {"start": written, "end": written}

    # Normalize lowercase service_type=ecs shapes from schema 1.4
    normalized_services = []
    for item in payload.get("services") or []:
        row = dict(item)
        if "identifiers" not in row:
            row["identifiers"] = {
                "cluster": row.get("cluster_name") or row.get("cluster"),
                "service_name": row.get("service_name") or row.get("service"),
                "task_definition_family": row.get("task_definition_family"),
                "task_definition_revision": row.get("task_definition_revision"),
            }
            if row.get("expected_image_digest"):
                row["expected_artifact"] = {"image_digest": row.get("expected_image_digest")}
        normalized_services.append(row)
    if normalized_services:
        payload["services"] = normalized_services


def _first_release_id(payload: dict[str, Any]) -> Any:
    releases = payload.get("releases") or []
    if releases:
        return releases[0].get("release_id") or releases[0].get("release_name")
    return None


def _merge_services(
    explicit: list[ExpectedService], discovered: list[ExpectedService]
) -> list[ExpectedService]:
    merged = list(explicit)
    seen = {
        (
            s.service_type.value,
            s.identifiers.service_name
            or s.identifiers.cluster
            or s.identifiers.function_name
            or s.identifiers.extra.get("bucket_name")
            or s.identifiers.extra.get("distribution_id")
            or s.identifiers.extra.get("repository_name")
            or s.identifiers.extra.get("stack_name")
            or s.name,
        )
        for s in merged
    }
    for service in discovered:
        key = (
            service.service_type.value,
            service.identifiers.service_name
            or service.identifiers.cluster
            or service.identifiers.function_name
            or service.identifiers.extra.get("bucket_name")
            or service.identifiers.extra.get("distribution_id")
            or service.identifiers.extra.get("repository_name")
            or service.identifiers.extra.get("stack_name")
            or service.name,
        )
        if key not in seen:
            merged.append(service)
            seen.add(key)
    return merged


def _parse_service(item: dict[str, Any]) -> ExpectedService:
    raw_type = str(item.get("service_type") or "OTHER").upper()
    try:
        service_type = ServiceType[raw_type]
    except KeyError:
        service_type = ServiceType.OTHER

    identifiers = item.get("identifiers") or {}
    artifact = item.get("expected_artifact") or {}
    known_id_keys = {
        "cluster",
        "service_name",
        "task_definition_family",
        "task_definition_revision",
        "log_group",
        "target_group_arn",
        "function_name",
    }
    extra = {key: value for key, value in identifiers.items() if key not in known_id_keys}

    return ExpectedService(
        service_type=service_type,
        name=str(item.get("name") or identifiers.get("service_name") or service_type.value),
        identifiers=ResourceIdentifiers(
            cluster=identifiers.get("cluster"),
            service_name=identifiers.get("service_name"),
            task_definition_family=identifiers.get("task_definition_family"),
            task_definition_revision=(
                str(identifiers["task_definition_revision"])
                if identifiers.get("task_definition_revision") is not None
                else None
            ),
            log_group=identifiers.get("log_group"),
            target_group_arn=identifiers.get("target_group_arn"),
            function_name=identifiers.get("function_name"),
            extra=extra,
        ),
        expected_artifact=ExpectedArtifact(
            image_repository=artifact.get("image_repository"),
            image_tag=artifact.get("image_tag"),
            image_digest=_normalize_digest(artifact.get("image_digest")),
            lambda_version=artifact.get("lambda_version"),
            code_sha=artifact.get("code_sha"),
        ),
        deployment_action=dict(item.get("deployment_action") or {}),
    )


def _validate_service(service: ExpectedService, deployment_kind: DeploymentKind) -> None:
    ids = service.identifiers
    mode = (service.deployment_action or {}).get("mode")
    infra_inventory = mode == "infra_inventory" or deployment_kind == DeploymentKind.INFRA

    if service.service_type == ServiceType.ECS:
        if not ids.cluster or not ids.service_name:
            raise ValueError(
                f"ECS service '{service.name}' requires identifiers.cluster and identifiers.service_name"
            )
        if str(ids.cluster).startswith("$(") or str(ids.service_name).startswith("$("):
            raise ValueError(
                f"ECS service '{service.name}' still has unresolved pipeline tokens. "
                "The Deployment Agent must resolve CloudFormation outputs before verification."
            )
        digest = service.expected_artifact.image_digest
        if not infra_inventory:
            if not digest:
                raise ValueError(
                    f"ECS service '{service.name}' requires expected_artifact.image_digest"
                )
            if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
                raise ValueError(
                    f"ECS service '{service.name}' expected_artifact.image_digest must be "
                    "a complete sha256 digest with 64 hexadecimal characters"
                )
        elif digest and not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
            raise ValueError(
                f"ECS service '{service.name}' expected_artifact.image_digest must be "
                "a complete sha256 digest with 64 hexadecimal characters"
            )
    if service.service_type == ServiceType.LAMBDA and not ids.function_name:
        raise ValueError(f"Lambda service '{service.name}' requires identifiers.function_name")
    if service.service_type == ServiceType.S3 and not ids.extra.get("bucket_name"):
        raise ValueError(f"S3 service '{service.name}' requires identifiers.extra.bucket_name")
    if service.service_type == ServiceType.CLOUDFRONT and not ids.extra.get("distribution_id"):
        raise ValueError(
            f"CloudFront service '{service.name}' requires identifiers.extra.distribution_id"
        )
    if service.service_type == ServiceType.ECR and not (
        ids.extra.get("repository_name") or service.expected_artifact.image_repository
    ):
        raise ValueError(f"ECR service '{service.name}' requires repository_name")
    if service.service_type == ServiceType.CFN_STACK and not ids.extra.get("stack_name"):
        raise ValueError(f"CFN stack '{service.name}' requires identifiers.extra.stack_name")
    if service.service_type == ServiceType.ECS_CLUSTER and not ids.cluster:
        raise ValueError(f"ECS cluster '{service.name}' requires identifiers.cluster")


def _normalize_digest(value: str | None) -> str | None:
    if not value:
        return None
    digest = value.strip()
    if digest.startswith("sha256:"):
        return digest
    if len(digest) == 64:
        return f"sha256:{digest}"
    return digest


def expected_services_as_dicts(context: DeploymentContext) -> list[dict[str, Any]]:
    return [
        {
            "name": service.name,
            "service_type": service.service_type.value,
            "identifiers": {
                "cluster": service.identifiers.cluster,
                "service_name": service.identifiers.service_name,
                "task_definition_family": service.identifiers.task_definition_family,
                "log_group": service.identifiers.log_group,
                "target_group_arn": service.identifiers.target_group_arn,
                "function_name": service.identifiers.function_name,
                "extra": service.identifiers.extra,
            },
            "deployment_action": service.deployment_action,
        }
        for service in context.services
    ]
