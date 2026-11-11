"""Passage matching for dossier records.

The earlier collector scored individual sentences by counting overlapping
words and accepted a hit when two words matched. That is easy to satisfy with
navigation text and easy to miss when a German page inflects a word. This
module instead:

* builds a weighted, corpus-aware term profile per record (rare terms count
  more than terms every record shares),
* matches curated anchors exactly first, then tolerantly,
* falls back to ranked two-sentence windows, and
* reports *why* a passage was accepted so the dashboard can show confidence.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from .text import (
    iter_sentences,
    normalise_text,
    token_equivalent,
    tokenize,
    window,
)

MAX_SCAN_CHARACTERS = 600_000

# Words that carry no evidentiary weight on their own. This merges the earlier
# ad-hoc list with English, German and French function words and the boilerplate
# that regulator/report pages repeat on every page.
STOPWORDS = frozenset(
    """
    a an and are as at be been being but by for from had has have he her him his
    i if in into is it its itself me my no nor not of on or our ours she should
    so than that the their theirs them then there these they this those to too
    under until up was we were what when where which while who whom why will with
    would you your
    der die das den dem des ein eine einer eines einem einen und oder aber auch
    als aus bei bis durch fuer für gegen hat haben hatte hatte ist sind war waren
    wird werden wurde wurden wie wenn wo waehrend während um zum zur vom von vor
    ueber über nach nicht kein keine keinen mit sich sie ihr ihre ihnen ich wir
    uns unser unsere sowie damit dabei dazu daher jedoch bereits schon sehr mehr
    moins plus pour par dans avec sans sur sous entre est sont ete été cette ces
    leur leurs aux des du le la les un une et ou mais
    about above after again against all also am any because before below between
    both can cannot could did do does doing down during each few further here how
    more most other over own same some such through very
    allianz group annual report reports company disclosure published business
    management risk framework records including record source case section table
    year years current information system systems describes described alternate
    category condition covers dashes describe entry every financial identifies
    includes index linked official pdf point related shows suisse swiss tracking
    primary secondary regulator court decision release statement filing page
    document documents evidence finding findings review reviewed research plan
    coverage result results next action open named specific official
    """.split()
)

LEGAL_SUFFIXES = frozenset(
    """
    ag sa sas se llc ltd limited plc inc incorporated corp corporation gmbh mbh
    pty bv nv kg co company branch zweigniederlassung versicherung
    versicherungs gesellschaft versicherungsgesellschaft group holding holdings
    """.split()
)

_ALLIANZ_RE = re.compile(r"\ballianz(?:\s*24|\s*suisse|\s*se|\s*group)?\b", re.IGNORECASE)


@dataclass(frozen=True)
class MatchResult:
    """A passage accepted as evidence for one record."""

    method: str
    confidence: float
    excerpt: str
    matched_terms: tuple[str, ...]
    anchor: str
    score: float
    start: int
    end: int

    def as_dict(self) -> dict:
        return {
            "method": self.method,
            "confidence": round(self.confidence, 3),
            "excerpt": self.excerpt,
            "matched_terms": list(self.matched_terms),
            "anchor": self.anchor,
            "score": round(self.score, 3),
            "start": self.start,
            "end": self.end,
        }


@dataclass
class RecordProfile:
    record_id: str
    title: str
    entity: str
    evidence_class: str
    terms: dict[str, float] = field(default_factory=dict)
    prefix_index: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    entity_terms: tuple[str, ...] = ()
    allianz_related: bool = True

    def matches_entity(self, tokens: list[str]) -> bool:
        if not self.entity_terms:
            return True
        return any(
            token_equivalent(token, entity_term)
            for token in tokens
            for entity_term in self.entity_terms
        )


def _add_terms(target: dict[str, float], value: object, weight: float) -> None:
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _add_terms(target, item, weight)
        return
    for token in tokenize(value):
        if token in STOPWORDS or len(token) < 3:
            continue
        if token.isdigit() and len(token) < 3:
            continue
        target[token] = max(target.get(token, 0.0), weight)


def _record_weight_map(record: dict) -> dict[str, float]:
    weights: dict[str, float] = {}
    _add_terms(weights, record.get("title", ""), 2.4)
    _add_terms(weights, record.get("entity", ""), 2.6)
    _add_terms(weights, record.get("summary", ""), 1.7)
    _add_terms(weights, record.get("details", []), 1.1)
    _add_terms(weights, record.get("category", ""), 0.9)
    _add_terms(weights, record.get("tags", []), 0.9)
    return weights


def _entity_terms(record: dict) -> tuple[str, ...]:
    tokens = []
    for token in tokenize(record.get("entity", "")):
        if len(token) < 4 or token in STOPWORDS or token in LEGAL_SUFFIXES:
            continue
        if token.isdigit():
            continue
        tokens.append(token)
    return tuple(dict.fromkeys(tokens))


def record_entity_terms(record: dict) -> tuple[str, ...]:
    """Public helper so verification can check entity attribution."""

    return _entity_terms(record)


def build_profiles(records: list[dict]) -> dict[str, RecordProfile]:
    """Build per-record term weights, down-weighting terms shared by records."""

    raw = {str(record.get("id") or index): _record_weight_map(record) for index, record in enumerate(records)}
    document_frequency: Counter[str] = Counter()
    for weights in raw.values():
        document_frequency.update(weights.keys())
    total = max(1, len(raw))

    profiles: dict[str, RecordProfile] = {}
    for index, record in enumerate(records):
        record_id = str(record.get("id") or index)
        terms: dict[str, float] = {}
        for token, field_weight in raw[record_id].items():
            idf = math.log((total + 1) / (document_frequency[token] + 0.5))
            terms[token] = field_weight * (0.5 + idf)
        prefix_index: dict[str, list[tuple[str, float]]] = {}
        for token, weight in terms.items():
            if len(token) >= 6:
                prefix_index.setdefault(token[:6], []).append((token, weight))
        entity_text = record.get("entity", "")
        profiles[record_id] = RecordProfile(
            record_id=record_id,
            title=str(record.get("title") or ""),
            entity=str(entity_text),
            evidence_class=str(record.get("evidence_class") or ""),
            terms=terms,
            prefix_index=prefix_index,
            entity_terms=_entity_terms(record),
            allianz_related=bool(_ALLIANZ_RE.search(str(entity_text))),
        )
    return profiles


def _score_tokens(tokens: list[str], profile: RecordProfile) -> tuple[float, list[str], int]:
    """Score a token list against a profile.

    Returns ``(score, matched_terms, distinctive_matches)`` where distinctive
    matches are terms that occur in only a few dossier records.
    """

    score = 0.0
    matched: list[str] = []
    distinctive = 0
    seen: set[str] = set()
    for token in tokens:
        matched_term = None
        weight = profile.terms.get(token)
        if weight is not None:
            matched_term = token
        elif len(token) >= 6:
            for candidate, candidate_weight in profile.prefix_index.get(token[:6], []):
                if token_equivalent(token, candidate):
                    matched_term = candidate
                    weight = candidate_weight * 0.9
                    break
        if matched_term is None or weight is None or matched_term in seen:
            continue
        seen.add(matched_term)
        matched.append(matched_term)
        score += weight
        # A term present in a single record (idf ≈ 1.9) is distinctive.
        if weight >= 1.8:
            distinctive += 1
    matched.sort(key=lambda term: profile.terms.get(term, 0.0), reverse=True)
    return score, matched, distinctive


def _anchor_regex(anchor: str) -> re.Pattern | None:
    anchor = normalise_text(anchor)
    parts = [part for part in re.split(r"\s+", anchor) if part]
    if len(parts) < 2:
        return None
    escaped = []
    for part in parts:
        piece = re.escape(part)
        piece = piece.replace(r"\-", "[- ]?")
        piece = piece.replace("'", "['\u2019]")
        piece = piece.replace(r"\.", r"[.,]?")
        escaped.append(piece)
    pattern = r"(?<!\w)" + r"[\s,;:]+".join(escaped) + r"(?!\w)"
    try:
        return re.compile(pattern, flags=re.IGNORECASE)
    except re.error:
        return None


def _fuzzy_anchor(text: str, anchor: str, max_characters: int) -> MatchResult | None:
    anchor_tokens = [token for token in tokenize(anchor) if len(token) >= 4]
    if len(anchor_tokens) < 3:
        return None
    sentences = list(iter_sentences(text))
    if not sentences:
        return None
    best: tuple[float, int, int] | None = None
    for index in range(len(sentences)):
        start = sentences[index][0]
        end = sentences[min(index + 2, len(sentences) - 1)][1]
        candidate = text[start:end]
        if len(candidate) > 6000:
            continue
        candidate_tokens = tokenize(candidate)
        if not candidate_tokens:
            continue
        covered = 0
        for anchor_token in anchor_tokens:
            if any(token_equivalent(anchor_token, token) for token in candidate_tokens):
                covered += 1
        coverage = covered / len(anchor_tokens)
        if coverage >= 0.7 and (best is None or coverage > best[0]):
            best = (coverage, start, end)
        if coverage == 1.0:
            break
    if best is None:
        return None
    coverage, start, end = best
    excerpt = window(text, start, end, max_characters)
    if not excerpt:
        return None
    return MatchResult(
        method="anchor_fuzzy",
        confidence=min(0.86, 0.45 + 0.4 * coverage),
        excerpt=excerpt,
        matched_terms=tuple(anchor_tokens[:5]),
        anchor=anchor,
        score=coverage,
        start=start,
        end=end,
    )


def match_anchor(text: str, anchor: str, max_characters: int = 1400) -> MatchResult | None:
    """Find a curated anchor exactly, then tolerantly."""

    text = normalise_text(text)
    if not text or not anchor:
        return None
    pattern = _anchor_regex(anchor)
    if pattern is not None:
        match = pattern.search(text)
        if match:
            excerpt = window(text, match.start(), match.end(), max_characters)
            if excerpt:
                return MatchResult(
                    method="anchor",
                    confidence=0.97,
                    excerpt=excerpt,
                    matched_terms=tuple(tokenize(anchor)[:6]),
                    anchor=anchor,
                    score=1.0,
                    start=match.start(),
                    end=match.end(),
                )
    return _fuzzy_anchor(text, anchor, max_characters)


def _candidate_windows(text: str):
    sentences = [
        (start, end, sentence)
        for start, end, sentence in iter_sentences(text)
        if sentence.strip()
    ]
    for index, (start, _, _) in enumerate(sentences):
        last = min(index + 1, len(sentences) - 1)
        end = sentences[last][1]
        if end - start < 20:
            continue
        yield start, end, text[start:end]


def _anchor_relates_to_profile(anchor: str, profile: RecordProfile) -> bool:
    """Allow a shared source anchor only when it is specific to the record."""

    tokens = tokenize(anchor)
    if profile.matches_entity(tokens) and profile.entity_terms:
        return True
    _, _, distinctive = _score_tokens(tokens, profile)
    return distinctive > 0


def find_excerpt(
    text: str,
    profile: RecordProfile,
    source: dict | None = None,
    max_characters: int = 1400,
    min_score: float = 2.0,
    min_confidence: float = 0.45,
    anchor_scope_single: bool = True,
) -> MatchResult | None:
    """Return the best evidence passage for *profile*, or ``None``.

    Anchors supplied in the source register win. Otherwise the best ranked
    window must contain at least two record terms, one of them distinctive,
    and reach the confidence floor. When a source is linked to several records,
    a source-level anchor is used only if it shares a term with the record so
    one record's anchor cannot stand in for another record's evidence.
    """

    text = normalise_text(text)[:MAX_SCAN_CHARACTERS]
    if not text:
        return None

    for anchor in (source or {}).get("excerpt_anchors", []) or []:
        if not anchor_scope_single and not _anchor_relates_to_profile(str(anchor), profile):
            continue
        result = match_anchor(text, str(anchor), max_characters)
        if result is not None:
            return result

    text_tokens = tokenize(text)
    allianz_present = any(token == "allianz" or token.startswith("allianz") for token in text_tokens)
    best: MatchResult | None = None
    for start, end, candidate in _candidate_windows(text):
        tokens = tokenize(candidate)
        if not tokens:
            continue
        score, matched, distinctive = _score_tokens(tokens, profile)
        if score < min_score or len(matched) < 2 or distinctive < 1:
            continue
        entity_match = profile.matches_entity(tokens)
        confidence = min(0.92, 0.30 + 0.05 * len(matched) + 0.06 * score)
        if not entity_match:
            confidence *= 0.78
        if profile.allianz_related and not allianz_present:
            confidence *= 0.7
        if confidence < min_confidence:
            continue
        excerpt = window(text, start, end, max_characters)
        if not excerpt:
            continue
        result = MatchResult(
            method="relevance",
            confidence=confidence,
            excerpt=excerpt,
            matched_terms=tuple(matched[:10]),
            anchor="",
            score=score,
            start=start,
            end=end,
        )
        if best is None or (result.confidence, result.score) > (best.confidence, best.score):
            best = result
    return best


def explain_no_match(text: str, profile: RecordProfile) -> dict:
    """Diagnostics for a source that yielded no passage for a record."""

    tokens = tokenize(text)
    score, matched, distinctive = _score_tokens(tokens, profile)
    return {
        "characters": len(text),
        "score": round(score, 3),
        "matched_terms": matched[:10],
        "distinctive_matches": distinctive,
        "allianz_mentioned": bool(_ALLIANZ_RE.search(text)),
        "entity_terms_matched": profile.matches_entity(tokens) if profile.entity_terms else False,
        "terms_considered": len(profile.terms),
    }


def allianz_mentioned(text: str) -> bool:
    return bool(_ALLIANZ_RE.search(normalise_text(text)))

