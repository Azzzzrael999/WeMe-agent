"""CLI entry point for the reproducible Day 7 Agent evaluation report."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.evaluation.runner import report_as_json, run_day7_evaluation


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the offline Day 7 Agent evaluation corpus.")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON output file. Without this flag the report is written to stdout.",
    )
    args = parser.parse_args()
    payload = report_as_json(run_day7_evaluation()) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
