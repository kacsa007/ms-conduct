import datetime as dt
import json
import re
import sqlite3
from pathlib import Path

from .analysis import BRAND, labels

DEFAULT_DB = Path("data/reddit/discussion.sqlite")
MAX_ITEMS = 4000
FRESH = 44 * 3600
ID = re.compile(r"[a-z0-9]{1,16}\Z")


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
        PRAGMA foreign_keys=ON;
        PRAGMA secure_delete=ON;
        CREATE TABLE IF NOT EXISTS items(
            name TEXT PRIMARY KEY, thread TEXT REFERENCES items(name) ON DELETE CASCADE,
            subreddit TEXT NOT NULL, created INTEGER NOT NULL, seen INTEGER NOT NULL,
            title TEXT NOT NULL, body TEXT NOT NULL, score INTEGER NOT NULL, direct INTEGER NOT NULL,
            checked_thread INTEGER NOT NULL DEFAULT 0, pending TEXT NOT NULL DEFAULT '[]'
        );
        CREATE INDEX IF NOT EXISTS by_thread ON items(thread);
        CREATE TABLE IF NOT EXISTS tags(
            name TEXT REFERENCES items(name) ON DELETE CASCADE, kind TEXT, label TEXT,
            PRIMARY KEY(name,kind,label)
        );
        CREATE TABLE IF NOT EXISTS daily_labels(
            day TEXT, kind TEXT, label TEXT, posts INTEGER, comments INTEGER,
            PRIMARY KEY(day,kind,label)
        );
        CREATE TABLE IF NOT EXISTS cursors(search TEXT PRIMARY KEY, after TEXT);
        CREATE TABLE IF NOT EXISTS runs(day TEXT PRIMARY KEY, summary TEXT NOT NULL);
    """)
    return db


def save(db, thing, now):
    kind, data = thing["kind"], thing["data"]
    ident = data.get("id", "")
    if kind not in {"t1", "t3"} or not ID.fullmatch(ident):
        return None
    name = kind + "_" + ident
    body = data.get("selftext" if kind == "t3" else "body", "")
    title = data.get("title", "")
    if (
        data.get("removed_by_category")
        or data.get("author") == "[deleted]"
        or any(
            value.strip().casefold() in {"[deleted]", "[removed]"}
            for value in (title, body)
        )
    ):
        db.execute("DELETE FROM items WHERE name=?", (name,))
        return None
    thread = None if kind == "t3" else data.get("link_id")
    if (
        kind == "t1"
        and not db.execute(
            "SELECT 1 FROM items WHERE name=? AND thread IS NULL", (thread,)
        ).fetchone()
    ):
        return None
    db.execute(
        """
        INSERT INTO items(name,thread,subreddit,created,seen,title,body,score,direct)
        VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET
        seen=excluded.seen,title=excluded.title,body=excluded.body,score=excluded.score,
        subreddit=excluded.subreddit,direct=excluded.direct
    """,
        (
            name,
            thread,
            data["subreddit"],
            int(data["created_utc"]),
            now,
            title,
            body,
            data.get("score", 0),
            int(bool(BRAND.search(title + " " + body))),
        ),
    )
    db.execute("DELETE FROM tags WHERE name=?", (name,))
    db.executemany(
        "INSERT INTO tags VALUES(?,?,?)",
        [(name, kind_, label) for kind_, label in labels(title + "\n" + body)],
    )
    return name


def prune(db, now):
    db.execute("DELETE FROM items WHERE seen<=?", (now - FRESH,))
    # Rank a thread by its newest retained contribution. Its parent always
    # precedes its replies, even when a very old post gets a new comment.
    before = db.execute("SELECT count(*) FROM items").fetchone()[0]
    db.execute(
        """WITH ranked AS (
        SELECT name,thread,created,max(created) OVER (PARTITION BY coalesce(thread,name)) latest
        FROM items)
        DELETE FROM items WHERE name IN (SELECT name FROM ranked
        ORDER BY latest DESC,coalesce(thread,name),thread IS NOT NULL,created DESC,name
        LIMIT -1 OFFSET ?)""",
        (MAX_ITEMS,),
    )
    return before - db.execute("SELECT count(*) FROM items").fetchone()[0]


def report(db, now):
    cutoff = now - FRESH

    def rows(sql, *args):
        return [dict(r) for r in db.execute(sql, args)]

    totals = rows(
        """SELECT count(*) items,count(thread) comments,
                  count(*)-count(thread) posts,coalesce(sum(direct),0) direct_mentions FROM items WHERE seen>?""",
        cutoff,
    )[0]
    return {
        "as_of": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(),
        "totals": totals,
        "labels": rows(
            """SELECT kind,label,count(*) items,count(*)-count(thread) posts,
                        count(thread) comments FROM tags JOIN items USING(name) WHERE seen>?
                        GROUP BY kind,label ORDER BY items DESC,label""",
            cutoff,
        ),
        "topic_cues": rows(
            """
            SELECT t.label topic,c.label wording,count(*) items,count(i.thread) comments,
                   count(*)-count(i.thread) posts,sum(i.direct) direct_mentions
            FROM items i JOIN tags t ON i.name=t.name AND t.kind='topic'
            JOIN tags c ON i.name=c.name AND c.kind='cue'
            WHERE seen>? GROUP BY t.label,c.label ORDER BY items DESC,topic,wording
        """,
            cutoff,
        ),
        "communities": rows(
            """SELECT subreddit,count(*) items FROM items WHERE seen>?
                             GROUP BY subreddit ORDER BY items DESC,subreddit LIMIT 20""",
            cutoff,
        ),
        "weeks": rows(
            """SELECT date(created,'unixepoch','-6 days','weekday 1') week,
                       count(*)-count(thread) posts,count(thread) comments FROM items WHERE seen>?
                       AND created>=? GROUP BY week ORDER BY week""",
            cutoff,
            now - 84 * 86400,
        ),
        "runs": [
            json.loads(r[0])
            for r in db.execute("SELECT summary FROM runs ORDER BY day DESC LIMIT 30")
        ],
    }


def examples(db, now, term="", label="", limit=50):
    rows = db.execute(
        """
        SELECT name,thread,subreddit,title,body FROM items WHERE seen>?
        AND (?='' OR instr(lower(title || ' ' || body),lower(?))>0)
        AND (?='' OR EXISTS (SELECT 1 FROM tags WHERE tags.name=items.name AND label=?))
        ORDER BY created DESC,name LIMIT ?
    """,
        (now - FRESH, term, term, label, label, limit),
    )
    for row in rows:
        thread = (row["thread"] or row["name"])[3:]
        link = "https://www.reddit.com/comments/" + thread + "/"
        if row["thread"]:
            link += "_/" + row["name"][3:] + "/"
        yield {
            "Community": row["subreddit"],
            "Type": "Comment" if row["thread"] else "Post",
            "Text": (row["title"] + " " + row["body"]).strip(),
            "Source": link,
        }
