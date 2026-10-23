import json
from datetime import date, datetime, timedelta, timezone

from newsbot import calendar_islam, claims, config, corrections, factcheck, ingest, pipeline, sources
from newsbot.models import Article, Tier
from newsbot.publish import site


def art(url="https://e.com/a", title="UN OCHA reports aid", summary="UN OCHA said 120 aid trucks entered Gaza on Monday.",
        tier=Tier.A, published=None):
    return Article(url=url, title=title, summary=summary, source_name="UN OCHA", tier=tier, published=published)


# --- config ---------------------------------------------------------------------------------
def test_config_loads_in_review_mode_with_only_text_platforms_on():
    cfg = config.load_config()
    assert cfg["mode"] == "review"  # every post needs a human tap
    assert {k for k, v in cfg["enable"].items() if v} == {"facebook", "threads"}  # instagram needs image cards
    assert cfg["limits"]["MAX_POSTS_PER_DAY"] == 10


def test_redact_removes_secret_values(monkeypatch):
    monkeypatch.setenv("SOME_API_KEY", "abcdef123456")
    assert "abcdef123456" not in config.redact("url?key=abcdef123456")


def test_require_env_lists_names_not_values(monkeypatch):
    monkeypatch.delenv("NOPE_ONE", raising=False)
    try:
        config.require_env("NOPE_ONE")
    except config.MissingSecret as exc:
        assert "NOPE_ONE" in str(exc)


# --- sources / ingest -----------------------------------------------------------------------
def test_seed_sources_load_and_tiers_valid():
    srcs = sources.load_sources()
    assert srcs and all(s.tier in (Tier.A, Tier.B, Tier.C) for s in srcs)
    assert all(s.tier != Tier.D for s in srcs)  # Tier D is never a registry source


def test_state_funded_outlets_are_labelled():
    aj = sources.by_name("Al Jazeera English")
    assert "state-funded" in sources.attribution_phrase(aj)


def test_dedupe_by_url_and_title():
    items = [art("https://e.com/a"), art("https://e.com/a/"), art("https://e.com/b", title="UN OCHA reports aid!")]
    assert len(ingest.dedupe(items)) == 1


def test_dedupe_skips_already_seen():
    assert ingest.dedupe([art()], seen_keys={art().key}) == []


def test_48h_window():
    now = datetime(2026, 10, 23, tzinfo=timezone.utc)
    old = art(published=now - timedelta(hours=49))
    new = art("https://e.com/n", title="new", published=now - timedelta(hours=2))
    assert ingest.within_window([old, new], now) == [new]


def test_parse_feed_strips_html():
    fake = {"entries": [{"link": "https://e.com/1", "title": "<b>Hello</b>", "summary": "<p>Body text</p>"}]}
    out = ingest.parse_feed(sources.load_sources()[0], fake)
    assert out[0].title == "Hello" and out[0].summary == "Body text"


# --- claims ---------------------------------------------------------------------------------
def test_parse_claims_tolerates_code_fences_and_garbage():
    a = art()
    raw = '```json\n[{"text": "120 aid trucks entered Gaza", "attribution": "UN OCHA"}]\n```'
    assert claims.parse_claims(raw, a)[0].attribution == "UN OCHA"
    assert claims.parse_claims("not json", a) == []


def test_hallucinated_claim_is_dropped():
    a = art()

    def ai(_prompt):
        return json.dumps([
            {"text": "120 aid trucks entered Gaza on Monday", "attribution": "UN OCHA"},
            {"text": "A secret treaty was signed by distant ministers", "attribution": "UN OCHA"},
        ])

    out = claims.extract_claims(a, ai)
    assert len(out) == 1 and "trucks" in out[0].text


# --- pipeline -------------------------------------------------------------------------------
def make_ai(draft_text):
    def ai(prompt):
        if prompt.startswith("You extract"):
            return json.dumps([{"text": "120 aid trucks entered Gaza on Monday", "attribution": "UN OCHA"}])
        return draft_text
    return ai


CLEAN = "Aid trucks reach Gaza\nUN OCHA said 120 aid trucks entered Gaza on Monday."


def test_review_mode_sends_everything_to_approval():
    out = pipeline.process_article(art(), make_ai(CLEAN), {**config.load_config(), "mode": "review"})
    assert out.action == "needs_approval"


def test_auto_mode_green_or_amber_is_ready():
    out = pipeline.process_article(art(), make_ai(CLEAN), {**config.load_config(), "mode": "auto"})
    assert out.action == "ready"


def test_red_always_needs_approval_even_in_auto():
    red = "Strike reported\nUN OCHA said children killed in a strike, according to the report."
    # numbers/claims still pass; lane is Red because of child victims
    ai = make_ai(red)
    out = pipeline.process_article(art(), ai, {**config.load_config(), "mode": "auto"})
    assert out.action == "needs_approval" and "child_victims" in out.reason


