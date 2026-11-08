import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, urlencode

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "discovery"
USER_AGENT = "ms-conduct-public-research/1.0 (local public-source collector)"
REQUEST_TIMEOUT = (8, 25)
MAX_BYTES = 20 * 1024 * 1024
MAX_EXCERPT = 700

DEFAULT_TERMS = ["Allianz Suisse", "Allianz Global Investors", "Structured Alpha"]
START_DATE = "2010-01-01"

STATIC_SOURCES = [
    {
        "source": "ASIC",
        "entity": "Allianz Australia Insurance Limited; AWP Australia Pty Ltd",
        "title_hint": "ASIC official conviction notice on Allianz and AWP travel insurance",
        "url": "https://www.asic.gov.au/about-asic/news-centre/find-a-media-release/2025-releases/25-028mr-allianz-and-awp-convicted-and-fined-168-million-for-making-false-or-misleading-statements",
    },
    {
        "source": "CSSF",
        "entity": "Allianz Global Investors GmbH, Luxembourg Branch",
        "title_hint": "CSSF administrative sanction for AML/CFT deficiencies",
        "url": "https://www.cssf.lu/wp-content/uploads/S_88_GFI_Allianz_Global_Investors_GmbH_Zweigniederlassung_Luxembourg_2022.03.25_en.pdf",
    },
    {
        "source": "DOJ",
        "entity": "Allianz Global Investors U.S. LLC",
        "title_hint": "DOJ Structured Alpha sentencing release",
        "note": "A 200 response without matching page text is treated as unavailable, not as a negative search result.",
        "no_text_is_unavailable": True,
        "url": "https://www.justice.gov/usao-sdny/pr/allianz-global-investors-us-sentenced-connection-multibillion-dollar-fraud-scheme",
    },
    {
        "source": "FINMA",
        "entity": "Allianz Suisse; Allianz entities",
        "title_hint": "FINMA enforcement reporting and anonymised case databases",
        "note": "General enforcement landing page only. FINMA records are anonymised; this page is not an Allianz-specific result.",
        "url": "https://www.finma.ch/en/documentation/enforcement-reporting/",
    },
    {
        "source": "Swiss Federal Supreme Court",
        "entity": "Allianz Suisse",
        "title_hint": "Official Swiss Federal Supreme Court search for Allianz Suisse",
        "url": "https://search.bger.ch/ext/eurospider/live/de/php/aza/http/index.php?" + urlencode(
            {
                "azaclir": "aza",
                "query_words": '"Allianz Suisse"',
                "from_date": "2010-01-01",
                "to_date": datetime.now(timezone.utc).date().isoformat(),
                "sort": "relevance",
                "rank": "0",
                "top_subcollection_aza": "all",
                "type": "simple_query",
            }
        ),
    },
    {
        "source": "Allianz Suisse",
        "entity": "Allianz Suisse; Swiss HIS operator",
        "title_hint": "Allianz Suisse HIS policy and data-rights disclosure",
        "url": "https://www.allianz.ch/de/informationen/his.html",
    },
    {
        "source": "Allianz SE",
        "entity": "Allianz Group",
        "title_hint": "Allianz Group Annual Report 2025",
        "url": "https://www.allianz.com/content/dam/onemarketing/azcom/Allianz_com/investor-relations/en/results-reports/annual-report/ar-2025/en-allianz-group-annual-report-2025.pdf",
    },
    {
        "source": "Allianz Suisse",
        "entity": "Allianz Suisse Versicherungs-Gesellschaft AG",
        "title_hint": "Allianz Suisse 2025 report on financial condition",
        "url": "https://www.allianz.ch/content/dam/onemarketing/azch/common/allianz/de/medienberichte/Bericht_%C3%BCber_die_Finanzlage_ASV_2025.pdf",
    },
]


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def candidate_id(url, title):
    return hashlib.sha256((url + "\n" + title).encode("utf-8")).hexdigest()[:20]


def compact_excerpt(content, terms):
    plain = re.sub(r"\s+", " ", content).strip()
    if not plain:
        return ""
    lower = plain.casefold()
    positions = [lower.find(term.casefold()) for term in terms if term and lower.find(term.casefold()) >= 0]
    start = max(0, min(positions) - 180) if positions else 0
    excerpt = plain[start : start + MAX_EXCERPT]
    if start:
        excerpt = "…" + excerpt
    if start + MAX_EXCERPT < len(plain):
        excerpt += "…"
    return excerpt


