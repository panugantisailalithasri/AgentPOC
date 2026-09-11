"""Classify deployment kind and discover resources from stack names + IaC variables."""

from __future__ import annotations

import re
from typing import Any

from deployment_verification.models import (
    DeploymentKind,
    ExpectedArtifact,
    ExpectedService,
    ResourceIdentifiers,
    ServiceType,
)

# Stack-name tokens that indicate infrastructure provisioning (not app code push).
_INFRA_STACK_TOKENS = (
    "alb-stack",
    "ecs-cluster-stack",
    "ecr-stack",
    "vpc-stack",
    "rds-stack",
    "waf",
    "nat-stack",
    "sg-stack",
    "iam-stack",
    "kms-stack",
    "secrets-stack",
    "s3-bucket-stack",
    "bucket-stack",
    "apigateway",
    "api-gateway",
    "lambda-stack",
    "glue",
    "cloudfront-stack",
)

# FE-oriented tokens in stack names / variable keys.
_FE_TOKENS = (
    "frontend",
    "front-end",
    "-ui-",
    "admin-ui",
    "static",
    "assets-storage",
    "cloudfront",
    "cf-distribution",
)

# BE / runtime service stacks.
_BE_STACK_TOKENS = (
    "-ecs-stack",
    "ecs-service",
    "task-definition",
    "-api-stack",
    "-be-stack",
    "service-stack",
)


def infer_deployment_kind(payload: dict[str, Any]) -> DeploymentKind:
    """Prefer stack-name signals; fall back to pipeline name / modules."""
    if payload.get("deployment_kind"):
        try:
            return DeploymentKind[str(payload["deployment_kind"]).upper()]
        except KeyError:
            pass

    stack_names = _collect_stack_names(payload)
    variables = _collect_variables(payload)
    scores = {"INFRA": 0, "FE": 0, "BE": 0}

    for stack in stack_names:
        lower = stack.lower()
        if any(token in lower for token in _FE_TOKENS):
            scores["FE"] += 2
        if any(token in lower for token in _INFRA_STACK_TOKENS):
            scores["INFRA"] += 2
        # ecs-stack that is not cluster/ecr counts as BE runtime service stack
        if "-ecs-stack" in lower and "ecs-cluster" not in lower and "ecr-stack" not in lower:
            scores["BE"] += 1
            scores["INFRA"] += 1  # often created by Infra-Automation together

    blob_vars = " ".join(variables.keys()).lower()
    if any(token in blob_vars for token in ("cloudfrontdistributionid", "frontend-assets", "stgdomain", "admin-ui")):
        scores["FE"] += 3
    if any(key.endswith("ClusterServiceName") for key in variables):
        scores["BE"] += 2
    if any(key.endswith("ECSClusterName") or key.endswith("ApplicationLoadBalancerARN") for key in variables):
        scores["INFRA"] += 1

    name = str(payload.get("pipeline_name") or "").lower()
    title = str(payload.get("title") or "").lower()
    pipe = f"{name} {title}"
    if "infra" in pipe or "iac" in pipe:
        scores["INFRA"] += 3
    if any(token in pipe for token in ("-fe", "frontend", "ui-service", "admin-center")):
        scores["FE"] += 3
    if any(token in pipe for token in ("-be", "backend", "api-service", "service-cd")):
        scores["BE"] += 3

    # Classic Infra-Automation that provisions ECS services is INFRA (provisioning),
    # even though BE resources appear in the inventory.
    if "infra" in pipe and scores["INFRA"] >= scores["FE"]:
        return DeploymentKind.INFRA

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    top_name, top_score = ranked[0]
    second_score = ranked[1][1]
    if top_score == 0:
        return DeploymentKind.UNKNOWN
    if second_score > 0 and top_score - second_score <= 1 and {ranked[0][0], ranked[1][0]} <= {"INFRA", "BE"}:
        # Infra pipeline that also stands up ECS services
        if "infra" in pipe:
            return DeploymentKind.INFRA
        return DeploymentKind.MIXED
    if second_score > 0 and top_score == second_score:
        return DeploymentKind.MIXED
    return DeploymentKind[top_name]


