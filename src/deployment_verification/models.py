from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    CHECK_MISSING = "CHECK_MISSING"
    INCONCLUSIVE = "INCONCLUSIVE"
    UNSUPPORTED = "UNSUPPORTED"


class ServiceType(str, Enum):
    ECS = "ECS"
    ECS_CLUSTER = "ECS_CLUSTER"
    LAMBDA = "LAMBDA"
    ALB = "ALB"
    NLB = "NLB"
    RDS = "RDS"
    API_GATEWAY = "API_GATEWAY"
    S3 = "S3"
    CLOUDFRONT = "CLOUDFRONT"
    ECR = "ECR"
    CFN_STACK = "CFN_STACK"
    OTHER = "OTHER"


class DeploymentKind(str, Enum):
    INFRA = "INFRA"
    FE = "FE"
    BE = "BE"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


IMPLEMENTED_PHASE1_TYPES = {
    ServiceType.ECS,
    ServiceType.ECS_CLUSTER,
    ServiceType.ALB,
    ServiceType.LAMBDA,
    ServiceType.S3,
    ServiceType.CLOUDFRONT,
    ServiceType.ECR,
    ServiceType.CFN_STACK,
}


@dataclass
class SmokeTest:
    name: str
    url: str
    expected_status: int = 200
    expected_body_contains: str | None = None


@dataclass
class ExpectedArtifact:
    image_repository: str | None = None
    image_tag: str | None = None
    image_digest: str | None = None
    lambda_version: str | None = None
    code_sha: str | None = None


@dataclass
class ResourceIdentifiers:
    cluster: str | None = None
    service_name: str | None = None
    task_definition_family: str | None = None
    task_definition_revision: str | None = None
    log_group: str | None = None
    target_group_arn: str | None = None
    function_name: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExpectedService:
    service_type: ServiceType
    name: str
    identifiers: ResourceIdentifiers
    expected_artifact: ExpectedArtifact = field(default_factory=ExpectedArtifact)
    deployment_action: dict[str, Any] = field(default_factory=dict)


@dataclass
class VerificationWindow:
    start: str
    end: str


@dataclass
class DeploymentContext:
    deployment_id: str
    release_id: str
    pipeline_run_id: str
    application: str
    environment: str
    deployment_status: str
    started_at: str
    completed_at: str
    verification_window: VerificationWindow
    aws_account_id: str
    aws_region: str
    services: list[ExpectedService]
    smoke_tests: list[SmokeTest]
    deployment_agent_checks: list[dict[str, Any]]
    raw: dict[str, Any]
    deployment_kind: DeploymentKind = DeploymentKind.UNKNOWN
    pipeline_name: str | None = None
    discovery_notes: list[str] = field(default_factory=list)
    pipeline_summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class CheckResult:
    check: str
    status: CheckStatus
    resource: str
    reason: str
    expected: Any = None
    actual: Any = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass
class VerificationResult:
    DeploymentHealthy: bool
    ServiceResults: list[dict[str, Any]]
    FailedChecks: list[dict[str, Any]]
    MissingChecks: list[dict[str, Any]]
    SkippedChecks: list[dict[str, Any]]
    ResourceNotFound: list[dict[str, Any]]
    ChecksPerformed: list[dict[str, Any]]
    ExpectedServices: list[dict[str, Any]]
    CoverageComplete: bool
    VerificationWindow: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Keep alias so existing internal references compile without changes
Phase1Result = VerificationResult
