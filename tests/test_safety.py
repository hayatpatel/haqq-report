from newsbot import safety
from newsbot.models import Claim, Draft, Lane, Tier


def draft(body, headline="Headline", claims=None, category="general"):
    claims = claims if claims is not None else [Claim("c", "UN OCHA", Tier.A)]
    return Draft(headline=headline, body=body, claims=claims, category=category)


def test_eastern_arabic_digits_are_normalised():
    assert safety.normalise_digits("١٢٣٤") == "1234"
    ok, unmatched = safety.numbers_match("قُتل ١٢٠ شخصا", "UN OCHA said 120 people died.")
    assert ok and not unmatched


def test_numbers_must_match_source():
    ok, unmatched = safety.numbers_match("OCHA reported 120 trucks", "OCHA reported 150 trucks")
    assert not ok and unmatched == {"150"}


def test_comma_numbers_match():
    ok, _ = safety.numbers_match("1,200 trucks entered", "UN OCHA said 1200 trucks entered")
    assert ok


def test_clean_attributed_draft_passes():
    d = draft("UN OCHA said 120 aid trucks entered on Monday.")
    assert safety.check_draft(d, "UN OCHA said 120 aid trucks entered on Monday").ok


def test_missing_attribution_fails():
    d = draft("Aid trucks entered on Monday.", claims=[Claim("c", "", Tier.A)])
    res = safety.check_draft(d)
    assert not res.ok and any("attribution" in f for f in res.failures)


def test_attribution_must_appear_in_text():
    d = draft("Aid trucks entered on Monday.")
    res = safety.check_draft(d)
    assert any("no attribution named" in f for f in res.failures)


def test_genocide_in_own_voice_fails():
    d = draft("What is happening is genocide. UN OCHA also noted access limits.")
    res = safety.check_draft(d)
    assert any("genocide" in f for f in res.failures)


def test_genocide_attributed_passes_language_check():
    d = draft("UN OCHA said the court found a plausible risk of genocide.")
    res = safety.check_draft(d)
    assert not any("genocide" in f for f in res.failures)


def test_casualty_figure_needs_named_source():
    d = draft("120 people were killed. UN OCHA published access data.")
    res = safety.check_draft(d)
    assert any("casualty figure" in f for f in res.failures)


def test_casualty_figure_with_source_passes():
    d = draft("The Gaza Health Ministry said 120 people were killed.",
              claims=[Claim("c", "Gaza Health Ministry", Tier.A)])
    assert safety.check_draft(d).ok


def test_collective_blame_fails():
    d = draft("UN OCHA reported delays. All Muslims are terrorists, some say.")
    res = safety.check_draft(d)
    assert any("collective-blame" in f for f in res.failures)


def test_praise_of_designated_group_fails():
    cfg = {"designated_group_keywords": ["hamas"]}
    d = draft("UN OCHA reported. Hamas fighters are heroic.")
    res = safety.check_draft(d, cfg=cfg)
    assert any("designated" in f for f in res.failures)


def test_numbers_not_in_source_fail():
    d = draft("UN OCHA said 999 trucks entered.")
    res = safety.check_draft(d, "UN OCHA said 120 trucks entered.")
    assert any("numbers not found" in f for f in res.failures)


def test_lane_red_for_child_victims():
    lane, reasons = safety.classify_lane(draft("UN OCHA reported children killed in a strike."), {"designated_group_keywords": []})
    assert lane == Lane.RED and "child_victims" in reasons


def test_lane_red_for_designated_group():
    lane, reasons = safety.classify_lane(draft("Officials discussed Hamas talks."), {"designated_group_keywords": ["hamas"]})
    assert lane == Lane.RED and "designated_group_mention" in reasons


def test_lane_red_for_sectarian():
    lane, _ = safety.classify_lane(draft("A Sunni and Shia dispute was reported."), {"designated_group_keywords": []})
    assert lane == Lane.RED


def test_lane_amber_for_court_ruling():
    lane, _ = safety.classify_lane(draft("A court issued a ruling on the case."), {"designated_group_keywords": []})
    assert lane == Lane.AMBER


def test_lane_green_for_occasion():
    lane, _ = safety.classify_lane(draft("Ramadan begins this week for many communities."), {"designated_group_keywords": []})
    assert lane == Lane.GREEN