def discover_services_from_payload(payload: dict[str, Any]) -> list[ExpectedService]:
    variables = _collect_variables(payload)
    stack_names = _collect_stack_names(payload)
    kind = infer_deployment_kind(payload)
    cluster = _first_value(variables, suffixes=("ECSClusterName",), key_contains=("ECSClusterName",))
    services: list[ExpectedService] = []

    for stack in stack_names:
        services.append(
            ExpectedService(
                service_type=ServiceType.CFN_STACK,
                name=stack,
                identifiers=ResourceIdentifiers(extra={"stack_name": stack, "stack_kind": _stack_kind(stack)}),
                deployment_action={"source": "infra_export", "mode": _mode_for_kind(kind)},
            )
        )

    if cluster:
        services.append(
            ExpectedService(
                service_type=ServiceType.ECS_CLUSTER,
                name=cluster,
                identifiers=ResourceIdentifiers(cluster=cluster, extra={"cluster": cluster}),
                deployment_action={"source": "infra_export", "mode": _mode_for_kind(kind)},
            )
        )

    for key, value in variables.items():
        if not (key.endswith("ClusterServiceName") or key.endswith("-ClusterServiceName")):
            continue
        service_name = str(value)
        task_def = _related_value(variables, key, ("TaskDefinition",))
        family, revision = _split_task_definition(task_def)
        ecr_repo = _related_ecr_repo(variables, key)
        services.append(
            ExpectedService(
                service_type=ServiceType.ECS,
                name=service_name,
                identifiers=ResourceIdentifiers(
                    cluster=cluster,
                    service_name=service_name,
                    task_definition_family=family,
                    task_definition_revision=revision,
                    extra={"source_key": key, "task_definition": task_def},
                ),
                expected_artifact=ExpectedArtifact(image_repository=ecr_repo),
                deployment_action={
                    "source": "infra_export",
                    "mode": _mode_for_kind(kind),
                    "resolve_digest_from_runtime": True,
                },
            )
        )

    for key, value in variables.items():
        lower = key.lower()
        if key.endswith("ECRRepoURI") or key.endswith("RepoURI"):
            repo = _ecr_repo_from_uri(str(value))
            if repo:
                services.append(
                    ExpectedService(
                        service_type=ServiceType.ECR,
                        name=repo,
                        identifiers=ResourceIdentifiers(
                            extra={"repository_name": repo, "repository_uri": str(value)}
                        ),
                        expected_artifact=ExpectedArtifact(image_repository=repo),
                        deployment_action={"source": "infra_export", "mode": _mode_for_kind(kind)},
                    )
                )
        elif key.endswith("RepoName") and "ecr" in lower:
            services.append(
                ExpectedService(
                    service_type=ServiceType.ECR,
                    name=str(value),
                    identifiers=ResourceIdentifiers(extra={"repository_name": str(value)}),
                    expected_artifact=ExpectedArtifact(image_repository=str(value)),
                    deployment_action={"source": "infra_export", "mode": _mode_for_kind(kind)},
                )
            )

    alb_arn = _first_value(variables, suffixes=("ApplicationLoadBalancerARN",), key_contains=("LoadBalancerARN",))
    alb_dns = _first_value(
        variables,
        suffixes=("ALBDNS", "ApplicationLoadBalancerDNS", "LoadBalancerName", "LoadBalancerDNS"),
    )
    if alb_arn or alb_dns:
        dns = _strip_http(alb_dns) if alb_dns else None
        services.append(
            ExpectedService(
                service_type=ServiceType.ALB,
                name=str(dns or alb_arn),
                identifiers=ResourceIdentifiers(
                    extra={
                        "load_balancer_arn": alb_arn,
                        "dns_name": dns,
                        "smoke_url": f"http://{dns}/" if dns else None,
                    }
                ),
                deployment_action={"source": "infra_export", "mode": _mode_for_kind(kind)},
            )
        )

    for key, value in variables.items():
        lower = key.lower()
        if key.endswith("S3BucketARN") or key.endswith("BucketARN") or str(value).startswith("arn:aws:s3:::"):
            continue
        if (
            key.endswith("S3BucketName")
            or key.endswith("BucketName")
            or "frontend-assets" in lower
            or ("s3bucket" in lower and "arn" not in lower)
        ):
            bucket = str(value)
            is_fe_bucket = any(token in lower or token in bucket.lower() for token in _FE_TOKENS)
            services.append(
                ExpectedService(
                    service_type=ServiceType.S3,
                    name=bucket,
                    identifiers=ResourceIdentifiers(
                        extra={"bucket_name": bucket, "fe_assets": is_fe_bucket}
                    ),
                    deployment_action={
                        "source": "infra_export",
                        "mode": "fe_deploy" if is_fe_bucket else _mode_for_kind(kind),
                    },
                )
            )
        if "cloudfrontdistributionid" in lower or lower.endswith("distributionid"):
            dist_id = str(value)
            origin_bucket = _first_value(
                variables,
                suffixes=("S3BucketName", "BucketName"),
                key_contains=("frontend-assets", "admin-ui", "frontend", "s3bucket"),
            )
            domain = None
            for dkey, dval in variables.items():
                dlower = dkey.lower()
                if dlower.startswith("stgdomain") or dlower.endswith("stgdomain") or "cloudfrontdomain" in dlower:
                    domain = str(dval)
                    break
            services.append(
                ExpectedService(
                    service_type=ServiceType.CLOUDFRONT,
                    name=dist_id,
                    identifiers=ResourceIdentifiers(
                        extra={
                            "distribution_id": dist_id,
                            "origin_bucket": origin_bucket,
                            "smoke_url": f"https://{domain}/" if domain else None,
                        }
                    ),
                    deployment_action={"source": "infra_export", "mode": "fe_deploy"},
                )
            )

    return _dedupe_services(services)


