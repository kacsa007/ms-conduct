import json
import sqlite3
from contextlib import closing
from unittest.mock import Mock

import pytest
import requests
from cryptography.fernet import Fernet

from reddit_monitor import archive, collect, store
from reddit_monitor.__main__ import markdown
from reddit_monitor.client import BudgetReached, Reddit

NOW = 1_795_439_000


def thing(kind, ident, text, thread=None, created=NOW):
    data = dict(
        id=ident, subreddit="Insurance", created_utc=created, author="fixture_person"
    )
    if kind == "t3":
        data.update(title=text, selftext="")
    else:
        data.update(body=text, link_id=thread)
    return {"kind": kind, "data": data}


def listing(*things, after=None):
    return {"data": {"children": list(things), "after": after}}


class API:
    def __init__(self):
        self.used = 0
        self.post = thing("t3", "a1", "Allianz travel claim?")
        self.other = thing("t3", "b2", "Travel insurance abroad")
        self.comment = thing("t1", "c3", "Still waiting", "t3_a1")
        self.mention = thing("t1", "d4", "Allianz reimbursed me", "t3_b2")
        self.extra = thing("t1", "e5", "My claim was denied", "t3_a1")

    def get(self, path, **params):
        self.used += 1
        if path == "/search":
            return listing(self.post)
        if path == "/r/all/comments":
            return listing(self.mention)
        if path.startswith("/r/"):
            return listing()
        if path == "/api/info":
            return listing(
                *(
                    t
                    for t in [
                        self.post,
                        self.other,
                        self.comment,
                        self.mention,
                        self.extra,
                    ]
                    if t["kind"] + "_" + t["data"]["id"] in params["id"].split(",")
                )
            )
        if path == "/comments/a1":
            return [
                listing(self.post),
                listing(self.comment, {"kind": "more", "data": {"children": ["e5"]}}),
            ]
        if path == "/comments/b2":
            return [listing(self.other), listing(self.mention)]
        if path == "/api/morechildren":
            assert params["children"] == "e5"
            return {"json": {"errors": [], "data": {"things": [self.extra]}}}
        raise AssertionError(path)


def test_repeated_collection_replies_topics_and_private_search(tmp_path):
    with closing(store.connect(tmp_path / "data")) as db:
        api = API()
        collect.collect(db, api, NOW)
        run = collect.collect(db, api, NOW + 120)
        data = store.report(db, NOW + 120)
        assert data["totals"] == dict(items=5, comments=3, posts=2, direct_mentions=2)
        assert run["threads_with_more"] == 0 and run["standalone_comment_hits"] == 1
        assert len(data["runs"]) == 1 and data["weeks"][0]["posts"] == 2
        assert db.execute(
            "SELECT posts,comments FROM daily_labels WHERE label='Claims'"
        ).fetchone()[:] == (1, 1)
        assert "Mentions waiting" in markdown(data)
        samples = list(
            store.examples(db, NOW, term="waiting", label="Mentions waiting")
        )
        assert samples[0]["Source"] == "https://www.reddit.com/comments/a1/_/c3/"
        assert "Still waiting" not in json.dumps(data)
        assert "fixture_person" not in "\n".join(db.iterdump())


def test_refresh_removes_missing_thread_replies_and_relabels_edits(tmp_path):
    with closing(store.connect(tmp_path / "data")) as db:
        api = API()
        for t in (api.post, api.comment, api.other, api.mention):
            store.save(db, t, NOW)
        api.mention["data"]["body"] = "Everything was delayed"

        def info(path, **params):
            return listing(api.mention, api.other)

        api.get = info
        stats = {"unavailable": 0}
        collect.refresh(db, api, NOW + 86400, stats)
        assert [r[0] for r in db.execute("SELECT name FROM items ORDER BY name")] == [
            "t1_d4",
            "t3_b2",
        ]
        assert (
            db.execute(
                "SELECT count(*) FROM tags WHERE label='Mentions a payout'"
            ).fetchone()[0]
            == 0
        )
        assert list(store.examples(db, NOW + 86400, label="Mentions waiting"))
        assert not list(store.examples(db, NOW + 86400 + store.FRESH))
        api.other["data"]["removed_by_category"] = "moderator"
        store.save(db, api.other, NOW + 86401)
        assert not list(db.execute("SELECT * FROM items"))


def test_capacity_keeps_parent_for_new_reply(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "MAX_ITEMS", 2)
    with closing(store.connect(tmp_path / "data")) as db:
        for t in [
            thing("t3", "a", "Old", created=NOW - 500),
            thing("t3", "b", "Newer", created=NOW - 1),
            thing("t1", "c", "Reply", "t3_a"),
        ]:
            store.save(db, t, NOW)
        assert store.prune(db, NOW) == 1
        assert {r[0] for r in db.execute("SELECT name FROM items")} == {"t3_a", "t1_c"}


