from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_settings(path: str | Path | None = None) -> dict[str, Any]:
    settings_path = Path(path) if path else Path(__file__).resolve().parents[2] / "config" / "settings.yaml"
    if not settings_path.exists():
        return {
            "stability_timeout_seconds": 180,
            "poll_interval_seconds": 15,
            "smoke_timeout_seconds": 10,
            "smoke_retries": 2,
            "allow_pending_tasks": False,
            "cloudwatch_log_limit": 50,
        }
    return yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
