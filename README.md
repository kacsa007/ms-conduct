# Allianz Suisse research dashboard

The dashboard and source collector support the source-linked articles in
[`article/README.md`](article/README.md) and
[`article/generated/allianz-conduct-review.md`](article/generated/allianz-conduct-review.md).
Findings stay attached to their evidence class, the exact Allianz entity and the
official source.

## What the code checks

The repository now separates four questions that a conduct dossier has to answer:

1. **Is the dossier internally consistent?** `evidence/schema.py` verifies
   required fields, evidence-class values, unique ids, source references,
   URL safety, publisher/domain agreement, and whether a confirmed outcome
   really links a primary source.
2. **Does a source passage actually support this finding?** `evidence/matching.py`
   builds a corpus-aware term profile per record, prefers curated anchors, and
   otherwise ranks sentence windows with confidence. Each linked record gets
   its own excerpt, so one page cannot silently stand in for another finding.
3. **Does the passage describe wrongdoing, a denial, a policy, or an acquittal?**
   `evidence/signals.py` classifies every hit as `asserted`, `alleged`,
   `negated`, `policy` or `exonerating`. "No indication of money laundering"
   and "our anti-corruption programme" therefore do not become findings.
4. **What is missing or newly visible?** `evidence/verification.py` reports
   records without a collected source, outcome records whose excerpt contains
   no outcome, allegation records whose source now shows a public outcome, and
   wrongdoing signals in a source that no dossier record covers.

These are review aids, not automatic findings. The dashboard shows them on the
**Evidence checks** page with the passage, source and reason for each flag.

## Run with Docker

Start the dashboard:

```bash
docker compose up --build -d dashboard
```

Open [http://127.0.0.1:8501](http://127.0.0.1:8501).

Collect the documents in the dossier's named source register:

```bash
docker compose --profile scrape run --rm scraper
```

The collector fetches named documents from the source register and writes
extracted snapshots to `data/discovery/source_snapshots.jsonl`, a verification
report to `data/discovery/verification_report.json`, and access results to
`data/discovery/run_status_latest.json`. Blocked sources remain visible with
their official links.

Exit codes: `0` at least one snapshot was collected, `1` the run collected no
new snapshot (previous snapshots are retained), `2` the dossier has structural
errors and the run was stopped before any request.

## Run without Docker

```bash
python3 -m pip install -r requirements.txt
python3 -m pip install -r scraper/requirements.txt
python3 -m streamlit run app.py
```

In another terminal:

```bash
python3 -m scraper
```

## Validate and report offline

```bash
python3 -m evidence validate                  # structure, references, URLs
python3 -m evidence validate --snapshots      # include collected snapshots
python3 -m evidence report --output data/discovery/verification_report.json
```

`validate` exits `1` on structural errors (`--strict` also fails on warnings).
Both commands work without network access.

## Generate the source-linked article

```bash
python3 -m publishing
```

This writes a Markdown draft and three local SVG figures to `article/generated/`.
The writer fails on a structurally invalid dossier, labels reported allegations
as claims, quotes company responses only where the record attributes them, and
includes an internal publication-check appendix. See
[`publishing/README.md`](publishing/README.md) for options and the pre-publication
checklist.

## Tests

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest
ruff check . --select F,E9
```

The suite uses fake HTTP responses, so it runs offline and covers retries,
fallbacks, access challenges, encoding, record-level excerpt separation,
wrongdoing polarity, dossier integrity, snapshot retention, Markdown escaping,
figure self-containment and the article's unresolved-source safeguards.

## Record labels

- **Regulatory or criminal outcome**: a regulator or court published the named outcome.
- **Court decision**: a court's written decision in the listed case.
- **Reported allegation**: a claim attributed to a news report.
- **Reported court outcome**: a court outcome described by a news report.
- **Company disclosure** and **Official policy**: information published by Allianz.

## Repository layout

```
app.py                      Streamlit dashboard
evidence/                   shared checks: text, matching, signals, schema, verification
scraper/                    on-demand source collector
publishing/                 Markdown article and SVG figure writer
data/dossier.json           findings, sources and research plan
data/discovery/             collected snapshots, run status, verification report, archive
article/                    source-linked article and generated review
tests/                      offline pytest suite
```
