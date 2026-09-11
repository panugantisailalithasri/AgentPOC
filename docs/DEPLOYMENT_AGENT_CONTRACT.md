# Deployment Agent → Verification Agent contract

This document lists what the Deployment Agent should emit so Infra / FE / BE post-deploy verification can run reliably.

## Always required

| Field | Why |
|-------|-----|
| `deployment_id` | Correlation / reports |
| `pipeline_name` + `pipeline_definition_id` | Classify INFRA vs FE vs BE and locate ADO definition |
| `stages[]` / environment | Map to AWS account / variable groups |
| `verification_window.start/end` | Object upload window, ECS deployment window |
| `aws.account_id`, `aws.region`, `aws.service_connection` | Target account for live checks |
| `qualification_gate.ready_for_verification_agent` | Gate before calling this agent |
| `releases[]` with status / times | Confirm pipeline succeeded independently of runtime health |

## Critical fix for Infra (from SLP-DLP sample)

Do **not** put the pipeline name as an ECS service under `cluster=default`.

Bad (caused false `running=0/0`):

```json
"services": [{
  "service_type": "ecs",
  "cluster_name": "default",
  "service_name": "ML-SLP-DLP-Infra-Automation"
}]
```

Good: emit resolved resources from IaC variable groups / stack outputs:

```json
"services": [
  {
    "service_type": "ECS",
    "name": "ff-ml-slp-dlp-devsecops-dlp-service",
    "identifiers": {
      "cluster": "ff-ml-slp-dlp-devsecops-cluster",
      "service_name": "ff-ml-slp-dlp-devsecops-dlp-service",
      "task_definition_revision": "6"
    }
  }
]
```

Or keep discovering from `infra_export` (supported by this agent).

## IaC variables vs digests / FE URLs

Checked against Freyr-Unified-RIMS patterns (URF CD YAML + SLP-DLP `infra_export`):

| Need | In ADO IaC / stack outputs? | Source |
|------|-----------------------------|--------|
| ECS cluster / service / task revision | Yes | `…ECSClusterName`, `…ClusterServiceName`, `…TaskDefinition` |
| ECR repo URI/name | Yes | `…ECRRepoURI`, `…RepoName` |
| ALB ARN/DNS | Yes | `…ApplicationLoadBalancerARN`, `…ALBDNS` |
| S3 bucket | Yes | `…S3BucketName` / frontend-assets bucket vars |
| CloudFront distribution ID / domain | Yes (FE CD) | `cloudFrontDistributionId*`, `stgDomain*` |
| Full `sha256:` image digest | **No** | CI artifact / running ECS task `imageDigest` / ECR |
| Smoke URL | Partially | FE: `https://{stgDomain}/`; Infra ALB DNS may be internal |

The Deployment Agent should keep exporting Library group values into `infra_export.variables` (current MCP cannot read ADO Library directly).

Verification checks: CFN status, ECS cluster ACTIVE, each ECS service steady, ECR repo exists, ALB active, S3 bucket exists.

## Backend (service) deployments — include

| Field | Why |
|-------|-----|
| `services[].identifiers.cluster` + `service_name` | ECS target |
| `services[].identifiers.task_definition_revision` | Revision pin |
| `services[].expected_artifact.image_digest` | Full `sha256:` (64 hex) |
| Optional `target_group_arn`, `log_group` | ALB TG + logs |
| Optional `smoke_tests[]` | HTTP health endpoints |

## Frontend deployments — include

| Field | Why |
|-------|-----|
| S3 `bucket_name` | Bucket exists + objects in window |
| CloudFront `distribution_id` | Distribution exists / enabled |
| `origin_bucket` (optional) | Origin points at deploy bucket |
| `smoke_tests[]` with public URL | End-user path check |
| Optional recent invalidation IDs | Stronger FE evidence |

## Recommended envelope additions

```json
{
  "deployment_kind": "INFRA|FE|BE|MIXED",
  "resource_inventory": [
    {"type": "CFN_STACK", "id": "..."},
    {"type": "ECS_CLUSTER", "id": "..."},
    {"type": "ECS_SERVICE", "cluster": "...", "service": "...", "task_definition": "..."},
    {"type": "ECR", "repository": "..."},
    {"type": "ALB", "arn": "...", "dns": "..."},
    {"type": "S3", "bucket": "..."},
    {"type": "CLOUDFRONT", "distribution_id": "..."}
  ],
  "expected_artifacts": [
    {"service": "...", "image_digest": "sha256:...", "image_tag": "..."}
  ],
  "smoke_tests": [
    {"name": "alb-root", "url": "http://....elb.amazonaws.com/", "expected_status": 200}
  ]
}
```

## What this verification agent does today

1. Accepts schema **1.2** (legacy ECS) and **1.4** (with `infra_export`)
2. Ignores bogus `default/<pipeline-name>` ECS rows
3. Discovers CFN / ECS cluster / ECS services / ECR / ALB / S3 / CloudFront from stack outputs
4. Runs Phase 1 checks offline via `--stub` or live AWS via boto3
5. Keeps Phase 2/3 investigation path for failures

## IAM needed for live runs

Read-only: CloudFormation DescribeStacks, ECS Describe*, ECR DescribeRepositories/Images, ELBv2 Describe*, S3 HeadBucket/ListBucket, CloudFront GetDistribution/ListInvalidations, optional CloudWatch Logs/Metrics.
