from evidence.text import (
    decode_bytes,
    detect_content_kind,
    fold_text,
    meaningful_characters,
    normalise_text,
    token_equivalent,
    tokenize,
)


def test_normalise_unifies_dashes_quotes_and_invisible_characters():
    raw = "Allianz\u2013Suisse\u00a0\u200b\u201ctest\u201d\u00ad"
    assert normalise_text(raw) == 'Allianz-Suisse "test"'


def test_normalise_repairs_pdf_hyphenation():
    text = normalise_text("Die Versicherungs-\ngesellschaft prüfte den Fall.")
    assert "Versicherungsgesellschaft" in text


def test_fold_and_tokenize_handle_umlauts():
    assert tokenize("Schäden, FÜR und Größe") == ["schaeden", "fuer", "und", "groesse"]
    assert fold_text("Allianz Suisse") == "allianz suisse"


def test_token_equivalence_matches_inflections_but_not_short_words():
    assert token_equivalent("convicted", "conviction")
    assert token_equivalent("versicherung", "versicherungs")
    assert not token_equivalent("fine", "find")


def test_decode_prefers_valid_utf8_over_wrong_header():
    content = "Für Schäden und Größe".encode("utf-8")
    text, encoding = decode_bytes(content, "text/html; charset=iso-8859-1")
    assert text == "Für Schäden und Größe"
    assert encoding == "utf-8"


def test_decode_handles_cp1252_fallback():
    content = "Für Schäden".encode("cp1252")
    text, encoding = decode_bytes(content, "text/html; charset=iso-8859-1")
    assert text == "Für Schäden"
    assert encoding in ("iso-8859-1", "cp1252")


def test_detect_content_kind():
    assert detect_content_kind(b"%PDF-1.7\n...", "application/octet-stream") == "pdf"
    assert detect_content_kind(b"<!doctype html><html></html>", "") == "html"
    assert detect_content_kind(b"<?xml version='1.0'?>", "application/xml") == "xml"
    assert detect_content_kind(b"\x00\x01\x02" * 100, "application/zip") == "binary"
    assert detect_content_kind(b"plain words", "text/plain") == "text"


def test_meaningful_characters_ignores_layout_whitespace():
    assert meaningful_characters(" a b\n c ") == 3
