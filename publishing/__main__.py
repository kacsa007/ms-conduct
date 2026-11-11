"""Command line entry point: ``python -m publishing``.

The command refuses to write a draft when the dossier has structural errors,
because broken source references would produce a misleading article. Warnings
are printed and included in the internal appendix.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from evidence.io import read_json, read_jsonl
from evidence.schema import summarise, validate_dossier

from .writer import ArticleOptions, build_article


def _resolve(root: Path, value: str | None, fallback: Path) -> Path:
    if not value:
        return fallback
    path = Path(value)
    return path if path.is_absolute() else root / path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m publishing",
        description="Generate a source-linked Markdown article and SVG figures from data/dossier.json.",
    )
    parser.add_argument("--root", default=".", help="Project root (default: current directory).")
    parser.add_argument("--dossier", default="", help="Dossier path (default: data/dossier.json).")
    parser.add_argument(
        "--snapshots",
        default="",
        help="Snapshot JSONL path (default: data/discovery/source_snapshots.jsonl when present).",
    )
    parser.add_argument(
        "--output",
        default="article/generated/allianz-conduct-review.md",
        help="Markdown output path, relative to the project root.",
    )
    parser.add_argument(
        "--assets",
        default="",
        help="Asset directory (default: an 'assets' directory beside the Markdown file).",
    )
    parser.add_argument("--title", default=ArticleOptions.title)
    parser.add_argument("--standfirst", default=ArticleOptions.standfirst)
    parser.add_argument("--author", default="", help="Optional byline for the method section.")
    parser.add_argument("--contact", default="", help="Optional corrections/replies contact line.")
    parser.add_argument("--no-verification", action="store_true", help="Omit the internal QA appendix.")
    parser.add_argument("--no-charts", action="store_true", help="Do not generate SVG figures.")
    parser.add_argument("--stdout", action="store_true", help="Print the Markdown instead of writing files.")
    parser.add_argument(
        "--allow-invalid",
        action="store_true",
        help="Build even when the dossier has structural errors (not recommended).",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    dossier_path = _resolve(root, args.dossier, root / "data" / "dossier.json")
    dossier, dossier_errors = read_json(dossier_path)
    if not isinstance(dossier, dict):
        for error in dossier_errors or ["Dossier root must be a JSON object."]:
            print(error, file=sys.stderr)
        return 2

    snapshot_path = _resolve(
        root,
        args.snapshots,
        root / "data" / "discovery" / "source_snapshots.jsonl",
    )
    snapshots: list[dict] = []
    if snapshot_path.exists():
        snapshots, snapshot_errors = read_jsonl(snapshot_path)
        for error in snapshot_errors:
            print("Snapshot warning: {}".format(error), file=sys.stderr)

    issues = validate_dossier(dossier, snapshots or None)
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors and not args.allow_invalid:
        print("Refusing to generate an article from a structurally invalid dossier:", file=sys.stderr)
        for issue in errors:
            print("  - {} ({}): {}".format(issue.code, issue.ref or "dossier", issue.message), file=sys.stderr)
        print("Fix the dossier or pass --allow-invalid to build a draft with unresolved references.", file=sys.stderr)
        return 2

    output_name = Path(args.output).name or "allianz-conduct-review.md"
    output_path = _resolve(root, args.output, root / "article" / "generated" / output_name)
    asset_dir = _resolve(root, args.assets, output_path.parent / "assets")
    try:
        assets_link = asset_dir.relative_to(output_path.parent).as_posix()
    except ValueError:
        assets_link = Path(os.path.relpath(asset_dir, output_path.parent)).as_posix()
    if not assets_link:
        assets_link = "assets"

    options = ArticleOptions(
        title=args.title,
        standfirst=args.standfirst,
        output_name=output_path.name,
        assets_dir_name=assets_link,
        include_verification=not args.no_verification,
        include_charts=not args.no_charts,
        author=args.author,
        contact=args.contact,
    )
    draft = build_article(
        dossier,
        snapshots,
        options=options,
        strict=False,
    )

    if args.stdout:
        print(draft.markdown)
        return 0

    written = draft.write(output_path, asset_dir)

    print("Wrote {} ({} words, {} records, {} sources)".format(output_path, draft.stats["words"], draft.stats["records"], draft.stats["sources"]))
    for path in written[1:]:
        print("Wrote {}".format(path))
    integrity = summarise(issues)
    print(
        "Dossier checks: {} error(s), {} warning(s), {} info item(s)".format(
            integrity["errors"], integrity["warnings"], integrity["infos"]
        )
    )
    for warning in draft.warnings:
        print("Draft warning: {}".format(warning), file=sys.stderr)
    print(json.dumps({"records": draft.stats["records"], "sources": draft.stats["sources"], "assets": draft.stats["assets"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
