import json
from pathlib import Path

from evidence.__main__ import main as cli_main
from evidence.schema import summarise, validate_dossier, validate_snapshots
from tests.factories import dossier, record, source

ROOT = Path(__file__).resolve().parents[1]


def _codes(issues):
    return {issue.code for issue in issues}


def test_valid_dossier_has_no_errors():
    issues = validate_dossier(dossier())
    assert summarise(issues)["errors"] == 0


def test_unknown_source_reference_is_an_error():
    value = dossier(records=[record(source_ids=["missing-source"])])
    issues = validate_dossier(value)
    assert "record_source_unknown" in _codes(issues)
    assert summarise(issues)["errors"] >= 1


def test_duplicate_ids_are_rejected():
    value = dossier(records=[record(), record(title="Another record")])
    assert "record_id_duplicate" in _codes(validate_dossier(value))


def test_unknown_evidence_class_is_rejected():
    value = dossier(records=[record(evidence_class="Rumour")])
    assert "evidence_class_unknown" in _codes(validate_dossier(value))


def test_confirmed_outcome_without_primary_source_warns():
    value = dossier(sources=[source(source_type="Secondary · reporting")])
    assert "confirmed_without_primary_source" in _codes(validate_dossier(value))


def test_private_url_is_rejected():
    value = dossier(sources=[source(url="http://127.0.0.1:8080/document")])
    assert "url_private" in _codes(validate_dossier(value))


def test_non_http_url_is_rejected():
    value = dossier(sources=[source(url="file:///etc/passwd")])
    assert "url_scheme" in _codes(validate_dossier(value))


def test_publisher_domain_mismatch_warns():
    value = dossier(sources=[source(publisher="SRF")])
    assert "publisher_domain_mismatch" in _codes(validate_dossier(value))


def test_outcome_record_without_outcome_language_warns():
    value = dossier(
        records=[
            record(
                title="Allianz opened an internal review",
                summary="Allianz opened an internal review of its processes.",
                details=[],
            )
        ]
    )
    assert "class_without_outcome_language" in _codes(validate_dossier(value))


def test_allegation_record_describing_an_outcome_warns():
    value = dossier(
        records=[
            record(
                evidence_class="Reported allegation",
                summary="The court convicted Allianz of fraud, a report said.",
            )
        ]
    )
    assert "allegation_record_describes_outcome" in _codes(validate_dossier(value))


def test_human_readable_reviewed_on_is_accepted():
    value = dossier()
    issues = validate_dossier(value)
    assert "reviewed_on_invalid" not in _codes(issues)


def test_snapshot_checks_flag_unknown_sources_and_bad_hashes():
    snapshots = [
        {
            "id": "not-registered",
            "text_excerpt": "text",
            "content_sha256": "abc",
            "retrieved_at": "not-a-date",
            "url": "https://example.org/x",
        }
    ]
    issues = validate_snapshots(snapshots, dossier())
    codes = _codes(issues)
    assert "snapshot_unknown_source" in codes
    assert "snapshot_hash_invalid" in codes
    assert "snapshot_timestamp_invalid" in codes


def test_repository_dossier_is_valid():
    value = json.loads((ROOT / "data" / "dossier.json").read_text(encoding="utf-8"))
    assert summarise(validate_dossier(value))["errors"] == 0


def test_cli_validate_and_report_run_offline(tmp_path):
    assert cli_main(["validate", "--root", str(ROOT), "--snapshots"]) == 0
    output = tmp_path / "report.json"
    assert cli_main(["report", "--root", str(ROOT), "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["summary"]["records"] == 10
    assert "integrity" in report


def test_human_readable_sort_date_is_rejected():
    value = dossier(records=[record(sort_date="8 November 2026")])
    assert "sort_date_invalid" in _codes(validate_dossier(value))


def test_url_with_invalid_port_or_whitespace_is_rejected():
    assert "url_malformed" in _codes(validate_dossier(dossier(sources=[source(url="https://www.asic.gov.au:99999/x")])))
    assert "url_whitespace" in _codes(validate_dossier(dossier(sources=[source(url="https://www.asic.gov.au/a b")])))


def test_identical_snapshot_content_under_two_sources_warns():
    digest = "a" * 64
    snapshots = [
        {"id": "s1", "text_excerpt": "x", "content_sha256": digest, "retrieved_at": "2026-01-01T00:00:00+00:00"},
        {"id": "s2", "text_excerpt": "x", "content_sha256": digest, "retrieved_at": "2026-01-01T00:00:00+00:00"},
    ]
    value = dossier(
        records=[record(source_ids=["s1"])],
        sources=[source(id="s1"), source(id="s2", url="https://www.asic.gov.au/other")],
    )
    assert "snapshot_duplicate_content" in _codes(validate_snapshots(snapshots, value))
