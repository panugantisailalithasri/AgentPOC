"""
Fetch ADO Library variable groups (redacted).

Usage (PowerShell) — do NOT paste the PAT into chat:

  $env:ADO_PAT = "<your raw PAT>"
  # OR if you already have Base64(email:pat) in mcp.json style:
  # $env:ADO_BASIC = "<base64 email:pat>"

  cd C:\\Users\\PanugantiSailalithaS\\deployment-verification-agent
  .\\.venv\\Scripts\\python.exe scripts\\fetch_ado_library_groups.py

Writes: fixtures/ado-library-groups-redacted.json (secrets masked as ***SECRET***)
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "fixtures" / "ado-library-groups-redacted.json"
ORG = "FreyrDevOps"
PROJECT = "Freyr-Unified-RIMS"


def _auth_candidates() -> list[tuple[str, str]]:
    """Return (label, Authorization header value) candidates without printing secrets."""
    candidates: list[tuple[str, str]] = []

    basic = os.environ.get("ADO_BASIC", "").strip()
    if basic:
        candidates.append(("ADO_BASIC", f"Basic {basic}"))

    pat = os.environ.get("ADO_PAT", "").strip()
    if pat:
        token = base64.b64encode(f":{pat}".encode("utf-8")).decode("ascii")
        candidates.append(("ADO_PAT", f"Basic {token}"))

    mcp_path = Path.home() / ".cursor" / "mcp.json"
    if mcp_path.exists():
        mcp = json.loads(mcp_path.read_text(encoding="utf-8-sig"))
        encoded = (
            ((mcp.get("mcpServers") or {}).get("ado") or {}).get("env") or {}
        ).get("PERSONAL_ACCESS_TOKEN")
        if encoded:
            candidates.append(("mcp.json-as-is", f"Basic {encoded}"))
            # If value is base64(email:pat) this is correct.
            # If value is raw PAT or base64(pat-only), try recovery forms.
            try:
                decoded = base64.b64decode(encoded).decode("utf-8")
                if ":" in decoded:
                    # already email:pat (or user:pat) — also try empty-user form with password part
                    secret = decoded.split(":", 1)[1]
                    token = base64.b64encode(f":{secret}".encode("utf-8")).decode("ascii")
                    candidates.append(("mcp.json-decoded-colon-empty-user", f"Basic {token}"))
                else:
                    # base64 of PAT alone
                    token = base64.b64encode(f":{decoded}".encode("utf-8")).decode("ascii")
                    candidates.append(("mcp.json-decoded-as-raw-pat", f"Basic {token}"))
            except Exception:
                # not valid base64 — treat as raw PAT
                token = base64.b64encode(f":{encoded}".encode("utf-8")).decode("ascii")
                candidates.append(("mcp.json-raw-pat", f"Basic {token}"))

    if not candidates:
        raise SystemExit(
            "Set ADO_PAT / ADO_BASIC or ado.PERSONAL_ACCESS_TOKEN in ~/.cursor/mcp.json."
        )
    return candidates


def _auth_header() -> str:
    # Back-compat for callers expecting a single header; prefer first candidate.
    return _auth_candidates()[0][1]


def get(url: str, headers: dict[str, str]):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Non-JSON response ({raw[:120]!r})") from exc


def main() -> int:
    q = urllib.parse.urlencode({"api-version": "7.1"})
    list_url = (
        f"https://dev.azure.com/{ORG}/{urllib.parse.quote(PROJECT)}"
        f"/_apis/distributedtask/variablegroups?{q}"
    )

    data = None
    used_label = None
    used_authorization = None
    last_status = None
    for label, authorization in _auth_candidates():
        headers = {"Authorization": authorization, "Accept": "application/json"}
        try:
            data = get(list_url, headers)
            used_label = label
            used_authorization = authorization
            print("AUTH_OK", label)
            break
        except urllib.error.HTTPError as exc:
            last_status = exc.code
            print("AUTH_TRY", label, "status", exc.code)
            continue
        except Exception as exc:
            print("AUTH_TRY", label, "error", type(exc).__name__)
            continue

    if data is None or used_authorization is None:
        print("LIST_STATUS", last_status)
        return 1

    headers = {"Authorization": used_authorization, "Accept": "application/json"}

    groups = data.get("value") or []
    print("TOTAL_GROUPS", len(groups))
    needles = ("slp", "dlp", "iac", "infra", "urf", "admin", "frontend", "aws infra")
    interesting = [g for g in groups if any(n in (g.get("name") or "").lower() for n in needles)]
    print("INTERESTING", len(interesting))
    for g in sorted(interesting, key=lambda x: (x.get("name") or "")):
        print(f"{g.get('id')}\t{g.get('name')}")

    targets = []
    for g in interesting:
        n = (g.get("name") or "").lower()
        if "slp" in n and "dlp" in n:
            targets.append(g)
        if "iac" in n and "slp" in n:
            targets.append(g)
        if "aws infra" in n and "slp" in n:
            targets.append(g)
    fe_added = 0
    for g in interesting:
        if fe_added >= 5:
            break
        n = (g.get("name") or "").lower()
        if any(x in n for x in ("admin-ui", "admin_center", "frontend", "urf-ac", "saas")):
            targets.append(g)
            fe_added += 1

    seen: set[int] = set()
    uniq = []
    for g in targets:
        gid = int(g["id"])
        if gid in seen:
            continue
        seen.add(gid)
        uniq.append(g)

    out = {"project": PROJECT, "groups": []}
    for g in uniq[:20]:
        gid = g["id"]
        detail_url = (
            f"https://dev.azure.com/{ORG}/{urllib.parse.quote(PROJECT)}"
            f"/_apis/distributedtask/variablegroups/{gid}?api-version=7.1"
        )
        try:
            detail = get(detail_url, headers)
        except urllib.error.HTTPError as exc:
            print("GET_FAIL", gid, exc.code)
            continue
        redacted = {}
        for key, value in (detail.get("variables") or {}).items():
            if isinstance(value, dict):
                is_secret = bool(value.get("isSecret"))
                redacted[key] = {
                    "isSecret": is_secret,
                    "value": "***SECRET***" if is_secret else value.get("value"),
                }
            else:
                redacted[key] = {"isSecret": False, "value": value}
        out["groups"].append(
            {
                "id": gid,
                "name": detail.get("name"),
                "variable_count": len(redacted),
                "variable_names": sorted(redacted.keys()),
                "variables": redacted,
            }
        )
        print("FETCHED", detail.get("name"), "vars", len(redacted))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("WROTE", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
