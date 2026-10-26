import json
from datetime import datetime, timedelta, timezone

import pytest

from newsbot import celebrity, config, fasttrack, islamophobia, runner, severity
from newsbot.models import Article, Draft, Tier
from newsbot.safety import check_draft

NOW = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)
CFG = {**config.load_config()}


def art(n, title, summary="", source="Al Jazeera English", tier=Tier.B, mins=20):
    return Article(url=f"https://e.com/{n}", title=title, summary=summary, source_name=source, tier=tier,
                   published=NOW - timedelta(minutes=mins))


# ---------- severity
@pytest.mark.parametrize("text,level", [
    ("Israeli strike kills 45 in Gaza school shelter, health ministry says", 3),
    ("ICJ issues ruling on Gaza genocide case", 3),
    ("Ceasefire announced between Israel and Hamas", 3),
    ("Mosque set on fire in northern England", 3),
    ("Airstrike kills 7 in Sudan market", 2),
    ("Council debates hijab ban in schools", 2),
    ("Minister attends conference on trade", 0),
])
def test_severity_levels(text, level):
    assert severity.severity(text)[0] == level


def test_casualty_numbers_are_read():
    assert severity.casualties("At least 1,200 people were killed") == 1200
    assert severity.casualties("The death toll rises to 31") == 31
    assert severity.casualties("Three meetings were held") == 0


# ---------- islamophobia
def test_islamophobia_detection():
    assert islamophobia.is_islamophobia("Police investigate arson attack on mosque in Kent")
    assert islamophobia.is_islamophobia("Tell MAMA reports rise in anti-Muslim incidents")
    assert not islamophobia.is_islamophobia("Mosque opens new library for students")


# ---------- classify
def test_fast_track_takes_fresh_severe_on_mission_only():
    watch = celebrity.watchlist(CFG)
    ok = art(1, "Israeli strike kills 45 in Gaza school, ministry says")
    old = art(2, "Israeli strike kills 45 in Gaza school, ministry says", mins=400)
    calm = art(3, "Gaza hosts football tournament")
    offtopic = art(4, "Storm kills 30 in Florida")
    assert fasttrack.classify(ok, CFG, watch, NOW) == ("breaking", 3)
    assert fasttrack.classify(old, CFG, watch, NOW) is None
    assert fasttrack.classify(calm, CFG, watch, NOW) is None
    assert fasttrack.classify(offtopic, CFG, watch, NOW) is None


# ---------- celebrity rules
class KV:
    def __init__(self):
        self.d = {}

    def get_setting(self, k, default=""):
        return self.d.get(k, default)

    def set_setting(self, k, v):
        self.d[k] = v

    def try_insert(self, table, row):
        if row["key"] in self.d:
            return False
        self.d[row["key"]] = row["value"]; return True

    def cas_setting(self, k, old, new):
        if self.d.get(k) != old:
            return False
        self.d[k] = new; return True

    def delete(self, table, params):
        self.d.pop(params["key"].removeprefix("eq."), None); return 1


def test_celebrity_match_uses_headline_only():
    watch = celebrity.watchlist(CFG)
    assert celebrity.match(art(1, "Mo Salah scores twice against City"), watch) == "Mohamed Salah"
    assert celebrity.match(art(2, "Liverpool win again", "Salah scored"), watch) is None


def test_one_post_per_celebrity_per_day_with_one_critical_update():
    kv, name = KV(), "Mohamed Salah"
    assert celebrity.lock(kv, name, 2, CFG, NOW) == "first"
    assert celebrity.lock(kv, name, 2, CFG, NOW) is None          # second routine item: skipped
    assert celebrity.lock(kv, name, 3, CFG, NOW) == "update"      # critical news: one UPDATE
    assert celebrity.lock(kv, name, 3, CFG, NOW) is None          # but only one
    next_day = NOW + timedelta(days=1)
    assert celebrity.lock(kv, name, 2, CFG, next_day) == "first"  # a new day starts fresh


