from app.services.disclaimer.matcher import normalize, classify

REQUIRED = "Tax benefits as per prevailing Income tax laws shall apply. Please check with your tax consultant for eligibility."
ANCHORS = ["tax benefits as per prevailing income tax laws"]


def test_present_verbatim():
    doc = "Intro text.\n\n" + REQUIRED + "\n\nFooter."
    status, sim = classify(REQUIRED, ANCHORS, doc, 0.85, 0.45)
    assert status == "present"
    assert sim >= 0.85


def test_present_reformatted_whitespace():
    doc = "Tax   benefits as per prevailing Income tax laws shall apply.\nPlease check with your tax consultant for   eligibility."
    status, _ = classify(REQUIRED, ANCHORS, doc, 0.85, 0.45)
    assert status == "present"


def test_altered_partial():
    doc = "Tax benefits as per prevailing Income tax laws shall apply."  # second sentence dropped
    status, sim = classify(REQUIRED, [], doc, 0.85, 0.45)
    assert status == "altered"
    assert 0.45 <= sim < 0.85


def test_missing():
    doc = "This article is about retirement planning. Nothing about tax here at all really."
    status, _ = classify(REQUIRED, ANCHORS, doc, 0.85, 0.45)
    assert status == "missing"


def test_anchor_absent_blocks_present():
    # Body is similar but the mandatory anchor phrase is absent -> not present.
    doc = "Benefits under the income laws may apply, consult an advisor for eligibility checks please."
    status, _ = classify(REQUIRED, ["tax benefits as per prevailing income tax laws"], doc, 0.50, 0.30)
    assert status != "present"
