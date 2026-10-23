from datetime import datetime, timedelta, timezone

from newsbot import runner, safety
from newsbot.models import Article, Draft, Tier

NOW = datetime.now(timezone.utc)
CFG = {"run": {"max_articles": 6, "max_per_source": 2, "off_mission_max": 1},
       "watch_topics": ["gaza", "sudan"], "mission_keywords": ["muslim"]}


def art(n, title, source="UN News", tier=Tier.A, age=0):
    return Article(url=f"https://e.com/{n}", title=title, summary="", source_name=source, tier=tier,
                   published=NOW - timedelta(hours=age))


def test_on_mission_first_with_source_cap_and_off_mission_quota():
    fresh = [art(1, "Bird count rises"), art(2, "Gaza aid update"), art(3, "Sudan famine"),
             art(4, "Gaza hospital"), art(5, "Muslim council meets", source="MEE"),
             art(6, "Football final"), art(7, "Stock markets")]
    picked = runner.select_for_run(fresh, CFG)
    titles = [a.title for a in picked]
    assert titles.count("Gaza aid update") == 1
    assert sum(1 for a in picked if a.source_name == "UN News") <= 2 + 1  # cap 2 on-mission + 1 off-mission slot
    assert sum(1 for a in picked if not runner.on_mission(a, CFG)) <= 1
    assert "Muslim council meets" in titles


def test_vague_attribution_fails_named_source_passes():
    bad = Draft(headline="H", body="The agency said 14 people were killed in the strike.")
    good = Draft(headline="H", body="OCHA said 14 people were killed in the strike.")
    assert not safety.check_draft(bad, cfg={}).ok
    assert not any("named source" in f for f in safety.check_draft(good, cfg={}).failures)


def test_clean_headline_and_emoji():
    assert safety.clean_headline('**"Aid reaches Gaza"**') == "Aid reaches Gaza"
    assert safety.strip_emoji("Flood kills 5 \U0001F622") == "Flood kills 5"
    assert safety.is_sensitive("Flood kills 5") and not safety.is_sensitive("New school opens")
