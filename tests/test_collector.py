import json
import requests

from scraper.collector import CollectorConfig, collect, extract_document, fetch_source
from evidence.matching import build_profiles
from evidence.verification import build_report
from tests.factories import dossier, html_page, record, source

GOOD_BODY = html_page(
    "The Supreme Court of NSW today convicted Allianz Australia Insurance Limited and "
    "imposed criminal fines of $13.5 million for making false or misleading statements "
    "about travel insurance. " * 4
)


class FakeResponse:
    def __init__(self, status_code=200, body=b"", headers=None, url="https://www.asic.gov.au/document"):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.url = url
        self.closed = False

    def iter_content(self, chunk_size=65536):
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start : start + chunk_size]

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected request for {}".format(url))
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _config(tmp_path, **overrides):
    values = {
        "dossier_path": tmp_path / "dossier.json",
        "output_dir": tmp_path / "discovery",
        "pause_seconds": 0,
        "max_attempts": 3,
        "backoff_base": 0.01,
        "max_backoff": 0.02,
        "max_bytes": 1024 * 1024,
    }
    values.update(overrides)
    return CollectorConfig(**values)


def _run(tmp_path, responses, value=None, **overrides):
    value = value or dossier()
    session = FakeSession(responses)
    sleeps = []
    code = collect(
        config=_config(tmp_path, **overrides),
        dossier=value,
        session=session,
        sleep=sleeps.append,
        verbose=False,
    )
    return code, session, sleeps, value


def _status(tmp_path):
    return json.loads((tmp_path / "discovery" / "run_status_latest.json").read_text(encoding="utf-8"))


def _snapshots(tmp_path):
    path = tmp_path / "discovery" / "source_snapshots.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_blocked_primary_uses_official_fallback(tmp_path):
    value = dossier(
        sources=[
            source(
                url="https://www.asic.gov.au/primary",
                fallback_url="https://www.asic.gov.au/fallback",
            )
        ]
    )
    code, session, _, _ = _run(
        tmp_path,
        [FakeResponse(403), FakeResponse(200, GOOD_BODY)],
        value=value,
    )
    assert code == 0
    assert len(session.calls) == 2
    assert _status(tmp_path)["sources"][0]["status"] == "fetched_alternate"
    snapshot = _snapshots(tmp_path)[0]
    assert "convicted" in snapshot["text_excerpt"]
    assert snapshot["record_matches"][0]["record_id"] == "asic-travel"


def test_transient_error_is_retried_and_recorded(tmp_path):
    code, session, sleeps, _ = _run(
        tmp_path,
        [FakeResponse(503), FakeResponse(200, GOOD_BODY)],
    )
    assert code == 0
    status = _status(tmp_path)["sources"][0]
    assert status["status"] == "fetched"
    assert [attempt.get("http_status") for attempt in status["attempts"]] == [503, 200]
    assert len(session.calls) == 2
    assert sleeps


def test_retry_exhaustion_is_reported(tmp_path):
    code, _, _, _ = _run(
        tmp_path,
        [FakeResponse(503), FakeResponse(503), FakeResponse(503)],
    )
    assert code == 1
    assert _status(tmp_path)["sources"][0]["status"] == "retry_exhausted"


def test_connection_errors_are_retried(tmp_path):
    code, session, _, _ = _run(
        tmp_path,
        [requests.ConnectionError("boom"), FakeResponse(200, GOOD_BODY)],
    )
    assert code == 0
    attempts = _status(tmp_path)["sources"][0]["attempts"]
    assert attempts[0]["error"] == "ConnectionError"
    assert len(session.calls) == 2


def test_access_challenge_is_not_saved_as_evidence(tmp_path):
    body = b"<html><body>bm-verify=abc Checking your browser before access.</body></html>"
    code, _, _, _ = _run(tmp_path, [FakeResponse(200, body)])
    assert code == 1
    assert _status(tmp_path)["sources"][0]["status"] == "access_challenge"
    assert not (tmp_path / "discovery" / "source_snapshots.jsonl").exists()


def test_oversized_document_is_skipped_before_download(tmp_path):
    response = FakeResponse(200, b"x", headers={"content-length": "999999"})
    code, _, _, _ = _run(tmp_path, [response], max_bytes=1000)
    assert code == 1
    assert _status(tmp_path)["sources"][0]["status"] == "skipped_large"


def test_binary_payload_is_not_parsed_as_html(tmp_path):
    response = FakeResponse(200, b"\x00\x01" * 500, headers={"content-type": "application/zip"})
    code, _, _, _ = _run(tmp_path, [response])
    assert code == 1
    assert _status(tmp_path)["sources"][0]["status"] == "unsupported_content"


def test_malformed_pdf_reports_extraction_error(tmp_path):
    response = FakeResponse(200, b"%PDF-1.7\nnot really a pdf", headers={"content-type": "application/pdf"})
    code, _, _, _ = _run(tmp_path, [response])
    assert code == 1
    assert _status(tmp_path)["sources"][0]["status"] == "extraction_error"


def test_unrelated_page_reports_no_relevant_text_with_diagnostics(tmp_path):
    page = html_page("Bread recipes and hiking routes. " * 20, title="Cooking | Example")
    code, _, _, _ = _run(tmp_path, [FakeResponse(200, page)])
    assert code == 1
    status = _status(tmp_path)["sources"][0]
    assert status["status"] == "no_relevant_text"
    assert "asic-travel" in status["match_diagnostics"]


