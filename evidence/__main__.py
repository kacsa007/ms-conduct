"""Command line checks that do not need the dashboard or the network.

Usage::

    python -m evidence validate [--snapshots] [--strict] [--json]
    python -m evidence report [--output data/discovery/verification_report.json]

``validate`` exits 1 when the dossier has structural errors (or any warning
with ``--strict``), which makes it usable in CI.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .io import read_json, read_jsonl
from .schema import summarise, validate_dossier
from .verification import build_report


def _load(root: Path):
    dossier, dossier_errors = read_json(root / "data" / "dossier.json")
    snapshots, snapshot_errors = read_jsonl(root / "data" / "discovery" / "source_snapshots.jsonl")
    return dossier, snapshots, dossier_errors + snapshot_errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evidence", description=__doc__)
    parser.add_argument("command", nargs="?", choices=("validate", "report"), default="validate")
    parser.add_argument("--root", default=".", help="Project root (default: current directory).")
    parser.add_argument("--snapshots", action="store_true", help="Also validate collected snapshots.")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--output", default="", help="Write the verification report to this path.")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    dossier, snapshots, read_errors = _load(root)
    if not isinstance(dossier, dict):
        message = "; ".join(read_errors) or "Dossier could not be read."
        if args.json:
            print(json.dumps({"ok": False, "errors": read_errors or [message]}, indent=2))
        else:
            print(message, file=sys.stderr)
        return 2

    if args.command == "validate":
        issues = validate_dossier(dossier, snapshots if args.snapshots else None)
        summary = summarise(issues)
        if args.json:
            print(
                json.dumps(
                    {
                        "summary": summary,
                        "issues": [issue.as_dict() for issue in issues],
                        "read_errors": read_errors,
                    },
                    indent=2,
                )
            )
        else:
            print(
                "Dossier checks: {error} error(s), {warning} warning(s), {info} info item(s).".format(
                    error=summary["errors"], warning=summary["warnings"], info=summary["infos"]
                )
            )
            for issue in issues:
                print(
                    "  [{severity}] {code} ({ref}): {message}".format(
                        severity=issue.severity,
                        code=issue.code,
                        ref=issue.ref or "-",
                        message=issue.message,
                    )
                )
            for error in read_errors:
                print("  [read] {}".format(error))
        failed = summary["errors"] > 0 or (args.strict and summary["warnings"] > 0)
        return 1 if failed else 0

    report = build_report(dossier, snapshots)
    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = root / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        summary = report["summary"]
        print(
            "Verification: {records} records, {supported} supported by a primary outcome, "
            "{warnings} with review flags, {leads} unrecorded lead(s).".format(
                records=summary["records"],
                supported=summary["records_supported"],
                warnings=summary["records_with_warnings"],
                leads=summary["leads"],
            )
        )
        for check in report["records"]:
            print(
                "  {priority:<6} {status:<32} {title}".format(
                    priority=check["priority"].upper(),
                    status=check["status"],
                    title=check["title"],
                )
            )
        if args.output:
            print("Report written to {}".format(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
