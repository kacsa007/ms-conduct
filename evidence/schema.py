"""Integrity checks for the dossier and for collected snapshots.

A mis-linked source is worse than a missing one: it makes a finding look
sourced while the reader lands on an unrelated page. These checks therefore
cover structure, references, URL safety, publisher/domain agreement, and
whether the evidence class matches the language used in the record.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urlsplit

from .signals import scan_text

EVIDENCE_CLASSES = (
    "Regulatory or criminal outcome",
    "Court decision",
    "Reported allegation",
    "Reported court outcome",
    "Company disclosure",
    "Official policy",
)
CONFIRMED_CLASSES = ("Regulatory or criminal outcome", "Court decision")
REPORTED_CLASSES = ("Reported allegation", "Reported court outcome")
DISCLOSURE_CLASSES = ("Company disclosure", "Official policy")

PUBLISHER_DOMAINS = {
    "U.S. Department of Justice": ("justice.gov",),
    "U.S. Securities and Exchange Commission": ("sec.gov",),
    "Australian Securities and Investments Commission": ("asic.gov.au",),
    "Commission de Surveillance du Secteur Financier": ("cssf.lu",),
    "Inside Paradeplatz": ("insideparadeplatz.ch",),
    "SRF": ("srf.ch",),
    "Allianz Suisse": ("allianz.ch",),
    "Allianz SE": ("allianz.com",),
    "Swiss Federal Supreme Court": ("bger.ch",),
    "FINMA": ("finma.ch",),
}

_PRIVATE_SUFFIXES = (".local", ".internal", ".localhost", ".home", ".lan")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Issue:
    severity: str  # error | warning | info
    code: str
    scope: str  # dossier | record | source | research_step | snapshot
    message: str
    ref: str = ""

    def as_dict(self) -> dict:
        return {
            "severity": self.severity,
            "code": self.code,
            "scope": self.scope,
            "message": self.message,
            "ref": self.ref,
        }


def summarise(issues: list[Issue]) -> dict:
    counts = {"error": 0, "warning": 0, "info": 0}
    for issue in issues:
        counts[issue.severity] = counts.get(issue.severity, 0) + 1
    return {
        "total": len(issues),
        "errors": counts["error"],
        "warnings": counts["warning"],
        "infos": counts["info"],
        "ok": counts["error"] == 0,
    }


def sort_issues(issues: list[Issue]) -> list[Issue]:
    rank = {"error": 0, "warning": 1, "info": 2}
    return sorted(issues, key=lambda issue: (rank.get(issue.severity, 3), issue.scope, issue.ref, issue.code))


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def url_problem(url: object) -> tuple[str, str]:
    """Return ``(code, message)`` for an invalid URL, else ``("", "")``."""

    value = _text(url)
    if not value:
        return "missing", "URL is missing."
    if any(character.isspace() for character in value):
        return "whitespace", "URL must not contain whitespace."
    try:
        parts = urlsplit(value)
        _ = parts.port  # raises ValueError for an invalid or out-of-range port
    except ValueError:
        return "malformed", "URL cannot be parsed."
    if parts.scheme.lower() not in ("http", "https"):
        return "scheme", "URL must use http or https."
    if parts.username or parts.password:
        return "credentials", "URL must not embed credentials."
    host = (parts.hostname or "").lower()
    if not host:
        return "host", "URL has no host."
    if host in ("localhost", "127.0.0.1", "::1") or host.endswith(_PRIVATE_SUFFIXES):
        return "private", "URL points at a local or internal host."
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
    ):
        return "private", "URL points at a private or reserved address."
    if "." not in host and ":" not in host:
        return "host", "URL host is not a public domain name."
    return "", ""


def host_matches(url: object, domains: tuple[str, ...]) -> bool:
    host = (urlsplit(_text(url)).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def _validate_url_field(
    issues: list[Issue],
    scope: str,
    ref: str,
    field: str,
    value: object,
    *,
    required: bool,
) -> None:
    code, message = url_problem(value)
    if not code:
        if urlsplit(_text(value)).scheme.lower() == "http":
            issues.append(
                Issue("warning", "url_not_https", scope, "{} uses plain HTTP; prefer the HTTPS source.".format(field), ref)
            )
        return
    severity = "error" if required else "info"
    if code == "missing" and not required:
        return
    issues.append(Issue(severity, "url_" + code, scope, "{}: {}".format(field, message), ref))


def validate_dossier(dossier: object, snapshots: list[dict] | None = None) -> list[Issue]:
    issues: list[Issue] = []
    if not isinstance(dossier, dict):
        return [Issue("error", "dossier_not_object", "dossier", "Dossier root must be a JSON object.")]

    reviewed_on = dossier.get("reviewed_on")
    if reviewed_on:
        parsed = _parse_date(str(reviewed_on))
        if parsed is None:
            issues.append(Issue("warning", "reviewed_on_invalid", "dossier", "reviewed_on is not a recognisable date.", "reviewed_on"))

    records_value = dossier.get("records")
    sources_value = dossier.get("sources")
    steps_value = dossier.get("research_steps")
    records = records_value if isinstance(records_value, list) else []
    sources = sources_value if isinstance(sources_value, list) else []
    steps = steps_value if isinstance(steps_value, list) else []
    if not isinstance(records_value, list) or not records:
        issues.append(Issue("error", "records_missing", "dossier", "Dossier must contain a non-empty records list."))
    if not isinstance(sources_value, list) or not sources:
        issues.append(Issue("error", "sources_missing", "dossier", "Dossier must contain a non-empty sources list."))
    if not isinstance(steps_value, list):
        issues.append(Issue("warning", "research_steps_missing", "dossier", "research_steps should be a list."))

    source_index: dict[str, dict] = {}
    seen_titles: dict[str, str] = {}
    seen_urls: dict[str, str] = {}
    for position, source in enumerate(sources):
        if not isinstance(source, dict):
            issues.append(Issue("error", "source_not_object", "source", "Source entry {} is not an object.".format(position)))
            continue
        source_id = _text(source.get("id"))
        ref = source_id or "sources[{}]".format(position)
        if not source_id:
            issues.append(Issue("error", "source_id_missing", "source", "Source entry has no id.", ref))
        elif source_id in source_index:
            issues.append(Issue("error", "source_id_duplicate", "source", "Duplicate source id '{}'.".format(source_id), ref))
        elif not _SLUG_RE.match(source_id):
            issues.append(Issue("warning", "source_id_not_slug", "source", "Source id should be a lowercase slug.", ref))
        if source_id:
            source_index.setdefault(source_id, source)

        for field in ("publisher", "title", "source_type"):
            if not _text(source.get(field)):
                issues.append(Issue("error", "source_field_missing", "source", "Source has no {}.".format(field), ref))
        if not _text(source.get("use")):
            issues.append(Issue("info", "source_use_missing", "source", "Source has no stated use.", ref))

        _validate_url_field(issues, "source", ref, "url", source.get("url"), required=True)
        _validate_url_field(issues, "source", ref, "alternate_url", source.get("alternate_url"), required=False)
        _validate_url_field(issues, "source", ref, "fallback_url", source.get("fallback_url"), required=False)

        publisher = _text(source.get("publisher"))
        domains = PUBLISHER_DOMAINS.get(publisher)
        if domains and _text(source.get("url")) and not host_matches(source.get("url"), domains):
            issues.append(
                Issue(
                    "warning",
                    "publisher_domain_mismatch",
                    "source",
                    "Source host does not match publisher '{}' ({}).".format(publisher, ", ".join(domains)),
                    ref,
                )
            )

        fallback = _text(source.get("fallback_url"))
        if fallback and fallback == _text(source.get("url")):
            issues.append(Issue("info", "fallback_redundant", "source", "fallback_url equals url.", ref))

        anchors = source.get("excerpt_anchors", [])
        if anchors is not None and not isinstance(anchors, list):
            issues.append(Issue("error", "anchors_not_list", "source", "excerpt_anchors must be a list.", ref))
            anchors = []
        seen_anchors: set[str] = set()
        for anchor in anchors or []:
            if not isinstance(anchor, str) or len(anchor.strip()) < 8:
                issues.append(Issue("warning", "anchor_too_short", "source", "Excerpt anchor is empty or too short.", ref))
                continue
            key = anchor.strip().casefold()
            if key in seen_anchors:
                issues.append(Issue("info", "anchor_duplicate", "source", "Duplicate excerpt anchor.", ref))
            seen_anchors.add(key)

        title_key = _text(source.get("title")).casefold()
        if title_key and title_key in seen_titles:
            issues.append(Issue("warning", "source_title_duplicate", "source", "Source title duplicates '{}'.".format(seen_titles[title_key]), ref))
        seen_titles.setdefault(title_key, ref)

        url_key = _text(source.get("url"))
        if url_key and url_key in seen_urls:
            issues.append(Issue("warning", "source_url_duplicate", "source", "Source URL duplicates '{}'.".format(seen_urls[url_key]), ref))
        seen_urls.setdefault(url_key, ref)

    referenced_sources: set[str] = set()
    seen_record_ids: set[str] = set()
    seen_record_titles: set[str] = set()

    for position, record in enumerate(records):
        if not isinstance(record, dict):
            issues.append(Issue("error", "record_not_object", "record", "Record entry {} is not an object.".format(position)))
            continue
        record_id = _text(record.get("id"))
        ref = record_id or "records[{}]".format(position)
        for field in ("id", "sort_date", "date_label", "category", "evidence_class", "geography", "title", "entity", "summary"):
            if not _text(record.get(field)):
                issues.append(Issue("error", "record_field_missing", "record", "Record has no {}.".format(field), ref))
        if not record_id:
            issues.append(Issue("error", "record_id_missing", "record", "Record has no id.", ref))
        elif record_id in seen_record_ids:
            issues.append(Issue("error", "record_id_duplicate", "record", "Duplicate record id '{}'.".format(record_id), ref))
        elif not _SLUG_RE.match(record_id):
            issues.append(Issue("warning", "record_id_not_slug", "record", "Record id should be a lowercase slug.", ref))
        seen_record_ids.add(record_id)

        title_key = _text(record.get("title")).casefold()
        if title_key and title_key in seen_record_titles:
            issues.append(Issue("warning", "record_title_duplicate", "record", "Record title is duplicated.", ref))
        seen_record_titles.add(title_key)

        evidence_class = _text(record.get("evidence_class"))
        if evidence_class and evidence_class not in EVIDENCE_CLASSES:
            issues.append(
                Issue("error", "evidence_class_unknown", "record", "Unknown evidence_class '{}'.".format(evidence_class), ref)
            )

        sort_date = _text(record.get("sort_date"))
        parsed_date = _parse_iso_date(sort_date)
        if sort_date and parsed_date is None:
            issues.append(Issue("error", "sort_date_invalid", "record", "sort_date must be an ISO date (YYYY-MM-DD).", ref))
        if parsed_date and _text(record.get("date_label")):
            years = _YEAR_RE.findall(_text(record.get("date_label")))
            if years and str(parsed_date.year) not in years:
                issues.append(
                    Issue(
                        "info",
                        "date_label_year_mismatch",
                        "record",
                        "sort_date year {} does not appear in date_label.".format(parsed_date.year),
                        ref,
                    )
                )

        source_ids = record.get("source_ids")
        if not isinstance(source_ids, list) or not source_ids:
            issues.append(Issue("error", "record_sources_missing", "record", "Record must link at least one source.", ref))
            source_ids = []
        linked_sources: list[dict] = []
        seen_linked: set[str] = set()
        for source_id in source_ids:
            key = _text(source_id)
            if not key:
                issues.append(Issue("error", "record_source_invalid", "record", "Record contains an empty source id.", ref))
                continue
            referenced_sources.add(key)
            if key in seen_linked:
                issues.append(Issue("warning", "record_source_duplicate", "record", "Record links source '{}' twice.".format(key), ref))
                continue
            seen_linked.add(key)
            if key not in source_index:
                issues.append(Issue("error", "record_source_unknown", "record", "Record links unknown source '{}'.".format(key), ref))
            else:
                linked_sources.append(source_index[key])

        tags = record.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            issues.append(Issue("warning", "record_tags_invalid", "record", "tags must be a list of strings.", ref))
            tags = []
        if "Outcome not public" in tags and evidence_class in CONFIRMED_CLASSES:
            issues.append(
                Issue("warning", "outcome_tag_conflict", "record", "Record is a confirmed outcome but is tagged 'Outcome not public'.", ref)
            )

        details = record.get("details", [])
        if not isinstance(details, list) or not all(isinstance(item, str) for item in details):
            issues.append(Issue("warning", "record_details_invalid", "record", "details must be a list of strings.", ref))
        elif not details:
            issues.append(Issue("info", "record_details_empty", "record", "Record has no supporting details.", ref))

        if evidence_class in CONFIRMED_CLASSES and linked_sources:
            if not any(_text(source.get("source_type")).casefold().startswith("primary") for source in linked_sources):
                issues.append(
                    Issue(
                        "warning",
                        "confirmed_without_primary_source",
                        "record",
                        "Confirmed outcome links no primary source.",
                        ref,
                    )
                )
        if evidence_class in REPORTED_CLASSES and linked_sources:
            if not any(
                _text(source.get("source_type")).casefold().startswith(("secondary", "media", "report"))
                for source in linked_sources
            ):
                issues.append(
                    Issue(
                        "info",
                        "reported_without_secondary_source",
                        "record",
                        "Reported record has no explicitly secondary source.",
                        ref,
                    )
                )
        if evidence_class in DISCLOSURE_CLASSES and linked_sources:
            if not all(
                _text(source.get("publisher")).casefold().startswith("allianz")
                for source in linked_sources
            ):
                issues.append(
                    Issue(
                        "warning",
                        "disclosure_not_company_source",
                        "record",
                        "Company disclosure does not link only to Allianz-published sources.",
                        ref,
                    )
                )

        if evidence_class == "Regulatory or criminal outcome":
            language = scan_text(
                " ".join(
                    [
                        _text(record.get("title")),
                        _text(record.get("summary")),
                        " ".join(item for item in details if isinstance(item, str)),
                    ]
                )
            )
            if not language.has_outcome:
                issues.append(
                    Issue(
                        "warning",
                        "class_without_outcome_language",
                        "record",
                        "Record is classed as an outcome but its text contains no outcome wording.",
                        ref,
                    )
                )
        if evidence_class == "Reported allegation":
            language = scan_text(" ".join([_text(record.get("title")), _text(record.get("summary"))]))
            if language.has_outcome:
                issues.append(
                    Issue(
                        "warning",
                        "allegation_record_describes_outcome",
                        "record",
                        "Reported allegation contains confirmed outcome wording; check whether the class should be updated.",
                        ref,
                    )
                )

    for position, step in enumerate(steps):
        if not isinstance(step, dict):
            issues.append(Issue("error", "research_step_not_object", "research_step", "Research step {} is not an object.".format(position)))
            continue
        name = _text(step.get("name"))
        ref = name or "research_steps[{}]".format(position)
        for field in ("name", "status", "coverage", "result"):
            if not _text(step.get(field)):
                issues.append(Issue("warning", "research_step_field_missing", "research_step", "Research step has no {}.".format(field), ref))
        source_ids = step.get("source_ids", [])
        if not isinstance(source_ids, list):
            issues.append(Issue("error", "research_step_sources_invalid", "research_step", "source_ids must be a list.", ref))
            source_ids = []
        for source_id in source_ids:
            key = _text(source_id)
            referenced_sources.add(key)
            if key and key not in source_index:
                issues.append(
                    Issue("warning", "research_step_source_unknown", "research_step", "Research step links unknown source '{}'.".format(key), ref)
                )

    unused = sorted(set(source_index) - referenced_sources)
    for source_id in unused:
        issues.append(Issue("info", "source_unused", "source", "Source is not linked to any record or research step.", source_id))

    if snapshots is not None:
        issues.extend(validate_snapshots(snapshots, dossier))

    return sort_issues(issues)


def validate_snapshots(snapshots: list[dict], dossier: dict | None = None) -> list[Issue]:
    issues: list[Issue] = []
    source_index = {}
    record_titles = set()
    if isinstance(dossier, dict):
        for source in dossier.get("sources") or []:
            if isinstance(source, dict) and _text(source.get("id")):
                source_index[_text(source.get("id"))] = source
        for record in dossier.get("records") or []:
            if isinstance(record, dict) and _text(record.get("title")):
                record_titles.add(_text(record.get("title")))

    seen_ids: set[str] = set()
    seen_hashes: dict[str, str] = {}
    for position, snapshot in enumerate(snapshots):
        if not isinstance(snapshot, dict):
            issues.append(Issue("error", "snapshot_not_object", "snapshot", "Snapshot entry {} is not an object.".format(position)))
            continue
        source_id = _text(snapshot.get("id"))
        ref = source_id or "snapshots[{}]".format(position)
        if not source_id:
            issues.append(Issue("warning", "snapshot_id_missing", "snapshot", "Snapshot has no source id.", ref))
        elif source_id in seen_ids:
            issues.append(Issue("warning", "snapshot_id_duplicate", "snapshot", "More than one snapshot for source '{}'.".format(source_id), ref))
        seen_ids.add(source_id)

        if source_index and source_id and source_id not in source_index:
            issues.append(Issue("warning", "snapshot_unknown_source", "snapshot", "Snapshot source '{}' is not in the dossier.".format(source_id), ref))
        elif source_id in source_index:
            expected = _text(source_index[source_id].get("url"))
            actual = _text(snapshot.get("url"))
            if expected and actual and expected != actual:
                issues.append(Issue("warning", "snapshot_url_mismatch", "snapshot", "Snapshot URL differs from the registered source URL.", ref))

        if not _text(snapshot.get("text_excerpt")):
            issues.append(Issue("warning", "snapshot_excerpt_empty", "snapshot", "Snapshot has no text excerpt.", ref))
        digest = _text(snapshot.get("content_sha256"))
        if digest and not _SHA256_RE.match(digest):
            issues.append(Issue("warning", "snapshot_hash_invalid", "snapshot", "content_sha256 is not a SHA-256 hex digest.", ref))
        elif digest:
            previous_source = seen_hashes.get(digest)
            if previous_source and previous_source != source_id:
                issues.append(
                    Issue(
                        "warning",
                        "snapshot_duplicate_content",
                        "snapshot",
                        "Identical content is registered under source '{}' and '{}'.".format(
                            previous_source, source_id
                        ),
                        ref,
                    )
                )
            seen_hashes.setdefault(digest, source_id)
        retrieved = _text(snapshot.get("retrieved_at"))
        if retrieved and _parse_datetime(retrieved) is None:
            issues.append(Issue("warning", "snapshot_timestamp_invalid", "snapshot", "retrieved_at is not an ISO timestamp.", ref))

        for title in snapshot.get("related_records") or []:
            if record_titles and isinstance(title, str) and title not in record_titles:
                issues.append(
                    Issue("info", "snapshot_unknown_record", "snapshot", "Snapshot references unknown record '{}'.".format(title), ref)
                )
        for match in snapshot.get("record_matches") or []:
            if isinstance(match, dict):
                confidence = match.get("confidence")
                if isinstance(confidence, (int, float)) and not 0 <= confidence <= 1:
                    issues.append(
                        Issue("warning", "snapshot_confidence_invalid", "snapshot", "record_matches confidence must be between 0 and 1.", ref)
                    )
    return issues


def _parse_date(value: str, allow_partial: bool = True) -> date | None:
    value = value.strip()
    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%d %B %Y",
        "%d. %B %Y",
        "%d %b %Y",
        "%B %d, %Y",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    if allow_partial:
        match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", value)
        if match:
            try:
                return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except ValueError:
                return None
    return None


def _parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


__all__ = [
    "CONFIRMED_CLASSES",
    "DISCLOSURE_CLASSES",
    "EVIDENCE_CLASSES",
    "Issue",
    "PUBLISHER_DOMAINS",
    "REPORTED_CLASSES",
    "host_matches",
    "sort_issues",
    "summarise",
    "url_problem",
    "validate_dossier",
    "validate_snapshots",
]
