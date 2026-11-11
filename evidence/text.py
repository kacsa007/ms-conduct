"""Text helpers used by matching, signal detection and validation.

Sources in this dossier are published in English, German and French and are a
mix of PDFs, old HTML pages and modern regulator releases. Normalising the
same way in every check keeps anchors, record terms and signal rules from
disagreeing about what a word is.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Iterator

# Characters that commonly break token matching after a PDF/HTML round trip.
_INVISIBLE_RE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060\ufeff\u00ad]")
_DASH_RE = re.compile("[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]")
_HYPHEN_LINEBREAK_RE = re.compile(r"-[ \t]*\n[ \t]*(?=[a-z\u00e4\u00f6\u00fc\u00df])")
_SPACE_RE = re.compile(r"[ \t\f\v]+")
_SPACE_AROUND_NEWLINE_RE = re.compile(r" *\n *")
_MANY_NEWLINES_RE = re.compile(r"\n{3,}")
_QUOTE_TRANSLATION = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u2032": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u00ab": '"',
        "\u00bb": '"',
    }
)
_GERMAN_FOLD = str.maketrans(
    {
        "\u00e4": "ae",
        "\u00f6": "oe",
        "\u00fc": "ue",
        "\u00df": "ss",
    }
)
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])[\"')\]]*\s+|\n+")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def normalise_text(value: object) -> str:
    """Return a whitespace- and punctuation-stable version of *value*."""

    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFKC", value)
    text = _INVISIBLE_RE.sub("", text)
    text = _DASH_RE.sub("-", text)
    text = text.translate(_QUOTE_TRANSLATION)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _HYPHEN_LINEBREAK_RE.sub("", text)
    text = _SPACE_RE.sub(" ", text)
    text = _SPACE_AROUND_NEWLINE_RE.sub("\n", text)
    text = _MANY_NEWLINES_RE.sub("\n\n", text)
    return text.strip()


def fold_text(value: object) -> str:
    """Case-, accent- and umlaut-insensitive key used for token matching."""

    text = normalise_text(value).casefold()
    text = text.translate(_GERMAN_FOLD)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return text


def tokenize(value: object) -> list[str]:
    return _TOKEN_RE.findall(fold_text(value))


def token_equivalent(left: str, right: str) -> bool:
    """Match inflectional variants without collapsing short words.

    ``convicted``/``conviction`` and ``Versicherung``/``Versicherungs`` are
    treated as the same token; ``fine``/``find`` are not, because only terms of
    six or more characters are compared on their prefix.
    """

    if not left or not right:
        return False
    if left == right:
        return True
    if len(left) < 6 or len(right) < 6:
        return False
    # Brand and case numbers carry meaning: never equate "allianz" with
    # "allianz24" or "8c" with "8c485" through prefix matching.
    if any(char.isdigit() for char in left) or any(char.isdigit() for char in right):
        return False
    return left[:6] == right[:6]


def find_token(tokens: Iterable[str], candidates: Iterable[str]) -> list[int]:
    """Return indexes of *tokens* equivalent to any of *candidates*."""

    candidate_list = [candidate for candidate in candidates if candidate]
    return [
        index
        for index, token in enumerate(tokens)
        if any(token_equivalent(token, candidate) for candidate in candidate_list)
    ]


def iter_sentences(text: str) -> Iterator[tuple[int, int, str]]:
    """Yield ``(start, end, sentence)`` spans in reading order."""

    text = normalise_text(text)
    start = 0
    for boundary in _SENTENCE_BOUNDARY_RE.finditer(text):
        end = boundary.start()
        yield start, end, text[start:end].strip()
        start = boundary.end()
    if start < len(text):
        yield start, len(text), text[start:].strip()


def sentences(text: str, minimum_length: int = 25) -> list[str]:
    return [
        normalise_text(sentence)
        for _, _, sentence in iter_sentences(text)
        if len(sentence.strip()) >= minimum_length
    ]


def window(text: str, start: int, end: int, max_characters: int = 1400) -> str:
    """Return readable surroundings for a match at ``[start, end)``.

    The window is expanded to sentence boundaries and includes at most one
    preceding sentence. Deliberately no trailing sentence is added: regulator
    and news pages put "related articles" immediately after the evidence, and
    those must not travel into the excerpt.
    """

    text = normalise_text(text)
    if not text:
        return ""
    start = max(0, min(start, len(text)))
    end = max(start, min(end, len(text)))
    spans = [(span_start, span_end, sentence) for span_start, span_end, sentence in iter_sentences(text) if sentence]
    if not spans:
        return text[:max_characters]

    first = None
    last = None
    for index, (span_start, span_end, _) in enumerate(spans):
        if span_end <= start:
            continue
        if first is None:
            first = index
        if span_start >= end:
            last = index - 1
            break
        last = index
    if first is None:
        first = len(spans) - 1
    if last is None or last < first:
        last = first

    excerpt = text[spans[max(0, first - 1)][0] : spans[last][1]].strip()
    if len(excerpt) > max_characters:
        excerpt = text[spans[first][0] : spans[last][1]].strip()
    if len(excerpt) > max_characters:
        excerpt = excerpt[: max(0, max_characters - 1)].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    return excerpt


def truncate(value: object, length: int, suffix: str = "…") -> str:
    text = normalise_text(value)
    if len(text) <= length:
        return text
    return text[: max(0, length - len(suffix))].rstrip() + suffix


def decode_bytes(content: bytes, content_type: str = "") -> tuple[str, str]:
    """Decode a downloaded document, preferring valid UTF-8 over headers.

    A surprising number of regulator pages declare ``ISO-8859-1`` while
    serving UTF-8 (or the reverse). Trying strict UTF-8 first avoids the
    mojibake that then breaks German anchor matching. cp1252 is the fallback
    for older Swiss and German pages.
    """

    if not content:
        return "", "utf-8"
    if content.startswith(b"\xef\xbb\xbf"):
        return content[3:].decode("utf-8", "replace"), "utf-8-sig"
    if content.startswith(b"\xff\xfe") or content.startswith(b"\xfe\xff"):
        return content.decode("utf-16", "replace"), "utf-16"

    declared = ""
    if content_type:
        match = re.search(r"charset\s*=\s*[\"']?([\w.:-]+)", content_type, flags=re.IGNORECASE)
        if match:
            declared = match.group(1).strip().lower()

    head = content[:4096].decode("latin-1", "ignore")
    meta = re.search(r"charset\s*=\s*[\"']?([\w.:-]+)", head, flags=re.IGNORECASE)
    meta_charset = meta.group(1).strip().lower() if meta else ""

    candidates = []
    for charset in ("utf-8", declared, meta_charset, "cp1252", "iso-8859-1"):
        if charset and charset not in candidates and charset not in ("latin-1", "latin1", "us-ascii", "ascii"):
            candidates.append(charset)

    for charset in candidates:
        try:
            decoded = content.decode(charset)
        except (LookupError, UnicodeDecodeError):
            continue
        if _plausible(decoded):
            return decoded, charset

    # Last resort: never lose the document because of a bad encoding label.
    return content.decode("utf-8", "replace"), "utf-8-replace"


def _plausible(decoded: str) -> bool:
    if "\ufffd" in decoded:
        return False
    if not decoded:
        return True
    controls = len(_CONTROL_RE.findall(decoded))
    return controls / len(decoded) < 0.01


def detect_content_kind(content: bytes, content_type: str = "") -> str:
    """Classify a response as ``pdf``, ``html``, ``xml``, ``text`` or ``binary``."""

    lowered = (content_type or "").lower()
    sample = content[:2048]
    stripped = sample.lstrip()
    if "pdf" in lowered or sample[:5] == b"%PDF-":
        return "pdf"
    if "html" in lowered or stripped[:15].lower().startswith((b"<!doctype html", b"<html")):
        return "html"
    if "xml" in lowered or stripped.startswith(b"<?xml"):
        return "xml"
    if lowered.startswith("text/") or lowered in ("application/json", "application/x-json"):
        return "text"
    binary_markers = (
        lowered.startswith(("image/", "audio/", "video/")),
        "zip" in lowered,
        "octet-stream" in lowered,
        "msword" in lowered,
        "officedocument" in lowered,
    )
    if any(binary_markers):
        return "binary"
    if b"\x00" in sample:
        return "binary"
    # Unknown but mostly printable: treat as text so a mislabelled page still
    # yields an excerpt instead of being discarded.
    printable = sum(1 for byte in sample if byte in b"\t\n\r" or 32 <= byte <= 126 or byte >= 160)
    return "text" if sample and printable / len(sample) > 0.85 else "binary"


def meaningful_characters(text: str) -> int:
    """Count characters that carry evidence, ignoring boilerplate whitespace."""

    return len(re.sub(r"\s+", "", text or ""))