def build_smoke_tests_from_inventory(
    services: list[ExpectedService], existing_urls: set[str]
) -> list[dict[str, Any]]:
    """Derive smoke URLs from ALB / CloudFront IaC outputs when DA omitted smoke_tests."""
    smokes: list[dict[str, Any]] = []
    for service in services:
        url = service.identifiers.extra.get("smoke_url")
        if not url or url in existing_urls:
            continue
        existing_urls.add(url)
        smokes.append({"name": f"smoke-{service.service_type.value.lower()}-{service.name}", "url": url})
    return smokes


def is_bogus_pipeline_named_ecs(service: ExpectedService, pipeline_name: str | None) -> bool:
    if service.service_type != ServiceType.ECS:
        return False
    cluster = (service.identifiers.cluster or "").strip().lower()
    name = (service.identifiers.service_name or service.name or "").strip().lower()
    pipe = (pipeline_name or "").strip().lower()
    return cluster in {"default", ""} and bool(pipe) and name == pipe


def summarize_pipeline_deployment(payload: dict[str, Any], kind: DeploymentKind) -> dict[str, Any]:
    """Human-readable summary: which pipeline ran and what it deployed."""
    releases = payload.get("releases") or []
    stacks = _collect_stack_names(payload)
    variables = _collect_variables(payload)
    return {
        "pipeline_name": payload.get("pipeline_name"),
        "pipeline_definition_id": payload.get("pipeline_definition_id"),
        "deployment_kind": kind.value,
        "stages": payload.get("stages") or [r.get("stage") for r in releases],
        "release_ids": [r.get("release_id") for r in releases],
        "stack_names": stacks,
        "resolved_from_iac": {
            "cluster": _first_value(variables, suffixes=("ECSClusterName",)),
            "ecs_services": [
                str(v) for k, v in variables.items() if k.endswith("ClusterServiceName")
            ],
            "task_definitions": [
                str(v) for k, v in variables.items() if k.endswith("TaskDefinition")
            ],
            "ecr_repos": [
                str(v)
                for k, v in variables.items()
                if k.endswith("RepoName") or k.endswith("ECRRepoURI")
            ],
            "alb_dns": _first_value(variables, suffixes=("ALBDNS", "ApplicationLoadBalancerDNS")),
            "s3_buckets": [
                str(v) for k, v in variables.items() if k.endswith("S3BucketName") or k.endswith("BucketName")
            ],
            "cloudfront_ids": [
                str(v)
                for k, v in variables.items()
                if "CloudFrontDistributionId" in k or k.endswith("DistributionId")
            ],
        },
        "notes": [
            "sha256 digests are not stored in ADO IaC variable groups; "
            "verifier resolves them from running ECS tasks / ECR when needed.",
            "ADO Library variable groups are not readable via current MCP; "
            "use infra_export.variables populated by the Deployment Agent from those groups.",
        ],
    }


