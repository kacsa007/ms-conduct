# Allianz Suisse corporate conduct dossier

A local, source-linked dashboard for reviewing the supplied research plan one source at a time. Python and Streamlit keep the research records and dashboard in one small project.

## Run the dashboard

```bash
python3 -m pip install -r requirements.txt
streamlit run app.py
```

The dashboard opens at `http://localhost:8501`. Findings, source details and the research queue live in `data/dossier.json` so the record can be updated without changing the interface.

## Evidence labels

- **Regulatory or criminal outcome** means an official regulator or court has published an outcome.
- **Court decision** means a judgment in an individual dispute. It does not establish a general pattern by itself.
- **Reported allegation** means a media report that has not been confirmed by a public regulator or court finding in the sources reviewed.
- **Reported court outcome** means the court result is described by media reporting and needs a primary judgment for full verification.
- **Company disclosure** and **Official policy** describe what Allianz publishes about its own controls or processes.

The dashboard distinguishes Allianz Suisse from other Allianz entities. It records source limits and unfinished research next to the relevant source rather than treating an unsuccessful name search as proof that nothing happened.
