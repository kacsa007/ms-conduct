import json
from collections import Counter
from pathlib import Path

import pandas as pd
import streamlit as st


ROOT = Path(__file__).parent
DATA_PATH = ROOT / "data" / "dossier.json"


@st.cache_data
def load_dossier():
    with DATA_PATH.open("r", encoding="utf-8") as dossier_file:
        return json.load(dossier_file)


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
      @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=DM+Serif+Display&display=swap');
      :root {
        --ink: #152b43;
        --blue: #07549a;
        --blue-dark: #003781;
        --paper: #f5f8fb;
        --rule: #cbd8e4;
        --muted: #53687d;
        --alert: #8a5b00;
        --alert-bg: #fff4d7;
        --success: #165b47;
        --success-bg: #e6f3ed;
      }
      html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
      .stApp { background: var(--paper); color: var(--ink); }
      [data-testid="stSidebar"] { background: #eaf0f6; border-right: 1px solid var(--rule); }
      [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2 { color: var(--blue-dark); }
      h1, h2, h3 { color: var(--ink); letter-spacing: -0.025em; }
      h1 { font-family: 'DM Serif Display', Georgia, serif !important; font-size: 2.5rem !important; font-weight: 400 !important; }
      h2 { font-size: 1.35rem !important; }
      h3 { font-size: 1.05rem !important; }
      p, li { line-height: 1.55; }
      a { color: var(--blue); }
      .masthead { border-top: 5px solid var(--blue-dark); border-bottom: 1px solid var(--rule); padding: 1.2rem 0 1rem; margin: 0 0 1.4rem; }
      .masthead-kicker { color: var(--blue); font-size: .78rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; margin-bottom: .25rem; }
      .masthead-title { color: var(--ink); font-family: 'DM Serif Display', Georgia, serif; font-size: clamp(2rem, 4vw, 3rem); line-height: 1.05; }
      .masthead-sub { color: var(--muted); margin-top: .65rem; max-width: 70ch; }
      .case-card { background: #fff; border: 1px solid var(--rule); border-left: 4px solid var(--blue); padding: 1rem 1.2rem 1.05rem; margin: 0 0 .9rem; }
      .case-card.reported { border-left-color: #b77b00; }
      .case-card.disclosure { border-left-color: #347a66; }
      .case-meta { color: var(--muted); font-size: .84rem; margin-bottom: .35rem; }
      .case-title { font-size: 1.18rem; font-weight: 700; line-height: 1.3; color: var(--ink); }
      .case-entity { color: var(--blue-dark); font-size: .92rem; font-weight: 600; margin-top: .22rem; }
      .case-summary { color: var(--ink); margin: .65rem 0 .4rem; }
      .case-scope { border-top: 1px solid #e0e8ef; color: var(--muted); font-size: .88rem; margin-top: .65rem; padding-top: .55rem; }
      .pill { display: inline-block; border: 1px solid #b4c7d9; color: #254865; background: #edf4fa; padding: .18rem .5rem; margin: .15rem .25rem .1rem 0; font-size: .76rem; font-weight: 600; }
      .pill.reported { border-color: #e6cf92; color: #785100; background: var(--alert-bg); }
      .source-line { font-size: .87rem; margin-top: .7rem; }
      .note { background: #edf3f8; border-left: 3px solid #7297b9; color: #304b64; padding: .65rem .85rem; font-size: .9rem; }
      .section-rule { border-top: 1px solid var(--rule); margin: 1.25rem 0 .95rem; }
      div[data-testid="stMetric"] { background: #fff; border: 1px solid var(--rule); border-top: 3px solid var(--blue); padding: .75rem .9rem; }
      div[data-testid="stMetricLabel"] { color: var(--muted); }
      .step-state { font-weight: 700; color: var(--blue-dark); }
      .small-muted { color: var(--muted); font-size: .87rem; }
      button:focus-visible, a:focus-visible, input:focus-visible { outline: 3px solid #f0b429 !important; outline-offset: 2px !important; }
      @media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important; animation-duration: .01ms !important; transition-duration: .01ms !important; } }
    </style>
    """,
    unsafe_allow_html=True,
)


def source_links(source_ids):
    return " · ".join(
        "[{}]({})".format(sources[source_id]["publisher"], sources[source_id]["url"])
        for source_id in source_ids
    )


def record_card(record):
    reported = record["evidence_class"] in ("Reported allegation", "Reported court outcome")
    disclosure = record["evidence_class"] in ("Company disclosure", "Official policy")
    card_class = "reported" if reported else "disclosure" if disclosure else "confirmed"
    pills = [record["evidence_class"], record["geography"]]
    pills.extend(record.get("tags", []))
    pill_html = "".join(
        '<span class="pill{}">{}</span>'.format(
            " reported" if reported and index == 0 else "",
            tag,
        )
        for index, tag in enumerate(pills)
    )
    details = "".join("<li>{}</li>".format(item) for item in record.get("details", []))
    st.markdown(
        """
        <article class="case-card {card_class}">
          <div class="case-meta">{date} &nbsp; / &nbsp; {category}</div>
          <div>{pills}</div>
          <div class="case-title">{title}</div>
          <div class="case-entity">{entity}</div>
          <div class="case-summary">{summary}</div>
          <ul>{details}</ul>
          <div class="case-scope"><strong>Scope:</strong> {scope_note}</div>
          <div class="source-line"><strong>Sources:</strong> {links}</div>
        </article>
        """.format(
            card_class=card_class,
            date=record["date_label"],
            category=record["category"],
            pills=pill_html,
            title=record["title"],
            entity=record["entity"],
            summary=record["summary"],
            details=details,
            scope_note=record["scope_note"],
            links=source_links(record["source_ids"]),
        ),
        unsafe_allow_html=True,
    )


with st.sidebar:
    st.markdown("### Research desk")
    st.caption("Allianz Suisse corporate conduct dossier")
    page = st.radio("View", ["Overview", "Findings", "Research plan", "Sources"], label_visibility="collapsed")
    st.markdown("---")
    st.caption("Evidence is tagged by its source and the Allianz entity involved.")
    st.caption("Last reviewed: {}".format(dossier["reviewed_on"]))


st.markdown(
    """
    <header class="masthead">
      <div class="masthead-kicker">Research file &nbsp; / &nbsp; Switzerland &nbsp; / &nbsp; 2010–2026</div>
      <div class="masthead-title">Allianz Suisse<br>corporate conduct</div>
      <div class="masthead-sub">A source-linked record of regulatory outcomes, court decisions, company disclosures and reported allegations.</div>
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
    metric_columns[1].metric("Primary-source outcomes", primary_outcomes)
    metric_columns[2].metric("Swiss-specific records", swiss_records)
    metric_columns[3].metric("Reported items", reported_records)

    st.markdown("<div class='section-rule'></div>", unsafe_allow_html=True)
    left, right = st.columns([1.2, 1])
    with left:
        st.subheader("Evidence by record type")
        evidence_counts = Counter(record["evidence_class"] for record in records)
        chart_data = pd.DataFrame(
            {"Records": list(evidence_counts.values())},
            index=list(evidence_counts.keys()),
        )
        st.bar_chart(chart_data, horizontal=True, color="#07549a", height=300)
        st.caption("Counts describe the records in this first research pass. One underlying case may have more than one record.")
    with right:
        st.subheader("What the evidence supports")
        st.markdown(
            """
            - Several confirmed group-level enforcement cases involve subsidiaries outside Switzerland.
            - Swiss-specific sources include two media reports, an individual court decision and Allianz's HIS disclosure.
            - The Zurich life-application story remains an unresolved media allegation in the sources reviewed.
            - This record does not establish a general pattern of misconduct by Allianz Suisse.
            """
        )
        st.markdown(
            '<div class="note">FINMA and insurance-court records are anonymised. A name search that returns no named Allianz record cannot establish that no proceeding exists.</div>',
            unsafe_allow_html=True,
        )

    st.markdown("<div class='section-rule'></div>", unsafe_allow_html=True)
    st.subheader("Latest and most relevant records")
    selected = sorted(records, key=lambda item: item["sort_date"], reverse=True)[:4]
    for record in selected:
        record_card(record)


elif page == "Findings":
    st.subheader("Findings register")
    st.write("Filter by evidence class, geography or search terms. Open each source to inspect the underlying record.")
    filter_columns = st.columns([1, 1, 1.4])
    evidence_options = sorted({record["evidence_class"] for record in records})
    geography_options = sorted({record["geography"] for record in records})
    selected_evidence = filter_columns[0].multiselect("Evidence class", evidence_options)
    selected_geography = filter_columns[1].multiselect("Geography", geography_options)
    search_text = filter_columns[2].text_input("Search", placeholder="Entity, topic or source detail")

    visible_records = []
    for record in records:
        if selected_evidence and record["evidence_class"] not in selected_evidence:
            continue
        if selected_geography and record["geography"] not in selected_geography:
            continue
        searchable = " ".join(
            [record["title"], record["entity"], record["summary"], record["scope_note"], record["category"]]
            + record.get("details", [])
            + record.get("tags", [])
        ).lower()
        if search_text and search_text.lower() not in searchable:
            continue
        visible_records.append(record)

    visible_records.sort(key=lambda item: item["sort_date"], reverse=True)
    st.caption("{} records shown".format(len(visible_records)))
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
        st.info("No records match those filters.")


elif page == "Research plan":
    st.subheader("Research plan, in order")
    st.write("This follows the source list and research priorities in the supplied plan. Open work is marked explicitly.")
    for index, step in enumerate(dossier["research_steps"], start=1):
        with st.expander("{}. {}   |   {}".format(index, step["name"], step["status"]), expanded=index <= 2):
            st.markdown("**Coverage:** {}".format(step["coverage"]))
            st.markdown("**Result or limit:** {}".format(step["result"]))
            if step.get("source_ids"):
                st.markdown("**Sources:** {}".format(source_links(step["source_ids"])))
            if step.get("next_action"):
                st.markdown("**Next action:** {}".format(step["next_action"]))

    st.markdown("<div class='section-rule'></div>", unsafe_allow_html=True)
    st.markdown(
        '<div class="note">No conclusion about a pattern should be drawn by combining unrelated subsidiaries, countries, legal regimes or evidence grades. The dossier keeps those records separate.</div>',
        unsafe_allow_html=True,
    )


else:
    st.subheader("Source register")
    st.write("Primary regulator, court and company records are labelled separately from journalism and aggregator leads.")
    query = st.text_input("Find a source", placeholder="Publisher, topic or document type")
    visible_sources = []
    for source in dossier["sources"]:
        haystack = " ".join([source["publisher"], source["title"], source["source_type"], source["use"]]).lower()
        if not query or query.lower() in haystack:
            visible_sources.append(source)
    st.caption("{} sources shown".format(len(visible_sources)))
    for source in visible_sources:
        st.markdown(
            "**[{}]({})**  \n{} · {}  \n{}".format(
                source["title"],
                source["url"],
                source["publisher"],
                source["source_type"],
                source["use"],
            )
        )
        st.markdown("<div class='section-rule'></div>", unsafe_allow_html=True)
