import html
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd
import streamlit as st

from evidence.io import read_json, read_jsonl_directory
from evidence.verification import STATUS_LABELS, STATUS_SEVERITY, build_report


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
    primary = '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>'.format(
        html.escape(url, quote=True), html.escape(label)
    )
    alternate_url = safe_url(source.get("alternate_url"))
    if alternate_url:
        alternate_label = text(source.get("alternate_label", "Official alternate"))
        primary += ' · <a href="{}" target="_blank" rel="noopener noreferrer">{}</a>'.format(
            html.escape(alternate_url, quote=True), html.escape(alternate_label)
        )
    return primary


def source_links(source_ids, sources):
    links = []
    missing = []
    for source_id in source_ids or []:
        if source_id in sources:
            links.append(source_link(sources[source_id]))
        else:
            missing.append(
                '<span class="missing-source">Source <code>{}</code> is missing from the register</span>'.format(
                    html.escape(text(source_id))
                )
            )
    return "<br>".join(links + missing) if (links or missing) else "No linked source listed."


def load_discovery_candidates():
    candidates, errors, files = read_jsonl_directory(DISCOVERY_PATH)
    for candidate in candidates:
        candidate.setdefault("_discovery_file", "")
    return candidates, errors, files


def load_latest_run_status():
    status_files = sorted(DISCOVERY_PATH.glob("run_status_*.json")) if DISCOVERY_PATH.exists() else []
    if not status_files:
        return None, []
    path = status_files[-1]
    value, errors = read_json(path)
    if not isinstance(value, dict):
        return None, errors or ["{}: expected a JSON object".format(path.name)]
    return value, errors


def record_field(record, field, default=""):
    value = record.get(field, default) if isinstance(record, dict) else default
    return value if value is not None else default


def record_text(record, field, default=""):
    return text(record_field(record, field, default))


def record_list(record, field):
    value = record_field(record, field, [])
    return value if isinstance(value, list) else []


def record_sort_key(record):
    return record_text(record, "sort_date") or "0000-00-00"


try:
    dossier = load_dossier()
except (OSError, json.JSONDecodeError) as error:
    st.set_page_config(page_title="Allianz Suisse | Evidence file", page_icon="A", layout="wide")
    st.error("The dossier could not be read: {}".format(error))
    st.stop()

if not isinstance(dossier, dict):
    st.set_page_config(page_title="Allianz Suisse | Evidence file", page_icon="A", layout="wide")
    st.error("The dossier root must be a JSON object.")
    st.stop()

