from evidence.verification import build_report
from tests.factories import dossier, record, source


def _record(record_id, title, evidence_class="Regulatory or criminal outcome", source_ids=None, **overrides):
    return record(
        id=record_id,
        title=title,
        evidence_class=evidence_class,
        source_ids=source_ids or [],
        **overrides,
    )


def _source(source_id, publisher="Allianz SE", url="https://www.allianz.com/en/statement", source_type="Primary · company statement"):
    return source(id=source_id, publisher=publisher, url=url, source_type=source_type, title="Statement " + source_id)


def _snapshot(source_id, excerpt, source_type="Primary · regulator decision", matches=None, publisher="Allianz SE"):
    snapshot = {
        "id": source_id,
        "source": publisher,
        "title": "Document " + source_id,
        "url": "https://www.allianz.com/en/statement",
        "source_type": source_type,
        "retrieved_at": "2026-01-01T00:00:00+00:00",
        "text_excerpt": excerpt,
    }
    if matches is not None:
        snapshot["record_matches"] = matches
    return snapshot


def _build_fixture():
    records = [
        _record("r1", "Court convicted Allianz of fraud", source_ids=["s1"]),
        _record("r2", "Court convicted Allianz of bribery", source_ids=["s2"]),
        _record(
            "r3",
            "Report alleged that Allianz misled customers",
            evidence_class="Reported allegation",
            source_ids=["s3"],
        ),
        _record("r4", "Court convicted Allianz of misleading statements", source_ids=["s1"]),
    ]
    sources = [
        _source("s1"),
        _source("s2"),
        _source("s3", source_type="Primary · regulator decision"),
        _source("s5", publisher="Inside Paradeplatz", url="https://insideparadeplatz.ch/story", source_type="Secondary · reporting"),
        _source("s6", publisher="Inside Paradeplatz", url="https://insideparadeplatz.ch/other", source_type="Secondary · reporting"),
    ]
    snapshots = [
        _snapshot(
            "s1",
            "The court convicted Allianz of fraud and imposed a fine.",
            matches=[
                {
                    "record_id": "r1",
                    "record_title": "Court convicted Allianz of fraud",
                    "excerpt": "The court convicted Allianz of fraud and imposed a fine.",
                    "confidence": 0.9,
                }
            ],
        ),
        _snapshot(
            "s3",
            "The regulator imposed a fine on Allianz for misleading customers.",
            source_type="Primary · regulator decision",
        ),
        _snapshot(
            "s5",
            "Allianz Insurance was convicted of paying bribes to public officials.",
            source_type="Secondary · reporting",
            publisher="Inside Paradeplatz",
        ),
        _snapshot(
            "s6",
            "The company was convicted of fraud and fined.",
            source_type="Secondary · reporting",
            publisher="Inside Paradeplatz",
        ),
    ]
    return dossier(records=records, sources=sources), snapshots


def test_record_statuses_distinguish_supported_gaps_and_upgrades():
    value, snapshots = _build_fixture()
    report = build_report(value, snapshots)
    by_id = {check["record_id"]: check for check in report["records"]}

    assert by_id["r1"]["status"] == "supported_by_primary_source"
    assert by_id["r2"]["status"] == "source_not_collected"
    assert by_id["r2"]["priority"] == "high"
    assert by_id["r3"]["status"] == "possible_public_outcome"
    assert "possible_class_upgrade" in {flag["code"] for flag in by_id["r3"]["flags"]}
    assert by_id["r4"]["status"] == "no_record_excerpt"
    assert by_id["r4"]["priority"] == "high"


def test_report_surfaces_unrecorded_wrongdoing_leads_only_with_entity_attribution():
    value, snapshots = _build_fixture()
    report = build_report(value, snapshots)

    source_ids = {lead["source_id"] for lead in report["leads"]}
    assert "s5" in source_ids
    assert "s6" not in source_ids
    bribery_lead = next(lead for lead in report["leads"] if lead["source_id"] == "s5")
    assert bribery_lead["category"] in ("bribery", "criminal_outcome")
    assert bribery_lead["allianz_mentioned"]


def test_report_summary_and_integrity_are_serialisable():
    value, snapshots = _build_fixture()
    report = build_report(value, snapshots)
    assert report["summary"]["records"] == 4
    assert report["summary"]["records_supported"] == 1
    assert report["summary"]["leads"] >= 1
    assert report["integrity"]["summary"]["ok"]
    assert "notice" in report


def test_legacy_snapshot_without_record_matches_still_verifies():
    value = dossier(records=[record()], sources=[source()])
    snapshots = [
        {
            "id": "asic-travel",
            "source": "Australian Securities and Investments Commission",
            "title": "Allianz and AWP convicted",
            "url": "https://www.asic.gov.au/x",
            "source_type": "Primary · regulator and court outcome",
            "retrieved_at": "2026-01-01T00:00:00+00:00",
            "text_excerpt": "The court convicted Allianz Australia of making false or misleading statements.",
        }
    ]
    report = build_report(value, snapshots)
    assert report["records"][0]["status"] == "supported_by_primary_source"


def test_named_individual_entity_counts_as_attribution():
    named_record = _record(
        "r5",
        "Former Allianz portfolio manager pleaded guilty",
        source_ids=["s7"],
        entity="Gregoire Tournant",
    )
    named_source = _source("s7", source_type="Primary · court filing")
    snapshot = _snapshot(
        "s7",
        "Tournant pleaded guilty to one count of securities fraud.",
        matches=[
            {
                "record_id": "r5",
                "record_title": named_record["title"],
                "excerpt": "Tournant pleaded guilty to one count of securities fraud.",
                "confidence": 0.9,
            }
        ],
    )
    report = build_report(dossier(records=[named_record], sources=[named_source]), [snapshot])
    check = report["records"][0]
    assert check["status"] == "supported_by_primary_source"
    assert check["entity_mentioned"] is True
    assert "no_entity_attribution" not in {flag["code"] for flag in check["flags"]}
