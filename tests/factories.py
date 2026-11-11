"""Small, realistic dossier fixtures used by the offline test suite."""

from __future__ import annotations

import copy


def record(**overrides):
    value = {
        "id": "asic-travel",
        "sort_date": "2025-02-28",
        "date_label": "2016–2018 conduct · convictions 28 February 2025",
        "category": "Insurance sales disclosures",
        "evidence_class": "Regulatory or criminal outcome",
        "geography": "Australia",
        "title": "Allianz Australia and AWP convicted over travel-insurance statements",
        "entity": "Allianz Australia Insurance Limited and AWP Australia Pty Ltd",
        "summary": (
            "The NSW Supreme Court convicted the companies after misleading online "
            "information overstated travel-insurance benefits."
        ),
        "details": ["The court imposed criminal fines of AUD 13.5 million on Allianz Australia."],
        "source_ids": ["asic-travel"],
        "tags": ["Insurance", "Criminal"],
    }
    value.update(copy.deepcopy(overrides))
    return value


def source(**overrides):
    value = {
        "id": "asic-travel",
        "publisher": "Australian Securities and Investments Commission",
        "title": "Allianz and AWP convicted and fined AUD 16.8 million",
        "url": "https://www.asic.gov.au/about-asic/news-centre/find-a-media-release/2025-releases/25-028mr-allianz-and-awp-convicted",
        "source_type": "Primary · regulator and court outcome",
        "use": "Convictions, conduct period and company-specific fines.",
    }
    value.update(copy.deepcopy(overrides))
    return value


def dossier(records=None, sources=None, steps=None):
    return {
        "reviewed_on": "8 November 2026",
        "records": copy.deepcopy(records) if records is not None else [record()],
        "research_steps": copy.deepcopy(steps) if steps is not None else [],
        "sources": copy.deepcopy(sources) if sources is not None else [source()],
    }


def html_page(body: str, title: str = "Allianz and AWP convicted | ASIC") -> bytes:
    return (
        "<!doctype html><html><head><title>{title}</title></head><body>"
        "<nav>Menu Home Contact Subscribe</nav>"
        "<header>Skip to main content</header>"
        "<main><h1>{title}</h1><p>{body}</p></main>"
        "<footer>Footer links and cookies</footer></body></html>"
    ).format(title=title, body=body).encode("utf-8")
