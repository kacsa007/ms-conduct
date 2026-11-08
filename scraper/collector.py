import hashlib
import json
import re
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
DOSSIER_PATH = ROOT / "data" / "dossier.json"
OUTPUT_DIR = ROOT / "data" / "discovery"
USER_AGENT = "ms-conduct-public-research/1.0"
REQUEST_TIMEOUT = (8, 25)
MAX_BYTES = 20 * 1024 * 1024
MAX_EXCERPT = 1400
PAUSE_SECONDS = 1.0

# These are discovery interfaces, not record-specific source documents.
SKIP_SOURCE_IDS = {
    "sec-edgar": "Search index; collect specific filings from the linked case sources instead.",
}


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_limited(response):
    body = bytearray()
    for chunk in response.iter_content(chunk_size=65536):
        if not chunk:
            continue
        if len(body) + len(chunk) > MAX_BYTES:
            return None, len(body) + len(chunk)
        body.extend(chunk)
    return bytes(body), len(body)


def extract_text(content, content_type):
    if "pdf" in content_type.lower() or content[:4] == b"%PDF":
        reader = PdfReader(BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    soup = BeautifulSoup(content, "html.parser")
    container = soup.find("main") or soup.find("article") or soup.body or soup
    for node in container.select(
        "script, style, noscript, svg, nav, footer, aside, .comments-area, #comments, .comment-list"
    ):
        node.decompose()
    return container.get_text("\n", strip=True)


def extract_title(content, fallback):
    soup = BeautifulSoup(content, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if not title:
        heading = soup.find("h1")
        title = heading.get_text(" ", strip=True) if heading else ""
    return title[:300] or fallback


def related_records(source_id, records):
    return [record for record in records if source_id in record.get("source_ids", [])]


def target_terms(source, records):
    context = " ".join(
        [source.get("use", ""), " ".join(source.get("excerpt_anchors", []))]
        + [
            " ".join(
                [record.get("title", ""), record.get("summary", ""), " ".join(record.get("details", []))]
            )
            for record in records
        ]
    ).lower()
    return {word for word in re.findall(r"[a-z0-9€$£]{3,}", context) if not word.isdigit() and word not in {
        "the", "and", "for", "from", "with", "that", "this", "were", "was", "has", "have",
        "had", "into", "after", "before", "over", "under", "says", "said", "its", "their",
        "they", "them", "then", "than", "not", "does", "did", "are", "but", "all", "one",
        "more", "only", "both", "each", "such", "into", "about", "record", "source", "case",
        "allianz", "group", "annual", "report", "reports", "company", "disclosure", "published",
        "business", "management", "risk", "framework", "records", "including", "which", "where",
        "there", "other", "work", "under", "section", "table", "year", "years", "current",
        "information", "system", "systems", "describes", "described", "alternate", "category",
        "condition", "covers", "dashes", "describe", "entry", "every", "financial", "identifies",
        "includes", "index", "linked", "official", "pdf", "point", "related", "shows", "suisse",
        "swiss", "tracking",
    }}


def source_excerpt(content_text, source, records):
    if len(content_text.strip()) < 400:
        return ""
    anchored = []
    for anchor in source.get("excerpt_anchors", []):
        pattern = r"\s+".join(re.escape(part) for part in anchor.split())
        match = re.search(pattern, content_text, flags=re.IGNORECASE)
        if match:
            start = max(0, match.start() - 120)
            end = min(len(content_text), match.end() + 520)
            anchored.append(re.sub(r"\s+", " ", content_text[start:end]).strip())
    if anchored:
        return "\n\n".join(anchored)[:MAX_EXCERPT]
    terms = target_terms(source, records)
    sentences = [
        re.sub(r"\s+", " ", item).strip()
        for item in re.split(r"(?<=[.!?])\s+|\n+", content_text)
        if len(item.strip()) > 35
    ]
    if not sentences:
        return ""
    ranked = []
    for index, sentence in enumerate(sentences):
        words = set(re.findall(r"[a-z0-9€$£]{3,}", sentence.lower()))
        score = len(words & terms)
        density = score / max(1, len(words))
        ranked.append((score, density, index, sentence))
    best_score, _, best_index, _ = max(ranked, key=lambda row: (row[0], row[1], -row[2]))
    if best_score < 2:
        return ""
    nearby = sentences[max(0, best_index - 1) : best_index + 3]
    return " ".join(nearby)[:MAX_EXCERPT]


def is_access_challenge(content):
    sample = content[:12000].lower()
    markers = (
        b"bm-verify=",
        b"/apology_objects/interstitial/",
        b"verify you are human",
        b"checking your browser",
    )
    return any(marker in sample for marker in markers)


def fetch_source(session, source, records):
    status = {
        "source_id": source.get("id", ""),
        "source": source.get("publisher", ""),
        "title": source.get("title", ""),
        "url": source.get("url", ""),
        "alternate_url": source.get("alternate_url", ""),
        "status": "request_error",
        "snapshot_count": 0,
    }
    response = None
    attempts = []
    urls = [("primary", source["url"])]
    if source.get("fallback_url") and source["fallback_url"] != source["url"]:
        urls.append(("fallback", source["fallback_url"]))
    route_used = "primary"
    try:
        for route, url in urls:
            try:
                response = session.get(url, timeout=REQUEST_TIMEOUT, stream=True)
            except requests.RequestException as error:
                attempts.append({"route": route, "error": type(error).__name__})
                if route == "primary" and len(urls) > 1:
                    continue
                status["status"] = "request_error"
                status["note"] = "{} while retrieving the official source.".format(type(error).__name__)
                status["attempts"] = attempts
                return status, None

            attempts.append({"route": route, "http_status": response.status_code})
            status["http_status"] = response.status_code
            if response.status_code in (401, 403) and route == "primary" and len(urls) > 1:
                response.close()
                response = None
                continue
            if response.status_code in (401, 403):
                status["status"] = "blocked"
                status["note"] = "The official source denied automated access (HTTP {}).".format(response.status_code)
                status["attempts"] = attempts
                return status, None
            if response.status_code == 429:
                status["status"] = "rate_limited"
                status["note"] = "The source returned HTTP 429. Retry during a later collection run."
                status["attempts"] = attempts
                return status, None
            if response.status_code >= 400:
                if route == "primary" and len(urls) > 1:
                    response.close()
                    response = None
                    continue
                status["status"] = "http_error"
                status["note"] = "The official source returned HTTP {}.".format(response.status_code)
                status["attempts"] = attempts
                return status, None
            route_used = route
            break
        if response is None:
            status["status"] = "http_error"
            status["note"] = "The primary and fallback official URLs could not be retrieved."
            status["attempts"] = attempts
            return status, None

        content_length = int(response.headers.get("content-length", "0") or 0)
        if content_length > MAX_BYTES:
            status["status"] = "skipped_large"
            status["note"] = "Source exceeds the {} MB download limit.".format(MAX_BYTES // (1024 * 1024))
            return status, None

        content, actual_length = read_limited(response)
        if content is None:
            status["status"] = "skipped_large"
            status["note"] = "Source exceeds the {} MB download limit.".format(MAX_BYTES // (1024 * 1024))
            status["content_length"] = actual_length
            return status, None

        content_type = response.headers.get("content-type", "")
        status["attempts"] = attempts
        if is_access_challenge(content):
            status["status"] = "access_challenge"
            status["note"] = "The server returned an automated-access challenge instead of the source document."
            return status, None
        try:
            text = extract_text(content, content_type)
        except Exception as error:
            status["status"] = "extraction_error"
            status["note"] = "Source downloaded, but text extraction failed: {}.".format(type(error).__name__)
            return status, None

        excerpt = source_excerpt(text, source, records)
        if not excerpt:
            status["status"] = "no_relevant_text"
            status["note"] = "The page loaded, but its extracted text did not match the linked finding."
            return status, None

        linked_records = [record.get("title", "") for record in records]
        entities = sorted({record.get("entity", "") for record in records if record.get("entity")})
        snapshot = {
            "id": source.get("id", ""),
            "source": source.get("publisher", ""),
            "entity": "; ".join(entities),
            "title": extract_title(content, source.get("title", "")),
            "date": "",
            "url": source["url"],
            "retrieved_url": response.url,
            "source_type": source.get("source_type", ""),
            "source_use": source.get("use", ""),
            "related_records": linked_records,
            "retrieved_at": now_iso(),
            "content_type": content_type,
            "content_sha256": hashlib.sha256(content).hexdigest(),
            "extracted_characters": len(text),
            "text_excerpt": excerpt,
            "evidence_status": "source_snapshot",
        }
        used_alternate = route_used == "fallback"
        status.update(
            {
                "status": "fetched_alternate" if used_alternate else "fetched",
                "content_type": content_type,
                "content_length": actual_length,
                "extracted_characters": len(text),
                "snapshot_count": 1,
            }
        )
        if used_alternate:
            status["note"] = "The primary URL was unavailable; the official fallback document was fetched."
        return status, snapshot
    finally:
        if response is not None:
            response.close()


def main():
    dossier = json.loads(DOSSIER_PATH.read_text(encoding="utf-8"))
    records = dossier.get("records", [])
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html, application/pdf;q=0.9, */*;q=0.8",
        }
    )

    statuses = []
    snapshots = []
    for source in dossier.get("sources", []):
        source_records = related_records(source.get("id", ""), records)
        if source.get("id") in SKIP_SOURCE_IDS:
            statuses.append(
                {
                    "source_id": source.get("id", ""),
                    "source": source.get("publisher", ""),
                    "title": source.get("title", ""),
                    "url": source.get("url", ""),
                    "alternate_url": source.get("alternate_url", ""),
                    "status": "skipped_index",
                    "snapshot_count": 0,
                    "note": SKIP_SOURCE_IDS[source["id"]],
                }
            )
            continue
        item_status, snapshot = fetch_source(session, source, source_records)
        statuses.append(item_status)
        if snapshot:
            snapshots.append(snapshot)
        time.sleep(PAUSE_SECONDS)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = OUTPUT_DIR / "source_snapshots.jsonl"
    with snapshot_path.open("w", encoding="utf-8") as output_file:
        for snapshot in snapshots:
            output_file.write(json.dumps(snapshot, ensure_ascii=False) + "\n")

    run_status = {
        "retrieved_at": now_iso(),
        "source_count": len(statuses),
        "snapshot_count": len(snapshots),
        "output_file": snapshot_path.name,
        "note": "Sources are collected from the dossier's named source register. Access failures are preserved as status rows.",
        "sources": statuses,
    }
    (OUTPUT_DIR / "run_status_latest.json").write_text(
        json.dumps(run_status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"snapshot_file": str(snapshot_path), "source_count": len(statuses), "snapshot_count": len(snapshots)}, indent=2))


if __name__ == "__main__":
    main()