def test_partial_search_checkpoint_and_failed_run(tmp_path):
    with closing(store.connect(tmp_path / "data")) as db:
        api = Mock(used=6)
        api.get.side_effect = [listing()] * len(collect.QUERIES) + [
            listing(thing("t3", "aa", "Allianz"), after="t3_aa"),
            BudgetReached(),
        ]
        stat = collect.collect(db, api, NOW)
        assert stat["budget_reached"] and stat["indexed_items"] == 1
        assert db.execute("SELECT after FROM cursors").fetchone()[0] == "t3_aa"
        api.get.side_effect = BudgetReached()
        with pytest.raises(RuntimeError, match="No search page"):
            collect.collect(db, api, NOW + 86400)
        assert len(list(db.execute("SELECT * FROM runs"))) == 1


def response(payload=None, status=200, headers=None, content=b""):
    r = requests.Response()
    r.status_code = status
    r._content = json.dumps(payload).encode() if payload is not None else content
    r.headers.update(headers or {})
    r.url = "https://example.invalid/"
    return r


def test_rate_limit_respects_reset_and_budget(monkeypatch):
    from reddit_monitor import client

    clock = [0]
    monkeypatch.setattr(client.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        client.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay)
    )
    for key in ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT"]:
        monkeypatch.setenv(key, "fixture")
    session = Mock(headers={})
    session.request.side_effect = [
        response({"access_token": "fake"}),
        response(status=429, headers={"Retry-After": "25"}),
        response(listing()),
        response(
            listing(),
            headers={"X-Ratelimit-Remaining": "0", "X-Ratelimit-Reset": "800"},
        ),
    ]
    monkeypatch.setattr(client.requests, "Session", lambda: session)
    api = Reddit(seconds=100)
    api.get("/search")
    assert clock[0] >= 26
    api.get("/search")
    with pytest.raises(BudgetReached):
        api.get("/search")
    assert api.used == 4


def test_archive_round_trip_upload_failure_and_wrong_key(tmp_path):
    old = {
        "id": 17,
        "body": archive.INITIAL,
        "assets": [{"name": "discussion-1-1.sqlite.gz.fernet", "id": 5}],
    }
    remote = archive.Archive()
    remote.session = Mock()
    key = Fernet(Fernet.generate_key())
    path = tmp_path / "live.sqlite"
    with closing(store.connect(path)) as db, db:
        store.save(db, thing("t3", "a", "Private text from fixture"), NOW)
    remote.session.post.return_value = response(status=502)
    with pytest.raises(requests.HTTPError):
        remote.publish(old, path, key, {}, 2, 1)
    remote.session.patch.assert_not_called()
    remote.session.delete.assert_not_called()

    uploaded = {}

    def upload(url, *, params, headers, data, timeout):
        uploaded.update(data=data, name=params["name"])
        return response({"state": "uploaded", "size": len(data)})

    remote.session.post.side_effect = upload
    remote.session.get.return_value = response(old)
    remote.session.patch.return_value = response({})
    remote.session.delete.return_value = response(status=204)
    remote.publish(old, path, key, {}, 2, 1)
    assert b"Private text" not in uploaded["data"]
    body = remote.session.patch.call_args.kwargs["json"]["body"]
    release = {"id": 17, "body": body, "assets": [{"id": 6, "name": uploaded["name"]}]}
    remote.session.get.return_value = response(content=uploaded["data"])
    restored = tmp_path / "restored.sqlite"
    with pytest.raises(ValueError, match="cannot decrypt"):
        remote.restore(release, restored, Fernet(Fernet.generate_key()))
    assert not restored.exists()
    assert remote.restore(release, restored, key)
    with closing(sqlite3.connect(restored)) as db:
        assert (
            db.execute("SELECT title FROM items").fetchone()[0]
            == "Private text from fixture"
        )


def test_dashboard_drills_down_to_matching_text(tmp_path, monkeypatch):
    import time
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from reddit_monitor import dashboard

    path = tmp_path / "discussion.sqlite"
    monkeypatch.setattr(dashboard, "DEFAULT_DB", path)
    with closing(store.connect(path)) as db:
        collect.collect(db, API(), int(time.time()))
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
    app.sidebar.radio[0].set_value("Reddit discussions").run()
    assert not app.exception
    assert [m.value for m in app.metric] == ["2", "2", "2"]
    app.text_input[0].set_value("waiting").run()
    assert not app.exception
    assert app.dataframe[-1].value.iloc[0]["Text"] == "Still waiting"
