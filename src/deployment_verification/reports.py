from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_reports(reports_dir: str | Path, basename: str, payload: dict[str, Any]) -> tuple[Path, Path]:
    directory = Path(reports_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{basename}.json"
    html_path = directory / f"{basename}.html"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    html_path.write_text(_to_html(basename, payload), encoding="utf-8")
    return json_path, html_path


def _to_html(title: str, payload: dict[str, Any]) -> str:
    body = json.dumps(payload, indent=2, default=str)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>{title}</title>
  <style>
    body {{ font-family: Segoe UI, sans-serif; margin: 24px; color: #1a1a1a; }}
    h1 {{ font-size: 20px; }}
    pre {{ background: #f5f5f5; padding: 16px; overflow: auto; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <pre>{_escape(body)}</pre>
</body>
</html>
"""


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