def matched_terms(content, terms):
    lower = content.casefold()
    return [term for term in terms if term.casefold() in lower]


def read_limited(response):
    content = bytearray()
    for chunk in response.iter_content(chunk_size=65536):
        if not chunk:
            continue
        if len(content) + len(chunk) > MAX_BYTES:
            return None, len(content) + len(chunk)
        content.extend(chunk)
    return bytes(content), len(content)


def response_text(content, content_type=""):
    content_type = content_type.lower()
    if "pdf" in content_type or content[:4] == b"%PDF":
        try:
            reader = PdfReader(BytesIO(content))
            return " ".join(page.extract_text() or "" for page in reader.pages)
        except Exception as error:
            return "[PDF downloaded; text extraction failed: {}]".format(error)
    soup = BeautifulSoup(content, "html.parser")
    for node in soup(["script", "style", "noscript", "svg"]):
        node.decompose()
    return soup.get_text(" ", strip=True)


def page_title(content, fallback):
    soup = BeautifulSoup(content, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    return title[:300] or fallback


def as_candidate(source, entity, title, url, excerpt, terms, retrieved_at):
    return {
        "id": candidate_id(url, title),
        "source": source,
        "entity": entity,
        "title": title,
        "date": "",
        "url": url,
        "retrieved_at": retrieved_at,
        "text_excerpt": excerpt[:MAX_EXCERPT],
        "matched_terms": terms,
        "evidence_status": "candidate_unreviewed",
    }


def sec_candidates(session, terms, start_date, end_date, limit, pause):
    output = []
    status = {"source": "SEC EDGAR", "status": "ok", "queries": []}
    endpoint = "https://efts.sec.gov/LATEST/search-index"
    for term in terms:
        params = {
            "q": '"{}"'.format(term),
            "dateRange": "custom",
            "startdt": start_date,
            "enddt": end_date,
            "from": 0,
            "size": limit,
        }
        url = endpoint + "?" + urlencode(params)
        query_status = {"term": term, "url": url}
        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT)
            query_status["http_status"] = response.status_code
            response.raise_for_status()
            payload = response.json()
            hits = payload.get("hits", {}).get("hits", [])
            total_hits = payload.get("hits", {}).get("total", {})
            query_status["result_count"] = total_hits.get("value", len(hits)) if isinstance(total_hits, dict) else total_hits
            query_status["result_relation"] = total_hits.get("relation", "eq") if isinstance(total_hits, dict) else "eq"
            query_status["api_returned_count"] = len(hits)
            hits = hits[:limit]
            query_status["returned_count"] = len(hits)
            if query_status["result_relation"] == "gte" or query_status["result_count"] > len(hits):
                query_status["note"] = "First {} results stored; EDGAR reports more matching hits.".format(len(hits))
            for hit in hits:
                source_data = hit.get("_source", {})
                display_names = source_data.get("display_names") or []
                filer = source_data.get("entity_name") or (display_names[0] if display_names else "Filer name unavailable")
                document_name = hit.get("_id", "").split(":", 1)[-1]
                title = source_data.get("file_description") or source_data.get("file_type") or source_data.get("form") or document_name or term
                filed = source_data.get("file_date") or source_data.get("period_ending") or ""
                adsh = source_data.get("adsh", "")
                ciks = source_data.get("ciks") or []
                cik = str(source_data.get("cik") or (ciks[0] if ciks else "")).lstrip("0")
                if cik and adsh:
                    archive_path = "https://www.sec.gov/Archives/edgar/data/{}/{}".format(cik, adsh.replace("-", ""))
                    filing_url = archive_path + "/{}".format(quote(document_name)) if document_name else archive_path + "/{}-index.htm".format(adsh)
                    index_url = archive_path + "/{}-index.htm".format(adsh)
                else:
                    filing_url = url
                    index_url = url
                output.append(
                    {
                        **as_candidate("SEC EDGAR", filer, title, filing_url, "", [term], now_iso()),
                        "date": filed,
                        "form": source_data.get("form", ""),
                        "accession": adsh,
                        "document_name": document_name,
                        "filing_index_url": index_url,
                        "match_note": "SEC EDGAR full-text search reported this phrase match. The scraper did not fetch filing text; verify the match in the linked filing.",
                    }
                )
        except Exception as error:
            query_status["error"] = "{}: {}".format(type(error).__name__, error)
            status["status"] = "partial"
        status["queries"].append(query_status)
        time.sleep(pause)
    return output, status


