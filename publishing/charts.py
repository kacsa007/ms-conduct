"""Deterministic SVG figures for the generated conduct review.

The figures are drawn from dossier metadata only. There are no stock photos,
illustrations or AI-generated images: every number in a chart can be traced
back to the record set and the source register printed in the article.
"""

from __future__ import annotations

import textwrap
from collections import Counter
from typing import Iterable
from xml.sax.saxutils import escape

PAPER = "#f3f6f8"
INK = "#142536"
NAVY = "#102d46"
BLUE = "#0758a5"
MUTED = "#42566a"
RULE = "#b9c7d4"
WHITE = "#ffffff"

CONFIRMED_CLASSES = ("Regulatory or criminal outcome", "Court decision")
REPORTED_CLASSES = ("Reported allegation", "Reported court outcome")
DISCLOSURE_CLASSES = ("Company disclosure", "Official policy")

CLASS_ORDER = (
    "Regulatory or criminal outcome",
    "Court decision",
    "Reported allegation",
    "Reported court outcome",
    "Company disclosure",
    "Official policy",
)

CLASS_COLORS = {
    "Regulatory or criminal outcome": ("#0758a5", "#e5f0fb"),
    "Court decision": ("#1c5f8a", "#eaf2f8"),
    "Reported allegation": ("#a16600", "#fff2ce"),
    "Reported court outcome": ("#8a5a00", "#fff7e3"),
    "Company disclosure": ("#1c7052", "#e1f2e9"),
    "Official policy": ("#316b58", "#eef8f2"),
}

BUCKET_COLORS = {
    "Confirmed regulatory or criminal outcome": BLUE,
    "Reported allegation or reported outcome": "#a16600",
    "Company disclosure or official policy": "#1c7052",
    "Unclassified": "#6b7a88",
}

BUCKET_LABELS = {
    "Confirmed regulatory or criminal outcome": "Confirmed outcome",
    "Reported allegation or reported outcome": "Reported allegation/outcome",
    "Company disclosure or official policy": "Company statement/policy",
    "Unclassified": "Unclassified",
}


def evidence_bucket(evidence_class: object) -> str:
    value = str(evidence_class or "")
    if value in CONFIRMED_CLASSES:
        return "Confirmed regulatory or criminal outcome"
    if value in REPORTED_CLASSES:
        return "Reported allegation or reported outcome"
    if value in DISCLOSURE_CLASSES:
        return "Company disclosure or official policy"
    return "Unclassified"


def _escape(value: object) -> str:
    return escape(str(value or ""), {'"': "&quot;"})


def _plain(value: object) -> str:
    return " ".join(str(value or "").split())


def _truncate(value: object, length: int) -> str:
    text = _plain(value)
    if len(text) <= length:
        return text
    return text[: max(0, length - 1)].rstrip(" ,;:") + "…"


def _wrap(value: object, width: int, max_lines: int = 2) -> list[str]:
    text = _plain(value)
    if not text:
        return [""]
    lines = textwrap.wrap(text, width=width, break_long_words=False, break_on_hyphens=False)
    if len(lines) <= max_lines:
        return lines
    lines = lines[:max_lines]
    lines[-1] = _truncate(lines[-1] + " …", width)
    return lines


def _svg(width: int, height: int, title: str, desc: str, body: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        'viewBox="0 0 {w} {h}" role="img" aria-labelledby="title desc">\n'
        '  <title id="title">{title}</title>\n'
        '  <desc id="desc">{desc}</desc>\n'
        '  <rect width="{w}" height="{h}" fill="{paper}"/>\n'
        "{body}\n"
        "</svg>\n"
    ).format(
        w=width,
        h=height,
        title=_escape(title),
        desc=_escape(desc),
        paper=PAPER,
        body=body,
    )


def _text(
    x: float,
    y: float,
    value: object,
    *,
    size: int = 16,
    weight: int = 400,
    fill: str = INK,
    anchor: str = "start",
    letter_spacing: float = 0,
) -> str:
    spacing = ' letter-spacing="{}"'.format(letter_spacing) if letter_spacing else ""
    return (
        '<text x="{x}" y="{y}" font-family="Arial, Helvetica, sans-serif" '
        'font-size="{size}" font-weight="{weight}" fill="{fill}" '
        'text-anchor="{anchor}"{spacing}>{value}</text>'
    ).format(
        x=x,
        y=y,
        size=size,
        weight=weight,
        fill=fill,
        anchor=anchor,
        spacing=spacing,
        value=_escape(value),
    )


_GEOGRAPHY_SHORT = {
    "Group and Switzerland": "Group / CH",
    "United States": "United States",
    "Switzerland": "Switzerland",
    "Luxembourg": "Luxembourg",
    "Australia": "Australia",
}


def _short_geography(value: object) -> str:
    text = _plain(value)
    return _GEOGRAPHY_SHORT.get(text, text)


