from evidence.matching import build_profiles, find_excerpt, match_anchor
from tests.factories import record

ASIC_TEXT = (
    "Home | Contact | Subscribe | Cookie settings. "
    "The Supreme Court of NSW today convicted Allianz Australia Insurance Limited and "
    "imposed criminal fines of $13.5 million for making false or misleading statements "
    "about travel insurance. The Court found that between 2016 and 2018 the companies "
    "published information online that overstated the cover available. "
    "Related articles: Five tips for cheaper travel."
)


def _asic_profile():
    return build_profiles([record()])["asic-travel"]


def test_relevance_match_finds_the_evidence_sentence():
    result = find_excerpt(ASIC_TEXT, _asic_profile())
    assert result is not None
    assert "convicted" in result.excerpt
    assert "cheaper travel" not in result.excerpt
    assert result.confidence >= 0.45
    assert "convicted" in result.matched_terms or "criminal" in result.matched_terms
    assert result.method == "relevance"


def test_exact_anchor_tolerates_whitespace_and_line_breaks():
    result = match_anchor(
        "Vorher. 1’000 der 2’500 Anträge   sind\nlückenhaft. Nachher.",
        "1'000 der 2'500 Anträge sind lückenhaft",
    )
    assert result is not None
    assert result.method == "anchor"
    assert result.confidence >= 0.95


def test_fuzzy_anchor_matches_transliterated_german():
    result = match_anchor(
        "Der Erkenntnisstand: 1'000 der Antraege sind lueckenhaft und unvollstaendig.",
        "1'000 der Anträge sind lückenhaft",
    )
    assert result is not None
    assert result.method in ("anchor", "anchor_fuzzy")


def test_unrelated_page_does_not_match():
    text = "Bread recipes and hiking routes. " * 40
    assert find_excerpt(text, _asic_profile()) is None


def test_source_anchor_is_not_reused_for_a_different_record():
    other = record(
        id="tax-ruling",
        sort_date="2024-01-10",
        date_label="Judgment 10 January 2024",
        category="Tax",
        evidence_class="Court decision",
        geography="Switzerland",
        title="Federal Supreme Court ruling on withholding tax",
        entity="Allianz Suisse",
        summary="The court considered a withholding-tax deduction on pension contributions.",
        details=["The ruling concerned payroll deductions."],
        source_ids=["srf"],
        tags=["Tax"],
    )
    profiles = build_profiles([record(), other])
    source = {"excerpt_anchors": ["convicted over travel-insurance statements"]}
    text = "The court convicted over travel-insurance statements. " + "Unrelated filler. " * 30

    assert find_excerpt(text, profiles["tax-ruling"], source, anchor_scope_single=True) is not None
    assert find_excerpt(text, profiles["tax-ruling"], source, anchor_scope_single=False) is None


def test_terse_regulator_notice_is_still_matched():
    short_record = record(
        id="short-notice",
        sort_date="2022-03-25",
        date_label="25 March 2022",
        category="AML/CFT controls",
        evidence_class="Regulatory or criminal outcome",
        geography="Luxembourg",
        title="Regulator fined Allianz Global Investors for AML failures",
        entity="Allianz Global Investors GmbH",
        summary="The regulator fined Allianz Global Investors for AML control failures.",
        details=["The fine was EUR 283,000."],
        source_ids=["cssf"],
        tags=["AML"],
    )
    profile = build_profiles([short_record])["short-notice"]
    result = find_excerpt("Fined EUR 283,000 for AML failures.", profile)
    assert result is not None
    assert "283" in result.excerpt
