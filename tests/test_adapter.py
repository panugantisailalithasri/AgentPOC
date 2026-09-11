from pathlib import Path

from deployment_verification.adapter import load_deployment_agent_output, normalize_deployment_agent_output
from deployment_verification.models import ServiceType
import json
import pytest

ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "fixtures" / "mock-deployment-agent-output.json"


def test_mock_input_loads():
    context = load_deployment_agent_output(MOCK)
    assert context.aws_account_id == "110133336476"
    assert context.aws_region == "us-east-1"
    assert context.raw["schema_version"] == "1.2"
    assert context.raw["outcome"]["deployed"] is True
    assert context.pipeline_run_id == context.raw["master_build_id"]
    assert context.services[0].service_type == ServiceType.ECS
    assert context.services[0].name == "cont-sccm-api"
    assert context.services[0].identifiers.cluster == "ff-cont-sccm-devsecops-cluster"
    assert context.services[0].identifiers.task_definition_revision == "8"
    assert context.services[0].expected_artifact.image_digest.startswith("sha256:")


def test_unresolved_tokens_are_rejected():
    payload = json.loads(MOCK.read_text(encoding="utf-8"))
    payload["services"][0]["identifiers"]["cluster"] = "$(Clustername-Cont-SCCM)"
    with pytest.raises(ValueError, match="unresolved pipeline tokens"):
        normalize_deployment_agent_output(payload)


def test_missing_expected_ecs_digest_is_rejected():
    payload = json.loads(MOCK.read_text(encoding="utf-8"))
    del payload["services"][0]["expected_artifact"]["image_digest"]
    with pytest.raises(ValueError, match="requires expected_artifact.image_digest"):
        normalize_deployment_agent_output(payload)


def test_abbreviated_expected_ecs_digest_is_rejected():
    payload = json.loads(MOCK.read_text(encoding="utf-8"))
    payload["services"][0]["expected_artifact"]["image_digest"] = "sha256:2e32f7d302ad"
    with pytest.raises(ValueError, match="complete sha256 digest"):
        normalize_deployment_agent_output(payload)


def test_missing_required_fields_rejected():
    with pytest.raises(ValueError, match="missing required fields"):
        normalize_deployment_agent_output({"deployment_id": "x"})
