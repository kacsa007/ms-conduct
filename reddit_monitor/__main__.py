import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import tempfile
import time

from .archive import Archive, DEFAULT_REPO, cipher_from_env
from .client import Reddit
from .collect import collect
from .store import DEFAULT_DB, connect, report


def markdown(data):
    t = data["totals"]
    lines = [
        "# Allianz on Reddit",
        "",
        f"As of {data['as_of']}. "
        f"{t['posts']} posts and {t['comments']} comments in the collected sample.",
        "",
        "Counts describe wording in sampled discussions. One item can have several labels.",
        "",
        "| Label | Posts | Comments |",
        "|---|---:|---:|",
    ]
    for row in data["labels"]:
        lines.append(f"| {row['label']} | {row['posts']} | {row['comments']} |")
    if data["topic_cues"]:
        lines.extend(
            [
                "",
                "| Topic | Wording in the same item | Posts | Comments |",
                "|---|---|---:|---:|",
            ]
        )
        for row in data["topic_cues"]:
            lines.append(
                f"| {row['topic']} | {row['wording']} | {row['posts']} | {row['comments']} |"
            )
    lines.extend(["", "| Week starting (UTC) | Posts | Comments |", "|---|---:|---:|"])
    for row in data["weeks"]:
        lines.append(f"| {row['week']} | {row['posts']} | {row['comments']} |")
    if data["runs"]:
        run = data["runs"][0]
        lines += [
            "",
            f"Last run visited {run['thread_visits']} threads. "
            f"{run['unvisited_threads']} await a first visit. "
            f"{run['threads_with_more']} have additional comment pages. "
            f"{run['capped_searches']} searches reached their page cap.",
        ]
    return "\n".join(lines)


def run_daily():
    required = (
        "REDDIT_CLIENT_ID",
        "REDDIT_CLIENT_SECRET",
        "REDDIT_USER_AGENT",
        "REDDIT_DB_KEY",
        "GITHUB_TOKEN",
        "GITHUB_REPOSITORY",
        "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT",
    )
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise SystemExit("Configure missing settings: " + ", ".join(missing))
    archive = Archive(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"])
    cipher = cipher_from_env()
    release = archive.release()
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "discussion.sqlite"
        archive.restore(release, path, cipher)
        now = int(time.time())
        with closing(connect(path)) as db:
            stats = collect(db, Reddit(), now)
            result = report(db, now)
            db.execute("VACUUM")
        archive.publish(
            release,
            path,
            cipher,
            result,
            os.environ["GITHUB_RUN_ID"],
            os.environ["GITHUB_RUN_ATTEMPT"],
        )
    print(json.dumps(stats))
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            print(markdown(result), file=handle)


def main():
    parser = argparse.ArgumentParser(
        description="Collect and report on public Reddit discussions about Allianz."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("collect", "report", "restore"):
        sub = commands.add_parser(name)
        sub.add_argument("--db", type=Path, default=DEFAULT_DB)
        if name == "report":
            sub.add_argument(
                "--format", choices=["markdown", "json"], default="markdown"
            )
        if name == "restore":
            sub.add_argument("--repo", default=DEFAULT_REPO)
            sub.add_argument(
                "--replace",
                action="store_true",
                help="replace an existing local database",
            )
    commands.add_parser("daily", help="scheduled GitHub Actions entry point")
    args = parser.parse_args()
    if args.command == "daily":
        return run_daily()
    if args.command == "restore":
        if args.db.exists() and not args.replace:
            parser.error("Database already exists. Use --replace to refresh it.")
        archive = Archive(args.repo, os.getenv("GITHUB_TOKEN"))
        if not archive.restore(archive.release(), args.db, cipher_from_env()):
            parser.error("No published database yet")
        return
    if args.command == "report" and not args.db.is_file():
        parser.error("No database yet. Run collect or restore first.")
    now = int(time.time())
    with closing(connect(args.db)) as db:
        if args.command == "collect":
            print(json.dumps(collect(db, Reddit(), now)))
        else:
            data = report(db, now)
            print(
                markdown(data)
                if args.format == "markdown"
                else json.dumps(data, ensure_ascii=False)
            )


if __name__ == "__main__":
    main()
