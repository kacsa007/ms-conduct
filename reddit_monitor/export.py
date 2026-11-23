"""Spreadsheet export of the current discussion sample."""

import csv
import datetime as dt
from collections import defaultdict
from pathlib import Path

from .store import DEFAULT_DB, FRESH

DEFAULT_OUTPUT = DEFAULT_DB.parent / "exports"


def spreadsheet_cell(value):
    if isinstance(value, str) and (
        value.lstrip().startswith(("=", "+", "-", "@"))
        or value.startswith(("\t", "\r", "\n"))
    ):
        return "'" + value
    return value


def export_csv(db, now, output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    tags = defaultdict(list)
    for row in db.execute("SELECT name,kind,label FROM tags ORDER BY label"):
        tags[row["name"], row["kind"]].append(row["label"])
    rows = db.execute(
        """SELECT i.*,coalesce(p.title,i.title) thread_title
           FROM items i LEFT JOIN items p ON p.name=i.thread AND p.seen>?
           WHERE i.seen>? ORDER BY i.created DESC,i.name""",
        (now - FRESH, now - FRESH),
    )
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "reddit_id",
                "type",
                "thread_id",
                "subreddit",
                "created_utc",
                "title",
                "text",
                "score",
                "direct_brand_mention",
                "topics",
                "wording",
                "source_url",
                "last_checked_utc",
            ]
        )
        count = 0
        for row in rows:
            thread = row["thread"] or row["name"]
            url = "https://www.reddit.com/comments/" + thread[3:] + "/"
            if row["thread"]:
                url += "_/" + row["name"][3:] + "/"
            values = [
                row["name"],
                "comment" if row["thread"] else "post",
                thread,
                row["subreddit"],
                dt.datetime.fromtimestamp(row["created"], dt.timezone.utc).isoformat(),
                row["thread_title"],
                row["body"],
                row["score"],
                row["direct"],
                " | ".join(tags[row["name"], "topic"]),
                " | ".join(tags[row["name"], "cue"]),
                url,
                dt.datetime.fromtimestamp(row["seen"], dt.timezone.utc).isoformat(),
            ]
            writer.writerow([spreadsheet_cell(value) for value in values])
            count += 1
    return count
