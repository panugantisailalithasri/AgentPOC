from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

CREDENTIALS_HELP = """AWS credentials were not found, so live ECS cannot be queried.

This machine has no ~/.aws profile and no AWS_ACCESS_KEY_ID in the environment.
The Azure DevOps service connection is only available inside the pipeline, not in your local terminal.

To read the real console values (desired/running), configure credentials for account 110133336476 / us-east-1, then rerun without --stub:

  aws configure
  python -m deployment_verification --input fixtures\\mock-deployment-agent-output.json --reports-dir reports

Or set:

  $env:AWS_ACCESS_KEY_ID = "<access-key>"
  $env:AWS_SECRET_ACCESS_KEY = "<secret-key>"
  $env:AWS_DEFAULT_REGION = "us-east-1"

Until credentials exist, use the offline stub (this does not call AWS):

  python -m deployment_verification --input fixtures\\mock-deployment-agent-output.json --stub fixtures\\aws-stub-healthy.json --reports-dir reports
"""


class AwsCredentialsError(RuntimeError):
    """Raised before any ECS call when boto3 has nothing to sign with."""


class AwsFacade(Protocol):
    def describe_ecs_service(self, cluster: str, service_name: str) -> dict[str, Any] | None: ...

    def list_ecs_service_deployments(
        self,
        cluster: str,
        service_name: str,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict[str, Any]] | None: ...

    def list_ecs_tasks(self, cluster: str, service_name: str, desired_status: str | None = None) -> list[dict[str, Any]]: ...

    def describe_task_definition(self, task_definition: str) -> dict[str, Any] | None: ...

    def describe_target_health(self, target_group_arn: str) -> list[dict[str, Any]]: ...

    def get_log_events(self, log_group: str, start: str, end: str, limit: int) -> list[dict[str, Any]]: ...

    def get_service_metrics(self, cluster: str, service_name: str) -> dict[str, Any]: ...

    def describe_ecr_image(self, repository: str, image_digest: str) -> dict[str, Any] | None: ...

    def describe_lambda_function(self, function_name: str) -> dict[str, Any] | None: ...

    def describe_db_instance(self, db_instance_id: str) -> dict[str, Any] | None: ...

    def describe_cloudformation_stack(self, stack_name: str) -> dict[str, Any] | None: ...

    def describe_ecs_cluster(self, cluster: str) -> dict[str, Any] | None: ...

    def describe_ecr_repository(self, repository: str) -> dict[str, Any] | None: ...

    def describe_load_balancer(
        self, arn: str | None = None, dns_name: str | None = None
    ) -> dict[str, Any] | None: ...

    def head_s3_bucket(self, bucket: str) -> bool: ...

    def list_s3_objects_updated_between(
        self, bucket: str, start: str, end: str, prefix: str = "", max_keys: int = 100
    ) -> list[dict[str, Any]]: ...

    def describe_cloudfront_distribution(self, distribution_id: str) -> dict[str, Any] | None: ...

    def list_cloudfront_invalidations(
        self, distribution_id: str, max_items: int = 5
    ) -> list[dict[str, Any]] | None: ...

    def list_ecr_images(self, repository: str, max_results: int = 5) -> list[dict[str, Any]]: ...

    def describe_alb_listeners(self, load_balancer_arn: str) -> list[dict[str, Any]]: ...

    def describe_alb_target_groups(self, load_balancer_arn: str) -> list[dict[str, Any]]: ...


