from pathlib import Path

from deployment_verification.orchestrator import run_verification

ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "fixtures" / "mock-deployment-agent-output.json"
UNHEALTHY = ROOT / "fixtures" / "aws-stub-unhealthy.json"
HEALTHY = ROOT / "fixtures" / "aws-stub-healthy.json"


def test_end_to_end_healthy_deterministic_only(tmp_path):
    result = run_verification(str(MOCK), str(tmp_path), stub_path=str(HEALTHY))
    assert result["exit_code"] == 0
    assert "phase2" not in result
    assert "phase3" not in result
    assert result["verification"]["DeploymentHealthy"] is True
    assert (tmp_path / "deployment-verification.json").exists()


def test_end_to_end_unhealthy_stays_deterministic_no_phase2(tmp_path):
    result = run_verification(str(MOCK), str(tmp_path), stub_path=str(UNHEALTHY))
    assert result["exit_code"] == 1
    assert "phase2" not in result
    assert "phase3" not in result
    assert result["verification"]["DeploymentHealthy"] is False
    assert not (tmp_path / "deployment-investigation.json").exists()
    assert not (tmp_path / "deployment-intelligence.json").exists()
