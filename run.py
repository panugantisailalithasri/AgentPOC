"""Run the agent from the project root without needing PYTHONPATH."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from deployment_verification.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
