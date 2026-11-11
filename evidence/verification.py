"""Cross-check dossier findings against collected source snapshots.

This is the part that turns the dossier from a list of assertions into a
reviewable evidence file:

* which records have a collected source at all,
* whether an "outcome" record's own source actually contains outcome wording,
* whether an allegation record's source now shows a public outcome (an upgrade
  lead rather than a silent contradiction), and
* which wrongdoing signals appear in a source but nowhere in the dossier
  (a lead for the next research pass).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Iterable

from pathlib import Path

from .io import read_json, read_jsonl
from .matching import allianz_mentioned, record_entity_terms
from .schema import (
    CONFIRMED_CLASSES,
    REPORTED_CLASSES,
    summarise,
    validate_dossier,
)
from .signals import SignalReport, is_primary_source, merge_reports, scan_text
from .text import token_equivalent, tokenize, truncate

STATUS_LABELS = {
    "supported_by_primary_source": "Supported by a primary outcome",
    "reported_outcome_only": "Outcome reported by a secondary source only",
    "possible_public_outcome": "Possible public outcome — check for upgrade",
    "allegation_reported": "Reported as an allegation",
    "not_reflected_as_outcome": "Outcome class not reflected in the excerpt",
    "no_signal_in_excerpt": "No wrongdoing signal in the excerpt",
    "source_not_collected": "Source not collected",
    "no_record_excerpt": "Source collected, but no excerpt for this record",
    "adverse_signal_in_disclosure": "Adverse signal inside a company disclosure",
    "disclosure_only": "Company disclosure — not an outcome",
}

STATUS_SEVERITY = {
    "supported_by_primary_source": "ok",
    "reported_outcome_only": "warning",
    "possible_public_outcome": "warning",
    "allegation_reported": "info",
    "not_reflected_as_outcome": "warning",
    "no_signal_in_excerpt": "info",
    "source_not_collected": "warning",
    "no_record_excerpt": "warning",
    "adverse_signal_in_disclosure": "warning",
    "disclosure_only": "info",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _snapshot_excerpt_for_record(snapshot: dict, record: dict) -> str:
    record_id = _text(record.get("id"))
    record_title = _text(record.get("title"))
    matches = [match for match in snapshot.get("record_matches") or [] if isinstance(match, dict)]
    for match in matches:
        if _text(match.get("record_id")) == record_id or _text(match.get("record_title")) == record_title:
            excerpt = _text(match.get("excerpt"))
            if excerpt:
                return excerpt
    # A newer snapshot records which excerpt belongs to which finding. Falling
    # back to the best overall excerpt would attribute one record's passage to
    # another record, so only legacy snapshots use the shared excerpt.
    if matches:
        return ""
    return _text(snapshot.get("text_excerpt"))


def _record_entity_mentioned(text: str, record: dict) -> bool:
    if allianz_mentioned(text):
        return True
    entity_terms = record_entity_terms(record)
    if not entity_terms:
        return False
    text_tokens = tokenize(text)
    return any(
        token_equivalent(token, entity_term)
        for token in text_tokens
        for entity_term in entity_terms
    )


def _signal_categories(record: dict) -> set[str]:
    report = scan_text(
        " ".join(
            [
                _text(record.get("title")),
                _text(record.get("summary")),
                " ".join(item for item in record.get("details") or [] if isinstance(item, str)),
                " ".join(item for item in record.get("tags") or [] if isinstance(item, str)),
            ]
        )
    )
    return set(report.categories_asserted) | set(report.categories_alleged)


def _relevant_snapshots(record: dict, snapshots_by_source: dict[str, list[dict]]) -> list[dict]:
    relevant = []
    for source_id in record.get("source_ids") or []:
        for snapshot in snapshots_by_source.get(_text(source_id), []):
            relevant.append(snapshot)
    return relevant


def _dedupe_snapshots(snapshots: Iterable[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    unique = []
    for snapshot in snapshots:
        key = (_text(snapshot.get("id")), _text(snapshot.get("content_sha256")))
        if key in seen:
            continue
        seen.add(key)
        unique.append(snapshot)
    return unique


def build_record_checks(
    dossier: dict,
    snapshots: list[dict],
) -> list[dict]:
    records = [record for record in dossier.get("records") or [] if isinstance(record, dict)]
    snapshots_by_source: dict[str, list[dict]] = {}
    for snapshot in snapshots:
        if isinstance(snapshot, dict):
            snapshots_by_source.setdefault(_text(snapshot.get("id")), []).append(snapshot)

    checks = []
    for record in records:
        relevant = _dedupe_snapshots(_relevant_snapshots(record, snapshots_by_source))
        per_source = []
        reports: list[SignalReport] = []
        excerpts: list[str] = []
        for snapshot in relevant:
            excerpt = _snapshot_excerpt_for_record(snapshot, record)
            if not excerpt:
                continue
            excerpts.append(excerpt)
            report = scan_text(excerpt, _text(snapshot.get("source_type")))
            reports.append(report)
            per_source.append(
                {
                    "source_id": _text(snapshot.get("id")),
                    "source": _text(snapshot.get("source")),
                    "title": _text(snapshot.get("title")),
                    "url": _text(snapshot.get("url")),
                    "source_type": _text(snapshot.get("source_type")),
                    "retrieved_at": _text(snapshot.get("retrieved_at")),
                    "verification_status": report.verification_status,
                    "severity_score": report.severity_score,
                    "categories_asserted": list(report.categories_asserted),
                    "categories_alleged": list(report.categories_alleged),
                    "excerpt": truncate(excerpt, 700),
                }
            )

        merged = merge_reports(reports) if reports else SignalReport()
        evidence_class = _text(record.get("evidence_class"))
        linked_ids = [_text(source_id) for source_id in record.get("source_ids") or []]
        collected_ids = sorted({_text(snapshot.get("id")) for snapshot in relevant})
        missing_ids = [source_id for source_id in linked_ids if source_id not in collected_ids]
        primary_outcome = any(report.verification_status == "primary_outcome" for report in reports)
        reported_outcome = any(report.verification_status == "reported_outcome" for report in reports)
        alleged = any(report.verification_status == "reported_allegation" for report in reports)

        flags: list[dict] = []

        def flag(severity: str, code: str, message: str) -> None:
            flags.append({"severity": severity, "code": code, "message": message})

        if not relevant:
            status = "source_not_collected"
        elif not reports:
            status = "no_record_excerpt"
            flag("warning", "no_record_excerpt", "The source was collected but yielded no excerpt for this record.")
        elif evidence_class in CONFIRMED_CLASSES:
            if primary_outcome:
                status = "supported_by_primary_source"
            elif reported_outcome:
                status = "reported_outcome_only"
                flag("warning", "secondary_outcome_only", "Outcome wording appears only in a secondary source; verify it in the primary record.")
            elif merged.has_allegation:
                status = "not_reflected_as_outcome"
                flag("warning", "outcome_not_in_excerpt", "Collected excerpts describe misconduct but no court or regulator outcome.")
            elif merged.has_exonerating:
                status = "not_reflected_as_outcome"
                flag("info", "exonerating_language", "The excerpt contains exonerating language; confirm the record still reflects the source.")
            else:
                status = "not_reflected_as_outcome"
                flag("warning", "outcome_not_in_excerpt", "The collected excerpt contains no outcome wording for this record.")
        elif evidence_class in REPORTED_CLASSES:
            if primary_outcome:
                status = "possible_public_outcome"
                flag("warning", "possible_class_upgrade", "A primary source now states an outcome; check whether this allegation should be reclassified.")
            elif alleged or merged.has_allegation:
                status = "allegation_reported"
            elif relevant:
                status = "no_signal_in_excerpt"
                flag("info", "no_signal_in_excerpt", "The excerpt does not contain the allegation wording; inspect the full source.")
        else:
            adverse = [
                signal
                for signal in merged.signals
                if signal.polarity == "asserted" and signal.kind in ("outcome", "misconduct", "control") and signal.weight >= 4
            ]
            if adverse:
                status = "adverse_signal_in_disclosure"
                flag("warning", "adverse_signal_in_disclosure", "A company disclosure excerpt contains wording about an adverse event; verify context.")
            else:
                status = "disclosure_only"

        if evidence_class in CONFIRMED_CLASSES and status == "source_not_collected":
            flag("warning", "missing_snapshot", "No collected snapshot exists for the linked source(s).")
        if missing_ids and status not in ("source_not_collected", "no_record_excerpt"):
            flag(
                "info",
                "partial_source_coverage",
                "{} of {} linked source(s) have no collected snapshot.".format(
                    len(missing_ids), len(linked_ids)
                ),
            )
        if merged.has_exonerating and status == "supported_by_primary_source":
            flag(
                "info",
                "mixed_outcome_language",
                "The checked excerpts also contain acquittal or dismissal wording; read the full source.",
            )
        if (
            evidence_class in CONFIRMED_CLASSES
            and excerpts
            and all(not _record_entity_mentioned(excerpt, record) for excerpt in excerpts)
        ):
            flag(
                "info",
                "no_entity_attribution",
                "No collected excerpt mentions Allianz; check that the source concerns the named entity.",
            )

        priority_score = {"high": 3, "medium": 2, "low": 1}
        if status in ("source_not_collected", "no_record_excerpt"):
            priority = "high" if evidence_class in CONFIRMED_CLASSES else "medium"
        elif status in ("reported_outcome_only", "not_reflected_as_outcome", "possible_public_outcome"):
            priority = "high" if evidence_class in CONFIRMED_CLASSES else "medium"
        elif status == "adverse_signal_in_disclosure":
            priority = "medium"
        elif status == "supported_by_primary_source":
            priority = "low"
        else:
            priority = "medium" if evidence_class in REPORTED_CLASSES else "low"

        checks.append(
            {
                "record_id": _text(record.get("id")),
                "title": _text(record.get("title")),
                "entity": _text(record.get("entity")),
                "evidence_class": evidence_class,
                "geography": _text(record.get("geography")),
                "linked_source_ids": linked_ids,
                "collected_source_ids": collected_ids,
                "missing_source_ids": missing_ids,
                "status": status,
                "status_label": STATUS_LABELS.get(status, status),
                "priority": priority,
                "priority_score": priority_score[priority],
                "flags": flags,
                "entity_mentioned": bool(excerpts)
                and all(_record_entity_mentioned(excerpt, record) for excerpt in excerpts),
                "signal_summary": merged.as_dict(),
                "sources": per_source,
            }
        )
    return checks


def build_leads(
    dossier: dict,
    snapshots: list[dict],
) -> list[dict]:
    """Find wrongdoing signals that the dossier does not yet account for."""

    source_to_records: dict[str, list[dict]] = {}
    for record in dossier.get("records") or []:
        if not isinstance(record, dict):
            continue
        for source_id in record.get("source_ids") or []:
            source_to_records.setdefault(_text(source_id), []).append(record)

    leads: list[dict] = []
    seen: set[str] = set()
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        source_id = _text(snapshot.get("id"))
        excerpt = _text(snapshot.get("text_excerpt"))
        if not excerpt:
            continue
        report = scan_text(excerpt, _text(snapshot.get("source_type")))
        linked_records = source_to_records.get(source_id, [])
        linked_confirmed = any(
            _text(record.get("evidence_class")) in CONFIRMED_CLASSES for record in linked_records
        )
        linked_categories: set[str] = set()
        for record in linked_records:
            linked_categories |= _signal_categories(record)

        candidates: list[dict] = [signal.as_dict() for signal in report.signals]
        for page_signal in snapshot.get("page_signals") or []:
            if isinstance(page_signal, dict):
                candidates.append(page_signal)

        for signal in candidates:
            if signal.get("polarity") != "asserted" or int(signal.get("weight") or 0) < 4:
                continue
            if signal.get("kind") == "exoneration":
                continue
            category = _text(signal.get("category"))
            if signal.get("kind") == "outcome" and linked_confirmed:
                continue
            if category in linked_categories:
                continue
            snippet = _text(signal.get("snippet"))
            if not snippet:
                continue
            if not (allianz_mentioned(snippet) or allianz_mentioned(excerpt)):
                continue
            key = hashlib.sha256(
                "|".join([source_id, category, snippet[:180]]).encode("utf-8")
            ).hexdigest()
            if key in seen:
                continue
            seen.add(key)
            leads.append(
                {
                    "source_id": source_id,
                    "source": _text(snapshot.get("source")),
                    "title": _text(snapshot.get("title")),
                    "url": _text(snapshot.get("url")),
                    "source_type": _text(snapshot.get("source_type")),
                    "retrieved_at": _text(snapshot.get("retrieved_at")),
                    "category": category,
                    "label": _text(signal.get("label")) or category,
                    "weight": int(signal.get("weight") or 0),
                    "snippet": truncate(snippet, 420),
                    "allianz_mentioned": allianz_mentioned(snippet),
                    "primary_source": is_primary_source(snapshot.get("source_type")),
                    "reason": (
                        "The source states an outcome that no linked record records."
                        if signal.get("kind") == "outcome"
                        else "The source contains a wrongdoing signal not covered by the linked record."
                    ),
                }
            )

    leads.sort(key=lambda lead: (-lead["weight"], lead["source"], lead["category"]))
    return leads


def build_report(
    dossier: dict,
    snapshots: list[dict],
    generated_at: str | None = None,
) -> dict:
    issues = validate_dossier(dossier, snapshots)
    integrity = summarise(issues)
    record_checks = build_record_checks(dossier, snapshots)
    leads = build_leads(dossier, snapshots)

    status_counts: dict[str, int] = {}
    priority_counts = {"high": 0, "medium": 0, "low": 0}
    for check in record_checks:
        status_counts[check["status"]] = status_counts.get(check["status"], 0) + 1
        priority_counts[check["priority"]] += 1

    linked_source_ids = {
        source_id
        for record in dossier.get("records") or []
        if isinstance(record, dict)
        for source_id in record.get("source_ids") or []
    }
    collected_source_ids = {
        _text(snapshot.get("id")) for snapshot in snapshots if isinstance(snapshot, dict)
    }
    missing_source_ids = sorted(source_id for source_id in linked_source_ids if source_id not in collected_source_ids)

    supported = status_counts.get("supported_by_primary_source", 0)
    return {
        "generated_at": generated_at or now_iso(),
        "summary": {
            "records": len(record_checks),
            "records_supported": supported,
            "records_with_warnings": priority_counts["high"] + priority_counts["medium"],
            "records_high_priority": priority_counts["high"],
            "leads": len(leads),
            "sources_linked": len(linked_source_ids),
            "sources_collected": len(linked_source_ids & collected_source_ids),
            "sources_missing": len(missing_source_ids),
            "integrity": integrity,
            "status_counts": status_counts,
            "priority_counts": priority_counts,
        },
        "integrity": {
            "summary": integrity,
            "issues": [issue.as_dict() for issue in issues],
        },
        "missing_source_ids": missing_source_ids,
        "records": record_checks,
        "leads": leads,
        "notice": (
            "Automated checks are review aids. A signal is not a finding: read the linked source and "
            "keep the evidence class attached to each record."
        ),
    }


def build_report_from_files(root: Path | str) -> tuple[dict | None, list[str]]:
    """Convenience entry point for the CLI and for offline debugging."""

    root = Path(root)
    errors: list[str] = []
    dossier, dossier_errors = read_json(root / "data" / "dossier.json")
    errors.extend(dossier_errors)
    snapshots, snapshot_errors = read_jsonl(root / "data" / "discovery" / "source_snapshots.jsonl")
    errors.extend(snapshot_errors)
    if not isinstance(dossier, dict):
        return None, errors or ["Dossier could not be read."]
    return build_report(dossier, snapshots), errors
