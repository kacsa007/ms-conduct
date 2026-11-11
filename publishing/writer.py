"""Build a source-linked Markdown article from the conduct dossier.

The writer is deliberately conservative:

* it reproduces the record text, entity and evidence class without adding
  adjectives or conclusions;
* every record links to numbered entries in a source register;
* reported allegations are labelled as claims, not findings;
* a company-response section is generated from statements already recorded;
* a "what this file does not establish" section and an internal publication
  check appendix travel with the draft.

It can refuse to publish if the dossier has structural errors (``strict``).
"""

from __future__ import annotations

import html
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from evidence.io import atomic_write_text
from evidence.matching import allianz_mentioned
from evidence.schema import (
    DISCLOSURE_CLASSES,
    EVIDENCE_CLASSES,
    REPORTED_CLASSES,
    summarise,
    url_problem,
    validate_dossier,
)
from evidence.signals import is_primary_source
from evidence.text import sentences
from evidence.verification import STATUS_LABELS, build_report

from .charts import build_figures

GEOGRAPHY_ORDER = (
    "United States",
    "Luxembourg",
    "Australia",
    "Switzerland",
    "Group and Switzerland",
)

CLASS_DESCRIPTIONS = {
    "Regulatory or criminal outcome": "A regulator or court published this outcome.",
    "Court decision": "A court's written decision in the listed case.",
    "Reported allegation": "A claim attributed to a news report; not an established fact.",
    "Reported court outcome": "A court outcome described by a news report.",
    "Company disclosure": "Information published by Allianz about itself.",
    "Official policy": "A policy or disclosure published by Allianz.",
}

CLASS_QUALIFIERS = {
    "Regulatory or criminal outcome": "published outcome",
    "Court decision": "court record",
    "Reported allegation": "reported claim — not a finding",
    "Reported court outcome": "reported court outcome",
    "Company disclosure": "company statement",
    "Official policy": "company policy",
}

_COMPANY_VERB_RE = re.compile(
    r"\b(?:said|says|stated|states|told|denied|denies|apolog\w*|acknowledg\w*|"
    r"confirmed|maintains|maintained|disputes|disputed|rejected|responded|"
    r"erklärt|sagte|bestreitet|dementiert)\b",
    re.IGNORECASE,
)
_SPOKESPERSON_RE = re.compile(
    r"\b(?:spokesperson|spokesman|spokeswoman|representative|press office)\b"
    r"[^.]{0,120}\b(?:said|told|stated|added|confirmed)\b",
    re.IGNORECASE,
)
_MARKDOWN_RE = re.compile(r"([`*_\[\]])")


class ArticleError(RuntimeError):
    """Raised when a dossier is too broken to produce a publishable draft."""


@dataclass(frozen=True)
class ArticleOptions:
    title: str = "Allianz: a source-linked review of selected conduct records"
    standfirst: str = (
        "Selected regulatory, court, company and reported records concerning Allianz "
        "entities and related matters, grouped by jurisdiction and kept distinct by evidence class."
    )
    output_name: str = "allianz-conduct-review.md"
    assets_dir_name: str = "assets"
    include_verification: bool = True
    include_charts: bool = True
    author: str = ""
    contact: str = ""
    generated_at: str = ""
    max_detail_items: int = 0

    def resolved_generated_at(self) -> str:
        return self.generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class ArticleDraft:
    markdown: str
    assets: dict[str, str]
    stats: dict
    warnings: list[str]
    options: ArticleOptions

    def write(self, output_path: Path | str, asset_dir: Path | str | None = None) -> list[Path]:
        output_path = Path(output_path)
        if asset_dir is None:
            asset_dir = output_path.parent / self.options.assets_dir_name
        asset_dir = Path(asset_dir)
        written = [output_path]
        atomic_write_text(output_path, self.markdown)
        for name, content in self.assets.items():
            target = asset_dir / name
            atomic_write_text(target, content)
            written.append(target)
        return written


@dataclass
class _Citation:
    number: int
    source: dict


