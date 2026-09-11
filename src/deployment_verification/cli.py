from __future__ import annotations

import argparse
import json
import sys

from deployment_verification.aws_facade import AwsCredentialsError
from deployment_verification.orchestrator import run_verification


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deployment Verification Agent — deterministic Infra/FE/BE checks"
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to Deployment Agent output JSON (mock file for this POC)",
    )
    parser.add_argument("--reports-dir", default="reports", help="Directory for JSON/HTML reports")
    parser.add_argument(
        "--stub",
        default=None,
        help="Optional AWS stub JSON for offline/demo runs. Omit to call live AWS APIs.",
    )
    parser.add_argument("--settings", default=None, help="Optional settings YAML path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_verification(
            input_path=args.input,
            reports_dir=args.reports_dir,
            stub_path=args.stub,
            settings_path=args.settings,
        )
    except AwsCredentialsError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "exit_code",
                    "deployment_kind",
                    "pipeline_summary",
                    "discovery_notes",
                    "verification",
                )
                if k in result
            },
            indent=2,
            default=str,
        )
    )
    return int(result["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