class StubAwsFacade:
    """Offline AWS responses loaded from fixtures — used for POC tests and demos."""

    def __init__(self, stub_path: str | Path):
        self.data = json.loads(Path(stub_path).read_text(encoding="utf-8"))

    def describe_ecs_service(self, cluster: str, service_name: str) -> dict[str, Any] | None:
        return (self.data.get("ecs_services") or {}).get(f"{cluster}|{service_name}")

    def list_ecs_service_deployments(
        self,
        cluster: str,
        service_name: str,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict[str, Any]] | None:
        deployments = self.data.get("ecs_service_deployments")
        if deployments is None:
            return None
        return list(deployments.get(f"{cluster}|{service_name}") or [])

    def list_ecs_tasks(self, cluster: str, service_name: str, desired_status: str | None = None) -> list[dict[str, Any]]:
        tasks = list((self.data.get("ecs_tasks") or {}).get(f"{cluster}|{service_name}") or [])
        if desired_status:
            return [task for task in tasks if task.get("desiredStatus") == desired_status or task.get("lastStatus") == desired_status]
        return tasks

    def describe_task_definition(self, task_definition: str) -> dict[str, Any] | None:
        return (self.data.get("task_definitions") or {}).get(task_definition)

    def describe_target_health(self, target_group_arn: str) -> list[dict[str, Any]]:
        return list((self.data.get("target_health") or {}).get(target_group_arn) or [])

    def get_log_events(self, log_group: str, start: str, end: str, limit: int) -> list[dict[str, Any]]:
        return list(self.data.get("logs") or [])[:limit]

    def get_service_metrics(self, cluster: str, service_name: str) -> dict[str, Any]:
        return dict(self.data.get("metrics") or {})

    def describe_ecr_image(self, repository: str, image_digest: str) -> dict[str, Any] | None:
        key = f"{repository}@{image_digest}"
        return (self.data.get("ecr_images") or {}).get(key)

    def describe_lambda_function(self, function_name: str) -> dict[str, Any] | None:
        return (self.data.get("lambda_functions") or {}).get(function_name)

    def describe_db_instance(self, db_instance_id: str) -> dict[str, Any] | None:
        return (self.data.get("rds_instances") or {}).get(db_instance_id)

    def describe_cloudformation_stack(self, stack_name: str) -> dict[str, Any] | None:
        return (self.data.get("cloudformation_stacks") or {}).get(stack_name)

    def describe_ecs_cluster(self, cluster: str) -> dict[str, Any] | None:
        return (self.data.get("ecs_clusters") or {}).get(cluster)

    def describe_ecr_repository(self, repository: str) -> dict[str, Any] | None:
        return (self.data.get("ecr_repositories") or {}).get(repository)

    def describe_load_balancer(
        self, arn: str | None = None, dns_name: str | None = None
    ) -> dict[str, Any] | None:
        albs = self.data.get("load_balancers") or {}
        if arn and arn in albs:
            return albs[arn]
        if dns_name:
            for item in albs.values():
                if item.get("DNSName") == dns_name or item.get("DNSName") == _strip_http(dns_name):
                    return item
        return None

    def head_s3_bucket(self, bucket: str) -> bool:
        return bucket in (self.data.get("s3_buckets") or {})

    def list_s3_objects_updated_between(
        self, bucket: str, start: str, end: str, prefix: str = "", max_keys: int = 100
    ) -> list[dict[str, Any]]:
        objects = list((self.data.get("s3_objects") or {}).get(bucket) or [])
        start_dt = _datetime(start)
        end_dt = _datetime(end)
        out = []
        for item in objects:
            modified = _datetime(item.get("LastModified"))
            if start_dt and end_dt and modified and start_dt <= modified <= end_dt:
                if not prefix or str(item.get("Key") or "").startswith(prefix):
                    out.append(item)
            if len(out) >= max_keys:
                break
        return out

    def describe_cloudfront_distribution(self, distribution_id: str) -> dict[str, Any] | None:
        return (self.data.get("cloudfront_distributions") or {}).get(distribution_id)

    def list_cloudfront_invalidations(
        self, distribution_id: str, max_items: int = 5
    ) -> list[dict[str, Any]] | None:
        items = list((self.data.get("cloudfront_invalidations") or {}).get(distribution_id) or [])
        return items[:max_items]

    def list_ecr_images(self, repository: str, max_results: int = 5) -> list[dict[str, Any]]:
        return list((self.data.get("ecr_image_lists") or {}).get(repository) or [])[:max_results]

    def describe_alb_listeners(self, load_balancer_arn: str) -> list[dict[str, Any]]:
        return list((self.data.get("alb_listeners") or {}).get(load_balancer_arn) or [])

    def describe_alb_target_groups(self, load_balancer_arn: str) -> list[dict[str, Any]]:
        return list((self.data.get("alb_target_groups") or {}).get(load_balancer_arn) or [])