class CitationIndex:
    def __init__(self, sources: dict[str, dict]):
        self._sources = sources
        self._entries: dict[str, _Citation] = {}
        self._order: list[str] = []
        self.missing: list[str] = []

    def add(self, source_id: object) -> _Citation | None:
        key = str(source_id or "")
        if not key:
            return None
        if key in self._entries:
            return self._entries[key]
        source = self._sources.get(key)
        if source is None:
            if key not in self.missing:
                self.missing.append(key)
            return None
        citation = _Citation(len(self._order) + 1, source)
        self._entries[key] = citation
        self._order.append(key)
        return citation

    def add_many(self, source_ids: Iterable[object]) -> None:
        for source_id in source_ids or []:
            self.add(source_id)

    def number(self, source_id: object) -> int | None:
        citation = self._entries.get(str(source_id or ""))
        return citation.number if citation else None

    def link(self, source_id: object) -> str:
        number = self.number(source_id)
        return "[{}](#source-{})".format(number, number) if number else ""

    def is_cited(self, source_id: object) -> bool:
        return str(source_id or "") in self._entries

    def citation_string(self, source_ids: Iterable[object]) -> str:
        links = [link for link in (self.link(source_id) for source_id in source_ids or []) if link]
        return " ".join(links)

    def entries(self) -> list[_Citation]:
        return [self._entries[key] for key in self._order]

    def __len__(self) -> int:
        return len(self._order)


def _text(value: object) -> str:
    return " ".join(str(value or "").split())


def _md(value: object) -> str:
    """Escape untrusted record text for inline Markdown (no HTML injection)."""

    text = _text(value)
    text = text.replace("\\", "\\\\")
    text = _MARKDOWN_RE.sub(r"\\\1", text)
    return html.escape(text, quote=False)


def _md_cell(value: object) -> str:
    return _md(value).replace("|", "\\|")


def _safe_http_url(value: object) -> str:
    url = str(value or "").strip()
    if url_problem(url)[0]:
        return ""
    return url.replace(" ", "%20").replace(">", "%3E")


def _link(label: object, url: object) -> str:
    safe_url = _safe_http_url(url)
    if not safe_url:
        return _md(label)
    return "[{}](<{}>)".format(_md(label), safe_url)


def _value(record: dict, key: str, default=""):
    value = record.get(key, default) if isinstance(record, dict) else default
    return default if value is None else value


def _list(record: dict, key: str) -> list:
    value = _value(record, key, [])
    return value if isinstance(value, list) else []


def _sort_key(record: dict) -> tuple[str, str]:
    return (str(_value(record, "sort_date")), str(_value(record, "title")))


def _class_of(record: dict) -> str:
    value = str(_value(record, "evidence_class"))
    return value if value in EVIDENCE_CLASSES else "Unclassified"


def _group_records(records: list[dict]) -> list[tuple[str, list[dict]]]:
    grouped: dict[str, list[dict]] = {}
    for record in records:
        geography = str(_value(record, "geography") or "Not recorded")
        grouped.setdefault(geography, []).append(record)
    order = [geography for geography in GEOGRAPHY_ORDER if geography in grouped]
    order.extend(sorted(set(grouped) - set(order)))
    return [(geography, sorted(grouped[geography], key=_sort_key)) for geography in order]


_PLURAL_CLASSES = {
    "Regulatory or criminal outcome": "regulatory or criminal outcomes",
    "Court decision": "court decisions",
    "Reported allegation": "reported allegations",
    "Reported court outcome": "reported court outcomes",
    "Company disclosure": "company disclosures",
    "Official policy": "official policies",
}


def _section_summary(records: list[dict]) -> str:
    counts = Counter(_class_of(record) for record in records)
    parts = []
    for evidence_class in EVIDENCE_CLASSES:
        count = counts.get(evidence_class)
        if not count:
            continue
        label = evidence_class.lower()
        parts.append(
            "{} {}".format(count, label if count == 1 else _PLURAL_CLASSES.get(evidence_class, label + "s"))
        )
    unknown = counts.get("Unclassified", 0)
    if unknown:
        parts.append("{} unclassified".format(unknown))
    return "This section contains {} record{}: {}.".format(
        len(records), "" if len(records) == 1 else "s", ", ".join(parts)
    )


