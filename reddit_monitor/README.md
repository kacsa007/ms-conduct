# Daily Allianz Reddit monitor

The workflow searches Reddit every day at **04:17 UTC**. It collects search
results across subreddits, replies in matching threads and direct mentions in
the latest comments from r/all and selected insurance, travel, finance and Swiss
communities. Search terms include Allianz, AllianzGI, AllianzDirect, AllianzTravel,
AllianzTrade, Euler Hermes and several local scripts. See `QUERIES` and `STREAMS`
in `collect.py` to change the coverage.

## Enable the live job

Use a Reddit web or script app approved for this use. The collector uses app-only
OAuth. Add these repository secrets under **Settings → Secrets and variables →
Actions**. Rotate the client secret if it has been shared in a screenshot.

| GitHub secret | Value |
| --- | --- |
| REDDIT_CLIENT_ID | Reddit app client ID |
| REDDIT_CLIENT_SECRET | Current Reddit app secret |
| REDDIT_USER_AGENT | For example, `linux:ms-conduct:v1.0 (by /u/YOUR_USERNAME)` |
| REDDIT_DB_KEY | Output of `openssl rand -base64 32`. Keep a private backup. |

The database key is independent of the Reddit credentials. Retain it for the
life of the database. A different key cannot restore the previous snapshot.
In **Actions → Daily Allianz Reddit collection → Run workflow**, select the
default branch for an initial run. The GitHub token supplied to the workflow
needs permission to write releases. The repository must allow a mutable
prerelease named `reddit-data-v1`.

## Data and reports

[**Latest Reddit CSV download**](https://github.com/kacsa007/ms-conduct/releases/tag/reddit-csv)
is the fixed download page. Sign in to GitHub and follow its full CSV link.
For an earlier export, open the [collection run history](https://github.com/kacsa007/ms-conduct/actions/workflows/reddit.yml)
and choose the run. The Artifacts section identifies each download by run ID and attempt.

* `reddit-discussions-csv-<run>-<attempt>` contains `reddit-discussions.csv`
  and `report.md`. It includes post and comment text, parent titles, dates, scores,
  brand flags, topic labels and source links. Each ZIP is retained for two days.
* `reddit-topic-counts-<run>-<attempt>` contains `topic-counts.csv` with the snapshot
  time, totals and post/comment counts for each topic or wording label. It is
  retained for 30 days. Counts can overlap across labels and collection days.

The fixed page links to the latest completed export even when a later run fails
before producing files. GitHub requires a signed-in account with repository
read access to download Actions artifacts.

The CSV uses UTF-8 with a byte-order mark for Excel. Cells beginning with
spreadsheet formula markers have a leading apostrophe so discussion text opens
as text. This affects the spreadsheet copy of those cells.

The job restores the previous SQLite database, updates it, encrypts it with
Fernet and uploads it to a GitHub prerelease. The release description points to
the new asset only after GitHub confirms the upload. The old asset is then
deleted. The public release description contains aggregate statistics. Individual
Reddit titles, bodies and source IDs are inside the encrypted asset. The collector
does not save author fields. The dashboard reads the latest public totals on
opening **Reddit discussions**. Forks can set `REDDIT_REPOSITORY=owner/repo`.

For the source text, supply `REDDIT_DB_KEY` to your local environment, then run:

```bash
python -m pip install -r reddit_monitor/requirements.txt
python -m reddit_monitor restore
python -m reddit_monitor report
python -m reddit_monitor report --format json > reddit-report.json
python -m reddit_monitor export
python -m streamlit run app.py
```

Use `restore --replace` to update an existing copy. The default local path is
`data/reddit/discussion.sqlite`. Anyone with this file can read its contents.
Keep it private and refresh it daily while in use. Remove unused copies. For a
standalone collection, export the three Reddit settings and run
`python -m reddit_monitor collect`. Local collection uses the same incremental
logic. Dashboard dependencies are in the repository's root requirements file.

Use SQLite, pandas or another SQL client to explore the database. One row in
`items` is one Reddit post or comment. `name` is its stable Reddit fullname,
`thread` links a comment to its parent post, `created` and `seen` are UTC Unix
timestamps and `direct` indicates a brand match in the item's own wording.
`tags` stores multiple topic and wording labels per item. `daily_labels` stores
each day's aggregate sample for longer-term comparison, including after individual
items leave the index. Each day is a snapshot and successive days can overlap.

```sql
SELECT day, posts, comments FROM daily_labels
WHERE kind = 'cue' AND label = 'Mentions waiting' ORDER BY day;

SELECT i.name, i.title, i.body FROM items i JOIN tags t USING (name)
WHERE i.direct = 1 AND t.label = 'Claims'
AND i.seen > unixepoch('now', '-44 hours') ORDER BY i.created DESC LIMIT 20;
```

## Coverage and operating limits

* Daily searches overlap the previous week. Historical searches rotate through
  four Reddit sort orders and resume pagination between runs. Indexed searches
  and comment listings have finite depth. Private, deleted and unindexed
  discussions and many comment-only mentions are outside coverage.
* The run budgets 100 HTTP attempts including authentication and retries, paces
  requests and observes Reddit's rate-limit headers. The collector budgets 7.5
  minutes and visits up to 32 threads per run. Deferred comment-tree expansions
  resume from stored child IDs. The job times out at 12 minutes. A 31-day month
  therefore uses at most 372 scheduled runner minutes. Standard hosted runners
  are free for public repositories. Private forks share their plan's allowance
  with other workflows. Full-text artifacts are retained for two days. The encrypted
  database is stored in the release.
* The active index holds up to 4,000 items. Each run rechecks due records using
  Reddit's bulk info endpoint. Unavailable items and replies to unavailable
  parents are removed. Successful runs discard items last seen 44 hours ago
  or earlier. Local reports also filter stale text. If the workflow stops,
  remove the release and local database when retiring the collector.
* The dashboard and Markdown report show post/comment counts, weekly activity
  and multilingual keyword labels such as claims, premiums, waiting and payout
  language. Unlabelled items stay in the sample. Words can occur in a question,
  a quotation or a denial. These counts describe the collected discussion and
  cannot estimate complaint rates or establish an outcome.
* Run coverage includes repeat search hits, skipped or incomplete threads,
  pending expansions, page caps and storage evictions. The action fails when
  zero search pages complete. A completed search with zero hits is valid.

[Reddit Data API guidance](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki)
· [Reddit access approval](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy)
· [GitHub Actions billing](https://docs.github.com/en/actions/concepts/billing-and-usage)