class LiveAwsFacade:
    def __init__(self, region: str):
        import boto3
        from botocore.exceptions import NoCredentialsError

        session = boto3.Session(region_name=region)
        if session.get_credentials() is None:
            raise AwsCredentialsError(CREDENTIALS_HELP)

        self.region = region
        self.last_error: str | None = None
        try:
            self.ecs = session.client("ecs")
            self.elbv2 = session.client("elbv2")
            self.logs = session.client("logs")
            self.cloudwatch = session.client("cloudwatch")
            self.lambda_client = session.client("lambda")
            self.rds = session.client("rds")
            self.ecr = session.client("ecr")
            self.sts = session.client("sts")
            self.cfn = session.client("cloudformation")
            self.s3 = session.client("s3")
            self.cloudfront = session.client("cloudfront")
        except NoCredentialsError as exc:
            raise AwsCredentialsError(CREDENTIALS_HELP) from exc

    def _caller_account(self) -> str:
        try:
            return str(self.sts.get_caller_identity().get("Account") or "unknown")
        except Exception:
            return "unknown"

    def _cluster_names(self) -> list[str]:
        try:
            arns = self.ecs.list_clusters().get("clusterArns") or []
            return [arn.rsplit("/", 1)[-1] for arn in arns]
        except Exception:
            return []

    def describe_ecs_service(self, cluster: str, service_name: str) -> dict[str, Any] | None:
        from botocore.exceptions import ClientError

        self.last_error = None
        try:
            response = self.ecs.describe_services(cluster=cluster, services=[service_name])
        except ClientError as exc:
            code = (exc.response.get("Error") or {}).get("Code", "")
            account = self._caller_account()
            clusters = self._cluster_names()
            self.last_error = (
                f"AWS {code} for cluster '{cluster}' / service '{service_name}' "
                f"in region {self.region}, signed-in account {account}. "
                f"Clusters visible in this account/region: {clusters or '(none or list-clusters denied)'}."
            )
            if code in {"ClusterNotFoundException", "ClusterNotFound"}:
                return None
            raise

        services = response.get("services") or []
        failures = response.get("failures") or []
        if failures and not services:
            self.last_error = str(failures)
            return None
        if not services:
            return None
        service = services[0]
        return {
            "status": service.get("status"),
            "desiredCount": service.get("desiredCount"),
            "runningCount": service.get("runningCount"),
            "pendingCount": service.get("pendingCount"),
            "taskDefinition": service.get("taskDefinition"),
            "deployments": service.get("deployments") or [],
            "events": [
                {
                    "createdAt": event.get("createdAt").isoformat() if hasattr(event.get("createdAt"), "isoformat") else str(event.get("createdAt")),
                    "message": event.get("message"),
                }
                for event in (service.get("events") or [])[:20]
            ],
            "loadBalancers": service.get("loadBalancers") or [],
        }

    def list_ecs_service_deployments(
        self,
        cluster: str,
        service_name: str,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict[str, Any]] | None:
        from botocore.exceptions import ClientError

        operation = getattr(self.ecs, "list_service_deployments", None)
        if operation is None:
            return None
        request: dict[str, Any] = {
            "cluster": cluster,
            "service": service_name,
            "maxResults": 100,
        }
        created_at = {
            key: parsed
            for key, parsed in (
                ("after", _datetime(start)),
                ("before", _datetime(end)),
            )
            if parsed is not None
        }
        if created_at:
            request["createdAt"] = created_at
        try:
            response = operation(**request)
        except ClientError as exc:
            code = (exc.response.get("Error") or {}).get("Code", "")
            if code == "UnsupportedFeatureException":
                return None
            raise

        deployments = []
        for item in response.get("serviceDeployments") or []:
            deployments.append(
                {
                    "serviceDeploymentArn": item.get("serviceDeploymentArn"),
                    "serviceArn": item.get("serviceArn"),
                    "clusterArn": item.get("clusterArn"),
                    "startedAt": _isoformat(item.get("startedAt")),
                    "createdAt": _isoformat(item.get("createdAt")),
                    "finishedAt": _isoformat(item.get("finishedAt")),
                    "targetServiceRevisionArn": item.get("targetServiceRevisionArn"),
                    "status": item.get("status"),
                    "statusReason": item.get("statusReason"),
                }
            )
        return deployments

    def list_ecs_tasks(self, cluster: str, service_name: str, desired_status: str | None = None) -> list[dict[str, Any]]:
        arns: list[str] = []
        paginator = self.ecs.get_paginator("list_tasks")
        statuses = [desired_status] if desired_status else ["RUNNING", "STOPPED"]
        for status in statuses:
            for page in paginator.paginate(cluster=cluster, serviceName=service_name, desiredStatus=status):
                arns.extend(page.get("taskArns") or [])
        arns = list(dict.fromkeys(arns))
        if not arns:
            return []
        described = self.ecs.describe_tasks(cluster=cluster, tasks=arns[:100])
        tasks = []
        for task in described.get("tasks") or []:
            tasks.append(
                {
                    "taskArn": task.get("taskArn"),
                    "lastStatus": task.get("lastStatus"),
                    "desiredStatus": task.get("desiredStatus"),
                    "healthStatus": task.get("healthStatus"),
                    "taskDefinitionArn": task.get("taskDefinitionArn"),
                    "startedAt": task.get("startedAt").isoformat() if hasattr(task.get("startedAt"), "isoformat") else task.get("startedAt"),
                    "stoppedAt": task.get("stoppedAt").isoformat() if hasattr(task.get("stoppedAt"), "isoformat") else task.get("stoppedAt"),
                    "stoppedReason": task.get("stoppedReason"),
                    "stopCode": task.get("stopCode"),
                    "containers": [
                        {
                            "name": container.get("name"),
                            "lastStatus": container.get("lastStatus"),
                            "healthStatus": container.get("healthStatus"),
                            "exitCode": container.get("exitCode"),
                            "reason": container.get("reason"),
                            "image": container.get("image"),
                            "imageDigest": container.get("imageDigest"),
                        }
                        for container in (task.get("containers") or [])
                    ],
                }
            )
        return tasks

    def describe_task_definition(self, task_definition: str) -> dict[str, Any] | None:
        response = self.ecs.describe_task_definition(taskDefinition=task_definition)
        td = response.get("taskDefinition")
        if not td:
            return None
        return {
            "family": td.get("family"),
            "revision": td.get("revision"),
            "containerDefinitions": [
                {"name": item.get("name"), "image": item.get("image")}
                for item in (td.get("containerDefinitions") or [])
            ],
        }

    def describe_target_health(self, target_group_arn: str) -> list[dict[str, Any]]:
        response = self.elbv2.describe_target_health(TargetGroupArn=target_group_arn)
        results = []
        for item in response.get("TargetHealthDescriptions") or []:
            health = item.get("TargetHealth") or {}
            target = item.get("Target") or {}
            results.append(
                {
                    "target": f"{target.get('Id')}:{target.get('Port')}",
                    "state": health.get("State"),
                    "reason": health.get("Reason"),
                    "description": health.get("Description"),
                }
            )
        return results

    def get_log_events(self, log_group: str, start: str, end: str, limit: int) -> list[dict[str, Any]]:
        from datetime import datetime, timezone

        def _ms(value: str) -> int:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return int(parsed.timestamp() * 1000)

        try:
            streams = self.logs.describe_log_streams(
                logGroupName=log_group,
                orderBy="LastEventTime",
                descending=True,
                limit=5,
            )
        except Exception:
            return []
        events: list[dict[str, Any]] = []
        for stream in streams.get("logStreams") or []:
            response = self.logs.get_log_events(
                logGroupName=log_group,
                logStreamName=stream.get("logStreamName"),
                startTime=_ms(start),
                endTime=_ms(end),
                limit=limit,
                startFromHead=False,
            )
            for event in response.get("events") or []:
                events.append(
                    {
                        "timestamp": event.get("timestamp"),
                        "message": event.get("message"),
                    }
                )
            if len(events) >= limit:
                break
        return events[:limit]

    def get_service_metrics(self, cluster: str, service_name: str) -> dict[str, Any]:
        metrics: dict[str, Any] = {}
        for metric_name in ("CPUUtilization", "MemoryUtilization"):
            try:
                response = self.cloudwatch.get_metric_statistics(
                    Namespace="AWS/ECS",
                    MetricName=metric_name,
                    Dimensions=[
                        {"Name": "ClusterName", "Value": cluster},
                        {"Name": "ServiceName", "Value": service_name},
                    ],
                    StartTime=__import__("datetime").datetime.utcnow() - __import__("datetime").timedelta(minutes=15),
                    EndTime=__import__("datetime").datetime.utcnow(),
                    Period=60,
                    Statistics=["Average"],
                )
                datapoints = response.get("Datapoints") or []
                if datapoints:
                    latest = max(datapoints, key=lambda item: item["Timestamp"])
                    metrics[metric_name] = latest.get("Average")
            except Exception:
                continue
        return metrics

    def describe_ecr_image(self, repository: str, image_digest: str) -> dict[str, Any] | None:
        """Return basic ECR image metadata if the digest exists; None if it is missing or inaccessible."""
        from botocore.exceptions import ClientError

        try:
            response = self.ecr.describe_images(
                repositoryName=repository,
                imageIds=[{"imageDigest": image_digest}],
            )
            images = response.get("imageDetails") or []
            if not images:
                return None
            img = images[0]
            return {
                "repositoryName": img.get("repositoryName"),
                "imageDigest": img.get("imageDigest"),
                "imagePushedAt": _isoformat(img.get("imagePushedAt")),
                "imageSizeInBytes": img.get("imageSizeInBytes"),
                "imageTags": img.get("imageTags") or [],
            }
        except ClientError as exc:
            code = (exc.response.get("Error") or {}).get("Code", "")
            if code in {"ImageNotFoundException", "RepositoryNotFoundException"}:
                return None
            raise

    def describe_lambda_function(self, function_name: str) -> dict[str, Any] | None:
        try:
            return self.lambda_client.get_function(FunctionName=function_name)
        except Exception:
            return None

    def describe_db_instance(self, db_instance_id: str) -> dict[str, Any] | None:
        try:
            response = self.rds.describe_db_instances(DBInstanceIdentifier=db_instance_id)
            instances = response.get("DBInstances") or []
            return instances[0] if instances else None
        except Exception:
            return None

    def describe_cloudformation_stack(self, stack_name: str) -> dict[str, Any] | None:
        from botocore.exceptions import ClientError

        try:
            response = self.cfn.describe_stacks(StackName=stack_name)
            stacks = response.get("Stacks") or []
            if not stacks:
                return None
            stack = stacks[0]
            return {
                "StackName": stack.get("StackName"),
                "StackStatus": stack.get("StackStatus"),
                "Outputs": [
                    {
                        "OutputKey": item.get("OutputKey"),
                        "OutputValue": item.get("OutputValue"),
                    }
                    for item in (stack.get("Outputs") or [])
                ],
            }
        except ClientError as exc:
            code = (exc.response.get("Error") or {}).get("Code", "")
            if code in {"ValidationError", "StackNotFoundException"}:
                return None
            raise

    def describe_ecs_cluster(self, cluster: str) -> dict[str, Any] | None:
        response = self.ecs.describe_clusters(clusters=[cluster])
        clusters = response.get("clusters") or []
        if not clusters:
            return None
        item = clusters[0]
        return {
            "clusterName": item.get("clusterName"),
            "status": item.get("status"),
            "activeServicesCount": item.get("activeServicesCount"),
            "runningTasksCount": item.get("runningTasksCount"),
        }

    def describe_ecr_repository(self, repository: str) -> dict[str, Any] | None:
        from botocore.exceptions import ClientError

        try:
            response = self.ecr.describe_repositories(repositoryNames=[repository])
            repos = response.get("repositories") or []
            return repos[0] if repos else None
        except ClientError as exc:
            code = (exc.response.get("Error") or {}).get("Code", "")
            if code in {"RepositoryNotFoundException", "RepositoryNotFound"}:
                return None
            raise

    def describe_load_balancer(
        self, arn: str | None = None, dns_name: str | None = None
    ) -> dict[str, Any] | None:
        from botocore.exceptions import ClientError

        try:
            if arn:
                response = self.elbv2.describe_load_balancers(LoadBalancerArns=[arn])
            elif dns_name:
                # DNS lookup via list + filter
                response = self.elbv2.describe_load_balancers()
            else:
                return None
            load_balancers = response.get("LoadBalancers") or []
            if dns_name and not arn:
                needle = _strip_http(dns_name)
                load_balancers = [item for item in load_balancers if item.get("DNSName") == needle]
            return load_balancers[0] if load_balancers else None
        except ClientError:
            return None

    def head_s3_bucket(self, bucket: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.s3.head_bucket(Bucket=bucket)
            return True
        except ClientError:
            return False

    def list_s3_objects_updated_between(
        self, bucket: str, start: str, end: str, prefix: str = "", max_keys: int = 100
    ) -> list[dict[str, Any]]:
        start_dt = _datetime(start)
        end_dt = _datetime(end)
        out: list[dict[str, Any]] = []
        token = None
        while len(out) < max_keys:
            kwargs: dict[str, Any] = {"Bucket": bucket, "MaxKeys": min(1000, max_keys)}
            if prefix:
                kwargs["Prefix"] = prefix
            if token:
                kwargs["ContinuationToken"] = token
            response = self.s3.list_objects_v2(**kwargs)
            for item in response.get("Contents") or []:
                modified = item.get("LastModified")
                if hasattr(modified, "tzinfo") and start_dt and end_dt:
                    if start_dt <= modified <= end_dt:
                        out.append(
                            {
                                "Key": item.get("Key"),
                                "LastModified": _isoformat(modified),
                                "Size": item.get("Size"),
                                "ETag": item.get("ETag"),
                            }
                        )
                if len(out) >= max_keys:
                    break
            if not response.get("IsTruncated"):
                break
            token = response.get("NextContinuationToken")
        return out

    def describe_cloudfront_distribution(self, distribution_id: str) -> dict[str, Any] | None:
        from botocore.exceptions import ClientError

        try:
            response = self.cloudfront.get_distribution(Id=distribution_id)
            dist = (response.get("Distribution") or {}).get("DistributionConfig") or {}
            origins = ((dist.get("Origins") or {}).get("Items")) or []
            return {
                "Id": distribution_id,
                "Status": (response.get("Distribution") or {}).get("Status"),
                "DomainName": (response.get("Distribution") or {}).get("DomainName"),
                "Enabled": dist.get("Enabled"),
                "Origins": [{"DomainName": item.get("DomainName"), "Id": item.get("Id")} for item in origins],
            }
        except ClientError:
            return None

    def list_cloudfront_invalidations(
        self, distribution_id: str, max_items: int = 5
    ) -> list[dict[str, Any]] | None:
        from botocore.exceptions import ClientError

        try:
            response = self.cloudfront.list_invalidations(DistributionId=distribution_id, MaxItems=str(max_items))
            items = ((response.get("InvalidationList") or {}).get("Items")) or []
            return [
                {
                    "Id": item.get("Id"),
                    "Status": item.get("Status"),
                    "CreateTime": _isoformat(item.get("CreateTime")),
                }
                for item in items
            ]
        except ClientError:
            return None

    def list_ecr_images(self, repository: str, max_results: int = 5) -> list[dict[str, Any]]:
        from botocore.exceptions import ClientError

        try:
            response = self.ecr.describe_images(
                repositoryName=repository,
                maxResults=max_results,
                filter={"tagStatus": "ANY"},
            )
            images = response.get("imageDetails") or []
            return [
                {
                    "imageDigest": img.get("imageDigest"),
                    "imageTags": img.get("imageTags") or [],
                    "imagePushedAt": _isoformat(img.get("imagePushedAt")),
                }
                for img in images
            ]
        except ClientError:
            return []

    def describe_alb_listeners(self, load_balancer_arn: str) -> list[dict[str, Any]]:
        from botocore.exceptions import ClientError

        try:
            response = self.elbv2.describe_listeners(LoadBalancerArn=load_balancer_arn)
            return [
                {
                    "ListenerArn": item.get("ListenerArn"),
                    "Port": item.get("Port"),
                    "Protocol": item.get("Protocol"),
                }
                for item in (response.get("Listeners") or [])
            ]
        except ClientError:
            return []

    def describe_alb_target_groups(self, load_balancer_arn: str) -> list[dict[str, Any]]:
        from botocore.exceptions import ClientError

        try:
            response = self.elbv2.describe_target_groups(LoadBalancerArn=load_balancer_arn)
            return [
                {
                    "TargetGroupArn": item.get("TargetGroupArn"),
                    "TargetGroupName": item.get("TargetGroupName"),
                    "Port": item.get("Port"),
                    "HealthCheckPath": item.get("HealthCheckPath"),
                }
                for item in (response.get("TargetGroups") or [])
            ]
        except ClientError:
            return []


def _strip_http(value: str) -> str:
    if value.startswith("http://"):
        return value[len("http://") :]
    if value.startswith("https://"):
        return value[len("https://") :]
    return value


def _isoformat(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _datetime(value: str | None) -> Any:
    if not value:
        return None
    from datetime import datetime, timezone

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def create_aws_facade(region: str, stub_path: str | None) -> AwsFacade:
    if stub_path:
        return StubAwsFacade(stub_path)
    return LiveAwsFacade(region)