def static_candidates(session, terms, pause):
    output = []
    statuses = []
    for source in STATIC_SOURCES:
        item_status = {"source": source["source"], "url": source["url"], "status": "error"}
        response = None
        try:
            response = session.get(source["url"], timeout=REQUEST_TIMEOUT, stream=True)
            item_status["http_status"] = response.status_code
            response.raise_for_status()
            content_length = int(response.headers.get("content-length", "0") or 0)
            if content_length > MAX_BYTES:
                item_status["status"] = "skipped_large"
                item_status["content_length"] = content_length
            else:
                content, actual_length = read_limited(response)
                if content is None:
                    item_status["status"] = "skipped_large"
                    item_status["content_length"] = actual_length
                else:
                    text = response_text(content, response.headers.get("content-type", ""))
                    title = page_title(content, source["title_hint"])
                    found = matched_terms(text, terms)
                    excerpt = compact_excerpt(text, terms) if found else ""
                    if found:
                        output.append(
                            as_candidate(
                                source["source"],
                                source["entity"],
                                title or source["title_hint"],
                                source["url"],
                                excerpt,
                                found,
                                now_iso(),
                            )
                        )
                        item_status["status"] = "ok"
                    else:
                        item_status["status"] = "unverified_no_match" if source.get("no_text_is_unavailable") else "no_matching_terms"
                        item_status["note"] = source.get(
                            "note", "Page loaded, but none of the requested terms appeared in extracted text."
                        )
                    item_status["title"] = title
                    item_status["matched_terms"] = found
                    item_status["excerpt_chars"] = len(excerpt)
        except Exception as error:
            item_status["error"] = "{}: {}".format(type(error).__name__, error)
        finally:
            if response is not None:
                response.close()
        statuses.append(item_status)
        time.sleep(pause)
    return output, statuses


def write_jsonl(path, candidates):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output_file:
        for candidate in candidates:
            output_file.write(json.dumps(candidate, ensure_ascii=False) + "\n")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Collect public Allianz-related source leads for manual review.")
    parser.add_argument("--terms", nargs="+", default=DEFAULT_TERMS, help="Search terms for SEC EDGAR EFTS.")
    parser.add_argument("--start-date", default=START_DATE, help="SEC search start date in YYYY-MM-DD format.")
    parser.add_argument("--end-date", default=datetime.now(timezone.utc).date().isoformat(), help="SEC search end date in YYYY-MM-DD format.")
    parser.add_argument("--limit-per-term", type=int, default=20, help="Maximum SEC EFTS results per term.")
    parser.add_argument("--pause", type=float, default=1.0, help="Seconds between requests.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT, help="Output directory for JSONL candidates and run status.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    retrieved_at = now_iso()
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/html, application/pdf;q=0.9, */*;q=0.8",
        }
    )

    sec_results, sec_status = sec_candidates(
        session, args.terms, args.start_date, args.end_date, max(1, min(args.limit_per_term, 100)), max(0.5, args.pause)
    )
    static_results, static_status = static_candidates(session, args.terms, max(0.5, args.pause))

    deduplicated = {}
    for candidate in sec_results + static_results:
        deduplicated[candidate["id"]] = candidate
    candidates = list(deduplicated.values())

    sec_status["candidate_count"] = sum(item.get("source") == "SEC EDGAR" for item in candidates)
    for source_status in static_status:
        source_status["candidate_count"] = sum(
            item.get("url") == source_status.get("url") for item in candidates
        )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = args.output_dir / "discovery_{}.jsonl".format(stamp)
    status_path = args.output_dir / "run_status_{}.json".format(stamp)
    write_jsonl(output_path, candidates)
    run_status = {
        "run_id": stamp,
        "retrieved_at": retrieved_at,
        "terms": args.terms,
        "window": {"start": args.start_date, "end": args.end_date},
        "candidate_count": len(candidates),
        "output_file": output_path.name,
        "evidence_note": "Every item is an unreviewed discovery lead. A match or excerpt is not a finding; verify the source and entity before using it.",
        "sources": [sec_status] + static_status,
    }
    status_path.write_text(json.dumps(run_status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"candidate_file": str(output_path), "status_file": str(status_path), "candidate_count": len(candidates)}, indent=2))
    for item in run_status["sources"]:
        if item.get("status") not in ("ok",):
            print("source status: {}".format(json.dumps(item, ensure_ascii=False)), file=sys.stderr)


if __name__ == "__main__":
    main()