def test_failed_draft_gives_the_slot_back():
    kv, name = KV(), "Riz Ahmed"
    assert celebrity.lock(kv, name, 2, CFG, NOW) == "first"
    celebrity.release(kv, name, "first", CFG, NOW)
    assert celebrity.lock(kv, name, 2, CFG, NOW) == "first"


def test_event_level_and_gossip_guard():
    assert celebrity.event_level("Salah scores hat-trick in win") == 2
    assert celebrity.event_level("Footballer dies aged 40, club confirms") == 3
    assert celebrity.event_level("Star dating rumours swirl after dinner") == 0
    d = Draft(headline="Star seen", body="Reportedly dating a model, sources say.", category="celebrity")
    assert any("gossip" in f for f in check_draft(d, "", CFG).failures)


def test_combine_puts_all_outlets_into_one_article():
    main = art(1, "Salah scores twice", "Liverpool beat City 3-1.", source="BBC Sport")
    other = art(2, "Salah named man of the match", "Awarded by Sky.", source="Sky Sports")
    merged = celebrity.combine(main, [other], "Mohamed Salah", "first")
    assert "BBC Sport reports" in merged.summary and "Sky Sports reports" in merged.summary
    assert merged.related_urls == [other.url]
    upd = celebrity.combine(main, [], "Mohamed Salah", "update", earlier="Salah scores twice")
    assert "NEW DEVELOPMENT" in upd.summary


# ---------- pillar and severity ordering in selection
def test_selection_reserves_islamophobia_and_palestine_slots_and_ranks_by_severity():
    cfg = {**CFG, "run": {"max_articles": 3, "max_per_source": 3, "off_mission_max": 0}}
    a = [art(1, "Gaza minister meets envoy"), art(2, "Airstrike kills 30 in Gaza, ministry says"),
         art(3, "Arson attack on mosque in Kent, police say", source="BBC News Middle East"),
         art(4, "Sudan talks resume")]
    picked = [x.title for x in runner.select_for_run(a, cfg)]
    assert "Arson attack on mosque in Kent, police say" in picked
    assert picked[0] in ("Airstrike kills 30 in Gaza, ministry says", "Arson attack on mosque in Kent, police say")
    assert len(picked) == 3


# ---------- the whole cycle, with fakes
class DB(KV):
    def __init__(self):
        super().__init__()
        self.articles, self.drafts, self.posts = {}, [], []

    def upsert(self, table, row, on_conflict):
        if table == "settings":
            self.d[row["key"]] = row["value"]; return row
        a = self.articles.setdefault(row["url"], {"id": len(self.articles) + 1, "processed": False, **row})
        return a

    def insert(self, table, row):
        if table == "drafts":
            row = {"id": len(self.drafts) + 1, "created_at": NOW.isoformat(), **row}
            self.drafts.append(row)
        elif table == "posts":
            self.posts.append(row)
        return row

    def select_raw(self, table, params):
        if table == "articles":
            return [{"url": u, "processed": a["processed"]} for u, a in self.articles.items()]
        if table == "drafts":
            rows = [d for d in self.drafts if d["status"] in ("pending", "approved", "posted")]
            if params.get("status") == "eq.posted":
                rows = [d for d in self.drafts if d["status"] == "posted"]
            if "flags" in params:
                rows = [d for d in rows if "cat:breaking" in d["flags"]]
            return rows
        return []

    def update(self, table, match, values):
        if table == "articles":
            next(a for a in self.articles.values() if a["id"] == match["id"]).update(values)
        elif table == "drafts":
            next(d for d in self.drafts if d["id"] == int(match["id"])).update(values)

    def claim_flag(self, table, row_id, column, from_value, to_value):
        a = next(a for a in self.articles.values() if a["id"] == row_id)
        if a[column] != from_value:
            return False
        a[column] = to_value; return True

    def claim(self, table, row_id, from_status, to_status, extra=None):
        d = next(d for d in self.drafts if d["id"] == int(row_id))
        if d["status"] != from_status:
            return False
        d["status"] = to_status; return True


