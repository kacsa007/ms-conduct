"""Public-source collector for the Allianz Suisse dossier.

The collector is deliberately narrow: it fetches named documents from the
dossier's source register and extracts a passage tied to each linked finding.
It does not discover URLs and it does not assert findings.

Robustness notes (see README):

* transient HTTP and connection failures are retried with exponential backoff
  and ``Retry-After`` support, and every attempt is recorded;
* redirects, encodings, PDFs and binary payloads are handled explicitly;
* each linked record gets its own excerpt and match diagnostics, so one
  page cannot silently stand in for several unrelated findings;
* outputs are written atomically, previous runs are archived, and the last
  good snapshot set is retained if a run collects nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

from evidence import __version__ as EVIDENCE_VERSION
from evidence.io import write_json, write_jsonl
from evidence.matching import (
    allianz_mentioned,
    build_profiles,
    explain_no_match,
    find_excerpt,
)
from evidence.schema import summarise as summarise_issues
from evidence.schema import url_problem, validate_dossier
from evidence.signals import merge_reports, scan_text
from evidence.text import (
    decode_bytes,
    detect_content_kind,
    meaningful_characters,
    normalise_text,
)
from evidence.verification import build_report, now_iso

ROOT = Path(__file__).resolve().parents[1]
DOSSIER_PATH = ROOT / "data" / "dossier.json"
OUTPUT_DIR = ROOT / "data" / "discovery"
ARCHIVE_DIR = OUTPUT_DIR / "archive"

COLLECTOR_VERSION = "2.0.0"
USER_AGENT = "ms-conduct-public-research/2.0 (+https://github.com/kacsa007/ms-conduct)"
REQUEST_TIMEOUT = (8, 25)
MAX_BYTES = 20 * 1024 * 1024
MAX_EXCERPT = 1400
MAX_PDF_PAGES = 400
PAUSE_SECONDS = 1.0
MAX_ATTEMPTS = 3
BACKOFF_BASE = 1.5
MAX_BACKOFF = 20.0
ARCHIVE_LIMIT = 50

RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504, 522, 524})
BLOCKED_STATUSES = frozenset({401, 403, 451})

# These are discovery interfaces, not record-specific source documents.
SKIP_SOURCE_IDS = {
    "sec-edgar": "Search index; collect specific filings from the linked case sources instead.",
}

_ACCESS_CHALLENGE_MARKERS = (
    b"bm-verify=",
    b"/apology_objects/interstitial/",
    b"cf-chl-",
)
_GENERIC_CHALLENGE_MARKERS = (
    b"verify you are human",
    b"checking your browser",
    b"just a moment",
    b"attention required",
    b"enable javascript and cookies to continue",
)
_JS_SHELL_RE = re.compile(r"enable\s+javascript|javascript\s+is\s+(?:required|disabled)", re.IGNORECASE)
_BOILERPLATE_LINE_RE = re.compile(
    r"^(?:skip to (?:main )?content|jump to (?:main )?content|cookies?(?: policy| settings)?|"
    r"accept all(?: cookies)?|we use cookies|share (?:this|on)|follow us|sign up|subscribe|"
    r"menu|search|home|print|download|back to top)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CollectorConfig:
    dossier_path: Path = DOSSIER_PATH
    output_dir: Path = OUTPUT_DIR
    request_timeout: tuple[float, float] = REQUEST_TIMEOUT
    max_bytes: int = MAX_BYTES
    max_excerpt: int = MAX_EXCERPT
    max_pdf_pages: int = MAX_PDF_PAGES
    pause_seconds: float = PAUSE_SECONDS
    max_attempts: int = MAX_ATTEMPTS
    backoff_base: float = BACKOFF_BASE
    max_backoff: float = MAX_BACKOFF
    archive_limit: int = ARCHIVE_LIMIT
    user_agent: str = USER_AGENT

    @classmethod
    def from_env(cls) -> "CollectorConfig":
        def env_float(name: str, default: float) -> float:
            try:
                return float(os.environ[name])
            except (KeyError, ValueError):
                return default

        def env_int(name: str, default: int) -> int:
            try:
                return int(os.environ[name])
            except (KeyError, ValueError):
                return default

        return cls(
            max_bytes=max(4096, env_int("COLLECTOR_MAX_BYTES", MAX_BYTES)),
            max_excerpt=max(300, env_int("COLLECTOR_MAX_EXCERPT", MAX_EXCERPT)),
            pause_seconds=max(0.0, env_float("COLLECTOR_PAUSE_SECONDS", PAUSE_SECONDS)),
            max_attempts=max(1, env_int("COLLECTOR_MAX_ATTEMPTS", MAX_ATTEMPTS)),
            max_backoff=max(0.0, env_float("COLLECTOR_MAX_BACKOFF", MAX_BACKOFF)),
            archive_limit=max(0, env_int("COLLECTOR_ARCHIVE_LIMIT", ARCHIVE_LIMIT)),
        )


@dataclass
class FetchMeta:
    route: str = "primary"
    attempts: list[dict] = field(default_factory=list)
    elapsed: float = 0.0


def _backoff(config: CollectorConfig, attempt: int) -> float:
    delay = min(config.max_backoff, config.backoff_base * (2 ** (attempt - 1)))
    return delay + random.uniform(0, min(0.5, delay * 0.25))


def _retry_after_seconds(response) -> float | None:
    value = None
    try:
        value = response.headers.get("Retry-After")
    except AttributeError:
        return None
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        pass
    try:
        target = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())


def _header(response, name: str) -> str:
    try:
        return str(response.headers.get(name, "") or "")
    except AttributeError:
        return ""


def _close(response) -> None:
    try:
        response.close()
    except Exception:
        pass


def _read_limited(response, max_bytes: int) -> tuple[bytes | None, int, str]:
    body = bytearray()
    try:
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            if len(body) + len(chunk) > max_bytes:
                return None, len(body) + len(chunk), ""
            body.extend(chunk)
    except Exception as error:  # requests raises several streaming exceptions
        return None, len(body), type(error).__name__
    return bytes(body), len(body), ""


def _clean_container_text(container) -> str:
    for node in container.select(
        "script, style, noscript, svg, nav, header, footer, aside, form, iframe, button, "
        ".comments-area, #comments, .comment-list, .related, .newsletter, .cookie, "
        ".breadcrumb, .social, [aria-hidden='true']"
    ):
        node.decompose()
    lines = []
    for line in container.get_text("\n", strip=True).splitlines():
        line = line.strip()
        if not line or _BOILERPLATE_LINE_RE.match(line):
            continue
        lines.append(line)
    return normalise_text("\n".join(lines))


def extract_title(value: str, fallback: str) -> str:
    soup = BeautifulSoup(value, "html.parser")
    for selector, attribute in (
        ("meta[property='og:title']", "content"),
        ("meta[name='citation_title']", "content"),
        ("meta[name='dc.title']", "content"),
    ):
        node = soup.select_one(selector)
        if node and node.get(attribute):
            title = normalise_text(node.get(attribute))
            if title:
                return title[:300]
    heading = soup.find("h1")
    title = normalise_text(heading.get_text(" ", strip=True)) if heading else ""
    if not title and soup.title:
        title = normalise_text(soup.title.get_text(" ", strip=True))
    if title and " | " in title:
        parts = [part.strip() for part in title.split(" | ") if part.strip()]
        if parts and len(parts[0]) >= 15:
            title = parts[0]
    return title[:300] or fallback


def _extract_pdf(content: bytes, max_pages: int) -> tuple[str, dict]:
    diagnostics = {"pages": 0, "page_errors": 0, "encrypted": False, "title": ""}
    reader = PdfReader(BytesIO(content))
    diagnostics["encrypted"] = bool(getattr(reader, "is_encrypted", False))
    if diagnostics["encrypted"]:
        try:
            reader.decrypt("")
        except Exception:
            pass
    try:
        metadata = reader.metadata or {}
        diagnostics["title"] = normalise_text(str(metadata.get("/Title", "")))[:300]
    except Exception:
        pass
    pages = []
    total = len(reader.pages)
    diagnostics["pages"] = total
    for index in range(min(total, max_pages)):
        try:
            pages.append(reader.pages[index].extract_text() or "")
        except Exception:
            diagnostics["page_errors"] += 1
    return normalise_text("\n".join(pages)), diagnostics


def extract_document(
    content: bytes,
    content_type: str,
    max_pdf_pages: int = MAX_PDF_PAGES,
) -> tuple[str, dict]:
    kind = detect_content_kind(content, content_type)
    diagnostics = {"kind": kind, "encoding": "", "pages": 0, "page_errors": 0}
    if kind == "binary":
        return "", diagnostics
    if kind == "pdf":
        text, pdf_diagnostics = _extract_pdf(content, max_pdf_pages)
        diagnostics.update(pdf_diagnostics)
        return text, diagnostics
    text, encoding = decode_bytes(content, content_type)
    diagnostics["encoding"] = encoding
    if kind in ("html", "xml"):
        soup = BeautifulSoup(text, "html.parser")
        container = soup.find("main") or soup.find("article") or soup.body or soup
        diagnostics["title"] = extract_title(text, "")
        text = _clean_container_text(container)
    else:
        text = normalise_text(text)
    return text, diagnostics


def _is_access_challenge(content: bytes, text: str) -> bool:
    sample = content[:16000].lower()
    if any(marker in sample for marker in _ACCESS_CHALLENGE_MARKERS):
        return True
    # Generic phrases also occur in articles; only treat them as a challenge
    # when the page has almost no content of its own.
    if len(text) < 4000 and any(marker in sample for marker in _GENERIC_CHALLENGE_MARKERS):
        return True
    if len(text) < 700 and _JS_SHELL_RE.search(text):
        return True
    return False


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _wait_for_host(host: str, last_request: dict[str, float], pause_seconds: float, sleep) -> None:
    if pause_seconds <= 0 or not host:
        return
    previous = last_request.get(host)
    if previous is not None:
        remaining = pause_seconds - (time.monotonic() - previous)
        if remaining > 0:
            sleep(remaining)
    last_request[host] = time.monotonic()


def _request_with_retries(
    session,
    url: str,
    route: str,
    config: CollectorConfig,
    meta: FetchMeta,
    sleep,
    last_request: dict[str, float],
):
    """Return ``(response, error_code, error_detail)``.

    ``response`` is an open response for a non-retryable status; the caller
    owns closing it. Transient failures are retried up to
    ``config.max_attempts``.
    """

    error_code = ""
    error_detail = ""
    for attempt in range(1, config.max_attempts + 1):
        _wait_for_host(_host(url), last_request, config.pause_seconds, sleep)
        try:
            response = session.get(
                url,
                timeout=config.request_timeout,
                stream=True,
                allow_redirects=True,
                headers={"User-Agent": config.user_agent},
            )
        except requests.RequestException as error:
            error_code = "request_error"
            error_detail = type(error).__name__
            meta.attempts.append(
                {"route": route, "attempt": attempt, "error": type(error).__name__}
            )
        except Exception as error:  # a broken adapter should not abort the run
            error_code = "request_error"
            error_detail = type(error).__name__
            meta.attempts.append({"route": route, "attempt": attempt, "error": type(error).__name__})
        else:
            status_code = int(getattr(response, "status_code", 0) or 0)
            meta.attempts.append(
                {"route": route, "attempt": attempt, "http_status": status_code}
            )
            if status_code in RETRY_STATUSES:
                retry_after = _retry_after_seconds(response)
                _close(response)
                if attempt >= config.max_attempts:
                    error_code = "rate_limited" if status_code == 429 else "retry_exhausted"
                    error_detail = "HTTP {}".format(status_code)
                    break
                delay = max(_backoff(config, attempt), retry_after or 0.0)
                meta.attempts[-1]["retry_after_seconds"] = round(delay, 2)
                sleep(min(delay, config.max_backoff))
                continue
            return response, "", ""

        if attempt >= config.max_attempts:
            break
        delay = _backoff(config, attempt)
        meta.attempts[-1]["retry_after_seconds"] = round(delay, 2)
        sleep(delay)

    return None, error_code or "request_error", error_detail


def fetch_source(
    session,
    source: dict,
    records: list[dict],
    profiles,
    config: CollectorConfig,
    sleep=time.sleep,
    last_request: dict[str, float] | None = None,
) -> tuple[dict, dict | None]:
    started = time.monotonic()
    last_request = last_request if last_request is not None else {}
    meta = FetchMeta()
    source_id = str(source.get("id") or "")
    status = {
        "source_id": source_id,
        "source": source.get("publisher", ""),
        "title": source.get("title", ""),
        "url": source.get("url", ""),
        "alternate_url": source.get("alternate_url", ""),
        "status": "request_error",
        "snapshot_count": 0,
        "attempts": meta.attempts,
    }

    primary_url = str(source.get("url") or "").strip()
    url_code, url_message = url_problem(primary_url)
    if url_code:
        status.update({"status": "invalid_url", "note": "Source URL rejected: {}".format(url_message)})
        status["elapsed_seconds"] = round(time.monotonic() - started, 2)
        return status, None

    routes = [("primary", primary_url)]
    fallback_url = str(source.get("fallback_url") or "").strip()
    if fallback_url and fallback_url != primary_url and not url_problem(fallback_url)[0]:
        routes.append(("fallback", fallback_url))
    elif fallback_url and fallback_url != primary_url:
        status["fallback_rejected"] = url_problem(fallback_url)[1]

    response = None
    route_used = ""
    final_error = ""
    for route, url in routes:
        meta.route = route
        response, error_code, error_detail = _request_with_retries(
            session, url, route, config, meta, sleep, last_request
        )
        if response is not None:
            status_code = int(getattr(response, "status_code", 0) or 0)
            if status_code >= 400 and route == "primary" and len(routes) > 1:
                meta.attempts.append(
                    {"route": route, "fallback_after_http_status": status_code}
                )
                _close(response)
                response = None
                continue
            route_used = route
            break
        final_error = error_code
        if route == "primary" and len(routes) > 1:
            continue
        status["status"] = error_code or "request_error"
        status["note"] = (
            "The source could not be retrieved after {} attempt(s) ({}).".format(
                config.max_attempts, error_detail or error_code
            )
        )
        status["elapsed_seconds"] = round(time.monotonic() - started, 2)
        return status, None

    if response is None:
        status["status"] = final_error or "request_error"
        status["note"] = "The primary and fallback official URLs could not be retrieved."
        status["elapsed_seconds"] = round(time.monotonic() - started, 2)
        return status, None

    try:
        status_code = int(getattr(response, "status_code", 0) or 0)
        status["http_status"] = status_code
        if status_code in BLOCKED_STATUSES:
            status["status"] = "blocked"
            status["note"] = "The official source denied automated access (HTTP {}).".format(status_code)
            return status, None
        if status_code == 429:
            status["status"] = "rate_limited"
            status["note"] = "The source returned HTTP 429. Retry during a later collection run."
            return status, None
        if status_code >= 400:
            status["status"] = "http_error"
            status["note"] = "The official source returned HTTP {}.".format(status_code)
            return status, None

        final_url = str(getattr(response, "url", "") or "")
        if final_url and url_problem(final_url)[0]:
            status["status"] = "invalid_redirect"
            status["note"] = "The source redirected to a URL that is not a public http(s) address."
            return status, None

        content_length_text = _header(response, "content-length")
        try:
            content_length = int(content_length_text or 0)
        except ValueError:
            content_length = 0
        if content_length > config.max_bytes:
            status["status"] = "skipped_large"
            status["note"] = "Source exceeds the {} MB download limit.".format(config.max_bytes // (1024 * 1024))
            return status, None

        content, actual_length, read_error = _read_limited(response, config.max_bytes)
        if content is None:
            if read_error:
                status["status"] = "request_error"
                status["note"] = "The response body could not be read ({}).".format(read_error)
            else:
                status["status"] = "skipped_large"
                status["note"] = "Source exceeds the {} MB download limit.".format(config.max_bytes // (1024 * 1024))
            status["content_length"] = actual_length
            return status, None

        content_type = _header(response, "content-type")
        status["content_type"] = content_type
        status["content_length"] = actual_length

        try:
            text, diagnostics = extract_document(content, content_type, config.max_pdf_pages)
        except Exception as error:
            status["status"] = "extraction_error"
            status["note"] = "Source downloaded, but text extraction failed ({}).".format(
                type(error).__name__
            )
            return status, None
        status["extraction"] = diagnostics
        if diagnostics.get("kind") == "binary":
            status["status"] = "unsupported_content"
            status["note"] = "The source is a binary document that this collector cannot read as text."
            return status, None
        if _is_access_challenge(content, text):
            status["status"] = "access_challenge"
            status["note"] = "The server returned an automated-access challenge instead of the source document."
            return status, None
        if meaningful_characters(text) < 200:
            status["status"] = "no_extractable_text"
            status["note"] = "The download contained too little readable text to check against a finding."
            return status, None

        records_for_source = [record for record in records if isinstance(record, dict)]
        if not records_for_source:
            status["status"] = "no_linked_record"
            status["note"] = (
                "Source is listed in the register but linked to no finding; no record-specific excerpt was stored."
            )
            return status, None
        matches = []
        diagnostics_by_record = {}
        for record in records_for_source:
            record_id = str(record.get("id") or "")
            profile = profiles.get(record_id)
            if profile is None:
                continue
            result = find_excerpt(
                text,
                profile,
                source,
                max_characters=config.max_excerpt,
                anchor_scope_single=len(records_for_source) == 1,
            )
            if result is None:
                diagnostics_by_record[record_id] = explain_no_match(text, profile)
                continue
            match = result.as_dict()
            match["record_id"] = record_id
            match["record_title"] = record.get("title", "")
            matches.append(match)

        if not matches:
            status["status"] = "no_relevant_text"
            status["note"] = "The page loaded, but no excerpt matched a linked finding."
            status["match_diagnostics"] = diagnostics_by_record
            return status, None

        matches.sort(key=lambda item: (-item.get("confidence", 0), -item.get("score", 0)))
        best = matches[0]
        excerpts = []
        reports = []
        for match in matches:
            if match["excerpt"] not in excerpts:
                excerpts.append(match["excerpt"])
                reports.append(scan_text(match["excerpt"], source.get("source_type", "")))
        merged = merge_reports(reports)
        page_report = scan_text(text[:400_000], source.get("source_type", ""))
        matched_snippets = {signal.snippet for signal in merged.signals}
        page_signals = [
            signal.as_dict()
            for signal in page_report.signals
            if signal.polarity == "asserted"
            and signal.weight >= 4
            and allianz_mentioned(signal.snippet)
            and signal.snippet not in matched_snippets
        ][:10]

        matched_records = [match for match in matches]
        entities = sorted(
            {
                str(record.get("entity") or "")
                for record in records_for_source
                if str(record.get("id") or "") in {match["record_id"] for match in matched_records}
                and record.get("entity")
            }
        )
        title = diagnostics.get("title") or str(source.get("title") or "")

        snapshot = {
            "id": source_id,
            "source": source.get("publisher", ""),
            "entity": "; ".join(entities),
            "title": title,
            "date": "",
            "url": primary_url,
            "retrieved_url": final_url or primary_url,
            "source_type": source.get("source_type", ""),
            "source_use": source.get("use", ""),
            "related_records": [match["record_title"] for match in matches],
            "retrieved_at": now_iso(),
            "content_type": content_type,
            "content_sha256": hashlib.sha256(content).hexdigest(),
            "extracted_characters": len(text),
            "text_excerpt": best["excerpt"],
            "record_matches": matches,
            "match_quality": round(float(best.get("confidence", 0)), 3),
            "signals": merged.as_dict(),
            "page_signals": page_signals,
            "evidence_status": "source_snapshot",
        }

        used_alternate = route_used == "fallback"
        status.update(
            {
                "status": "fetched_alternate" if used_alternate else "fetched",
                "extracted_characters": len(text),
                "meaningful_characters": meaningful_characters(text),
                "snapshot_count": 1,
                "match_count": len(matches),
                "match_quality": snapshot["match_quality"],
                "signal_status": merged.verification_status,
                "signal_score": merged.severity_score,
            }
        )
        if used_alternate:
            status["note"] = "The primary URL was unavailable; the official fallback document was fetched."
        return status, snapshot
    finally:
        _close(response)
        status["attempts"] = meta.attempts
        status["elapsed_seconds"] = round(time.monotonic() - started, 2)


def archive_previous(path: Path, archive_dir: Path, prefix: str, limit: int) -> str | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = archive_dir / "{}_{}{}".format(prefix, stamp, path.suffix)
    suffix = 1
    while target.exists():
        target = archive_dir / "{}_{}_{}{}".format(prefix, stamp, suffix, path.suffix)
        suffix += 1
    shutil.copy2(path, target)
    archived = sorted(archive_dir.glob("{}_*{}".format(prefix, path.suffix)))
    for old in archived[:-limit] if limit > 0 else []:
        try:
            old.unlink()
        except OSError:
            pass
    return target.name


def build_session(config: CollectorConfig):
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": config.user_agent,
            "Accept": "text/html, application/xhtml+xml, application/pdf;q=0.9, */*;q=0.8",
            "Accept-Language": "en,de;q=0.8,fr;q=0.6",
        }
    )
    adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=8, max_retries=0)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _write_outputs(
    config: CollectorConfig,
    statuses: list[dict],
    snapshots: list[dict],
    dossier: dict,
    started_at: float,
) -> dict:
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = output_dir / "source_snapshots.jsonl"
    status_path = output_dir / "run_status_latest.json"
    archive_dir = output_dir / "archive"

    archive_error = ""
    try:
        archived_snapshots = archive_previous(snapshot_path, archive_dir, "discovery", config.archive_limit)
        archived_status = archive_previous(status_path, archive_dir, "run_status", config.archive_limit)
    except OSError as error:
        archived_snapshots = archived_status = None
        archive_error = "{}: {}".format(type(error).__name__, error)

    retained = False
    existing_snapshots = _load_existing_snapshots(snapshot_path)
    existing_count = len(existing_snapshots)
    if snapshots:
        write_jsonl(snapshot_path, snapshots)
    elif existing_count:
        retained = True

    verification_error = ""
    try:
        report = build_report(dossier, snapshots if snapshots else existing_snapshots)
    except Exception as error:  # keep the run status even if the report fails
        verification_error = "{}: {}".format(type(error).__name__, error)
        report = {
            "generated_at": now_iso(),
            "summary": {
                "records": 0,
                "records_supported": 0,
                "records_with_warnings": 0,
                "leads": 0,
                "integrity": summarise_issues(validate_dossier(dossier)),
            },
            "integrity": {"summary": summarise_issues(validate_dossier(dossier)), "issues": []},
            "records": [],
            "leads": [],
            "missing_source_ids": [],
            "notice": "Verification report could not be generated; see verification_error in run status.",
        }
    report_summary = report["summary"]
    status_counts: dict[str, int] = {}
    for item in statuses:
        key = str(item.get("status") or "unknown")
        status_counts[key] = status_counts.get(key, 0) + 1

    integrity = summarise_issues(validate_dossier(dossier))
    effective_snapshot_count = len(snapshots) if snapshots else (existing_count if retained else 0)
    run_status = {
        "retrieved_at": now_iso(),
        "collector_version": COLLECTOR_VERSION,
        "evidence_version": EVIDENCE_VERSION,
        "duration_seconds": round(time.monotonic() - started_at, 2),
        "source_count": len(statuses),
        "snapshot_count": effective_snapshot_count,
        "new_snapshot_count": len(snapshots),
        "output_file": snapshot_path.name,
        "status_counts": status_counts,
        "integrity": integrity,
        "verification": report_summary,
        "note": (
            "Sources are collected from the dossier's named source register. Access failures are "
            "preserved as status rows; a run that collects nothing keeps the previous snapshot set."
        ),
        "snapshots_retained": retained,
        "archived": [name for name in (archived_snapshots, archived_status) if name],
        "verification_error": verification_error,
        "archive_error": archive_error,
        "sources": statuses,
    }
    write_json(status_path, run_status)
    write_json(output_dir / "verification_report.json", report)
    return run_status


def _load_existing_snapshots(path: Path) -> list[dict]:
    rows = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    except OSError:
        pass
    return rows


def collect(
    config: CollectorConfig | None = None,
    dossier: dict | None = None,
    session=None,
    sleep=time.sleep,
    verbose: bool = True,
) -> int:
    config = config or CollectorConfig.from_env()
    started_at = time.monotonic()

    if dossier is None:
        try:
            dossier = json.loads(config.dossier_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            print("Dossier could not be read: {}".format(error))
            return 2
    if not isinstance(dossier, dict):
        print("Dossier root must be a JSON object.")
        return 2

    issues = validate_dossier(dossier)
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        print("Dossier integrity errors must be fixed before collecting:")
        for issue in errors:
            print("  - {} ({}): {}".format(issue.code, issue.ref, issue.message))
        return 2
    warnings = [issue for issue in issues if issue.severity == "warning"]
    if warnings and verbose:
        print("Dossier warnings ({}): collection continues.".format(len(warnings)))
        for issue in warnings:
            print("  - {} ({}): {}".format(issue.code, issue.ref, issue.message))

    records = [record for record in dossier.get("records") or [] if isinstance(record, dict)]
    sources = [source for source in dossier.get("sources") or [] if isinstance(source, dict)]
    profiles = build_profiles(records)
    session = session or build_session(config)
    last_request: dict[str, float] = {}
    statuses: list[dict] = []
    snapshots: list[dict] = []

    for source in sources:
        source_id = str(source.get("id") or "")
        records_for_source = [
            record for record in records if source_id in (record.get("source_ids") or [])
        ]
        if source_id in SKIP_SOURCE_IDS:
            statuses.append(
                {
                    "source_id": source_id,
                    "source": source.get("publisher", ""),
                    "title": source.get("title", ""),
                    "url": source.get("url", ""),
                    "alternate_url": source.get("alternate_url", ""),
                    "status": "skipped_index",
                    "snapshot_count": 0,
                    "note": SKIP_SOURCE_IDS[source_id],
                }
            )
            continue
        try:
            status, snapshot = fetch_source(
                session,
                source,
                records_for_source,
                profiles,
                config,
                sleep=sleep,
                last_request=last_request,
            )
        except Exception as error:  # never lose the rest of the run to one source
            status = {
                "source_id": source_id,
                "source": source.get("publisher", ""),
                "title": source.get("title", ""),
                "url": source.get("url", ""),
                "status": "internal_error",
                "snapshot_count": 0,
                "note": "Collector error: {}".format(type(error).__name__),
            }
            snapshot = None
        statuses.append(status)
        if snapshot:
            snapshots.append(snapshot)
        if verbose:
            print(
                "{:<32} {:<18} {}".format(
                    source_id, status.get("status", ""), status.get("note", "")
                )
            )

    run_status = _write_outputs(config, statuses, snapshots, dossier, started_at)
    if verbose:
        print(
            json.dumps(
                {
                    "source_count": run_status["source_count"],
                    "snapshot_count": run_status["snapshot_count"],
                    "status_counts": run_status["status_counts"],
                    "verification": run_status["verification"],
                    "output_dir": str(config.output_dir),
                },
                indent=2,
            )
        )
    return 0 if snapshots else 1


def main() -> int:
    return collect(CollectorConfig.from_env())


if __name__ == "__main__":
    raise SystemExit(main())
