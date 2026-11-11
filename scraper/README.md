# Source collector

Run `python -m scraper` from the project root to start one on-demand crawl.
The collector reads the named source register in `data/dossier.json`, downloads
those record-specific pages or documents, and extracts a passage tied to each
linked finding. It does not discover URLs or run on a schedule.

## What one run does

1. Validates the dossier. Structural errors stop the run before any request is
   made; warnings are printed but do not block collection.
2. Fetches each registered source, trying the official `fallback_url` when the
   primary URL is blocked, missing or repeatedly failing.
3. Extracts readable text from HTML, XML, plain text and PDFs. Automated-access
   interstitials, binary payloads and empty documents are reported, not stored
   as evidence.
4. Matches a passage separately for every linked record, using carefully
   normalised anchors first and ranked, corpus-aware term matching second. Each
   match records its method, confidence and matched terms.
5. Classifies wrongdoing signals in the matched passages and in the wider page,
   keeping denials, policy text, allegations and exonerating language distinct.
6. Writes the outputs atomically, archives the previous run, and rebuilds the
   verification report.

## Outputs

- `data/discovery/source_snapshots.jsonl`: source id, publisher, title, entity,
  retrieval time, final URL, content type, SHA-256 document hash, extracted
  character count, best excerpt, per-record excerpts (`record_matches`), match
  quality, wrongdoing signals and wider-page signal leads.
- `data/discovery/verification_report.json`: integrity issues, per-record
  evidence status, missing sources and unrecorded wrongdoing leads.
- `data/discovery/run_status_latest.json`: HTTP status, attempts, extraction
  details, match diagnostics, run summary and evidence-check summary.
- `data/discovery/archive/`: timestamped copies of earlier snapshots and status
  files (latest 50 per prefix).

If a run collects no snapshots, the previous `source_snapshots.jsonl` is kept
rather than replaced by an empty file. `run_status_latest.json` records
`snapshots_retained` and `new_snapshot_count` so the dashboard can explain it.

## HTTP behaviour

- Three attempts per URL with exponential backoff, jitter and `Retry-After`
  support. Transient statuses are `408, 425, 429, 500, 502, 503, 504, 522, 524`.
- One request per host per second by default.
- Eight-second connect and 25-second read timeouts, 20 MB download limit.
- Only public `http(s)` URLs; local/private hosts, embedded credentials and
  non-http schemes are rejected.
- Redirect target scheme/host is re-validated before parsing.
- UTF-8 is preferred when it decodes cleanly, falling back to the declared
  charset, cp1252 and ISO-8859-1. This avoids mojibake in German anchors.
- PDF extraction tolerates encrypted files (`pypdf`), per-page failures and a
  page cap; malformed PDFs become `extraction_error`.

Environment overrides: `COLLECTOR_MAX_BYTES`, `COLLECTOR_MAX_EXCERPT`,
`COLLECTOR_PAUSE_SECONDS`, `COLLECTOR_MAX_ATTEMPTS`, `COLLECTOR_MAX_BACKOFF`,
`COLLECTOR_ARCHIVE_LIMIT`.

## Status values

| Status | Meaning |
| --- | --- |
| `fetched` / `fetched_alternate` | Snapshot saved from the primary or official fallback URL |
| `blocked` | HTTP 401/403/451; the official link stays visible in the dashboard |
| `access_challenge` | Automated-access or JavaScript interstitial, not saved as evidence |
| `rate_limited` / `retry_exhausted` | Retry budget spent on 429 or transient 5xx responses |
| `http_error` / `request_error` | Non-retryable HTTP or network failure |
| `invalid_url` / `invalid_redirect` | URL safety check failed |
| `unsupported_content` | Binary document type the collector cannot read as text |
| `extraction_error` | Download succeeded but text extraction failed |
| `no_extractable_text` | Too little readable text to check |
| `no_relevant_text` | Page loaded but no passage matched a linked finding; diagnostics included |
| `no_linked_record` | Fetched for the register, but no dossier finding links it, so no excerpt is stored |
| `skipped_large` | Over the download limit |
| `skipped_index` | Generic search/database entry point, intentionally not fetched |
| `internal_error` | Unexpected collector failure isolated to one source |

## Checks

```bash
python -m evidence validate --snapshots
python -m evidence report --output data/discovery/verification_report.json
python -m pytest
```

A source that denies automated access remains visible in the dashboard with its
official link. An automated flag is never promoted to a finding without reading
the source and keeping the existing evidence class review.
