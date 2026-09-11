"""Deterministic deployment checks (Infra / FE / BE). Formerly phase1."""

from __future__ import annotations

from deployment_verification.phase1 import run_phase1

# Public alias — keep phase1 implementation, drop phased naming at API boundary.
run_deterministic_checks = run_phase1
