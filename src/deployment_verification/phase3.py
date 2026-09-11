from __future__ import annotations

from deployment_verification.models import DeploymentContext, Phase1Result, Phase2Result, Phase3Result


def run_phase3(context: DeploymentContext, phase1: Phase1Result, phase2: Phase2Result) -> Phase3Result:
    observed: list[str] = []
    inferred: list[str] = []
    correlated: list[str] = []
    uncertainties: list[str] = []

    for check in phase1.FailedChecks:
        observed.append(f"FAIL {check.get('check')}: expected={check.get('expected')} actual={check.get('actual')} ({check.get('reason')})")
    for check in phase1.ResourceNotFound:
        observed.append(f"RESOURCE_NOT_FOUND {check.get('check')} on {check.get('resource')}")
    for check in phase1.MissingChecks:
        observed.append(f"CHECK_MISSING {check.get('check')} on {check.get('resource')}")

    exits = []
    for block in (phase2.Evidence or {}).get("ecs") or []:
        exits.extend(block.get("container_exit_reasons") or [])
        for event in block.get("events") or []:
            correlated.append(str(event.get("message")))
    for log_block in (phase2.Evidence or {}).get("logs") or []:
        for event in log_block.get("events") or []:
            correlated.append(str(event.get("message")))
            observed.append(f"Log line: {event.get('message')}")
    for alb in (phase2.Evidence or {}).get("alb") or []:
        for target in alb.get("targets") or []:
            correlated.append(f"Target {target.get('target')} state={target.get('state')} reason={target.get('reason')}")

    root_cause, confidence, risk, rollback = _classify(phase1, exits, correlated)
    inferred.append(root_cause)
    if not correlated:
        uncertainties.append("Limited runtime evidence beyond Phase 1 check results.")
    if any(item.get("status") == "INCONCLUSIVE" for item in phase2.DynamicCheckResults):
        uncertainties.append("One or more dynamic checks were inconclusive.")
        confidence = "low" if confidence == "high" else confidence

    actions = _actions(root_cause, context)
    affected = phase2.AffectedServices or [service.name for service in context.services]
    status = "FAIL" if not phase1.DeploymentHealthy else "PASS"

    teams = (
        f"Status: {status}. Service: {', '.join(affected)}. "
        f"Root cause: {root_cause} Confidence: {confidence}. Risk: {risk}. "
        f"Rollback: {rollback}. Next: {actions[0] if actions else 'Review evidence'}."
    )

    return Phase3Result(
        OverallDeploymentStatus=status,
        AffectedServices=affected,
        RootCause=root_cause,
        CorrelatedEvidence=correlated[:20],
        ObservedFacts=observed,
        InferredConclusions=inferred,
        DeploymentRisk=risk,
        RollbackRecommendation=rollback,
        SuggestedActions=actions,
        ConfidenceLevel=confidence,
        KnownUncertainties=uncertainties,
        TeamsSummary=teams,
    )


def _classify(phase1: Phase1Result, exits: list[dict], correlated: list[str]) -> tuple[str, str, str, str]:
    joined = " ".join(correlated).lower()
    if phase1.ResourceNotFound:
        return (
            "Expected ECS cluster/service was not found in the target account/region. The Deployment Agent identifiers may be unresolved or the service was not created.",
            "high",
            "High",
            "Not Required",
        )
    db_logs = "database" in joined or "datasource" in joined or "could not connect" in joined
    essential_exit = any("essential container" in str(item.get("reason") or "").lower() for item in exits) or any(
        item.get("exitCode") not in (None, 0) for item in exits
    )
    if db_logs and essential_exit:
        return (
            "New ECS tasks are crashing on startup because the application cannot connect to its database. This is inferred from container exit code 1 plus runtime logs in the verification window.",
            "high",
            "High",
            "Recommended",
        )
    if essential_exit:
        return (
            "New ECS tasks stopped because an essential container exited. The process failed during startup or immediately after start.",
            "medium",
            "High",
            "Recommended",
        )
    unhealthy = "unhealthy" in joined or "health checks failed" in joined
    count_fail = any("desired count equals running" in str(check.get("check") or "").lower() for check in phase1.FailedChecks)
    if unhealthy and count_fail:
        return (
            "The ECS service cannot reach desired count because tasks or ALB targets are failing health checks.",
            "high",
            "High",
            "Recommended",
        )
    if any("image digest" in str(check.get("check") or "").lower() for check in phase1.FailedChecks):
        return (
            "Running tasks are not using the expected image digest from this deployment. force-new-deployment may have launched the previous image, or the digest in the Deployment Agent output is wrong.",
            "medium",
            "Medium",
            "Optional",
        )
    if phase1.MissingChecks and not phase1.FailedChecks:
        return (
            "Deterministic Phase 1 coverage was incomplete for at least one existing service. Dynamic checks were used; treat conclusions as limited to those checks.",
            "low",
            "Medium",
            "Not Required",
        )
    if phase1.FailedChecks:
        return (
            "One or more mandatory ECS health checks failed. See FailedChecks and Phase 2 evidence for the specific mismatch.",
            "medium",
            "High",
            "Optional",
        )
    return (
        "No failure pattern was identified. Phase 3 should not have been required for a fully healthy, fully covered deployment.",
        "low",
        "Low",
        "Not Required",
    )


def _actions(root_cause: str, context: DeploymentContext) -> list[str]:
    actions = [
        "Immediate: inspect ECS stopped-task reason and container exit code in the verification window.",
        "Immediate: review application/runtime logs in the configured log group.",
        "Follow-up: verify configuration/secrets and database connectivity for the docs-api service.",
        "Follow-up: confirm the ECR image digest that was pushed matches the digest in the Deployment Agent output.",
    ]
    lowered = root_cause.lower()
    if "database" in lowered:
        actions.insert(0, "Immediate: validate database connectivity, credentials, and security-group rules for docs-api.")
    if "image digest" in lowered:
        actions.insert(0, "Immediate: confirm update-service --force-new-deployment pulled the newly pushed digest.")
    if "not found" in lowered:
        actions.insert(0, "Immediate: confirm the Deployment Agent resolved Clustername-Docs and ECSName-Docs-Service CloudFormation outputs.")
    if "Recommended" in root_cause or "crashing" in lowered or "exited" in lowered:
        actions.append(
            "If this environment allows it, roll back to the previous ECS task definition. Do not auto-execute rollback from this agent."
        )
    actions.append(f"Preserve evidence for release {context.release_id} / deployment {context.deployment_id}.")
    return actions
