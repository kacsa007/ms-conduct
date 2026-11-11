from evidence.signals import merge_reports, scan_text


def test_primary_conviction_is_an_asserted_outcome():
    report = scan_text(
        "The Supreme Court of NSW today convicted Allianz Australia Insurance Limited "
        "and imposed criminal fines of $13.5 million for making false or misleading statements.",
        "Primary · regulator and court outcome",
    )
    assert report.has_outcome
    assert report.verification_status == "primary_outcome"
    assert report.severity_score >= 5
    categories = {signal.category for signal in report.signals if signal.polarity == "asserted"}
    assert {"criminal_outcome", "misleading"} <= categories


def test_company_denial_is_not_counted_as_an_asserted_signal():
    report = scan_text(
        "An Allianz spokesperson said there was no indication of money laundering or fraud.",
        "Primary · company statement",
    )
    assert not report.has_outcome
    assert report.severity_score == 0
    assert report.verification_status == "denial_or_policy"
    assert all(signal.polarity == "negated" for signal in report.signals)


def test_german_denial_is_recognised():
    report = scan_text(
        "Allianz erklärte, es gebe keine Hinweise auf Geldwäscherei.",
        "Secondary · reporting",
    )
    assert report.severity_score == 0
    assert all(signal.polarity == "negated" for signal in report.signals)


def test_policy_page_is_not_an_outcome():
    report = scan_text(
        "Allianz's anti-corruption programme includes annual training for employees.",
        "Primary · current company policy",
    )
    assert report.severity_score == 0
    assert report.verification_status == "policy_or_program"


def test_database_description_is_not_an_outcome():
    report = scan_text(
        "FINMA publishes anonymised summaries of its enforcement actions in a database.",
        "Primary · regulator database",
    )
    assert report.severity_score == 0
    assert report.verification_status == "policy_or_program"


def test_acquittal_suppresses_the_misconduct_match():
    report = scan_text(
        "The court acquitted the company of fraud charges.",
        "Secondary · reporting",
    )
    assert report.has_exonerating
    assert report.severity_score == 0
    assert report.verification_status == "exonerating"
    assert all(signal.polarity in ("negated", "exonerating") for signal in report.signals)


def test_allegation_language_is_hedged():
    report = scan_text(
        "The SEC alleges that Allianz committed securities fraud.",
        "Primary · SEC litigation release",
    )
    assert not report.has_outcome
    assert report.has_allegation
    assert report.verification_status == "reported_allegation"


def test_german_conviction_is_detected():
    report = scan_text(
        "Das Gericht hat Allianz wegen Betrugs verurteilt.",
        "Primary · court decision",
    )
    assert report.has_outcome
    assert "criminal_outcome" in report.categories_asserted
    assert "fraud" in report.categories_asserted


def test_merge_reports_keeps_outcome_and_allegation_separate():
    primary = scan_text("Allianz was convicted of fraud.", "Primary · court decision")
    secondary = scan_text("A report alleged that Allianz committed fraud.", "Secondary · reporting")
    merged = merge_reports([primary, secondary])
    assert merged.has_outcome
    assert not merged.has_allegation
    assert merged.verification_status == "primary_outcome"
    assert merged.severity_score > 0


def test_no_doubt_is_not_treated_as_a_denial():
    report = scan_text(
        "There is no doubt that Allianz committed fraud.",
        "Primary · court decision",
    )
    assert report.has_outcome is False
    assert any(signal.polarity == "asserted" for signal in report.signals if signal.category == "fraud")


def test_not_guilty_verdict_is_exonerating():
    report = scan_text(
        "The jury found the company not guilty of fraud.",
        "Secondary · reporting",
    )
    assert report.has_exonerating
    assert report.severity_score == 0


def test_court_decision_wording_is_an_outcome():
    report = scan_text(
        "The Federal Supreme Court held that Allianz Suisse owed accident-insurance benefits.",
        "Primary · court judgment",
    )
    assert report.has_outcome
    assert report.verification_status == "primary_outcome"
    assert "court_decision" in report.categories_asserted
