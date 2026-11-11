"""Tests for the deterministic SVG figures."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from publishing.charts import (
    BUCKET_COLORS,
    build_figures,
    evidence_bucket,
    evidence_mix_svg,
    jurisdiction_svg,
    timeline_svg,
)
from tests.factories import record


def _records():
    return [
        record(id="older", sort_date="2016-10-26", title="Older court case", geography="Switzerland", evidence_class="Court decision"),
        record(id="newer", sort_date="2025-02-28", title="Newer conviction", geography="Australia"),
        record(
            id="allegation",
            sort_date="2022-12-15",
            title="Reported allegation",
            geography="Switzerland",
            evidence_class="Reported allegation",
        ),
        record(
            id="policy",
            sort_date="2025-12-31",
            title="Company policy",
            geography="Switzerland",
            evidence_class="Official policy",
        ),
    ]


@pytest.mark.parametrize(
    "svg",
    [
        timeline_svg(_records()),
        evidence_mix_svg(_records()),
        jurisdiction_svg(_records()),
    ],
)
def test_svg_is_well_formed_and_self_contained(svg):
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    assert root.get("role") == "img"
    assert root.get("viewBox")
    assert not root.findall(".//*[@href]")
    assert not root.findall(".//*[@{http://www.w3.org/1999/xlink}href]")
    assert "<image" not in svg
    assert "<script" not in svg


def test_build_figures_returns_the_three_article_assets():
    figures = build_figures(_records())
    assert set(figures) == {"records-timeline.svg", "evidence-mix.svg", "jurisdictions.svg"}
    assert all(svg.endswith("</svg>\n") for svg in figures.values())


def test_figures_are_deterministic():
    assert build_figures(_records()) == build_figures(_records())


def test_timeline_orders_records_by_date():
    svg = timeline_svg(_records())
    assert svg.index("Older court case") < svg.index("Reported allegation") < svg.index("Newer conviction")


def test_timeline_escapes_record_text():
    rows = [
        record(
            title='Allianz <script>alert("x")</script> &amp; fraud',
            entity="A & B",
        )
    ]
    svg = timeline_svg(rows)
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg
    assert "&amp;amp;" in svg


def test_evidence_bucket_labels_unknown_classes_rather_than_guessing():
    assert evidence_bucket("Court decision") == "Confirmed regulatory or criminal outcome"
    assert evidence_bucket("Reported allegation") == "Reported allegation or reported outcome"
    assert evidence_bucket("Official policy") == "Company disclosure or official policy"
    assert evidence_bucket("Something else") == "Unclassified"
    assert BUCKET_COLORS["Unclassified"]


def test_unknown_class_does_not_crash_or_pretend_to_be_a_company_statement():
    svg = jurisdiction_svg([record(evidence_class="Something else", geography="Atlantis")])
    assert "Unclassified" in svg
    assert BUCKET_COLORS["Unclassified"] in svg


def test_empty_record_set_produces_valid_figures():
    figures = build_figures([])
    for svg in figures.values():
        ET.fromstring(svg)
    assert "No records" in figures["evidence-mix.svg"]
    assert "No records" in figures["jurisdictions.svg"]


def test_long_text_is_truncated_without_breaking_markup():
    svg = timeline_svg([record(title="x" * 400, entity="y" * 400, date_label="z" * 400)])
    assert len(svg) < 20000
    assert "…" in svg
    ET.fromstring(svg)