def _sorted_records(records: Iterable[dict]) -> list[dict]:
    rows = [record for record in records if isinstance(record, dict)]
    return sorted(
        rows,
        key=lambda record: (
            str(record.get("sort_date") or ""),
            str(record.get("title") or ""),
        ),
    )


def _legend(x: int, y: int, items: list[tuple[str, str]]) -> str:
    parts = []
    cursor = x
    for label, color in items:
        parts.append(
            '<rect x="{}" y="{}" width="14" height="14" rx="3" fill="{}"/>'.format(cursor, y - 11, color)
        )
        parts.append(_text(cursor + 22, y, label, size=13, fill=MUTED))
        cursor += 46 + int(len(label) * 7.4)
    return "".join(parts)


def timeline_svg(records: Iterable[dict]) -> str:
    rows = _sorted_records(records)
    top = 152
    row_height = 82
    width = 1200
    height = top + max(1, len(rows)) * row_height + 86
    body = [
        _text(64, 60, "SELECTED PUBLIC RECORD", size=17, weight=700, fill=BLUE, letter_spacing=2),
        _text(64, 106, "A timeline of the records in this file", size=34, weight=700, fill=INK),
        _text(
            64,
            134,
            "Dates are record dates, not the start of every underlying matter. Colour shows the evidence class.",
            size=14,
            fill=MUTED,
        ),
        '<line x1="150" y1="{}" x2="150" y2="{}" stroke="{}" stroke-width="3"/>'.format(
            top + 20, top + max(1, len(rows)) * row_height - 18, RULE
        ),
    ]
    for index, record in enumerate(rows):
        row_y = top + index * row_height
        year = str(record.get("sort_date") or "")[:4] or "—"
        evidence_class = str(record.get("evidence_class") or "Unclassified")
        bucket = evidence_bucket(evidence_class)
        color = BUCKET_COLORS[bucket]
        body.append(_text(64, row_y + 26, year, size=21, weight=700, fill=INK))
        body.append(_text(64, row_y + 46, _truncate(_short_geography(record.get("geography")), 18), size=12, fill=MUTED))
        body.append('<circle cx="150" cy="{}" r="8" fill="{}"/>'.format(row_y + 22, color))
        body.append(
            '<rect x="190" y="{y}" width="946" height="66" rx="8" fill="{white}" stroke="#ccd6df"/>'.format(
                y=row_y, white=WHITE
            )
        )
        body.append(
            '<rect x="190" y="{y}" width="6" height="66" rx="3" fill="{color}"/>'.format(
                y=row_y, color=color
            )
        )
        body.append(_text(214, row_y + 25, _truncate(record.get("title"), 100), size=17, weight=700))
        body.append(_text(214, row_y + 45, _truncate(record.get("entity"), 112), size=13, fill="#174a75"))
        body.append(
            _text(
                214,
                row_y + 61,
                "{} · {}".format(_truncate(record.get("date_label"), 108), evidence_class),
                size=12,
                fill=MUTED,
            )
        )
    body.append(
        _legend(
            64,
            height - 38,
            [
                ("Confirmed outcome", BUCKET_COLORS["Confirmed regulatory or criminal outcome"]),
                ("Reported allegation/outcome", BUCKET_COLORS["Reported allegation or reported outcome"]),
                ("Company statement/policy", BUCKET_COLORS["Company disclosure or official policy"]),
            ],
        )
    )
    body.append(
        _text(
            64,
            height - 14,
            "A court decision can resolve an individual benefit dispute without a finding of wrongdoing.",
            size=12,
            fill=MUTED,
        )
    )
    return _svg(
        width,
        height,
        "Timeline of selected Allianz records",
        "A timeline of {} records by date, jurisdiction and evidence class. "
        "Confirmed outcomes, reported allegations and company statements are distinguished.".format(len(rows)),
        "\n".join(body),
    )


