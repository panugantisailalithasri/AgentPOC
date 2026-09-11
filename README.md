# Deployment Verification Agent (POC)

Verifies whether a backend ECS deployment succeeded after the Deployment Agent (or a mock of it) finishes.

This POC implements the three-phase spec:

1. **Phase 1** — deterministic ECS health and coverage checks
2. **Phase 2** — investigation when a check fails, a resource is missing, or coverage is incomplete
3. **Phase 3** — root cause, risk, and rollback *recommendation* only (no AWS writes)

The real Deployment Agent is not required. Use `fixtures/mock-deployment-agent-output.json`.

## What this verifies (deterministic only)

No Phase 2/3. One pass of Infra/FE/BE checks based on Deployment Agent output:

1. Read `pipeline_name` + `releases[]` → which ADO pipeline/stage ran  
2. Classify **deployment kind** from **stack names** + IaC variable keys (+ pipeline name)  
3. Resolve inventory from `infra_export.variables` / stack outputs (ADO Library groups are already exported here by the Deployment Agent)  
4. Run checks:
   - **INFRA:** CFN stacks, ECS cluster, ECR repos, ALB, S3 buckets, ECS services steady  
   - **BE:** cluster/service + task revision from IaC; `sha256` from running task/ECR when DA did not supply it (digests are **not** in IaC variable groups)  
   - **FE:** S3 assets bucket (+ objects in window), CloudFront distribution/origin, smoke URL from `stgDomain` / CF domain when present  

See `docs/DEPLOYMENT_AGENT_CONTRACT.md`.

## Setup

You must run from the **project folder**, not from `C:\Users\PanugantiSailalithaS`. The prompt should show `deployment-verification-agent`. Use the project venv (not system Python).

```powershell
cd C:\Users\PanugantiSailalithaS\deployment-verification-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

After activate, the prompt starts with `(.venv)`. Then either:

```powershell
python -m deployment_verification --input fixtures\mock-deployment-agent-output.json --reports-dir reports
```

or (works even if the venv is not activated, as long as you are in the project folder):

```powershell
cd C:\Users\PanugantiSailalithaS\deployment-verification-agent
.\.venv\Scripts\python.exe run.py --input fixtures\mock-deployment-agent-output.json --reports-dir reports
```

## Run offline (no AWS credentials)

Healthy path (Phase 1 only):

```powershell
python -m deployment_verification --input fixtures\mock-deployment-agent-output.json --stub fixtures\aws-stub-healthy.json --reports-dir reports
```

Unhealthy path (Phase 1 + 2 + 3):

```powershell
python -m deployment_verification --input fixtures\mock-deployment-agent-output.json --stub fixtures\aws-stub-unhealthy.json --reports-dir reports
```

Infra path (schema 1.4 + stack output discovery):

```powershell
python -m deployment_verification --input fixtures\mock-infra-slp-dlp-output.json --stub fixtures\aws-stub-infra-healthy.json --reports-dir reports
```

## Run against live AWS

Without `--stub`, boto3 calls real ECS. That needs credentials on **this machine**. The ADO service connection (`AWS_FreyDevOps_CIcd-automation-Freyr-Unified-RIMS`) is not available in your local PowerShell. If credentials are missing you will get a clear error instead of a boto3 stack trace.

```powershell
aws configure
# Access key, secret key, region us-east-1, output json
python -m deployment_verification --input fixtures\mock-deployment-agent-output.json --reports-dir reports
```

Or:

```powershell
$env:AWS_ACCESS_KEY_ID = "<access-key>"
$env:AWS_SECRET_ACCESS_KEY = "<secret-key>"
$env:AWS_DEFAULT_REGION = "us-east-1"
python -m deployment_verification --input fixtures\mock-deployment-agent-output.json --reports-dir reports
```

Needs read-only IAM: `ecs:Describe*`, `ecs:ListTasks`, `ecs:ListServiceDeployments`, `ecr:DescribeImages`, `elasticloadbalancing:DescribeTargetHealth`, `logs:GetLogEvents`, `cloudwatch:GetMetricStatistics`.

Phase 1 uses the current ECS service-deployment history shown on the console's **Deployments** tab. A deployment passes only when a service deployment created inside the input verification window has status `SUCCESSFUL`. For older SDKs or regions where this API is unavailable, the agent falls back to the legacy `DescribeServices.deployments[].rolloutState` check and labels that fallback in the report.

Confirm credentials first:

```powershell
aws sts get-caller-identity
aws ecs describe-services --cluster ff-docs-docs-devsecops-cluster --services ff-docs-docs-devsecops-docsapi-service --region us-east-1
```

## Tests

```powershell
pytest
```

## Reports

| File | When |
|---|---|
| `reports/deployment-verification.json` / `.html` | Always (Phase 1) |
| `reports/deployment-investigation.json` / `.html` | Phase 2 required |
| `reports/deployment-intelligence.json` / `.html` | After Phase 2 |

A mandatory Phase 1 failure keeps the process exit code at **1**. Phase 3 never changes that.

## Input contract (Deployment Agent)

Required: `deployment_id`, `aws.account_id`, `aws.region`, `verification_window`, `services[]` with `service_type=ECS` and **resolved** `cluster` + `service_name`, plus `identifiers.task_definition_revision` and `expected_artifact.image_digest`.

Do not pass live running counts. Optional: smoke URLs, log group, target group ARN (otherwise discovered from the ECS service).

For ECS, `expected_artifact.image_digest` must be a complete `sha256:` digest with 64 hexadecimal characters. A tag such as `latest`, a Git commit ID, or an abbreviated digest is not accepted. If AWS does not return `imageDigest` for a running container, Phase 1 reports `CHECK_MISSING` instead of claiming a match.

The mock also preserves the Deployment Agent 1.2 envelope, including `written_at`, build/work-item metadata, `outcome`, `qualification_gate`, `environment_contract`, and process metadata. Fields that the verifier needs but the Deployment Agent output does not currently provide are appended at the root using the verification contract above. The adapter retains the complete source payload in `DeploymentContext.raw` while normalizing the appended verification fields.

Values that are not known must remain `null` or empty in the Deployment Agent metadata. Required verification fields must contain resolved values before a live verification run; do not invent resource identifiers or copy them from another application.

## Out of scope (still)

Automatic rollback, write APIs to AWS, and classic Release definition scraping from ADO (Deployment Agent should resolve IaC outputs before calling this verifier).