def _mode_for_kind(kind: DeploymentKind) -> str:
    if kind == DeploymentKind.FE:
        return "fe_deploy"
    if kind == DeploymentKind.BE:
        return "app_deploy"
    return "infra_inventory"


def _stack_kind(stack: str) -> str:
    lower = stack.lower()
    if any(token in lower for token in _FE_TOKENS):
        return "FE"
    if "ecs-cluster" in lower or "ecr-stack" in lower or "alb-stack" in lower:
        return "INFRA"
    if "-ecs-stack" in lower:
        return "BE"
    if any(token in lower for token in _INFRA_STACK_TOKENS):
        return "INFRA"
    return "OTHER"


def _collect_variables(payload: dict[str, Any]) -> dict[str, Any]:
    variables: dict[str, Any] = {}
    infra = payload.get("infra_export") or {}
    if isinstance(infra.get("variables"), dict):
        variables.update(infra["variables"])
    contract = payload.get("environment_contract") or {}
    if isinstance(contract.get("stack_outputs"), dict):
        for key, value in contract["stack_outputs"].items():
            variables.setdefault(key, value)
    return {str(k): v for k, v in variables.items() if v not in (None, "")}


def _collect_stack_names(payload: dict[str, Any]) -> list[str]:
    names: list[str] = []
    infra = payload.get("infra_export") or {}
    names.extend(str(x) for x in (infra.get("stack_names") or []) if x)
    contract = payload.get("environment_contract") or {}
    names.extend(str(x) for x in (contract.get("cloudformation_stack_names") or []) if x)
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _first_value(
    variables: dict[str, Any],
    suffixes: tuple[str, ...] = (),
    key_contains: tuple[str, ...] = (),
) -> str | None:
    for key, value in variables.items():
        if any(key.endswith(suffix) for suffix in suffixes):
            return str(value)
    for key, value in variables.items():
        lower = key.lower()
        if any(token.lower() in lower for token in key_contains):
            return str(value)
    for key, value in variables.items():
        lower = key.lower()
        if any(lower.endswith(suffix.lower()) for suffix in suffixes):
            return str(value)
    return None


def _related_value(variables: dict[str, Any], source_key: str, suffixes: tuple[str, ...]) -> str | None:
    prefix = re.sub(r"ClusterServiceName$", "", source_key)
    for suffix in suffixes:
        direct = variables.get(f"{prefix}{suffix}")
        if direct:
            return str(direct)
    for key, value in variables.items():
        if key.startswith(prefix) and any(key.endswith(suffix) for suffix in suffixes):
            return str(value)
    return None


def _related_ecr_repo(variables: dict[str, Any], service_key: str) -> str | None:
    families = ("dlpapi", "slpapi", "dlpprocessor", "dlpextractor", "api", "ui")
    for family in families:
        if family in service_key.lower():
            for key, value in variables.items():
                if family in key.lower() and (key.endswith("RepoName") or key.endswith("ECRRepoURI")):
                    if key.endswith("ECRRepoURI"):
                        return _ecr_repo_from_uri(str(value))
                    return str(value)
    return None


def _ecr_repo_from_uri(uri: str) -> str | None:
    if "/" not in uri:
        return uri or None
    return uri.rsplit("/", 1)[-1] or None


def _split_task_definition(task_def: str | None) -> tuple[str | None, str | None]:
    if not task_def:
        return None, None
    name = task_def.rsplit("/", 1)[-1]
    if ":" in name:
        family, revision = name.rsplit(":", 1)
        return family, revision
    return name, None


def _strip_http(value: str) -> str:
    return re.sub(r"^https?://", "", value).rstrip("/")


def _dedupe_services(services: list[ExpectedService]) -> list[ExpectedService]:
    seen: set[tuple[str, str]] = set()
    out: list[ExpectedService] = []
    for service in services:
        key = (
            service.service_type.value,
            str(
                service.identifiers.service_name
                or service.identifiers.cluster
                or service.identifiers.function_name
                or service.identifiers.extra.get("bucket_name")
                or service.identifiers.extra.get("distribution_id")
                or service.identifiers.extra.get("repository_name")
                or service.identifiers.extra.get("stack_name")
                or service.name
            ),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(service)
    return out
