# Public-source collector

Run `python -m scraper` from the project root. By default it queries SEC EDGAR for the phrases `Allianz Suisse`, `Allianz Global Investors` and `Structured Alpha`, then fetches a fixed set of public source pages and documents. It writes one JSONL file and a separate JSON status report to `data/discovery/`.

```bash
python -m scraper
```

Options include `--start-date`, `--end-date`, `--limit-per-term`, `--pause` and `--output-dir`. SEC full-text search and source pages can return incomplete results or reject automated requests. Each run records HTTP status or the error for each source. A source that could not be reached is not represented as having no results.

Every JSONL entry is marked `candidate_unreviewed`. SEC EDGAR returns filing metadata and links; the collector does not claim that it extracted the phrase context from the filing itself. The collector does not classify misconduct, infer corporate relationships from name matches, or modify `data/dossier.json`.