def _figure(filename: str, assets_dir: str, alt: str, caption: str, number: int) -> list[str]:
    directory = (assets_dir or "assets").rstrip("/")
    return [
        "![{}]({}/{})".format(_text(alt), directory, filename),
        "",
        "*Figure {}. {}*".format(number, _text(caption)),
        "",
    ]


def _render_record(record: dict, citations: CitationIndex, options: ArticleOptions) -> list[str]:
    title = _value(record, "title") or "Untitled record"
    evidence_class = _class_of(record)
    qualifier = CLASS_QUALIFIERS.get(evidence_class)
    lines = [
        "#### {}".format(_md(title)),
        "",
        "- **Entity:** {}".format(_md(_value(record, "entity") or "Not recorded")),
        "- **Date:** {}".format(_md(_value(record, "date_label") or _value(record, "sort_date"))),
        "- **Evidence class:** {}{}".format(
            _md(evidence_class),
            " ({})".format(_md(qualifier)) if qualifier else "",
        ),
        "",
        _md(_value(record, "summary")),
        "",
    ]
    if evidence_class in REPORTED_CLASSES:
        if evidence_class == "Reported allegation":
            note = (
                "*This is a reported claim attributed to the named publication. It is not a finding "
                "by a court or regulator, and the underlying documents were not independently "
                "reviewed here.*"
            )
        else:
            note = (
                "*This outcome is described by the named publication. The underlying judgment was "
                "not reviewed here.*"
            )
        lines.extend([note, ""])
    details = [detail for detail in _list(record, "details") if _text(detail)]
    if options.max_detail_items:
        details = details[: options.max_detail_items]
    if details:
        lines.extend("- {}".format(_md(detail)) for detail in details)
        lines.append("")
    source_ids = [source_id for source_id in _list(record, "source_ids") if _text(source_id)]
    citation_text = citations.citation_string(source_ids)
    lines.append("**Sources:** {}".format(citation_text or "No source recorded."))
    lines.append("")
    return lines


def _is_company_response(sentence: str) -> bool:
    """Return True only when Allianz (not a court or reporter) is the speaker."""

    if _SPOKESPERSON_RE.search(sentence):
        return True
    for match in re.finditer(r"\bAllianz(?!['\u2019]s)\b", sentence):
        prefix = sentence[: match.start()]
        if re.search(
            r"\b(?:reports?|reported|said|says|wrote|noted|stated|according to)\b[^.]*\bthat\s*$",
            prefix,
            re.IGNORECASE,
        ):
            continue
        tail = sentence[match.end() : match.end() + 140]
        if _COMPANY_VERB_RE.search(tail):
            return True
    return False


def _response_lines(records: list[dict], citations: CitationIndex) -> list[str]:
    responses: list[tuple[str, str, str]] = []
    statements: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()

    for record in records:
        evidence_class = _class_of(record)
        entity = _value(record, "entity") or "Allianz entity"
        source_text = citations.citation_string(_list(record, "source_ids"))
        summary = _text(_value(record, "summary"))

        if evidence_class in DISCLOSURE_CLASSES and summary:
            key = summary.casefold()
            if key not in seen:
                seen.add(key)
                statements.append((entity, summary, source_text, evidence_class))
            continue

        texts = [summary] + [
            detail for detail in _list(record, "details") if isinstance(detail, str)
        ] + [_value(record, "excerpt")]
        for text in texts:
            for sentence in sentences(text, minimum_length=25):
                if not allianz_mentioned(sentence):
                    continue
                if not _is_company_response(sentence):
                    continue
                key = sentence.casefold()
                if key in seen:
                    continue
                seen.add(key)
                responses.append((entity, sentence, source_text))

    lines = [
        "## Responses, denials and company statements",
        "",
        "This section reproduces positions already recorded in the file. A response is shown only "
        "where the record attributes it to Allianz or one of its representatives; where no response "
        "is recorded, none is asserted. Company disclosures and policies are the company's own "
        "descriptions, not independent verification. The passages are not an endorsement and do not "
        "resolve the underlying claims.",
        "",
    ]
    if responses:
        lines.extend(["### Responses and denials recorded", ""])
        for entity, sentence, source_text in responses:
            lines.append(
                "- **{}** — {}{}".format(
                    _md(entity),
                    _md(sentence),
                    " " + source_text if source_text else "",
                )
            )
        lines.append("")
    if statements:
        lines.extend(["### Company statements", ""])
        for entity, summary, source_text, evidence_class in statements:
            lines.append(
                "- **{}** — {}{} *({})*".format(
                    _md(entity),
                    _md(summary),
                    " " + source_text if source_text else "",
                    _md(evidence_class),
                )
            )
        lines.append("")
    if not responses and not statements:
        lines.extend(
            [
                "The selected records do not contain a company response or company statement. Any "
                "reply received before publication should be added here with its date and source.",
                "",
            ]
        )
    return lines


