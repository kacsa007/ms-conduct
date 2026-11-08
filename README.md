# Allianz Suisse research dashboard

The dashboard and source collector support the source-linked article in [`article/README.md`](article/README.md).

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

The collector fetches named documents from the source register and writes extracted snapshots to `data/discovery/source_snapshots.jsonl`. Access results are in `data/discovery/run_status_latest.json`; blocked sources remain visible with their official links.

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

## Record labels

- **Regulatory or criminal outcome**: a regulator or court published the named outcome.
- **Court decision**: a court's written decision in the listed case.
- **Reported allegation**: a claim attributed to a news report.
- **Reported court outcome**: a court outcome described by a news report.
- **Company disclosure** and **Official policy**: information published by Allianz.
