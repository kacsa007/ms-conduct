import datetime as dt
import os
import sqlite3
import time
from contextlib import closing

import pandas as pd
import requests
import streamlit as st

from .archive import Archive, DEFAULT_REPO
from .store import DEFAULT_DB, examples, report


@st.cache_data(ttl=600, show_spinner=False)
def published(repo):
    archive = Archive(repo)
    manifest = archive.manifest(archive.release())
    return manifest["report"] if manifest else None


def show(root):
    st.subheader("Allianz on Reddit")
    st.caption(
        "Public discussions across countries and businesses. Keyword labels describe wording. "
        "Counts include replies in matching threads and can include venues, sport and investments."
    )
    path = root / DEFAULT_DB
    now = int(time.time())
    try:
        if path.is_file():
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                db.row_factory = sqlite3.Row
                data = report(db, now)
        else:
            data = published(os.getenv("REDDIT_REPOSITORY", DEFAULT_REPO))
    except (requests.RequestException, ValueError, KeyError, sqlite3.Error):
        st.error(
            "Could not read the discussion data. Check the database or retry the latest release."
        )
        return
    if data is None:
        st.info("The first successful daily Reddit collection will populate this page.")
        return
    runs = data["runs"]
    if runs:
        st.caption(f"Latest collection (UTC) · {runs[0]['day']}")
        last = dt.date.fromisoformat(runs[0]["day"])
        if (dt.datetime.fromtimestamp(now, dt.timezone.utc).date() - last).days >= 2:
            st.warning("The last collection is at least two days old.")
    totals = data["totals"]
    for col, label, key in zip(
        st.columns(3),
        ["Posts", "Comments", "Explicit brand mentions"],
        ["posts", "comments", "direct_mentions"],
    ):
        col.metric(label, totals[key])
    if data["labels"]:
        st.markdown("#### What people discuss")
        frame = pd.DataFrame(data["labels"]).rename(
            columns={"label": "Label", "posts": "Posts", "comments": "Comments"}
        )
        for kind, title in (
            ("topic", "Topics"),
            ("cue", "Language in posts and comments"),
        ):
            subset = frame[frame["kind"] == kind]
            if not subset.empty:
                st.caption(title)
                st.dataframe(subset[["Label", "Posts", "Comments"]], hide_index=True)
    if data["topic_cues"]:
        st.markdown("#### Wording by topic")
        topic = st.selectbox("Topic", sorted({r["topic"] for r in data["topic_cues"]}))
        selected = pd.DataFrame(r for r in data["topic_cues"] if r["topic"] == topic)
        st.dataframe(
            selected[["wording", "posts", "comments", "direct_mentions"]],
            hide_index=True,
        )
    if data["weeks"]:
        st.markdown("#### When the sampled items were posted")
        st.bar_chart(
            pd.DataFrame(data["weeks"]).set_index("week")[["posts", "comments"]]
        )
    if data["communities"]:
        with st.expander("Most represented communities"):
            st.dataframe(data["communities"], hide_index=True)
    with st.expander("Collection coverage"):
        st.write(
            "Search pages and comment streams cover a sample of Reddit. Each week is grouped by "
            "publication date. The same post can match several topics. Counts of search hits and "
            "comment hits in the run log can include repeats."
        )
        if runs:
            st.dataframe(runs, hide_index=True)
    if path.is_file():
        st.markdown("#### Read the source discussions")
        term = st.text_input("Search post and comment text")
        label = st.selectbox(
            "Filter by wording or topic",
            [""] + [r["label"] for r in data["labels"]],
            format_func=lambda x: x or "All labels",
        )
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            matches = list(examples(db, now, term, label))
        if matches:
            st.dataframe(
                matches,
                column_config={"Source": st.column_config.LinkColumn("Source")},
                hide_index=True,
            )
        else:
            st.caption("No matching current items.")
    else:
        st.caption(
            "Run python -m reddit_monitor restore with the database key to search "
            "individual posts, comments and source links in your local dashboard."
        )
