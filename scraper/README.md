# Source collector

Run `python -m scraper` from the project root to start one on-demand crawl. The collector reads the named source register in `data/dossier.json`, downloads those record-specific pages or documents, and extracts a short passage tied to each linked finding. It does not discover URLs or run on a schedule. Add a verified source to the register before the next crawl. It removes page navigation and comment sections before extracting text.

The collector writes:

- `data/discovery/source_snapshots.jsonl`: publisher, title, entity, related finding, text excerpt, retrieval time, extracted length and SHA-256 document hash.
- `data/discovery/run_status_latest.json`: HTTP status, extraction result and snapshot count for each source.

Generic search indexes are listed as skipped until a specific record is selected. If a source has an equivalent official fallback document URL, the collector tries it when the primary URL fails and records which route supplied the snapshot. Alternate index links remain available in the dashboard for browser access and are not treated as the source document. A source that denies automated access is marked `blocked`. Automated-access interstitial pages are marked `access_challenge`; their page text is not saved as evidence. Excerpts must match terms from the linked finding, so unrelated page recommendations are not stored as source snapshots.

Requests use an eight-second connect timeout, a 25-second read timeout, a one-second delay and a 20 MB download limit. PDF text extraction uses `pypdf`.
