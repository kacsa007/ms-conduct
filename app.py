import html
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd
import streamlit as st


ROOT = Path(__file__).parent
DATA_PATH = ROOT / "data" / "dossier.json"
DISCOVERY_PATH = ROOT / "data" / "discovery"


def load_dossier():
    with DATA_PATH.open("r", encoding="utf-8") as dossier_file:
        return json.load(dossier_file)


def text(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def safe_url(value):
    value = text(value).strip()
    if urlsplit(value).scheme.lower() in ("http", "https"):
        return value
    return ""


def source_link(source):
    url = safe_url(source.get("url"))
    label = "{}: {}".format(text(source.get("publisher")), text(source.get("title")))
    if not url:
        return html.escape(label)
    return '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>'.format(
        html.escape(url, quote=True), html.escape(label)
    )


def source_links(source_ids, sources):
    links = [source_link(sources[source_id]) for source_id in source_ids if source_id in sources]
    return "<br>".join(links) if links else "No linked source listed."


def load_discovery_candidates():
    candidates = []
    errors = []
    files = sorted(DISCOVERY_PATH.glob("*.jsonl")) if DISCOVERY_PATH.exists() else []
    for path in files:
        try:
            with path.open("r", encoding="utf-8") as discovery_file:
                for line_number, line in enumerate(discovery_file, start=1):
                    if not line.strip():
                        continue
                    try:
                        value = json.loads(line)
                        if not isinstance(value, dict):
                            raise ValueError("expected a JSON object")
                        value["_discovery_file"] = path.name
                        candidates.append(value)
                    except (json.JSONDecodeError, ValueError) as error:
                        errors.append("{} line {}: {}".format(path.name, line_number, error))
        except OSError as error:
            errors.append("{}: {}".format(path.name, error))
    return candidates, errors, files


def load_latest_run_status():
    status_files = sorted(DISCOVERY_PATH.glob("run_status_*.json")) if DISCOVERY_PATH.exists() else []
    if not status_files:
        return None, []
    path = status_files[-1]
    try:
        with path.open("r", encoding="utf-8") as status_file:
            return json.load(status_file), []
    except (OSError, json.JSONDecodeError) as error:
        return None, ["{}: {}".format(path.name, error)]


dossier = load_dossier()
records = dossier["records"]
sources = {source["id"]: source for source in dossier["sources"]}

st.set_page_config(
    page_title="Allianz Suisse | Evidence file",
    page_icon="A",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      :root {
        --ink: #142536;
        --navy: #102d46;
        --blue: #0758a5;
        --blue-light: #e5f0fb;
        --paper: #f1f4f7;
        --white: #ffffff;
        --rule: #b9c7d4;
        --muted: #42566a;
        --amber: #704500;
        --amber-bg: #fff2ce;
        --green: #15563f;
        --green-bg: #e1f2e9;
      }
      html, body, [class*="css"] { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
      .stApp { background: var(--paper); color: var(--ink); }
      [data-testid="stAppViewContainer"] { background: var(--paper); }
      [data-testid="stMainBlockContainer"] { max-width: 1480px; padding-top: 1.6rem; padding-bottom: 3rem; }
      [data-testid="stSidebar"] { background: var(--navy); border-right: 1px solid #092033; }
      [data-testid="stSidebar"] * { color: #f5f8fb; }
      [data-testid="stSidebar"] [data-testid="stCaptionContainer"] { color: #d5e1ec; }
      [data-testid="stSidebar"] hr { border-color: #557087; }
      [data-testid="stSidebar"] [role="radiogroup"] label { font-size: 1.04rem; font-weight: 600; padding: .26rem 0; }
      h1, h2, h3 { color: var(--ink); letter-spacing: -0.02em; }
      h1 { font-size: clamp(2rem, 3.8vw, 2.8rem) !important; line-height: 1.08 !important; }
      h2 { font-size: 1.65rem !important; margin-top: .8rem !important; }
      h3 { font-size: 1.2rem !important; line-height: 1.35 !important; }
      .stMarkdown p, .stMarkdown li { font-size: 1.02rem; line-height: 1.58; }
      .stCaption, [data-testid="stCaptionContainer"] { color: var(--muted); font-size: .94rem; }
      a { color: #064f91 !important; text-decoration-thickness: 1px; text-underline-offset: 2px; }
      a:hover { color: #003b70 !important; text-decoration-thickness: 2px; }
      .masthead { background: var(--navy); color: white; border-left: 8px solid #1c78c0; padding: 1.5rem 1.7rem; margin: 0 0 1.4rem; }
      .masthead-kicker { color: #c5d9ea; font-size: 1rem; font-weight: 700; margin-bottom: .55rem; }
      .masthead-title { color: white; font-size: clamp(2.1rem, 4vw, 3.3rem); font-weight: 750; line-height: 1.04; letter-spacing: -.035em; }
      .masthead-sub { color: #eef4fa; margin-top: .7rem; max-width: 76ch; font-size: 1.05rem; line-height: 1.55; }
      .evidence-card { background: var(--white); border: 1px solid var(--rule); border-left: 6px solid var(--blue); padding: 1.15rem 1.35rem 1.2rem; margin: 0 0 1rem; }
      .evidence-card.reported { border-left-color: #a16600; }
      .evidence-card.disclosure { border-left-color: #1c7052; }
      .evidence-card-top { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: .5rem 1rem; margin-bottom: .4rem; }
      .evidence-date { color: #33495c; font-size: .98rem; font-weight: 700; }
      .evidence-grade { display: inline-block; border: 1px solid #7390a7; background: #eaf2f8; color: #163c5e; padding: .22rem .55rem; font-weight: 700; font-size: .92rem; }
      .evidence-grade.reported { border-color: #c09236; background: var(--amber-bg); color: var(--amber); }
      .evidence-grade.disclosure { border-color: #579177; background: var(--green-bg); color: var(--green); }
      .evidence-title { color: var(--ink); font-size: 1.32rem; font-weight: 750; line-height: 1.34; margin: .35rem 0 .28rem; }
      .evidence-entity { color: #174a75; font-size: 1rem; font-weight: 700; margin-bottom: .5rem; }
      .evidence-summary { color: var(--ink); font-size: 1.02rem; line-height: 1.58; margin: .45rem 0; }
      .evidence-tags { margin: .45rem 0 .55rem; }
      .evidence-tag { display: inline-block; border: 1px solid #a9bac8; background: #f2f6f9; color: #263f54; padding: .15rem .45rem; margin: 0 .35rem .25rem 0; font-size: .9rem; }
      .evidence-details { color: var(--ink); margin: .35rem 0 .75rem; padding-left: 1.3rem; }
      .evidence-details li { margin: .18rem 0; line-height: 1.5; }
      .evidence-scope { background: #f0f4f7; border-top: 1px solid var(--rule); padding: .7rem .8rem; margin-top: .7rem; font-size: .98rem; line-height: 1.55; }
      .evidence-sources { margin-top: .75rem; font-size: .95rem; line-height: 1.65; }
      .discovery-card { background: white; border: 1px solid var(--rule); padding: 1rem 1.2rem; margin: .8rem 0; }
      .discovery-label { color: #6e4300; background: #fff0c2; border: 1px solid #c38b20; display: inline-block; font-size: .9rem; font-weight: 800; padding: .2rem .55rem; margin-bottom: .55rem; }
      .discovery-meta { color: #33495c; font-size: .96rem; line-height: 1.6; }
      .discovery-excerpt { border-left: 4px solid #8297aa; padding: .55rem .75rem; margin: .6rem 0 0; background: #f4f7f9; color: var(--ink); line-height: 1.55; }
      .section-rule { border-top: 1px solid var(--rule); margin: 1.4rem 0 1rem; }
      div[data-testid="stMetric"] { background: white; border: 1px solid var(--rule); border-top: 5px solid var(--blue); padding: .85rem 1rem; min-height: 8rem; }
      div[data-testid="stMetricLabel"] { color: #33495c; font-size: .96rem; }
      div[data-testid="stMetricValue"] { color: var(--ink); font-size: 2.2rem; font-weight: 750; }
      div[data-testid="stTextInput"] label, div[data-testid="stMultiSelect"] label, div[data-testid="stSelectbox"] label { color: var(--ink); font-size: 1rem; font-weight: 700; }
      input, textarea, [data-baseweb="select"] { font-size: 1rem !important; }
      button:focus-visible, a:focus-visible, input:focus-visible, [tabindex="0"]:focus-visible { outline: 3px solid #f0b429 !important; outline-offset: 2px !important; }
      @media (max-width: 720px) {
        [data-testid="stMainBlockContainer"] { padding: 1rem 1rem 2rem; }
        .masthead { padding: 1.2rem; }
        .evidence-card { padding: .95rem; }
      }
      @media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important; animation-duration: .01ms !important; transition-duration: .01ms !important; } }
    </style>
    """,
    unsafe_allow_html=True,
)


def record_card(record):
    evidence_class = text(record.get("evidence_class"))
    reported = evidence_class in ("Reported allegation", "Reported court outcome")
    disclosure = evidence_class in ("Company disclosure", "Official policy")
    card_class = "reported" if reported else "disclosure" if disclosure else "confirmed"
    grade_class = "reported" if reported else "disclosure" if disclosure else ""
    tags = [record.get("geography", "")]
    tags.extend(record.get("tags", []))
    tag_html = "".join(
        '<span class="evidence-tag">{}</span>'.format(html.escape(text(tag)))
        for tag in tags
        if text(tag)
    )
    details = "".join("<li>{}</li>".format(html.escape(text(item))) for item in record.get("details", []))
    st.markdown(
        """
        <article class="evidence-card {card_class}">
          <div class="evidence-card-top">
            <div class="evidence-date">{date} &nbsp; | &nbsp; {category}</div>
            <span class="evidence-grade {grade_class}">{evidence_class}</span>
          </div>
          <div class="evidence-tags">{tags}</div>
          <h3 class="evidence-title">{title}</h3>
          <div class="evidence-entity">{entity}</div>
          <div class="evidence-summary">{summary}</div>
          {details_html}
          <div class="evidence-scope"><strong>Scope of this record:</strong> {scope_note}</div>
          <div class="evidence-sources"><strong>Sources</strong><br>{links}</div>
        </article>
        """.format(
            card_class=card_class,
            grade_class=grade_class,
            date=html.escape(text(record.get("date_label"))),
            category=html.escape(text(record.get("category"))),
            evidence_class=html.escape(evidence_class),
            tags=tag_html,
            title=html.escape(text(record.get("title"))),
            entity=html.escape(text(record.get("entity"))),
            summary=html.escape(text(record.get("summary"))),
            details_html='<ul class="evidence-details">{}</ul>'.format(details) if details else "",
            scope_note=html.escape(text(record.get("scope_note"))),
            links=source_links(record.get("source_ids", []), sources),
        ),
        unsafe_allow_html=True,
    )


with st.sidebar:
    st.markdown("## Research desk")
    st.caption("Allianz Suisse corporate conduct dossier")
    page = st.radio(
        "Go to",
        ["Overview", "Findings", "Research plan", "Sources", "Discovery leads"],
        label_visibility="collapsed",
    )
    st.markdown("---")
    st.caption("Each finding keeps its evidence type and Allianz entity visible.")
    st.caption("Last reviewed: {}".format(dossier.get("reviewed_on", "Not recorded")))


st.markdown(
    """
    <header class="masthead">
      <div class="masthead-kicker">Research file for Switzerland, 2010–2026</div>
      <div class="masthead-title">Allianz Suisse<br>corporate conduct</div>
      <div class="masthead-sub">Source-linked records of regulatory outcomes, court decisions, company disclosures and reported allegations.</div>
    </header>
    """,
    unsafe_allow_html=True,
)


if page == "Overview":
    primary_outcomes = sum(
        1 for record in records if record["evidence_class"] in ("Regulatory or criminal outcome", "Court decision")
    )
    swiss_records = sum(1 for record in records if record["geography"] == "Switzerland")
    reported_records = sum(
        1 for record in records if record["evidence_class"] in ("Reported allegation", "Reported court outcome")
    )
    metric_columns = st.columns(4)
    metric_columns[0].metric("Dossier records", len(records))
    metric_columns[1].metric("Regulatory or court outcomes", primary_outcomes)
    metric_columns[2].metric("Switzerland records", swiss_records)
    metric_columns[3].metric("Reported allegations or outcomes", reported_records)

    st.markdown("<div class='section-rule'></div>", unsafe_allow_html=True)
    left, right = st.columns([1, 1])
    with left:
        st.subheader("Records by evidence type")
        evidence_counts = Counter(record["evidence_class"] for record in records)
        chart_data = pd.DataFrame(
            {"Records": list(evidence_counts.values())},
            index=list(evidence_counts.keys()),
        )
        st.bar_chart(chart_data, horizontal=True, color="#0758a5", height=320)
        st.caption("Counts describe records in this research pass. A case can have more than one record.")
    with right:
        st.subheader("What this file supports")
        st.markdown(
            """
            - Several confirmed group-level enforcement cases involve subsidiaries outside Switzerland.
            - The selected Swiss court cases have different outcomes: a 2016 decision favored Allianz, while a 2024 decision required Allianz Suisse to pay benefits.
            - The Zurich life-application story remains an unresolved media allegation in the sources reviewed.
            - This record does not establish a general pattern of misconduct by Allianz Suisse.
            """
        )
        st.info("FINMA and insurance-court records are anonymised. A name search with no named Allianz result cannot establish that no proceeding exists.")

    st.markdown("<div class='section-rule'></div>", unsafe_allow_html=True)
    st.subheader("Latest records")
    for record in sorted(records, key=lambda item: item["sort_date"], reverse=True)[:4]:
        record_card(record)


elif page == "Findings":
    st.subheader("Findings register")
    st.write("Filter the records, then open a source link to inspect the underlying material.")
    filter_columns = st.columns([1.1, 1, 1.5])
    evidence_options = sorted({record["evidence_class"] for record in records})
    geography_options = sorted({record["geography"] for record in records})
    selected_evidence = filter_columns[0].multiselect("Evidence class", evidence_options)
    selected_geography = filter_columns[1].multiselect("Geography", geography_options)
    search_text = filter_columns[2].text_input("Search records and sources", placeholder="Entity, topic, publisher or source detail")

    visible_records = []
    for record in records:
        if selected_evidence and record["evidence_class"] not in selected_evidence:
            continue
        if selected_geography and record["geography"] not in selected_geography:
            continue
        linked_source_text = " ".join(
            "{} {} {} {}".format(
                sources[source_id].get("publisher", ""),
                sources[source_id].get("title", ""),
                sources[source_id].get("source_type", ""),
                sources[source_id].get("use", ""),
            )
            for source_id in record.get("source_ids", [])
            if source_id in sources
        )
        searchable = " ".join(
            [
                text(record.get("title")),
                text(record.get("entity")),
                text(record.get("summary")),
                text(record.get("scope_note")),
                text(record.get("category")),
                linked_source_text,
            ]
            + [text(item) for item in record.get("details", [])]
            + [text(item) for item in record.get("tags", [])]
        ).lower()
        if search_text and search_text.lower() not in searchable:
            continue
        visible_records.append(record)

    visible_records.sort(key=lambda item: item["sort_date"], reverse=True)
    st.caption("{} of {} records shown".format(len(visible_records), len(records)))
    if visible_records:
        for record in visible_records:
            record_card(record)
        export_rows = [
            {
                "date": record["date_label"],
                "title": record["title"],
                "entity": record["entity"],
                "geography": record["geography"],
                "evidence_class": record["evidence_class"],
                "summary": record["summary"],
                "scope_note": record["scope_note"],
                "sources": " | ".join(sources[source_id]["url"] for source_id in record["source_ids"]),
            }
            for record in visible_records
        ]
        st.download_button(
            "Download visible records as CSV",
            pd.DataFrame(export_rows).to_csv(index=False).encode("utf-8"),
            file_name="allianz-suisse-dossier.csv",
            mime="text/csv",
        )
    else:
        st.info("No records match those filters. Clear one or more filters to widen the results.")


elif page == "Research plan":
    st.subheader("Research plan")
    st.write("Steps from the supplied plan, with coverage, results and outstanding actions.")
    for index, step in enumerate(dossier["research_steps"], start=1):
        step_title = "{}. {}  |  {}".format(index, step["name"], step["status"])
        with st.expander(step_title, expanded=index <= 2):
            st.markdown("**Coverage**")
            st.write(step["coverage"])
            st.markdown("**Result or limit**")
            st.write(step["result"])
            if step.get("source_ids"):
                st.markdown("**Sources**")
                st.markdown(source_links(step["source_ids"], sources), unsafe_allow_html=True)
            if step.get("next_action"):
                st.markdown("**Next action**")
                st.write(step["next_action"])

    st.markdown("<div class='section-rule'></div>", unsafe_allow_html=True)
    st.info("Do not combine unrelated subsidiaries, countries, legal regimes or evidence grades to infer a pattern.")


elif page == "Sources":
    st.subheader("Source register")
    st.write("Primary regulator, court and company records are identified separately from journalism and aggregator leads.")
    query = st.text_input("Find a source", placeholder="Publisher, topic or document type")
    visible_sources = []
    for source in dossier["sources"]:
        haystack = " ".join(
            text(source.get(key)) for key in ("publisher", "title", "source_type", "use")
        ).lower()
        if not query or query.lower() in haystack:
            visible_sources.append(source)
    st.caption("{} of {} sources shown".format(len(visible_sources), len(dossier["sources"])))
    for source in visible_sources:
        with st.container(border=True):
            st.markdown("### {}".format(html.escape(text(source.get("title")))))
            st.markdown(
                "**{}**  |  {}".format(
                    html.escape(text(source.get("publisher"))),
                    html.escape(text(source.get("source_type"))),
                )
            )
            st.write(source.get("use", ""))
            url = safe_url(source.get("url"))
            if url:
                st.markdown(
                    '<a href="{}" target="_blank" rel="noopener noreferrer">Open source</a>'.format(
                        html.escape(url, quote=True)
                    ),
                    unsafe_allow_html=True,
                )


else:
    st.subheader("Discovery leads")
    st.write("Scraped results are unreviewed leads. Check the original source and confirm relevance before adding anything to the findings register.")
    candidates, discovery_errors, discovery_files = load_discovery_candidates()
    latest_status, status_errors = load_latest_run_status()
    if discovery_errors:
        for error in discovery_errors:
            st.warning("Could not read {}".format(error))
    for error in status_errors:
        st.warning("Could not read crawl status {}".format(error))

    if latest_status:
        status_rows = []
        for source_status in latest_status.get("sources", []):
            common = {
                "Source": text(source_status.get("source")),
                "HTTP": text(source_status.get("http_status")),
                "Status": text(source_status.get("status")),
                "Candidates": text(source_status.get("candidate_count", "")),
                "Note": text(source_status.get("note") or source_status.get("error")),
            }
            if source_status.get("queries"):
                for query in source_status["queries"]:
                    hits = text(query.get("result_count", ""))
                    if hits and query.get("result_relation") == "gte":
                        hits = "≥" + hits
                    status_rows.append(
                        {
                            **common,
                            "Query": text(query.get("term")),
                            "Hits": hits,
                            "Status": text(query.get("error") and "error" or source_status.get("status")),
                            "Returned": text(query.get("returned_count", "")),
                            "Note": text(query.get("error") or query.get("note") or common["Note"]),
                        }
                    )
            else:
                status_rows.append({**common, "Query": "", "Hits": "", "Returned": ""})
        if status_rows:
            st.markdown("#### Latest crawl coverage")
            st.caption("A blocked or non-searchable source is shown with its status. It is not counted as a zero-result search.")
            st.dataframe(pd.DataFrame(status_rows), hide_index=True, use_container_width=True)

    if not discovery_files or not candidates:
        if discovery_files and discovery_errors:
            st.info("No readable discovery candidates are available yet. Check the file errors above.")
        else:
            st.info("No discovery files yet. Candidates will appear here after the scraper writes JSONL files under data/discovery/.")
    else:
        candidate_sources = sorted({text(item.get("source")) for item in candidates if text(item.get("source"))})
        search_col, source_col = st.columns([1.5, 1])
        discovery_query = search_col.text_input("Search leads", placeholder="Title, entity, excerpt or matched term")
        selected_source = source_col.selectbox("Source", ["All sources"] + candidate_sources)
        visible_candidates = []
        for candidate in candidates:
            if selected_source != "All sources" and text(candidate.get("source")) != selected_source:
                continue
            searchable = " ".join(
                [
                    text(candidate.get("title")),
                    text(candidate.get("entity")),
                    text(candidate.get("source")),
                    text(candidate.get("text_excerpt")),
                    text(candidate.get("evidence_status")),
                    " ".join(text(term) for term in candidate.get("matched_terms", [])),
                ]
            ).lower()
            if discovery_query and discovery_query.lower() not in searchable:
                continue
            visible_candidates.append(candidate)
        visible_candidates.sort(key=lambda item: text(item.get("retrieved_at")), reverse=True)
        st.caption("{} of {} leads shown across {} file(s)".format(len(visible_candidates), len(candidates), len(discovery_files)))

        for candidate in visible_candidates:
            title = text(candidate.get("title")) or "Untitled lead"
            source = text(candidate.get("source")) or "Source not recorded"
            file_name = text(candidate.get("_discovery_file"))
            candidate_id = text(candidate.get("id"))
            with st.container(border=True):
                st.markdown('<div class="discovery-label">UNREVIEWED LEAD</div>', unsafe_allow_html=True)
                st.markdown("### {}".format(html.escape(title)))
                meta = []
                if text(candidate.get("date")):
                    meta.append("Date: {}".format(text(candidate.get("date"))))
                if text(candidate.get("entity")):
                    meta.append("Entity: {}".format(text(candidate.get("entity"))))
                if text(candidate.get("evidence_status")):
                    meta.append("Scraper status: {}".format(text(candidate.get("evidence_status"))))
                if text(candidate.get("retrieved_at")):
                    meta.append("Retrieved: {}".format(text(candidate.get("retrieved_at"))))
                if candidate_id:
                    meta.append("ID: {}".format(candidate_id))
                meta.append("File: {}".format(file_name))
                st.markdown('<div class="discovery-meta"><strong>Source:</strong> {}<br>{}</div>'.format(
                    html.escape(source), "<br>".join(html.escape(item) for item in meta)
                ), unsafe_allow_html=True)
                matched_terms = candidate.get("matched_terms", [])
                if matched_terms:
                    st.markdown("**Matched terms:** {}".format(", ".join(text(term) for term in matched_terms)))
                match_note = text(candidate.get("match_note"))
                if match_note:
                    st.info(match_note)
                excerpt = text(candidate.get("text_excerpt"))
                if excerpt:
                    st.markdown('<div class="discovery-excerpt">{}</div>'.format(html.escape(excerpt)), unsafe_allow_html=True)
                url = safe_url(candidate.get("url"))
                if url:
                    st.markdown(
                        '<a href="{}" target="_blank" rel="noopener noreferrer">Open original source</a>'.format(
                            html.escape(url, quote=True)
                        ),
                        unsafe_allow_html=True,
                    )

