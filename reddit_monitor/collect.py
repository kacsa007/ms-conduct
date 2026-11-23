import datetime as dt
import itertools
import json

from .analysis import BRAND
from .client import BudgetReached, RedditError
from .store import ID, prune, save

QUERIES = (
    "Allianz",
    "AllianzDirect OR AllianzGI OR AllianzTravel OR AllianzTrade",
    '"Euler Hermes"',
    "安联 OR 安聯 OR アリアンツ OR 알리안츠",
)
SORTS = ("new", "top", "relevance", "comments")


def listing(result):
    children = result["data"]["children"]
    if not isinstance(children, list):
        raise ValueError("Malformed Reddit listing")
    return children


def refresh(db, api, now, stats):
    due = [
        r[0]
        for r in db.execute(
            "SELECT name FROM items WHERE seen<=? ORDER BY thread IS NOT NULL, seen,name",
            (now - 18 * 3600,),
        )
    ]
    for start in range(0, len(due), 100):
        batch = due[start : start + 100]
        returned = listing(api.get("/api/info", id=",".join(batch)))
        keys = set()
        for thing in sorted(returned, key=lambda t: t["kind"] != "t3"):
            keys.add(save(db, thing, now))
        missing = set(batch) - keys
        db.executemany("DELETE FROM items WHERE name=?", [(key,) for key in missing])
        stats["unavailable"] += len(missing)


def search(db, api, now, stats):
    def pages(query, sort, window, maximum, key=None):
        after = None
        if key:
            row = db.execute(
                "SELECT after FROM cursors WHERE search=?", (key,)
            ).fetchone()
            after = row[0] if row else None
        for _ in range(maximum):
            response = api.get(
                "/search",
                q=query,
                sort=sort,
                t=window,
                type="link",
                limit=100,
                after=after,
            )
            for thing in listing(response):
                if thing["kind"] == "t3" and save(db, thing, now):
                    stats["post_hits"] += 1
            stats["search_pages"] += 1
            previous, after = after, response["data"]["after"]
            if after == previous:
                after = None
            if key:
                db.execute("INSERT OR REPLACE INTO cursors VALUES(?,?)", (key, after))
            if not after:
                return
        stats["capped_searches"] += 1

    # All terms get a fresh overlapping search each day. One historical sort per
    # day also advances persisted cursors, including across a manual rerun.
    for query in QUERIES:
        pages(query, "new", "week", 3)
    sort = SORTS[(now // 86400) % len(SORTS)]
    for query in QUERIES:
        pages(query, sort, "all", 3, query + "/" + sort)


# These streams can find a brand mention in a comment even when the parent
# post never names Allianz. Reddit provides no global comment-search endpoint.
STREAMS = (
    "all",
    "Insurance+TravelInsurance+travel+personalfinance",
    "Switzerland+askswitzerland+Finanzen+Versicherung+vosfinances",
)


def comment_mentions(db, api, now, stats):
    for stream in STREAMS:
        try:
            page = api.get("/r/" + stream + "/comments", limit=100)
        except RedditError as error:
            if error.status not in (403, 404, 451):
                raise
            stats["unavailable_streams"] += 1
            continue
        hits = [
            t
            for t in listing(page)
            if t["kind"] == "t1" and BRAND.search(t["data"].get("body") or "")
        ]
        ids = sorted({t["data"]["link_id"] for t in hits})
        if ids:
            for post in listing(api.get("/api/info", id=",".join(ids))):
                save(db, post, now)
            for thing in hits:
                if save(db, thing, now):
                    stats["standalone_comment_hits"] += 1


def ingest_comments(db, things, now, stats):
    queue, pending = list(things), []
    while queue:
        thing = queue.pop()
        data = thing["data"]
        if thing["kind"] == "more":
            pending.extend(i for i in data.get("children", []) if ID.fullmatch(i))
        elif thing["kind"] == "t1":
            if save(db, thing, now):
                stats["comment_hits"] += 1
            if isinstance(data.get("replies"), dict):
                queue.extend(listing(data["replies"]))
    return pending


def threads(db, api, now, stats):
    # Share visits between new posts and existing threads so active threads
    # can acquire new comments while backfilled posts still get initial visits.
    fresh = db.execute(
        "SELECT * FROM items WHERE thread IS NULL AND checked_thread=0 "
        "ORDER BY created DESC,name LIMIT 32"
    ).fetchall()
    old = db.execute(
        "SELECT * FROM items WHERE thread IS NULL AND checked_thread>0 "
        "ORDER BY checked_thread,created DESC,name LIMIT 32"
    ).fetchall()
    visits = [
        r for pair in itertools.zip_longest(fresh, old) for r in pair if r is not None
    ]
    for row in visits[:32]:
        thread = row["name"]
        pending = json.loads(row["pending"])
        try:
            if pending:
                page = api.get(
                    "/api/morechildren",
                    link_id=thread,
                    children=",".join(pending[:100]),
                    api_type="json",
                    sort="new",
                )
                if page["json"]["errors"]:
                    raise RuntimeError("Reddit rejected a comment expansion")
                found = ingest_comments(db, page["json"]["data"]["things"], now, stats)
                pending = pending[100:] + found
            else:
                post, comments = api.get(
                    "/comments/" + thread[3:], sort="new", limit=100, depth=8
                )
                parent = listing(post)
                if not parent or not save(db, parent[0], now):
                    db.execute("DELETE FROM items WHERE name=?", (thread,))
                    continue
                pending = ingest_comments(db, listing(comments), now, stats)
        except RedditError as error:
            if error.status not in (403, 404, 451):
                raise
            stats["inaccessible_threads"] += 1
            db.execute("DELETE FROM items WHERE name=?", (thread,))
            continue
        db.execute(
            "UPDATE items SET checked_thread=?,pending=? WHERE name=?",
            (now, json.dumps(list(dict.fromkeys(pending))), thread),
        )
        stats["thread_visits"] += 1


def collect(db, api, now):
    day = dt.datetime.fromtimestamp(now, dt.timezone.utc).date().isoformat()
    stats = dict(
        day=day,
        post_hits=0,
        comment_hits=0,
        search_pages=0,
        thread_visits=0,
        unavailable=0,
        inaccessible_threads=0,
        unavailable_streams=0,
        standalone_comment_hits=0,
        capped_searches=0,
        budget_reached=False,
    )
    with db:
        try:
            refresh(db, api, now, stats)
            search(db, api, now, stats)
            comment_mentions(db, api, now, stats)
            threads(db, api, now, stats)
        except BudgetReached:
            stats["budget_reached"] = True
        if not stats["search_pages"]:
            raise RuntimeError(
                "No search page completed. Previous published snapshot was retained."
            )
        stats["capacity_evictions"] = prune(db, now)
        stats["unvisited_threads"] = db.execute(
            "SELECT count(*) FROM items WHERE thread IS NULL AND checked_thread=0"
        ).fetchone()[0]
        stats["threads_with_more"] = db.execute(
            "SELECT count(*) FROM items WHERE thread IS NULL AND pending!='[]'"
        ).fetchone()[0]
        stats["requests"] = api.used
        stats["indexed_items"] = db.execute("SELECT count(*) FROM items").fetchone()[0]
        db.execute("DELETE FROM daily_labels WHERE day=?", (day,))
        db.execute(
            """INSERT INTO daily_labels
                   SELECT ?,kind,label,count(*)-count(thread),count(thread)
                   FROM tags JOIN items USING(name) GROUP BY kind,label""",
            (day,),
        )
        db.execute("INSERT OR REPLACE INTO runs VALUES(?,?)", (day, json.dumps(stats)))
    return stats
