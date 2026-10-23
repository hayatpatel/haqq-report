from newsbot import scoring, verify
from newsbot.models import Claim, Tier


def claim(tier, attribution="Reporter X"):
    return Claim("something happened", attribution, tier)


def test_tier_a_and_b_publish_with_attribution():
    assert verify.evaluate_claim(claim(Tier.A, "UN OCHA")).publishable
    assert verify.evaluate_claim(claim(Tier.B, "Reuters")).publishable


def test_no_attribution_is_held():
    v = verify.evaluate_claim(claim(Tier.A, ""))
    assert not v.publishable and v.status == "held"


def test_tier_c_is_labelled_on_ground_report():
    v = verify.evaluate_claim(claim(Tier.C, "Jane Doe"))
    assert v.publishable and v.status == "on_ground"
    assert "reported by Jane Doe" in v.label and "not independently confirmed yet" in v.label


def test_tier_c_upgraded_when_tier_a_confirms():
    v = verify.evaluate_claim(claim(Tier.C), verify.Evidence(tier_a_confirms=True))
    assert v.status == "verified" and not v.label


def test_tier_d_never_alone():
    assert not verify.evaluate_claim(claim(Tier.D)).publishable


def test_tier_d_upgraded_by_two_independent_reporters():
    ev = verify.Evidence(independent_b_or_c_reporters=2)
    assert verify.evaluate_claim(claim(Tier.D), ev).publishable


def test_geolocation_by_verification_group_upgrades():
    ev = verify.Evidence(geolocated_by="Bellingcat")
    assert verify.is_upgraded(ev)


def test_creator_score_9_needs_low_risk():
    assert not verify.is_upgraded(verify.Evidence(creator_score=9.5, low_risk=False))
    assert verify.is_upgraded(verify.Evidence(creator_score=9.5, low_risk=True))


def test_auto_reject_beats_upgrade():
    ev = verify.Evidence(tier_a_confirms=True, reject_flags=["ai_generated_image"])
    v = verify.evaluate_claim(claim(Tier.A, "UN"), ev)
    assert not v.publishable and v.status == "rejected"


def test_draft_needs_every_claim_publishable():
    ok, _ = verify.evaluate_claims([claim(Tier.A, "UN"), claim(Tier.D)])
    assert not ok
    ok, _ = verify.evaluate_claims([])
    assert not ok


CFG = {
    "limits": {"GAP_HOURS": 3},
    "scoring": {"weights": {"significance": 0.35, "impact": 0.30, "interest": 0.35},
                "gap_bonus": 0.8, "watch_topic_bonus": 0.5, "gap_major_wires_max": 2},
    "watch_topics": ["gaza", "sudan"],
}


def test_base_formula():
    assert scoring.score_story(10, 10, 10, cfg=CFG) == 10.0
    assert scoring.score_story(5, 5, 5, cfg=CFG) == 5.0


def test_gap_bonus_when_under_reported():
    base = scoring.score_story(5, 5, 5, wires_carrying=0, hours_since_published=1, cfg=CFG)
    gap = scoring.score_story(5, 5, 5, wires_carrying=1, hours_since_published=4, cfg=CFG)
    assert round(gap - base, 3) == 0.8


def test_no_gap_bonus_for_tier_d():
    score = scoring.score_story(5, 5, 5, source_tier=Tier.D, wires_carrying=0, hours_since_published=10, cfg=CFG)
    assert score == 5.0


def test_watch_topic_bonus():
    assert scoring.score_story(5, 5, 5, text="Aid to Gaza", cfg=CFG) == 5.5
