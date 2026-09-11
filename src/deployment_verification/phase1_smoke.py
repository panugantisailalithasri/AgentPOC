from __future__ import annotations

from typing import Any

import requests

from deployment_verification.models import CheckResult, CheckStatus, SmokeTest


def run_smoke_checks(smoke_tests: list[SmokeTest], timeout_seconds: int, retries: int) -> list[CheckResult]:
    if not smoke_tests:
        return []

    results: list[CheckResult] = []
    for test in smoke_tests:
        last_error = None
        response_status = None
        body = None
        passed = False
        attempts = max(retries, 0) + 1
        for _ in range(attempts):
            try:
                response = requests.get(test.url, timeout=timeout_seconds)
                response_status = response.status_code
                body = response.text[:500]
                status_ok = response_status == test.expected_status
                body_ok = True if not test.expected_body_contains else test.expected_body_contains in (body or "")
                if status_ok and body_ok:
                    passed = True
                    break
            except Exception as exc:  # noqa: BLE001 - capture smoke failure as FAIL
                last_error = str(exc)
        results.append(
            CheckResult(
                check=f"Smoke test: {test.name}",
                status=CheckStatus.PASS if passed else CheckStatus.FAIL,
                resource=test.url,
                reason="Smoke test returned the expected result" if passed else "Smoke test failed",
                expected={"status": test.expected_status, "body_contains": test.expected_body_contains},
                actual={"status": response_status, "error": last_error, "body": body},
            )
        )
    return results