def evidence_mix_svg(records: Iterable[dict]) -> str:
    rows = [record for record in records if isinstance(record, dict)]
    counts = Counter(str(record.get("evidence_class") or "Unclassified") for record in rows)
    classes = [evidence_class for evidence_class in CLASS_ORDER if counts.get(evidence_class)]
    extra = sorted(set(counts) - set(CLASS_ORDER))
    classes.extend(extra)
    if not classes:
        classes = ["No records"]
    top = 142
    row_height = 56
    width = 1100
    height = top + len(classes) * row_height + 56
    maximum = max(counts.values()) if counts else 1
    body = [
        _text(64, 58, "EVIDENCE MIX", size=17, weight=700, fill=BLUE, letter_spacing=2),
        _text(64, 104, "Records by evidence class", size=34, weight=700, fill=INK),
        _text(
            64,
            130,
            "A record count, not a count of separate cases or people. Classes are defined in the article.",
            size=14,
            fill=MUTED,
        ),
    ]
    for index, evidence_class in enumerate(classes):
        row_y = top + index * row_height
        count = counts.get(evidence_class, 0)
        bar_width = int(640 * (count / maximum)) if maximum else 0
        stroke, fill = CLASS_COLORS.get(evidence_class, (MUTED, "#eef2f5"))
        body.append(
            _text(64, row_y + 22, _truncate(evidence_class, 42), size=15, weight=700, fill=INK)
        )
        body.append('<rect x="420" y="{}" width="640" height="22" rx="5" fill="#e5ebf0"/>'.format(row_y + 5))
        if bar_width:
            body.append(
                '<rect x="420" y="{}" width="{}" height="22" rx="5" fill="{}"/>'.format(
                    row_y + 5, bar_width, stroke
                )
            )
        body.append(_text(420 + max(bar_width, 0) + 12, row_y + 22, str(count), size=16, weight=700, fill=INK))
        body.append(_text(64, row_y + 40, "Source-linked record", size=11, fill=MUTED))
    return _svg(
        width,
        height,
        "Records by evidence class",
        "Bar chart of {} records by evidence class: {}.".format(
            len(rows),
            ", ".join("{} {}".format(counts[key], key) for key in classes if counts.get(key)),
        ),
        "\n".join(body),
    )


def jurisdiction_svg(records: Iterable[dict]) -> str:
    rows = [record for record in records if isinstance(record, dict)]
    grouped: dict[str, Counter] = {}
    for record in rows:
        geography = str(record.get("geography") or "Not recorded")
        grouped.setdefault(geography, Counter())[evidence_bucket(record.get("evidence_class"))] += 1
    order = sorted(grouped, key=lambda key: (-sum(grouped[key].values()), key))
    if not order:
        order = ["No records"]
        grouped = {"No records": Counter()}
    top = 152
    row_height = 60
    width = 1100
    height = top + len(order) * row_height + 84
    maximum = max((sum(grouped[geography].values()) for geography in order), default=1)
    bar_width = 610
    body = [
        _text(64, 58, "JURISDICTIONS", size=17, weight=700, fill=BLUE, letter_spacing=2),
        _text(64, 104, "Records by jurisdiction and evidence type", size=34, weight=700, fill=INK),
        _text(
            64,
            130,
            "Different legal systems and entities; the counts are not a ranking of misconduct.",
            size=14,
            fill=MUTED,
        ),
    ]
    for index, geography in enumerate(order):
        row_y = top + index * row_height
        counter = grouped[geography]
        total = sum(counter.values())
        body.append(_text(64, row_y + 24, _truncate(geography, 36), size=16, weight=700))
        body.append(_text(64, row_y + 42, "{} record(s)".format(total), size=12, fill=MUTED))
        cursor = 360
        for bucket in (
            "Confirmed regulatory or criminal outcome",
            "Reported allegation or reported outcome",
            "Company disclosure or official policy",
            "Unclassified",
        ):
            count = counter.get(bucket, 0)
            if not count:
                continue
            segment = int(bar_width * (count / maximum)) if maximum else 0
            segment = max(segment, 4)
            body.append(
                '<rect x="{}" y="{}" width="{}" height="24" fill="{}"/>'.format(
                    cursor, row_y + 8, segment, BUCKET_COLORS[bucket]
                )
            )
            if segment >= 24:
                body.append(
                    _text(
                        cursor + segment / 2,
                        row_y + 25,
                        str(count),
                        size=13,
                        weight=700,
                        fill=WHITE,
                        anchor="middle",
                    )
                )
            cursor += segment
        body.append(_text(360 + bar_width + 14, row_y + 25, str(total), size=14, weight=700, fill=INK))
    legend_items = [
        ("Confirmed outcome", BUCKET_COLORS["Confirmed regulatory or criminal outcome"]),
        ("Reported allegation/outcome", BUCKET_COLORS["Reported allegation or reported outcome"]),
        ("Company statement/policy", BUCKET_COLORS["Company disclosure or official policy"]),
    ]
    if any(grouped[geography].get("Unclassified") for geography in order):
        legend_items.append(("Unclassified", BUCKET_COLORS["Unclassified"]))
    body.append(_legend(64, height - 34, legend_items))
    return _svg(
        width,
        height,
        "Records by jurisdiction and evidence type",
        "Stacked bar chart of {} records across {} jurisdictions, split by confirmed outcomes, "
        "reported allegations and company statements.".format(len(rows), len(order)),
        "\n".join(body),
    )


def build_figures(records: Iterable[dict]) -> dict[str, str]:
    """Return ``{filename: svg}`` for the article package."""

    rows = list(records)
    return {
        "records-timeline.svg": timeline_svg(rows),
        "evidence-mix.svg": evidence_mix_svg(rows),
        "jurisdictions.svg": jurisdiction_svg(rows),
    }