def test_each_record_gets_its_own_excerpt(tmp_path):
    second = record(
        id="credit-check",
        sort_date="2014-11-06",
        date_label="Reported 6 November 2014",
        category="Customer screening",
        evidence_class="Reported allegation",
        geography="Switzerland",
        title="Allianz24 applicant rejected after an incorrect credit check",
        entity="Allianz Suisse online brand Allianz24",
        summary="An applicant was refused motor insurance after a creditworthiness check.",
        details=["Allianz apologised for the error."],
        source_ids=["asic-travel"],
        tags=["Customer data"],
    )
    value = dossier(records=[record(), second], sources=[source()])
    code, _, _, _ = _run(tmp_path, [FakeResponse(200, GOOD_BODY)], value=value)
    assert code == 0
    snapshot = _snapshots(tmp_path)[0]
    assert {match["record_id"] for match in snapshot["record_matches"]} == {"asic-travel"}

    report = build_report(value, [snapshot])
    by_id = {check["record_id"]: check for check in report["records"]}
    assert by_id["asic-travel"]["status"] == "supported_by_primary_source"
    assert by_id["credit-check"]["status"] == "no_record_excerpt"


def test_failed_run_retains_previous_snapshots_and_archives_outputs(tmp_path):
    value = dossier()
    assert collect(
        config=_config(tmp_path),
        dossier=value,
        session=FakeSession([FakeResponse(200, GOOD_BODY)]),
        sleep=lambda _: None,
        verbose=False,
    ) == 0
    before = (tmp_path / "discovery" / "source_snapshots.jsonl").read_text(encoding="utf-8")

    code = collect(
        config=_config(tmp_path),
        dossier=value,
        session=FakeSession([FakeResponse(403)]),
        sleep=lambda _: None,
        verbose=False,
    )
    assert code == 1
    after = (tmp_path / "discovery" / "source_snapshots.jsonl").read_text(encoding="utf-8")
    status = _status(tmp_path)
    assert before == after
    assert status["snapshots_retained"] is True
    assert status["new_snapshot_count"] == 0
    assert status["snapshot_count"] == 1
    assert status["verification"]["records"] == 1

    archive = tmp_path / "discovery" / "archive"
    assert list(archive.glob("discovery_*.jsonl"))
    assert list(archive.glob("run_status_*.json"))


def test_private_url_is_rejected_by_fetch_source(tmp_path):
    value = dossier(sources=[source(url="http://127.0.0.1/private")])
    records = value["records"]
    profiles = build_profiles(records)
    status, snapshot = fetch_source(
        FakeSession([]),
        value["sources"][0],
        records,
        profiles,
        _config(tmp_path),
        sleep=lambda _: None,
    )
    assert status["status"] == "invalid_url"
    assert snapshot is None


def test_collect_aborts_on_structural_dossier_errors(tmp_path):
    value = dossier(records=[record(source_ids=["not-registered"])])
    code, session, _, _ = _run(tmp_path, [], value=value)
    assert code == 2
    assert not session.calls
    assert not (tmp_path / "discovery" / "run_status_latest.json").exists()


def test_outputs_are_json_serialisable_and_report_exists(tmp_path):
    value = dossier()
    assert collect(
        config=_config(tmp_path),
        dossier=value,
        session=FakeSession([FakeResponse(200, GOOD_BODY)]),
        sleep=lambda _: None,
        verbose=False,
    ) == 0
    report = json.loads((tmp_path / "discovery" / "verification_report.json").read_text(encoding="utf-8"))
    assert report["summary"]["records"] == 1
    assert report["summary"]["records_supported"] == 1
    assert report["integrity"]["summary"]["ok"]


def test_unlinked_source_is_fetched_but_not_stored(tmp_path):
    unused = source(
        id="unused-source",
        title="General enforcement database",
        url="https://www.asic.gov.au/database",
    )
    value = dossier(sources=[source(), unused])
    code, session, _, _ = _run(
        tmp_path,
        [FakeResponse(200, GOOD_BODY), FakeResponse(200, GOOD_BODY)],
        value=value,
    )
    assert code == 0
    statuses = {item["source_id"]: item for item in _status(tmp_path)["sources"]}
    assert statuses["unused-source"]["status"] == "no_linked_record"
    assert [snapshot["id"] for snapshot in _snapshots(tmp_path)] == ["asic-travel"]


def test_boilerplate_filter_does_not_drop_content_lines():
    text, diagnostics = extract_document(
        html_page("Home insurance premiums rose sharply after the storm."),
        "text/html; charset=utf-8",
    )
    assert diagnostics["kind"] == "html"
    assert "Home insurance premiums rose sharply" in text
    assert "Skip to main content" not in text


def test_retry_after_is_capped_and_retried(tmp_path):
    code, _, sleeps, _ = _run(
        tmp_path,
        [FakeResponse(429, headers={"Retry-After": "5"}), FakeResponse(200, GOOD_BODY)],
        max_backoff=0.5,
    )
    assert code == 0
    assert sleeps[0] == 0.5
    assert _status(tmp_path)["sources"][0]["status"] == "fetched"
