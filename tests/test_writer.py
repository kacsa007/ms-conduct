"""Tests for the Markdown article writer and its publication safeguards."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from evidence.io import read_json
from publishing.writer import ArticleError, ArticleOptions, build_article
from tests.factories import dossier, record, source

FIXED = ArticleOptions(generated_at="2026-11-11T12:00:00+00:00")


def _source(source_id="s1", **overrides):
    values = {"id": source_id, "use": "Test use."}
    values.update(overrides)
    return source(**values)


def _record(source_ids=("s1",), **overrides):
    values = {"source_ids": list(source_ids)}
    values.update(overrides)
    return record(**values)


def _build(records=None, sources=None, options=FIXED, strict=False):
    records = records if records is not None else [_record()]
    sources = sources if sources is not None else [_source()]
    return build_article(dossier(records=records, sources=sources), [], options=options, strict=strict)


def test_minimal_draft_has_required_sections():
    draft = _build()
    markdown = draft.markdown
    assert markdown.startswith("# Allianz: a source-linked review")
    assert "## At a glance" in markdown
    assert "## The record at a glance" in markdown
    assert "## The records" in markdown
    assert "## Responses, denials and company statements" in markdown
    assert "## What this file does not establish" in markdown
    assert "## Method and limitations" in markdown
    assert "## Appendix: publication checks (internal)" in markdown
    assert "## Source register" in markdown
    assert draft.stats["records"] == 1
    assert draft.stats["sources"] == 1
    assert not draft.warnings


def test_every_record_links_to_its_numbered_source_and_anchor():
    markdown = _build().markdown
    assert "**Sources:** [1](#source-1)" in markdown
    assert '<a id="source-1"></a>1' in markdown


def test_article_is_deterministic_with_a_fixed_timestamp():
    first = _build().markdown
    second = _build().markdown
    assert first == second
    assert first.count("## At a glance") == 1
    assert first.count("## Source register") == 1


def test_markdown_escapes_untrusted_record_and_source_text():
    hostile = (
        "Allianz *star* [bracket] _under_ `tick` | pipe "
        "<script>alert(1)</script> \\ end"
    )
    draft = _build(
        records=[_record(title=hostile, summary=hostile, details=[hostile])],
        sources=[_source(title=hostile, publisher=hostile)],
    )
    markdown = draft.markdown
    assert "<script>" not in markdown
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in markdown
    assert "\\*star\\*" in markdown
    assert "\\[bracket\\]" in markdown
    assert "\\_under\\_" in markdown
    assert "\\`tick\\`" in markdown
    assert "|" in markdown  # table separators remain, untrusted pipes are escaped
    assert "\\| pipe" in markdown


def test_reported_allegation_keeps_its_claim_qualifier():
    text = "A newspaper reported that Allianz was accused of misleading customers."
    draft = _build(
        records=[
            _record(
                title="Allianz accused of misleading customers",
                summary=text,
                evidence_class="Reported allegation",
            )
        ]
    )
    markdown = draft.markdown
    assert "**Evidence class:** Reported allegation (reported claim — not a finding)" in markdown
    assert text in markdown
    assert "This is a reported claim attributed to the named publication." in markdown
    assert "Allianz committed" not in markdown


def test_company_response_is_quoted_and_court_statement_is_not():
    draft = _build(
        records=[
            _record(
                summary=(
                    "The court allowed Allianz's appeal and confirmed its denial. "
                    "An Allianz spokesperson said the company would review the decision."
                )
            )
        ]
    )
    markdown = draft.markdown
    responses = markdown.split("### Responses and denials recorded", 1)[1].split(
        "### Company statements", 1
    )[0]
    assert "spokesperson said" in responses
    assert "The court allowed" not in responses


def test_secondary_report_attribution_is_not_mistaken_for_a_company_response():
    draft = _build(
        records=[
            _record(
                summary=(
                    "The source reports that Allianz maintained a list of former customers. "
                    "Allianz denied operating such a list."
                )
            )
        ]
    )
    responses = draft.markdown.split("### Responses and denials recorded", 1)[1]
    assert "The source reports that" not in responses
    assert "Allianz denied operating such a list" in responses


def test_company_disclosure_appears_as_a_labelled_statement():
    draft = _build(
        records=[
            _record(
                title="Allianz annual report describes compliance controls",
                evidence_class="Company disclosure",
                summary="Allianz's annual report describes its compliance controls.",
            )
        ]
    )
    markdown = draft.markdown
    assert "### Company statements" in markdown
    assert "*(Company disclosure)*" in markdown


def test_missing_source_reference_is_reported_and_omitted_from_the_register():
    draft = _build(
        records=[_record(source_ids=["s1", "missing-source"])],
        sources=[_source()],
    )
    assert "Missing source reference(s): missing-source" in draft.warnings
    assert "**Unresolved source reference(s):** `missing-source`" in draft.markdown
    assert "#source-2" not in draft.markdown


def test_strict_mode_refuses_a_structurally_invalid_dossier():
    with pytest.raises(ArticleError, match="record_source_unknown"):
        _build(records=[_record(source_ids=["missing-source"])], strict=True)


def test_uncited_sources_are_listed_as_research_resources():
    draft = _build(
        records=[_record(source_ids=["s1"])],
        sources=[
            _source("s1"),
            _source(
                "database",
                publisher="FINMA",
                title="Enforcement reporting",
                url="https://www.finma.ch/en/documentation/enforcement-reporting/",
            ),
        ],
    )
    markdown = draft.markdown
    resources = markdown.split("## Research resources", 1)[1]
    assert "FINMA — Enforcement reporting" in resources
    assert "Used for" not in resources.split("FINMA", 1)[0]


def test_unsafe_source_urls_are_not_rendered_as_links():
    draft = _build(
        sources=[
            _source("s1", url="javascript:alert(1)"),
            _source("s2", url="http://127.0.0.1/private"),
        ],
        records=[_record(source_ids=["s1", "s2"])],
    )
    markdown = draft.markdown
    assert "javascript:" not in markdown
    assert "127.0.0.1" not in markdown
    assert markdown.count("No URL recorded") == 2


def test_figures_are_local_and_write_with_the_markdown(tmp_path):
    draft = _build()
    output = tmp_path / "article.md"
    written = draft.write(output, tmp_path / "assets")
    assert written[0] == output
    assert output.exists()
    assert set(path.name for path in written[1:]) == {
        "records-timeline.svg",
        "evidence-mix.svg",
        "jurisdictions.svg",
    }
    markdown = output.read_text(encoding="utf-8")
    image_links = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", markdown)
    assert image_links == [
        "assets/records-timeline.svg",
        "assets/evidence-mix.svg",
        "assets/jurisdictions.svg",
    ]
    assert all((tmp_path / link).exists() for link in image_links)


def test_options_can_omit_internal_checks_and_figures():
    draft = _build(
        options=ArticleOptions(
            generated_at=FIXED.generated_at,
            include_verification=False,
            include_charts=False,
            author="Research desk",
            contact="corrections@example.org",
        )
    )
    markdown = draft.markdown
    assert "## Appendix" not in markdown
    assert "## The record at a glance" not in markdown
    assert "Prepared by Research desk." in markdown
    assert "Corrections and replies: corrections@example.org." in markdown
    assert not draft.assets


def test_repository_dossier_builds_without_unresolved_references():
    root = Path(__file__).resolve().parents[1]
    dossier_data, errors = read_json(root / "data" / "dossier.json")
    assert not errors
    draft = build_article(
        dossier_data,
        [],
        options=ArticleOptions(generated_at=FIXED.generated_at),
    )
    assert draft.stats["records"] == 10
    assert draft.stats["sources"] == 16
    assert not any("Missing source reference" in warning for warning in draft.warnings)
    assert "Unresolved source reference" not in draft.markdown
    assert draft.markdown.count("### ") >= 5
    assert "FINMA — Enforcement reporting" in draft.markdown
    assert "U.S. Securities and Exchange Commission — EDGAR full-text search" in draft.markdown
    assert "## Scope and open questions" in draft.markdown
    assert "Historical Swiss front-running report" in draft.markdown
    assert "Violation Tracker Global" in draft.markdown
    assert "none is treated as a finding" in draft.markdown


def test_written_markdown_is_valid_utf8_json_metadata_roundtrips(tmp_path):
    draft = _build()
    output = tmp_path / "article.md"
    draft.write(output)
    payload = json.loads(json.dumps(draft.stats, ensure_ascii=False))
    assert payload["records"] == 1
    assert output.read_text(encoding="utf-8").endswith("\n")