records = [record for record in dossier.get("records", []) if isinstance(record, dict)]
sources = {
    text(source.get("id")): source
    for source in dossier.get("sources", [])
    if isinstance(source, dict) and text(source.get("id"))
}
research_steps = [step for step in dossier.get("research_steps", []) if isinstance(step, dict)]
snapshots, discovery_errors, discovery_files = load_discovery_candidates()
verification = build_report(dossier, snapshots)
integrity_summary = verification.get("integrity", {}).get("summary", {})
verification_by_record = {
    check.get("record_id"): check for check in verification.get("records", []) if isinstance(check, dict)
}

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
      .evidence-sources { margin-top: .75rem; font-size: .95rem; line-height: 1.65; }
      .missing-source { color: #8a2c14; background: #fff0ea; border: 1px solid #d59a83; padding: .12rem .4rem; font-size: .9rem; }
      .evidence-check { margin: .75rem 0 .2rem; padding: .55rem .72rem; border-left: 4px solid #7390a7; background: #f4f8fb; font-size: .95rem; line-height: 1.5; }
      .evidence-check.ok { border-left-color: #1c7052; background: #eef8f2; }
      .evidence-check.warning { border-left-color: #a16600; background: #fff7e3; }
      .evidence-check ul { margin: .3rem 0 0; padding-left: 1.2rem; }
      .evidence-check li { margin: .12rem 0; }
      .lead-card { background: white; border: 1px solid var(--rule); border-left: 6px solid #a16600; padding: 1rem 1.2rem; margin: .8rem 0; }
      .lead-label { color: #6e4300; background: #fff0c2; border: 1px solid #c38b20; display: inline-block; font-size: .88rem; font-weight: 800; padding: .18rem .5rem; margin-bottom: .45rem; }
      .lead-snippet { border-left: 4px solid #8297aa; padding: .55rem .75rem; margin: .55rem 0 0; background: #f4f7f9; line-height: 1.55; }
      .status-chip { display: inline-block; border: 1px solid #7390a7; background: #eaf2f8; color: #163c5e; padding: .2rem .5rem; font-weight: 700; font-size: .9rem; }
      .status-chip.warning { border-color: #c09236; background: var(--amber-bg); color: var(--amber); }
      .status-chip.ok { border-color: #579177; background: var(--green-bg); color: var(--green); }
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


def record_card(record, check=None):
    evidence_class = record_text(record, "evidence_class")
    reported = evidence_class in ("Reported allegation", "Reported court outcome")
    disclosure = evidence_class in ("Company disclosure", "Official policy")
    card_class = "reported" if reported else "disclosure" if disclosure else "confirmed"
    grade_class = "reported" if reported else "disclosure" if disclosure else ""
    tags = [record_text(record, "geography")] + [text(tag) for tag in record_list(record, "tags")]
    tag_html = "".join(
        '<span class="evidence-tag">{}</span>'.format(html.escape(tag))
        for tag in tags
        if tag
    )
    details = "".join(
        "<li>{}</li>".format(html.escape(text(item))) for item in record_list(record, "details")
    )
    verification_html = ""
    if isinstance(check, dict):
        status = text(check.get("status"))
        severity = STATUS_SEVERITY.get(status, "info")
        label = text(check.get("status_label")) or STATUS_LABELS.get(status, status)
        flag_items = "".join(
            "<li>{}</li>".format(html.escape(text(flag.get("message"))))
            for flag in (check.get("flags") or [])[:3]
            if isinstance(flag, dict)
        )
        verification_html = '<div class="evidence-check {}"><strong>Evidence check:</strong> {}{}</div>'.format(
            html.escape(severity, quote=True),
            html.escape(label),
            '<ul>{}</ul>'.format(flag_items) if flag_items else "",
        )
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
          {verification_html}
          <div class="evidence-sources"><strong>Sources</strong><br>{links}</div>
        </article>
        """.format(
            card_class=card_class,
            grade_class=grade_class,
            date=html.escape(record_text(record, "date_label")),
            category=html.escape(record_text(record, "category")),
            evidence_class=html.escape(evidence_class or "Unclassified"),
            tags=tag_html,
            title=html.escape(record_text(record, "title")),
            entity=html.escape(record_text(record, "entity")),
            summary=html.escape(record_text(record, "summary")),
            details_html='<ul class="evidence-details">{}</ul>'.format(details) if details else "",
            verification_html=verification_html,
            links=source_links(record_list(record, "source_ids"), sources),
        ),
        unsafe_allow_html=True,
    )


with st.sidebar:
    st.markdown("## Research desk")
    st.caption("Allianz Suisse corporate conduct dossier")
    page = st.radio(
        "Go to",
        ["Overview", "Findings", "Evidence checks", "Research plan", "Sources", "Collected sources"],
        label_visibility="collapsed",
    )
    st.markdown("---")
    st.caption("Each finding keeps its evidence type and Allianz entity visible.")
    if integrity_summary["errors"]:
        st.caption("Data checks: {} error(s), {} warning(s)".format(integrity_summary["errors"], integrity_summary["warnings"]))
    elif integrity_summary["warnings"]:
        st.caption("Data checks: {} warning(s), no errors".format(integrity_summary["warnings"]))
    else:
        st.caption("Data checks: no structural issues")
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
        1
        for record in records
        if record_text(record, "evidence_class") in ("Regulatory or criminal outcome", "Court decision")
    )
    swiss_records = sum(1 for record in records if record_text(record, "geography") == "Switzerland")
    reported_records = sum(
        1
        for record in records
        if record_text(record, "evidence_class") in ("Reported allegation", "Reported court outcome")
    )
    metric_columns = st.columns(4)
    metric_columns[0].metric("Dossier records", len(records))
    metric_columns[1].metric("Regulatory or court outcomes", primary_outcomes)
    metric_columns[2].metric("Switzerland records", swiss_records)
    metric_columns[3].metric("Reported allegations or outcomes", reported_records)
    st.caption(
        "Evidence checks: {} of {} records supported by a primary outcome · "
        "{} record(s) with review flags · {} unrecorded lead(s).".format(
            verification["summary"]["records_supported"],
            verification["summary"]["records"],
            verification["summary"]["records_with_warnings"],
            verification["summary"]["leads"],
        )
    )

    st.markdown("<div class='section-rule'></div>", unsafe_allow_html=True)
    left, right = st.columns([1, 1])
    with left:
        st.subheader("Records by evidence type")
        evidence_counts = Counter(record_text(record, "evidence_class") or "Unclassified" for record in records)
        chart_data = pd.DataFrame(
            {"Records": list(evidence_counts.values())},
            index=list(evidence_counts.keys()),
        )
        st.bar_chart(chart_data, horizontal=True, color="#0758a5", height=320)
        st.caption("Counts describe records in this research pass. A case can have more than one record.")
    with right:
        st.subheader("Records in this file")
        st.markdown(
            """
            - U.S. DOJ: Allianz Global Investors U.S. LLC, Structured Alpha, 2023.
            - Luxembourg CSSF: Allianz Global Investors GmbH, Luxembourg Branch, AML/CFT sanction.
            - Australia: Allianz Australia Insurance Limited and AWP Australia, travel-insurance convictions.
            - Switzerland: Allianz24 screening report, Zurich life-application reporting, and Allianz Suisse court records.
            """
        )

    st.markdown("<div class='section-rule'></div>", unsafe_allow_html=True)
    st.subheader("Latest records")
    for record in sorted(records, key=record_sort_key, reverse=True)[:4]:
        record_card(record, verification_by_record.get(record_text(record, "id")))


elif page == "Findings":
    st.subheader("Findings register")
    st.write("Filter the records, then open a source link to inspect the underlying material.")
    filter_columns = st.columns([1.1, 1, 1.5])
    evidence_options = sorted({record_text(record, "evidence_class") or "Unclassified" for record in records})
    geography_options = sorted({record_text(record, "geography") or "Not recorded" for record in records})
    selected_evidence = filter_columns[0].multiselect("Evidence class", evidence_options)
    selected_geography = filter_columns[1].multiselect("Geography", geography_options)
    search_text = filter_columns[2].text_input("Search records and sources", placeholder="Entity, topic, publisher or source detail")

    visible_records = []
    for record in records:
        if selected_evidence and (record_text(record, "evidence_class") or "Unclassified") not in selected_evidence:
            continue
        if selected_geography and (record_text(record, "geography") or "Not recorded") not in selected_geography:
            continue
        linked_source_text = " ".join(
            "{} {} {} {}".format(
                sources[source_id].get("publisher", ""),
                sources[source_id].get("title", ""),
                sources[source_id].get("source_type", ""),
                sources[source_id].get("use", ""),
            )
            for source_id in record_list(record, "source_ids")
            if source_id in sources
        )
        searchable = " ".join(
            [
                record_text(record, "title"),
                record_text(record, "entity"),
                record_text(record, "summary"),
                record_text(record, "category"),
                linked_source_text,
            ]
            + [text(item) for item in record_list(record, "details")]
            + [text(item) for item in record_list(record, "tags")]
        ).lower()
        if search_text and search_text.lower() not in searchable:
            continue
        visible_records.append(record)

    visible_records.sort(key=record_sort_key, reverse=True)
    st.caption("{} of {} records shown".format(len(visible_records), len(records)))
    if visible_records:
        for record in visible_records:
            record_card(record, verification_by_record.get(record_text(record, "id")))
        export_rows = [
            {
                "date": record_text(record, "date_label"),
                "title": record_text(record, "title"),
                "entity": record_text(record, "entity"),
                "geography": record_text(record, "geography"),
                "evidence_class": record_text(record, "evidence_class"),
                "summary": record_text(record, "summary"),
                "sources": " | ".join(
                    safe_url(sources[source_id].get("url"))
                    for source_id in record_list(record, "source_ids")
                    if source_id in sources
                ),
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


elif page == "Evidence checks":
    st.subheader("Evidence checks")
    st.write(
        "Automated cross-checks between each finding and the collected source passages. "
        "A flag is a review lead, not a finding: open the source before changing a record."
    )
    summary = verification.get("summary", {})
    metric_columns = st.columns(4)
    metric_columns[0].metric("Records checked", summary.get("records", 0))
    metric_columns[1].metric("Supported by primary outcome", summary.get("records_supported", 0))
    metric_columns[2].metric("Records with review flags", summary.get("records_with_warnings", 0))
    metric_columns[3].metric("Unrecorded leads", summary.get("leads", 0))
    integrity = verification.get("integrity", {}).get("summary", {})
    if integrity.get("errors"):
        st.error(
            "The dossier has {} structural error(s). Fix them before relying on these checks.".format(
                integrity["errors"]
            )
        )
    elif integrity.get("warnings"):
        st.warning(
            "The dossier has {} integrity warning(s); see the Integrity tab.".format(integrity["warnings"])
        )
    st.caption(
        "Checks generated {} from {} collected snapshot(s). {}".format(
            text(verification.get("generated_at")),
            len(snapshots),
            text(verification.get("notice")),
        )
    )
    st.download_button(
        "Download verification report (JSON)",
        json.dumps(verification, ensure_ascii=False, indent=2).encode("utf-8"),
        file_name="allianz-verification-report.json",
        mime="application/json",
    )

    record_tab, leads_tab, integrity_tab = st.tabs(["Records", "Unrecorded leads", "Integrity"])

    with record_tab:
        filter_columns = st.columns([1, 1.6])
        priority_options = ["high", "medium", "low"]
        selected_priorities = filter_columns[0].multiselect(
            "Review priority", priority_options, default=priority_options
        )
        present_statuses = sorted(
            {text(check.get("status")) for check in verification.get("records", []) if text(check.get("status"))}
        )
        selected_statuses = filter_columns[1].multiselect(
            "Evidence status",
            present_statuses,
            default=present_statuses,
            format_func=lambda value: STATUS_LABELS.get(value, value),
        )
        visible_checks = [
            check
            for check in verification.get("records", [])
            if check.get("priority") in selected_priorities
            and check.get("status") in selected_statuses
        ]
        st.caption("{} of {} records shown".format(len(visible_checks), summary.get("records", 0)))
        if visible_checks:
            status_rows = [
                {
                    "Priority": text(check.get("priority")).upper(),
                    "Status": text(check.get("status_label")),
                    "Evidence class": text(check.get("evidence_class")),
                    "Record": text(check.get("title")),
                    "Flags": "; ".join(
                        text(flag.get("code")) for flag in check.get("flags", []) if isinstance(flag, dict)
                    ),
                }
                for check in visible_checks
            ]
            st.dataframe(pd.DataFrame(status_rows), hide_index=True, use_container_width=True)
            for check in visible_checks:
                status = text(check.get("status"))
                severity = STATUS_SEVERITY.get(status, "info")
                with st.container(border=True):
                    st.markdown("### {}".format(html.escape(text(check.get("title")))))
                    st.markdown(
                        '<span class="status-chip {}">{}</span>'.format(
                            html.escape(severity, quote=True),
                            html.escape(text(check.get("status_label"))),
                        ),
                        unsafe_allow_html=True,
                    )
                    st.caption(
                        "Priority: {} · {} · {} linked source(s), {} collected".format(
                            text(check.get("priority")),
                            text(check.get("evidence_class")),
                            len(check.get("linked_source_ids", [])),
                            len(check.get("collected_source_ids", [])),
                        )
                    )
                    flags = [flag for flag in check.get("flags", []) if isinstance(flag, dict)]
                    if flags:
                        st.markdown(
                            "\n".join(
                                "- {}".format(html.escape(text(flag.get("message")))) for flag in flags
                            )
                        )
                    signal_summary = check.get("signal_summary", {})
                    categories = list(signal_summary.get("categories_asserted", [])) + list(
                        signal_summary.get("categories_alleged", [])
                    )
                    if categories:
                        st.caption("Signals in the checked excerpts: {}".format(", ".join(sorted(set(categories)))))
                    with st.expander("Evidence passages and sources"):
                        for checked_source in check.get("sources", []):
                            if not isinstance(checked_source, dict):
                                continue
                            st.markdown(
                                "**{}** · {}".format(
                                    html.escape(text(checked_source.get("title"))),
                                    html.escape(text(checked_source.get("source_type"))),
                                )
                            )
                            st.caption(
                                "Retrieved: {}".format(text(checked_source.get("retrieved_at")) or "not recorded")
                            )
                            excerpt = text(checked_source.get("excerpt"))
                            if excerpt:
                                st.markdown(
                                    '<div class="discovery-excerpt">{}</div>'.format(html.escape(excerpt)),
                                    unsafe_allow_html=True,
                                )
                            url = safe_url(checked_source.get("url"))
                            if url:
                                st.markdown(
                                    '<a href="{}" target="_blank" rel="noopener noreferrer">Open source</a>'.format(
                                        html.escape(url, quote=True)
                                    ),
                                    unsafe_allow_html=True,
                                )
                        st.markdown(
                            source_links(check.get("linked_source_ids", []), sources), unsafe_allow_html=True
                        )
        else:
            st.info("No records match those filters.")

    with leads_tab:
        leads = [lead for lead in verification.get("leads", []) if isinstance(lead, dict)]
        if not leads:
            st.success(
                "No unrecorded wrongdoing signals were found in the collected excerpts. "
                "This is not proof that no further matters exist; collect more sources and widen the register."
            )
        else:
            lead_filters = st.columns([1.4, 1])
            categories = sorted({text(lead.get("category")) for lead in leads if text(lead.get("category"))})
            selected_categories = lead_filters[0].multiselect("Signal category", categories, default=categories)
            source_scope = lead_filters[1].radio(
                "Source type", ["All", "Primary", "Secondary"], horizontal=True
            )
            visible_leads = []
            for lead in leads:
                if text(lead.get("category")) not in selected_categories:
                    continue
                if source_scope == "Primary" and not lead.get("primary_source"):
                    continue
                if source_scope == "Secondary" and lead.get("primary_source"):
                    continue
                visible_leads.append(lead)
            st.caption("{} of {} lead(s) shown".format(len(visible_leads), len(leads)))
            for lead in visible_leads:
                source_id = text(lead.get("source_id"))
                link = source_link(sources[source_id]) if source_id in sources else html.escape(text(lead.get("title")))
                st.markdown(
                    """
                    <div class="lead-card">
                      <div class="lead-label">{label} · not yet in the dossier</div>
                      <div><strong>{source}</strong> · {source_type}</div>
                      <div class="lead-snippet">{snippet}</div>
                      <div class="discovery-meta">{reason}</div>
                      <div class="evidence-sources">{link}</div>
                    </div>
                    """.format(
                        label=html.escape(text(lead.get("label"))),
                        source=html.escape(text(lead.get("source"))),
                        source_type=html.escape(text(lead.get("source_type"))),
                        snippet=html.escape(text(lead.get("snippet"))),
                        reason=html.escape(text(lead.get("reason"))),
                        link=link,
                    ),
                    unsafe_allow_html=True,
                )

    with integrity_tab:
        issues = [issue for issue in verification.get("integrity", {}).get("issues", []) if isinstance(issue, dict)]
        selected_severities = st.multiselect(
            "Severity", ["error", "warning", "info"], default=["error", "warning"]
        )
        visible_issues = [issue for issue in issues if issue.get("severity") in selected_severities]
        st.caption(
            "{} of {} integrity item(s) shown. Errors block the collector; warnings need a human decision.".format(
                len(visible_issues), len(issues)
            )
        )
        if visible_issues:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Severity": text(issue.get("severity")).upper(),
                            "Check": text(issue.get("code")),
                            "Reference": text(issue.get("ref")) or "—",
                            "Message": text(issue.get("message")),
                        }
                        for issue in visible_issues
                    ]
                ),
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.success("No integrity items at the selected severities.")
        missing_sources = verification.get("missing_source_ids", [])
        if missing_sources:
            st.markdown("**Sources linked to findings but not yet collected**")
            st.write(", ".join(missing_sources))

    st.markdown("<div class='section-rule'></div>", unsafe_allow_html=True)


elif page == "Research plan":
    st.subheader("Research plan")
    st.write("Plan items, current coverage and the work still open.")
    for index, step in enumerate(research_steps, start=1):
        step_title = "{}. {}  |  {}".format(
            index, text(step.get("name")) or "Untitled step", text(step.get("status")) or "Status not recorded"
        )
        with st.expander(step_title, expanded=index <= 2):
            st.markdown("**Coverage**")
            st.write(text(step.get("coverage")))
            st.markdown("**Source result**")
            st.write(text(step.get("result")))
            if step.get("source_ids"):
                st.markdown("**Sources**")
                st.markdown(source_links(step.get("source_ids", []), sources), unsafe_allow_html=True)
            if step.get("next_action"):
                st.markdown("**Next action**")
                st.write(text(step.get("next_action")))

    st.markdown("<div class='section-rule'></div>", unsafe_allow_html=True)


elif page == "Sources":
    st.subheader("Source register")
    st.write("Primary regulator, court and company records are identified separately from journalism and aggregator leads.")
    query = st.text_input("Find a source", placeholder="Publisher, topic or document type")
    visible_sources = []
    for source in sources.values():
        haystack = " ".join(
            text(source.get(key)) for key in ("publisher", "title", "source_type", "use")
        ).lower()
        if not query or query.lower() in haystack:
            visible_sources.append(source)
    st.caption("{} of {} sources shown".format(len(visible_sources), len(sources)))
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


elif page == "Collected sources":
    st.subheader("Collected sources")
    st.write("The collector is an on-demand, one-shot job. It fetches named source URLs already linked to findings and stores a short excerpt, document hash and access status. It does not discover new URLs or run on a schedule.")
    candidates = snapshots
    latest_status, status_errors = load_latest_run_status()
    if discovery_errors:
        for error in discovery_errors:
            st.warning("Could not read {}".format(error))
    for error in status_errors:
        st.warning("Could not read crawl status {}".format(error))

    if latest_status:
        st.caption(
            "Latest run: {} · {} listed sources · {} saved snapshots".format(
                latest_status.get("retrieved_at", "unknown"),
                latest_status.get("source_count", 0),
                latest_status.get("snapshot_count", 0),
            )
        )
        status_labels = {
            "fetched": "Fetched",
            "fetched_alternate": "Fetched official fallback",
            "blocked": "Access denied",
            "access_challenge": "Automated access challenge",
            "no_relevant_text": "No relevant text",
            "no_linked_record": "No linked finding",
            "no_extractable_text": "No readable text",
            "request_error": "Request failed",
            "retry_exhausted": "Retries exhausted",
            "http_error": "HTTP error",
            "rate_limited": "Rate limited",
            "invalid_url": "Invalid source URL",
            "invalid_redirect": "Unsafe redirect",
            "unsupported_content": "Unsupported document type",
            "internal_error": "Collector error",
            "extraction_error": "Text extraction failed",
            "skipped_large": "Over size limit",
            "skipped_index": "Skipped index",
        }
        status_items = [item for item in latest_status.get("sources", []) if isinstance(item, dict)]
        status_rows = []
        for source_status in status_items:
            status_rows.append(
                {
                    "Source": text(source_status.get("source")),
                    "Document": text(source_status.get("title")),
                    "HTTP": text(source_status.get("http_status")),
                    "Status": status_labels.get(source_status.get("status"), text(source_status.get("status"))),
                    "Snapshots": text(source_status.get("snapshot_count", "")),
                    "Note": text(source_status.get("note", "")),
                }
            )
        if status_rows:
            st.markdown("#### Source collection status")
            st.caption("Access-denied sources remain listed. Open the official source links below to read them in a browser.")
            st.dataframe(pd.DataFrame(status_rows), hide_index=True, use_container_width=True)
            with st.expander("Official source links"):
                for source_status in status_items:
                    source = sources.get(source_status.get("source_id"), source_status)
                    st.markdown(source_link(source), unsafe_allow_html=True)

    if not discovery_files or not candidates:
        if discovery_files and discovery_errors:
            st.info("No readable source snapshots are available yet. Check the file errors above.")
        else:
            st.info("No source snapshots yet. Run the collector to fetch the named documents in the source register.")
    else:
        candidate_sources = sorted({text(item.get("source")) for item in candidates if text(item.get("source"))})
        search_col, source_col = st.columns([1.5, 1])
        discovery_query = search_col.text_input("Search sources", placeholder="Title, entity, related record or excerpt")
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
                    text(candidate.get("source_use")),
                    " ".join(text(record) for record in candidate.get("related_records", [])),
                ]
            ).lower()
            if discovery_query and discovery_query.lower() not in searchable:
                continue
            visible_candidates.append(candidate)
        visible_candidates.sort(key=lambda item: text(item.get("retrieved_at")), reverse=True)
        st.caption("{} of {} source snapshots shown".format(len(visible_candidates), len(candidates)))

        for candidate in visible_candidates:
            title = text(candidate.get("title")) or "Untitled source"
            source = text(candidate.get("source")) or "Source not recorded"
            with st.container(border=True):
                st.markdown('<div class="discovery-label">SOURCE SNAPSHOT</div>', unsafe_allow_html=True)
                st.markdown("### {}".format(html.escape(title)))
                meta = []
                if text(candidate.get("entity")):
                    meta.append("Entity: {}".format(text(candidate.get("entity"))))
                if text(candidate.get("source_type")):
                    meta.append("Record type: {}".format(text(candidate.get("source_type"))))
                if text(candidate.get("content_type")):
                    meta.append("Format: {}".format(text(candidate.get("content_type"))))
                if candidate.get("extracted_characters"):
                    meta.append("Extracted characters: {}".format(text(candidate.get("extracted_characters"))))
                if text(candidate.get("retrieved_at")):
                    meta.append("Retrieved: {}".format(text(candidate.get("retrieved_at"))))
                st.markdown('<div class="discovery-meta"><strong>Source:</strong> {}<br>{}</div>'.format(
                    html.escape(source), "<br>".join(html.escape(item) for item in meta)
                ), unsafe_allow_html=True)
                if text(candidate.get("source_use")):
                    st.write(text(candidate.get("source_use")))
                related_records = candidate.get("related_records", [])
                linked_titles = (
                    [text(record) for record in related_records]
                    if isinstance(related_records, list)
                    else []
                )
                if linked_titles:
                    st.caption("Linked records: {}".format(" · ".join(linked_titles)))
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