def fake_ai(prompt):
    if prompt.startswith("You extract factual claims"):
        if "Salah" in prompt:
            return json.dumps([{"text": "Mo Salah scores twice against City. Liverpool won 3-1.", "attribution": "BBC Sport"}])
        return json.dumps([{"text": "45 people were killed in a strike on a school shelter", "attribution": "the Gaza Health Ministry"}])
    if "public figure" in prompt:
        return "Salah scores twice in Liverpool win\nBBC Sport reported that Mo Salah scored twice against City as Liverpool won 3-1."
    return ("Strike on Gaza school shelter kills 45, ministry says\nAl Jazeera reported that the Gaza Health Ministry "
            "said 45 people were killed in a strike on a school shelter.")


@pytest.fixture
def posted(monkeypatch):
    from newsbot import pipeline
    from newsbot.publish.base import PublishResult
    sent = []
    monkeypatch.setattr(pipeline, "publish_draft", lambda d, *a, **k: sent.append(d) or [PublishResult("facebook", ok=True, post_id="1_1")])
    return sent


STRIKE = ("Israeli strike kills 45 in Gaza school, ministry says",
          "Al Jazeera reports the Gaza Health Ministry said 45 people were killed in a strike on a school shelter.")


def test_critical_story_is_posted_once_even_if_seen_again(posted):
    db, cfg = DB(), {**CFG, "mode": "auto", "limits": {**CFG["limits"]}}
    feed = lambda: [art(1, STRIKE[0], STRIKE[1]), art(2, "Gaza school strike kills 45, ministry says", STRIKE[1], source="Middle East Eye")]
    first = fasttrack.cycle(db, cfg, fake_ai, {}, fetcher=feed, celeb_fetcher=lambda w: [], now=NOW)
    assert first["published"] == 1 and len(posted) == 1                   # same event from two outlets: ONE post
    assert posted[0].category == "breaking"
    again = fasttrack.cycle(db, cfg, fake_ai, {}, fetcher=feed, celeb_fetcher=lambda w: [], now=NOW)
    assert again["published"] == 0 and len(posted) == 1                   # next cycle: nothing new to do


def test_celebrity_gets_one_combined_post_a_day(posted):
    db, cfg = DB(), {**CFG, "mode": "auto"}
    items = lambda w: [art(11, "Mo Salah scores twice against City", "Liverpool won 3-1.", source="BBC Sport"),
                       art(12, "Mohamed Salah wins man of the match award", "Sky Sports says he was outstanding.", source="Sky Sports")]
    s1 = fasttrack.cycle(db, cfg, fake_ai, {}, fetcher=lambda: [], celeb_fetcher=items, now=NOW)
    assert s1["published"] == 1 and posted[0].category == "celebrity" and "#MohamedSalah" in posted[0].tags
    assert len(posted[0].source_urls) == 2                                # both outlets feed ONE post
    later = lambda w: [art(13, "Salah signs new contract", "Club confirms.", source="BBC Sport", mins=5)]
    s2 = fasttrack.cycle(db, cfg, fake_ai, {}, fetcher=lambda: [], celeb_fetcher=later, now=NOW + timedelta(hours=2))
    assert s2["published"] == 0 and s2.get("celebrity_repeat") == 1       # same day: skipped


def test_weekly_report_names_best_hours_and_is_sent_once_a_week(monkeypatch):
    from newsbot import insights, owner_feed
    sent = []
    monkeypatch.setattr(owner_feed, "message", sent.append)
    mon = datetime(2026, 10, 26, 4, 0, tzinfo=timezone.utc)  # Monday 09:30 IST

    class S(KV):
        def select_raw(self, table, params):
            return [{"key": f"stat:{i}", "value": json.dumps({"t": "2026-10-22T15:30:00+00:00", "p": "threads", "h": f"H{i}",
                                                              "c": "general", "tp": "Gaza", "m": {}, "s": 10 + i})} for i in range(4)]
    st = S()
    assert insights.weekly_report(st, CFG, mon) is True
    assert "Best posting hours (IST)" in sent[0] and "21:00 to 24:00" in sent[0]
    assert insights.weekly_report(st, CFG, mon) is False          # already sent this week
    assert insights.weekly_report(S(), CFG, mon - timedelta(days=1)) is False  # not Monday
