"""Wrongdoing-signal detection with polarity and context guards.

Searching for words such as "fraud" or "fine" alone produces three kinds of
false positive that matter in a conduct dossier:

* a company denying an allegation ("no indication of money laundering"),
* a policy or training page that names a risk ("our anti-corruption
  programme"), and
* an acquittal, dismissal or overturned conviction.

Every rule therefore records a polarity — ``asserted``, ``alleged``,
``negated``, ``policy`` or ``exonerating`` — and the aggregate report keeps
asserted outcomes separate from mere allegations. A flag is a review lead,
never a finding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from .text import iter_sentences, normalise_text, truncate

MAX_SIGNALS = 30
_SNIPPET_LENGTH = 380
_PRIMARY_RE = re.compile(r"^\s*primary\b", re.IGNORECASE)

_NEGATION_RE = re.compile(
    r"\b(?:no|not|never|none|nobody|nothing|without|denies|denied|deny|"
    r"refutes?|refuted|dismisses|dismissed|rejects?|rejected|"
    r"keine[mnrs]?|kein|nicht|nie|ohne|widerlegt|bestreitet|verneint|"
    r"aucun(?:e)?|pas|jamais|sans|démenti|nie)\b",
    re.IGNORECASE,
)
_NOT_ONLY_RE = re.compile(r"\bnot\s+only\s*$", re.IGNORECASE)
_NO_DOUBT_RE = re.compile(r"\b(?:no\s+doubt|without\s+doubt|no\s+question)\b", re.IGNORECASE)
_POST_NEGATION_RE = re.compile(
    r"\b(?:no\s+(?:evidence|indication|finding|sign)s?\b|"
    r"(?:were|was|is|are|has\s+been|have\s+been)?\s*(?:not|never)\s+"
    r"(?:filed|brought|proven|established|substantiated|charged)\b|"
    r"(?:allegations?|charges?)\s+(?:were\s+|have\s+been\s+)?"
    r"(?:dismissed|dropped|withdrawn)\b)",
    re.IGNORECASE,
)
_HEDGE_RE = re.compile(
    r"\b(?:alleged|allegedly|allegations?|accused|accusations?|reportedly|"
    r"reported\s+that|according\s+to|suspected|alleges?|alleging|"
    r"prosecutors?\s+(?:say|said|allege)|"
    r"claims?\s+that|angeblich|mutmasslich|mutmaßlich|vorgeworfen|vorwurf|"
    r"berichtet|laut|allégué|selon)\b",
    re.IGNORECASE,
)
_POLICY_RE = re.compile(
    r"\b(?:programme|program|policy|framework|training|code\s+of\s+conduct|"
    r"guidelines?|prevention|compliance\s+system|policy\s+statement|"
    r"anti-corruption\s+and\s+anti-bribery)\b",
    re.IGNORECASE,
)
_TABLE_RE = re.compile(
    r"\b(?:G1-4|indicators?|key\s+figures?|dashes|not\s+applicable|"
    r"database|data\s+base|n/?a)\b",
    re.IGNORECASE,
)
_EXONERATION_CONTEXT_RE = re.compile(
    r"\b(?:acquitted|acquittal|cleared\s+of|"
    r"conviction\w*\s+(?:was\s+)?(?:overturned|quashed|set\s+aside)|"
    r"charges?\s+(?:were\s+|have\s+been\s+)?(?:dismissed|dropped|withdrawn)|"
    r"not\s+guilty|freigesprochen)\b",
    re.IGNORECASE,
)
_ACTION_RE = re.compile(
    r"\b(?:fined|convicted|pleaded|sentenced|sanctioned|penalt(?:y|ies)|"
    r"imposed|ordered\s+to\s+pay|breach(?:ed|es)?|fail(?:ed|ure|ures)?|"
    r"deficien\w*|gaps?|weakness\w*|inadequa\w*|insufficient\w*|"
    r"overstated|misleading|misrepresent\w*)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SignalRule:
    category: str
    label: str
    pattern: str
    weight: int
    kind: str  # outcome | misconduct | control | exoneration


RULES: tuple[SignalRule, ...] = (
    SignalRule(
        "criminal_outcome",
        "Criminal outcome",
        r"\b(?:pleaded|pleads|plea\s+of)\s+guilty\b|\bguilty\s+plea\b|"
        r"\bconvicted\b|\bconvictions?\b|\bsentenced\b|\bfound\s+guilty\b|"
        r"\bverurteilt\w*\b|\bverurteilung\w*\b|\bcondamn\w*\b",
        5,
        "outcome",
    ),
    SignalRule(
        "regulatory_outcome",
        "Regulatory sanction",
        r"\b(?:administrative\s+sanction|financial\s+sanction|"
        r"sanction\s+(?:of|imposed|against)|sanctioned\b|fined\b|fine\s+of\b|"
        r"fines?\s+(?:totall?ing|amounting|of)|imposes?[^.]{0,60}\bfines?\b|"
        r"penalt(?:y|ies)\s+(?:of|for)|cease\s+and\s+desist|consent\s+order|"
        r"enforcement\s+action|ordered\s+to\s+pay|busse\b|geldbusse\b|"
        r"geldstrafe\b|amende\b)",
        4,
        "outcome",
    ),
    SignalRule(
        "civil_judgment",
        "Civil judgment",
        r"\b(?:final\s+(?:civil\s+)?judg?ments?|consent\s+judg?ment|"
        r"judg?ment\s+(?:against|entered)|settlement\s+agreement)\b",
        3,
        "outcome",
    ),
    SignalRule(
        "court_decision",
        "Court decision",
        r"\b(?:court|tribunal)\s+(?:held|found|ruled|decided|allowed|dismissed|upheld|"
        r"set\s+aside)\b|\b(?:appeal|complaint)\s+(?:was\s+)?(?:allowed|dismissed|upheld)\b|"
        r"\bjudg?ment\s+of\s+the\b",
        2,
        "outcome",
    ),
    SignalRule(
        "settlement",
        "Settlement or resolution",
        r"\bsettled\b[^.]{0,50}\b(?:charges|investigation|allegations)\b|"
        r"\bsettlement\b[^.]{0,50}\b(?:SEC|DOJ|regulator|enforcement)\b",
        3,
        "outcome",
    ),
    SignalRule(
        "fraud",
        "Fraud or deception",
        r"\b(?:securities\s+fraud|fraud(?:ulent)?|defraud\w*|ponzi|"
        r"betrug\w*|betrüger\w*|fraude\b)",
        5,
        "misconduct",
    ),
    SignalRule(
        "misleading",
        "Misleading statements",
        r"\b(?:misleading|misrepresent\w*|false\s+or\s+misleading|"
        r"conceal\w*|deceiv\w*|false\s+statements?|irreführend\w*|"
        r"täusch\w*|trompeur\w*)",
        4,
        "misconduct",
    ),
    SignalRule(
        "bribery",
        "Bribery or corruption",
        r"\b(?:brib\w*|corruption|anti[- ]?bribery|bestechung\w*|korruption\b)",
        4,
        "misconduct",
    ),
    SignalRule(
        "market_abuse",
        "Market abuse",
        r"\b(?:market\s+manipulation|insider\s+trading|front[- ]?running|"
        r"kursmanipulation)",
        4,
        "misconduct",
    ),
    SignalRule(
        "money_laundering",
        "Money laundering or AML gaps",
        r"\b(?:money\s+laundering|anti[- ]?money[- ]?laundering|"
        r"aml(?:\s*/\s*cft)?|terrorist\s+financing|geldwäscherei|"
        r"geldwaescherei|blanchiment)\b",
        4,
        "misconduct",
    ),
    SignalRule(
        "sanctions_breach",
        "Sanctions breach",
        r"\b(?:sanctions?\s+(?:breach|violation|evasion)|"
        r"breach(?:ed|es)?[^.]{0,30}\bsanctions?\b)",
        4,
        "misconduct",
    ),
    SignalRule(
        "consumer_harm",
        "Consumer harm",
        r"\b(?:mis-?selling|overstated|failed\s+to\s+disclose|"
        r"undue\s+pressure|unfair\s+(?:terms|treatment)|false\s+or\s+misleading)\b",
        3,
        "misconduct",
    ),
    SignalRule(
        "data_protection",
        "Data protection",
        r"\b(?:data\s+protection|personal\s+data|unlawful\s+processing|"
        r"gdpr|nfadp|datenschutz\w*|privacy\s+(?:breach|violation))\b",
        3,
        "misconduct",
    ),
    SignalRule(
        "claims_handling",
        "Claims handling",
        r"\b(?:denied\s+(?:the\s+)?(?:claim|benefits?)|"
        r"denial\s+of\s+(?:benefits?|cover)|bad\s+faith|claims?\s+handling)\b",
        2,
        "misconduct",
    ),
    SignalRule(
        "discrimination",
        "Discrimination or unfair screening",
        r"\b(?:discriminat\w*|racial\s+profiling|blacklist\w*|diskriminier\w*|"
        r"based\s+on\s+(?:her|his|their)\s+name|because\s+of\s+(?:her|his|their)\s+name|"
        r"aufgrund\s+(?:ihres|seines)\s+namens|wegen\s+(?:ihres|seines)\s+namens)",
        3,
        "misconduct",
    ),
    SignalRule(
        "tax",
        "Tax misconduct",
        r"\b(?:tax\s+(?:evasion|fraud)|steuerbetrug)",
        3,
        "misconduct",
    ),
    SignalRule(
        "antitrust",
        "Antitrust misconduct",
        r"\b(?:antitrust|cartel|competition\s+law\s+violation|kartell\w*)",
        3,
        "misconduct",
    ),
    SignalRule(
        "compliance_failure",
        "Control failure",
        r"\b(?:compliance\s+(?:failure|failures|gaps?|deficien\w*|breach\w*|weakness\w*)|"
        r"control\s+(?:failure|failures|deficien\w*|weakness\w*)|"
        r"governance\s+(?:failure|deficien\w*)|"
        r"internal\s+audit\s+(?:cycle|findings?)|whistleblower\w*|"
        r"retaliat\w*|compliance[- ]verstoss)",
        3,
        "control",
    ),
    SignalRule(
        "exoneration",
        "Exonerating outcome",
        r"\b(?:acquitted|acquittal|cleared\s+of|"
        r"conviction\w*\s+(?:was\s+)?(?:overturned|quashed|set\s+aside)|"
        r"charges?\s+(?:were\s+|have\s+been\s+)?(?:dismissed|dropped|withdrawn)|"
        r"not\s+guilty|"
        r"freigesprochen)\b",
        2,
        "exoneration",
    ),
)

_COMPILED = tuple(
    (rule, re.compile(rule.pattern, flags=re.IGNORECASE)) for rule in RULES
)


@dataclass(frozen=True)
class Signal:
    category: str
    label: str
    kind: str
    weight: int
    polarity: str
    snippet: str
    match: str

    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "label": self.label,
            "kind": self.kind,
            "weight": self.weight,
            "polarity": self.polarity,
            "snippet": self.snippet,
            "match": self.match,
        }


@dataclass
class SignalReport:
    signals: list[Signal] = field(default_factory=list)
    categories_asserted: tuple[str, ...] = ()
    categories_alleged: tuple[str, ...] = ()
    has_outcome: bool = False
    has_allegation: bool = False
    has_exonerating: bool = False
    severity_score: float = 0.0
    verification_status: str = "no_signal"

    def as_dict(self) -> dict:
        return {
            "signals": [signal.as_dict() for signal in self.signals],
            "categories_asserted": list(self.categories_asserted),
            "categories_alleged": list(self.categories_alleged),
            "has_outcome": self.has_outcome,
            "has_allegation": self.has_allegation,
            "has_exonerating": self.has_exonerating,
            "severity_score": self.severity_score,
            "verification_status": self.verification_status,
        }


def is_primary_source(source_type: object) -> bool:
    return bool(_PRIMARY_RE.match(str(source_type or "")))


def _aggregate_severity(signals: "list[Signal]") -> float:
    """Score breadth of categories, not repetition of the same word."""

    asserted: dict[str, int] = {}
    alleged: dict[str, int] = {}
    for signal in signals:
        if signal.polarity == "asserted" and signal.kind != "exoneration":
            asserted[signal.category] = max(asserted.get(signal.category, 0), signal.weight)
        elif signal.polarity == "alleged":
            alleged[signal.category] = max(alleged.get(signal.category, 0), signal.weight)
    score = sum(asserted.values()) + 0.5 * sum(alleged.values())
    return round(min(10.0, score), 1)


def _sentence_span(text: str, position: int) -> tuple[int, int, str]:
    for start, end, sentence in iter_sentences(text):
        if start <= position <= end:
            return start, end, sentence
    start = max(0, position - 200)
    return start, min(len(text), position + 400), text[start : position + 400]


def _polarity(sentence: str, start: int, end: int, rule: SignalRule) -> str:
    if rule.kind == "exoneration":
        return "exonerating"
    if _EXONERATION_CONTEXT_RE.search(sentence):
        return "negated"
    before = sentence[max(0, start - 110) : start]
    after = sentence[end : end + 100]
    if (
        _NEGATION_RE.search(before)
        and not _NOT_ONLY_RE.search(before)
        and not _NO_DOUBT_RE.search(before)
    ):
        return "negated"
    if _POST_NEGATION_RE.search(after):
        return "negated"
    if _TABLE_RE.search(sentence):
        return "policy"
    if _POLICY_RE.search(sentence) and not _ACTION_RE.search(sentence):
        return "policy"
    if _HEDGE_RE.search(before):
        return "alleged"
    return "asserted"


def scan_text(
    value: object,
    source_type: str = "",
    max_signals: int = MAX_SIGNALS,
) -> SignalReport:
    """Detect and classify wrongdoing signals in *value*."""

    text = normalise_text(value)
    if not text:
        return SignalReport()

    found: list[Signal] = []
    for rule, regex in _COMPILED:
        for match in regex.finditer(text):
            sentence_start, _, sentence = _sentence_span(text, match.start())
            if not sentence:
                continue
            relative_start = max(0, min(match.start() - sentence_start, len(sentence)))
            relative_end = max(relative_start, min(match.end() - sentence_start, len(sentence)))
            polarity = _polarity(sentence, relative_start, relative_end, rule)
            found.append(
                Signal(
                    category=rule.category,
                    label=rule.label,
                    kind=rule.kind,
                    weight=rule.weight,
                    polarity=polarity,
                    snippet=truncate(sentence, _SNIPPET_LENGTH),
                    match=truncate(match.group(0), 80),
                )
            )

    # Collapse duplicates produced by overlapping rules or repeated phrases.
    deduped: dict[tuple[str, str, str], Signal] = {}
    for signal in found:
        key = (signal.category, signal.polarity, signal.snippet[:150])
        existing = deduped.get(key)
        if existing is None or signal.weight > existing.weight:
            deduped[key] = signal
    signals = sorted(
        deduped.values(),
        key=lambda item: (-item.weight, item.category, item.snippet),
    )[:max_signals]

    asserted = [signal for signal in signals if signal.polarity == "asserted"]
    alleged = [signal for signal in signals if signal.polarity == "alleged"]
    exonerating = [signal for signal in signals if signal.polarity == "exonerating"]
    negated = [signal for signal in signals if signal.polarity == "negated"]
    policy = [signal for signal in signals if signal.polarity == "policy"]

    has_outcome = any(signal.kind == "outcome" for signal in asserted)
    has_asserted_misconduct = any(
        signal.kind in ("misconduct", "control") for signal in asserted
    )
    has_allegation = bool(alleged) or has_asserted_misconduct
    has_exonerating = bool(exonerating)
    severity = _aggregate_severity(signals)

    if has_outcome:
        verification_status = "primary_outcome" if is_primary_source(source_type) else "reported_outcome"
    elif alleged:
        verification_status = "reported_allegation"
    elif has_asserted_misconduct:
        verification_status = (
            "asserted_misconduct" if is_primary_source(source_type) else "reported_allegation"
        )
    elif has_exonerating and not asserted:
        verification_status = "exonerating"
    elif negated:
        verification_status = "denial_or_policy"
    elif policy:
        verification_status = "policy_or_program"
    else:
        verification_status = "no_signal"

    return SignalReport(
        signals=signals,
        categories_asserted=tuple(sorted({signal.category for signal in asserted})),
        categories_alleged=tuple(sorted({signal.category for signal in alleged})),
        has_outcome=has_outcome,
        has_allegation=has_allegation and not has_outcome,
        has_exonerating=has_exonerating,
        severity_score=severity,
        verification_status=verification_status,
    )


def merge_reports(reports: Iterable[SignalReport]) -> SignalReport:
    """Merge per-passage reports without double-counting categories."""

    signals: list[Signal] = []
    for report in reports:
        signals.extend(report.signals)
    unique: dict[tuple[str, str, str], Signal] = {}
    for signal in signals:
        key = (signal.category, signal.polarity, signal.snippet[:150])
        unique.setdefault(key, signal)
    ordered = sorted(unique.values(), key=lambda item: (-item.weight, item.category))
    asserted = [signal for signal in ordered if signal.polarity == "asserted"]
    alleged = [signal for signal in ordered if signal.polarity == "alleged"]
    statuses = [report.verification_status for report in reports]
    has_outcome = any(signal.kind == "outcome" for signal in asserted)
    has_asserted_misconduct = any(
        signal.kind in ("misconduct", "control") for signal in asserted
    )
    severity = _aggregate_severity(ordered)
    if has_outcome:
        status = "primary_outcome" if "primary_outcome" in statuses else "reported_outcome"
    elif "reported_allegation" in statuses:
        status = "reported_allegation"
    elif has_asserted_misconduct:
        status = "asserted_misconduct"
    elif "exonerating" in statuses:
        status = "exonerating"
    elif "denial_or_policy" in statuses:
        status = "denial_or_policy"
    elif "policy_or_program" in statuses:
        status = "policy_or_program"
    else:
        status = "no_signal"
    return SignalReport(
        signals=ordered,
        categories_asserted=tuple(sorted({signal.category for signal in asserted})),
        categories_alleged=tuple(sorted({signal.category for signal in alleged})),
        has_outcome=has_outcome,
        has_allegation=(bool(alleged) or has_asserted_misconduct) and not has_outcome,
        has_exonerating=any(signal.polarity == "exonerating" for signal in ordered),
        severity_score=severity,
        verification_status=status,
    )