def test_draft_failing_checks_twice_is_skipped():
    bad = "Headline\nWhat is happening is genocide."
    out = pipeline.process_article(art(), make_ai(bad), {**config.load_config(), "mode": "auto"})
    assert out.action == "skipped"


def test_tier_d_article_is_held():
    out = pipeline.process_article(art(tier=Tier.D), make_ai(CLEAN), {**config.load_config(), "mode": "auto"})
    assert out.action == "held"


def test_dry_run_publishes_nothing_and_needs_no_credentials():
    cfg = {**config.load_config(), "enable": {**config.load_config()["enable"], "facebook": True, "instagram": True}}
    out = pipeline.process_article(art(), make_ai(CLEAN), {**cfg, "mode": "auto"})
    results = pipeline.publish_draft(out.draft, "https://x/y.jpg", dry_run=True, cfg=cfg)
    assert results and all(r.skipped for r in results)


def test_disabled_platforms_are_not_touched():
    cfg = {**config.load_config(), "enable": {k: False for k in config.load_config()["enable"]}}
    out = pipeline.process_article(art(), make_ai(CLEAN), {**cfg, "mode": "auto"})
    assert pipeline.publish_draft(out.draft, "https://x/y.jpg", dry_run=False, cfg=cfg) == []


# --- corrections / site / calendar / factcheck -----------------------------------------------
def test_corrections_log_roundtrip_and_kpi(tmp_path):
    p = tmp_path / "c.jsonl"
    corrections.add_correction("fb_1", "Fixed a figure", path=p)
    corrections.add_correction("ig_2", "Retracted", kind="retraction", manual_removal_pending=True, path=p)
    items = corrections.read_corrections(p)
    assert len(items) == 2 and items[1].manual_removal_pending
    assert corrections.corrections_per_100_posts(200, p) == 1.0
    assert "Retracted" in corrections.render_html(p)


def test_site_page_is_sourced_and_escaped(tmp_path):
    html = site.render_post("A <b>title</b>", "Para one\n\nPara two", ["https://e.com/src"], label="On-ground report")
    assert "&lt;b&gt;" in html and "https://e.com/src" in html and "On-ground report" in html
    path = site.write_post("Aid reaches Gaza", "Body", ["https://e.com/s"], out_dir=tmp_path)
    assert path.exists() and "aid-reaches-gaza" in str(path)


def test_election_blackout_reminder_and_window():
    cfg = {"election_silence": {"reminder_days_before": 14,
                                "blackouts": [{"country": "India", "date": "2026-11-10", "days": 2}]}}
    assert calendar_islam.blackout_reminders(date(2026, 10, 27), cfg)
    assert not calendar_islam.blackout_reminders(date(2026, 10, 28), cfg)
    assert calendar_islam.is_blackout(date(2026, 11, 11), cfg, "India")
    assert not calendar_islam.is_blackout(date(2026, 11, 12), cfg, "India")


def test_upcoming_occasions_with_fake_api():
    def fake(url, params):
        if "gToH" in url:
            return {"data": {"hijri": {"year": "1448", "month": {"number": 1}, "day": "1"}}}
        d, m, y = url.rsplit("/", 1)[1].split("-")
        # pretend every hijri date maps to a fixed Gregorian day offset from 2026-10-23
        day = 23 + (int(m) - 1) * 0 + (int(d) - 1)
        return {"data": {"gregorian": {"date": f"{min(day, 28):02d}-10-2026"}}}

    found = calendar_islam.upcoming_occasions(date(2026, 10, 23), 3, fake)
    assert any(o["name"] == "Islamic New Year" for o in found)
    assert all("moon sighting" in o["moon_note"] for o in found)


def test_factcheck_rules():
    own = factcheck.FactCheck("claim", "false", ["https://a"])
    assert not factcheck.can_publish(own)[0]  # needs two sources
    own.evidence_urls.append("https://b")
    assert factcheck.can_publish(own)[0]
    rel = factcheck.FactCheck("claim", "false", ["https://a", "https://b"], religious=True)
    assert not factcheck.can_publish(rel)[0]  # needs scholar review
    rel.scholar_reviewed = True
    assert factcheck.can_publish(rel)[0]
    credited = factcheck.FactCheck("claim", "false", ["https://a"], credited_checker="Alt News")
    assert factcheck.can_publish(credited)[0]


def test_religious_ruling_backstop():
    assert factcheck.contains_religious_ruling("The fatwa on this is clear")
    assert not factcheck.contains_religious_ruling("Eid al-Fitr is on 10 Shawwal")
