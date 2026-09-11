from __future__ import annotations

from typing import Any

from deployment_verification.adapter import load_deployment_agent_output
from deployment_verification.aws_facade import create_aws_facade
from deployment_verification.checks import run_deterministic_checks
from deployment_verification.reports import write_reports
from deployment_verification.settings import load_settings


def run_verification(
    input_path: str,
    reports_dir: str,
    stub_path: str | None = None,
    settings_path: str | None = None,
) -> dict[str, Any]:
    """Run deterministic Infra/FE/BE checks only (no investigation / RCA phases)."""
    settings = load_settings(settings_path)
    context = load_deployment_agent_output(input_path)
    aws = create_aws_facade(context.aws_region, stub_path)
    result = run_deterministic_checks(context, aws, settings)

    payload = {
        "deployment": {
            "deployment_id": context.deployment_id,
            "release_id": context.release_id,
            "environment": context.environment,
            "application": context.application,
            "pipeline_name": context.pipeline_name,
            "deployment_kind": context.deployment_kind.value,
            "aws": {"account_id": context.aws_account_id, "region": context.aws_region},
            "pipeline_summary": context.pipeline_summary,
            "discovery_notes": context.discovery_notes,
        },
        **result.to_dict(),
    }
    write_reports(reports_dir, "deployment-verification", payload)

    return {
        "exit_code": 0 if result.DeploymentHealthy else 1,
        "deployment_kind": context.deployment_kind.value,
        "pipeline_summary": context.pipeline_summary,
        "discovery_notes": context.discovery_notes,
        "verification": result.to_dict(),
    }