def _publication_checks(verification: dict | None, citations: CitationIndex) -> list[str]:
    if not verification:
        return []
    lines = [
        "## Appendix: publication checks (internal)",
        "",
        "*Remove this appendix before publication. It records automated review signals, not "
        "additional findings.*",
        "",
    ]
    summary = verification.get("summary", {})
    integrity = verification.get("integrity", {}).get("summary", {})
    lines.append(
        "Dossier integrity: {} error(s), {} warning(s), {} info item(s). Records supported by a "
        "primary outcome: {}/{}.".format(
            integrity.get("errors", 0),
            integrity.get("warnings", 0),
            integrity.get("infos", 0),
            summary.get("records_supported", 0),
            summary.get("records", 0),
        )
    )
    lines.append("")
    checks = [check for check in verification.get("records", []) if isinstance(check, dict)]
    if checks:
        lines.extend(
            [
                "| Record | Evidence class | Review status | Sources collected | Review flags |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for check in checks:
            linked = len(check.get("linked_source_ids") or [])
            collected = len(check.get("collected_source_ids") or [])
            flags = ", ".join(
                str(flag.get("code"))
                for flag in check.get("flags", [])
                if isinstance(flag, dict)
            )
            lines.append(
                "| {} | {} | {} | {}/{} | {} |".format(
                    _md_cell(check.get("title")),
                    _md_cell(check.get("evidence_class")),
                    _md_cell(check.get("status_label") or STATUS_LABELS.get(check.get("status"), check.get("status"))),
                    collected,
                    linked,
                    _md_cell(flags or "—"),
                )
            )
        lines.append("")
    lines.extend(
        [
            "Do not publish a record whose only source is a secondary report as if it were a "
            "confirmed outcome. Review every high-priority row and resolve missing sources before "
            "publication.",
            "",
        ]
    )
    return lines


def _source_register(citations: CitationIndex) -> list[str]:
    lines = [
        "## Source register",
        "",
        "Numbers match the citations in the text. Links open the publisher's page; access status "
        "and snapshot details are recorded in the internal publication checks.",
        "",
        "| # | Publisher | Document | Type | Used for | Link |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for citation in citations.entries():
        source = citation.source
        links = []
        if _safe_http_url(source.get("url")):
            links.append(_link("Open source", source.get("url")))
        alternate = _safe_http_url(source.get("alternate_url"))
        if alternate:
            links.append(_link("Alternate", alternate))
        lines.append(
            "| {} | {} | {} | {} | {} | {} |".format(
                '<a id="source-{0}"></a>{0}'.format(citation.number),
                _md_cell(source.get("publisher")),
                _md_cell(source.get("title")),
                _md_cell(source.get("source_type")),
                _md_cell(source.get("use")),
                " · ".join(links) if links else "No URL recorded",
            )
        )
    lines.append("")
    if citations.missing:
        lines.extend(
            [
                "> **Unresolved source reference(s):** {}. These citations were omitted and must "
                "be fixed in the dossier before publication.".format(
                    ", ".join("`{}`".format(_md(value)) for value in citations.missing)
                ),
                "",
            ]
        )
    return lines


def _class_table(records: list[dict]) -> list[str]:
    counts = Counter(_class_of(record) for record in records)
    lines = [
        "## Evidence classes in this file",
        "",
        "| Evidence class | What it means | Records |",
        "| --- | --- | --- |",
    ]
    for evidence_class in EVIDENCE_CLASSES:
        lines.append(
            "| {} | {} | {} |".format(
                _md_cell(evidence_class),
                _md_cell(CLASS_DESCRIPTIONS[evidence_class]),
                counts.get(evidence_class, 0),
            )
        )
    if counts.get("Unclassified"):
        lines.append(
            "| Unclassified | The evidence class is not recognised; do not publish this record. | {} |".format(
                counts["Unclassified"]
            )
        )
    lines.append("")
    lines.extend(
        [
            "A record count is not a count of cases. One case can generate several records, and a "
            "court decision can resolve an individual benefit dispute without a finding of wrongdoing.",
            "",
        ]
    )
    return lines


def _research_resources(dossier: dict, citations: CitationIndex) -> list[str]:
    unused = [
        source
        for source in dossier.get("sources") or []
        if isinstance(source, dict) and not citations.is_cited(source.get("id"))
    ]
    if not unused:
        return []
    lines = [
        "## Research resources",
        "",
        "These indexes and databases were consulted to locate material. They are not the source of a "
        "specific statement above and are listed for transparency.",
        "",
    ]
    for source in unused:
        label = "{} — {}".format(
            _md(source.get("publisher") or "Publisher not recorded"),
            _md(source.get("title") or "Untitled resource"),
        )
        link = _link("Open", source.get("url"))
        lines.append("- {}{}".format(label, " · " + link if link else ""))
    lines.append("")
    return lines


_COUNT_PLURALS = {
    "company disclosure": "company disclosures",
    "official policy": "official policies",
}


def _count_label(label: str, count: int) -> str:
    if count == 1:
        return label
    return _COUNT_PLURALS.get(label, label + "s")


def _at_a_glance(records: list[dict], citations: CitationIndex, jurisdictions: int) -> list[str]:
    counts = Counter(_class_of(record) for record in records)
    primary = sum(
        1
        for citation in citations.entries()
        if is_primary_source(citation.source.get("source_type"))
    )
    return [
        "## At a glance",
        "",
        "- **{}** records across **{}** sections.".format(len(records), jurisdictions),
        "- **{}** regulatory or criminal outcomes and **{}** court decisions.".format(
            counts.get("Regulatory or criminal outcome", 0),
            counts.get("Court decision", 0),
        ),
        "- **{}** reported allegations and **{}** reported court outcomes.".format(
            counts.get("Reported allegation", 0),
            counts.get("Reported court outcome", 0),
        ),
        "- **{}** {} and **{}** {}.".format(
            counts.get("Company disclosure", 0),
            _count_label("company disclosure", counts.get("Company disclosure", 0)),
            counts.get("Official policy", 0),
            _count_label("official policy", counts.get("Official policy", 0)),
        ),
        "- **{}** cited sources, of which **{}** are marked primary.".format(
            len(citations), primary
        ),
        "",
    ]


def _source_coverage(verification: dict | None, include_appendix: bool = True) -> list[str]:
    if not verification:
        return []
    summary = verification.get("summary", {})
    linked = summary.get("sources_linked", 0)
    collected = summary.get("sources_collected", 0)
    missing = summary.get("sources_missing", 0)
    records = summary.get("records", 0)
    supported = summary.get("records_supported", 0)
    flagged = summary.get("records_with_warnings", 0)
    return [
        "## Source coverage",
        "",
        "Coverage is reported rather than assumed. At the time of writing, the collector had "
        "retrieved a usable snapshot for **{} of {}** cited sources; **{}** did not return one. "
        "**{} of {}** records are supported by a primary outcome in the collected text, and "
        "**{}** carry a review flag.".format(collected, linked, missing, supported, records, flagged),
        "",
        "A missing snapshot is a gap in this review, not evidence that the source does not exist. "
        "Every source remains linked in the register."
        + (" The internal appendix lists the affected records." if include_appendix else ""),
        "",
    ]


_OPEN_STATUS_MARKERS = ("open", "not yet", "in progress", "pending")


def _open_questions(dossier: dict) -> list[str]:
    steps = [
        step
        for step in dossier.get("research_steps") or []
        if isinstance(step, dict)
        and _text(step.get("name"))
        and any(
            marker in _text(step.get("status")).casefold()
            for marker in _OPEN_STATUS_MARKERS
        )
    ]
    if not steps:
        return []
    lines = [
        "## Scope and open questions",
        "",
        "This is a selected review, not a complete inventory of every Allianz entity or matter. The "
        "research plan records the following open lines of inquiry; none is treated as a finding "
        "above.",
        "",
    ]
    for step in steps:
        lines.append(
            "- **{}** — {}.".format(_md(step.get("name")), _md(step.get("status")))
        )
    lines.append("")
    return lines


def _not_established() -> list[str]:
    return [
        "## What this file does not establish",
        "",
        "- A **reported allegation** is a claim attributed to its publisher, not a proven fact.",
        "- An enforcement outcome or court decision concerns the **named entity and matter**, not "
        "every company in the Allianz group.",
        "- A court decision can concern an individual benefit dispute without any finding of misconduct.",
        "- Amounts reported in different matters are **not added together** here; fines, restitution, "
        "forfeiture and settlements are different legal categories.",
        "- Missing, blocked or uncollected sources can leave gaps. The internal publication checks "
        "list them; an absence of records is not evidence that other conduct did not occur.",
        "- Automated wrongdoing signals and evidence checks are **review aids**, not evidence.",
        "",
    ]


def _readable_datetime(value: str) -> str:
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return value or "date not recorded"
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    return "{} at {:02d}:{:02d} UTC".format(
        "{} {}".format(moment.day, moment.strftime("%B %Y")),
        moment.hour,
        moment.minute,
    )


def _method(records: list[dict], options: ArticleOptions, reviewed_on: str) -> list[str]:
    return [
        "## Method and limitations",
        "",
        "This draft is generated from the research file's {} records and the named sources attached "
        "to them. It adds no new reporting and no unnamed sourcing.".format(len(records)),
        "",
        "- Each record keeps its original evidence class, entity and source links.",
        "- Primary regulator, court and company documents are kept distinct from secondary reporting.",
        "- Dates and amounts are reproduced as recorded; they are not normalised across jurisdictions.",
        "- The source collector is an on-demand job that fetches named sources. It does not discover "
        "new URLs, and a blocked source remains visible with its access status.",
        "- The figures are generated from record metadata. This article uses no stock photography, "
        "illustrative reconstructions or AI-generated imagery.",
        "- Research file last reviewed: {}.".format(reviewed_on or "not recorded"),
        "",
        (
            "Prepared by {}. Generated on {} with the ms-conduct article writer.".format(
                _text(options.author), _readable_datetime(options.resolved_generated_at())
            )
            if options.author
            else "Generated on {} with the ms-conduct article writer.".format(
                _readable_datetime(options.resolved_generated_at())
            )
        ),
    ]


def build_article(
    dossier: dict,
    snapshots: list[dict] | None = None,
    options: ArticleOptions | None = None,
    strict: bool = False,
) -> ArticleDraft:
    options = options or ArticleOptions()
    if not isinstance(dossier, dict):
        raise ArticleError("Dossier root must be a JSON object.")

    issues = validate_dossier(dossier, snapshots)
    errors = [issue for issue in issues if issue.severity == "error"]
    warning_issues = [issue for issue in issues if issue.severity == "warning"]
    if errors and strict:
        raise ArticleError(
            "Dossier has structural errors: "
            + "; ".join("{} ({})".format(issue.code, issue.ref or "dossier") for issue in errors)
        )

    records = [record for record in dossier.get("records") or [] if isinstance(record, dict)]
    records.sort(key=_sort_key)
    sources = {
        str(source.get("id")): source
        for source in dossier.get("sources") or []
        if isinstance(source, dict) and str(source.get("id") or "")
    }
    groups = _group_records(records)

    citations = CitationIndex(sources)
    for _, group_records in groups:
        for record in group_records:
            citations.add_many(_list(record, "source_ids"))

    verification = build_report(dossier, snapshots or [])
    assets = build_figures(records) if options.include_charts else {}

    lines: list[str] = [
        "# {}".format(_md(options.title)),
        "",
        "*{}*".format(_md(options.standfirst)),
        "",
        "Generated {} · Research file reviewed {} · {} records · {} cited sources".format(
            _readable_datetime(options.resolved_generated_at()),
            _md(dossier.get("reviewed_on") or "date not recorded"),
            len(records),
            len(citations),
        ),
        "",
        "> **How to read this file.** Every record keeps its evidence class. A published regulatory "
        "or court outcome is reported as the authority published it; a reported allegation is "
        "attributed to the outlet that published it and is not presented as a proven fact. Company "
        "statements are labelled as company statements. The internal appendix is not evidence.",
        "",
    ]
    lines.extend(_at_a_glance(records, citations, len(groups)))

    if assets:
        lines.extend(["## The record at a glance", ""])
        figure_number = 1
        lines.extend(
            _figure(
                "records-timeline.svg",
                options.assets_dir_name,
                "Timeline of selected Allianz records by date, jurisdiction and evidence class",
                "Selected records by date. Blue marks a confirmed regulatory or criminal outcome, "
                "amber a reported allegation or reported outcome, and green a company statement or policy.",
                figure_number,
            )
        )
        figure_number += 1
        lines.extend(
            _figure(
                "evidence-mix.svg",
                options.assets_dir_name,
                "Bar chart of selected records by evidence class",
                "Records by evidence class. Counts describe records in this file, not separate cases.",
                figure_number,
            )
        )
        figure_number += 1
        lines.extend(
            _figure(
                "jurisdictions.svg",
                options.assets_dir_name,
                "Stacked bar chart of records by jurisdiction and evidence type",
                "Records by jurisdiction, split by confirmed outcomes, reported allegations and company "
                "statements. The chart is not a ranking of misconduct.",
                figure_number,
            )
        )

    lines.extend(_class_table(records))
    lines.extend(["## The records", ""])
    lines.append(
        "The records are grouped by the jurisdiction named in the research file and ordered by date. "
        "Each heading is a record, not a separate allegation or case. Source numbers refer to the "
        "register at the end of this file."
    )
    lines.append("")
    for geography, group_records in groups:
        lines.extend(["### {}".format(_md(geography)), "", _section_summary(group_records), ""])
        for record in group_records:
            lines.extend(_render_record(record, citations, options))

    lines.extend(_response_lines(records, citations))
    lines.extend(_source_coverage(verification, options.include_verification))
    lines.extend(_open_questions(dossier))
    lines.extend(_not_established())
    lines.extend(_method(records, options, str(dossier.get("reviewed_on") or "")))
    if options.contact:
        lines.extend(["Corrections and replies: {}.".format(_md(options.contact)), ""])
    if options.include_verification:
        lines.append("")
        lines.extend(_publication_checks(verification, citations))
    lines.extend(_source_register(citations))
    lines.extend(_research_resources(dossier, citations))

    markdown = "\n".join(lines).rstrip() + "\n"
    warnings = [
        "{}: {} ({})".format(issue.severity, issue.message, issue.ref or "dossier")
        for issue in errors + warning_issues
    ]
    if citations.missing:
        warnings.append(
            "Missing source reference(s): {}".format(", ".join(citations.missing))
        )
    stats = {
        "records": len(records),
        "sources": len(citations),
        "jurisdictions": len(groups),
        "assets": sorted(assets),
        "words": len(markdown.split()),
        "classes": dict(Counter(_class_of(record) for record in records)),
        "generated_at": options.resolved_generated_at(),
        "integrity": summarise(issues),
    }
    return ArticleDraft(
        markdown=markdown,
        assets=assets,
        stats=stats,
        warnings=warnings,
        options=options,
    )
