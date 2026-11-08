# Allianz Suisse corporate conduct dossier

A local evidence dashboard with a public-source discovery collector. Python and Streamlit keep the reviewable records and interface in one small project. Scraped items stay separate as unreviewed leads until a person checks the original source.

## Run with Docker

Start the dashboard:

```bash
docker compose up --build -d dashboard
```

Open [http://127.0.0.1:8501](http://127.0.0.1:8501). The service binds to localhost and stores records under the mounted `data/` directory.

Run a one-shot public-source crawl:

```bash
docker compose --profile scrape run --rm scraper
```

The crawler writes source excerpts where pages are accessible, SEC filing metadata and direct filing links to `data/discovery/*.jsonl`, plus per-source run status to `data/discovery/run_status_*.json`. SEC matches are not treated as extracted evidence until the linked filing is checked. Open **Discovery leads** to review candidates. The crawler does not add candidates to curated findings automatically.

## Run without Docker

```bash
python3 -m pip install -r requirements.txt
python3 -m pip install -r scraper/requirements.txt
python3 -m streamlit run app.py
```

Run the collector in a second terminal:

```bash
python3 -m scraper
```

The current collector searches SEC EDGAR and fetches public records or search pages from ASIC, CSSF, DOJ, FINMA, the Swiss Federal Supreme Court, and Allianz. It uses a one-second delay, request timeouts, a 20 MB file limit and a named user agent. A failed or blocked source is recorded in the run-status JSON instead of being treated as an empty result.

## Evidence labels

- **Regulatory or criminal outcome** means an official regulator or court has published an outcome.
- **Court decision** means a judgment in an individual dispute. It does not establish a general pattern by itself.
- **Reported allegation** means a media report that has not been confirmed by a public regulator or court finding in the sources reviewed.
- **Reported court outcome** means the result is described by media reporting and needs a primary judgment for full verification.
- **Company disclosure** and **Official policy** describe what Allianz publishes about its own controls or processes.
- **Unreviewed lead** means a scraper found a source candidate. It is not a finding until the original document and entity are checked.
