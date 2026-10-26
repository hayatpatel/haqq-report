from newsbot import cards, photos, pipeline, runner, template
from newsbot.config import load_config
from newsbot.models import Draft, Lane
from newsbot.pipeline import PublishResult

CFG = load_config()
D = Draft(headline="Aid trucks reach Gaza City for first time in weeks", body="OCHA said 40 trucks crossed.",
          lane=Lane.GREEN, source_urls=["https://news.un.org/x"])


def test_only_safe_licences_are_accepted():
    assert all(photos.license_ok(x) for x in ["CC BY 2.0", "CC BY 4.0", "CC0", "Public domain"])
    assert not any(photos.license_ok(x) for x in ["CC BY-SA 4.0", "CC BY-NC 2.0", "CC BY-ND 2.0", "", "Fair use"])


def test_graphic_or_child_files_are_blocked():
    assert photos.BAD.search("Dead child in Gaza") and photos.BAD.search("Funeral procession")
    assert not photos.BAD.search("Gaza City skyline")


def test_search_filters_licence_and_title(monkeypatch):
    def page(i, title, lic, artist="A. Photographer"):
        return {"title": title, "index": i, "imageinfo": [{"thumburl": "u", "descriptionurl": "d", "width": 3000, "height": 2000,
                "extmetadata": {"LicenseShortName": {"value": lic}, "Artist": {"value": artist}}}]}
    pages = [page(1, "File:Gaza SA.jpg", "CC BY-SA 4.0"), page(2, "File:Gaza dead children.jpg", "CC BY 2.0"),
             page(3, "File:Gaza City street.jpg", "CC BY 2.0"), page(4, "File:Gaza harbour.jpg", "CC0", "")]

    class R:
        def json(self): return {"query": {"pages": pages}}
    monkeypatch.setattr(photos.requests, "get", lambda *a, **k: R())
    got = photos.search("Gaza City", "gaza", CFG, set())
    assert got.title == "File:Gaza City street.jpg" and got.credit() == "Representative photo: A. Photographer / Wikimedia Commons, CC BY 2.0"


def test_caption_carries_photo_credit_within_limit():
    D.photo_credit = "Representative photo: A. Photographer / Wikimedia Commons, CC BY 2.0"
    for platform in ("facebook", "instagram", "threads"):
        text = template.build_caption(D, platform, cfg=CFG)
        assert "Wikimedia Commons" in text and len(text) <= CFG["caption_limits"][platform]
    D.photo_credit = ""


def test_story_and_feed_cards_have_right_size():
    from io import BytesIO
    from PIL import Image
    for size in ((1080, 1350), (1080, 1920)):
        img = Image.open(BytesIO(cards.render_layout(D, CFG, size, None, "Representative photo: X / Wikimedia Commons, CC0")))
        assert img.size == size
    photo = BytesIO(); Image.new("RGB", (1600, 900), (200, 50, 50)).save(photo, "JPEG")
    assert Image.open(BytesIO(cards.render_layout(D, CFG, (1080, 1920), photo.getvalue(), "c"))).size == (1080, 1920)


def test_publish_posts_stories_and_skips_done_platforms(monkeypatch):
    calls = []
    media = cards.Media(feed_url="https://x/f.jpg", story_url="https://x/s.jpg", credit="Representative photo: A / Wikimedia Commons, CC0")
    monkeypatch.setattr(cards, "build", lambda *a, **k: media)
    monkeypatch.setattr(pipeline.meta_fb, "post_photo", lambda u, c: calls.append("fb") or "1")
    monkeypatch.setattr(pipeline.meta_fb, "first_comment", lambda *a: "c")
    monkeypatch.setattr(pipeline.meta_fb, "post_story", lambda u: calls.append("fbs") or "2")
    monkeypatch.setattr(pipeline.instagram, "publish_image", lambda u, c: calls.append("ig") or "3")
    monkeypatch.setattr(pipeline.instagram, "publish_story", lambda u: calls.append("igs") or "4")
    monkeypatch.setattr(pipeline.instagram, "permalink", lambda i: "")
    monkeypatch.setattr(pipeline.threads, "publish", lambda c, i=None: calls.append("th") or "5")
    monkeypatch.setattr(pipeline.threads, "permalink", lambda i: "")
    monkeypatch.setattr(pipeline.owner_feed, "post_results", lambda *a, **k: None)
    res = pipeline.publish_draft(Draft(headline="h", body="b", source_urls=["https://x"]), dry_run=False, cfg=CFG)
    assert sorted(calls) == ["fb", "fbs", "ig", "igs", "th"] and all(r.ok for r in res)
    calls.clear()
    pipeline.publish_draft(Draft(headline="h", body="b"), dry_run=False, cfg=CFG, story_only=True)
    assert sorted(calls) == ["fbs", "igs"]
    calls.clear()
    pipeline.publish_draft(Draft(headline="h", body="b"), dry_run=False, cfg=CFG, skip={"facebook", "facebook_story"})
    assert sorted(calls) == ["ig", "igs", "th"]


def test_story_only_when_daily_cap_reached_and_not_when_paused(monkeypatch):
    class S:
        rows = []
        def select_raw(self, t, p):
            if t == "settings": return [{"value": "true" if self.paused else "false"}]
            if t == "posts": return [r for r in self.rows]
            return [{"id": i} for i in range(20)]  # drafts posted today: over the cap
        def insert(self, t, row): self.rows.append(row)
    s = S(); s.paused = False
    seen = []
    monkeypatch.setattr(runner, "is_paused", lambda st: st.paused)
    monkeypatch.setattr(pipeline, "publish_draft", lambda *a, **k: seen.append(k) or [PublishResult("facebook_story", ok=True, post_id="9")])
    runner.publish_ready(s, 1, D, CFG, {})
    assert seen and seen[0]["story_only"] is True and s.rows[0]["platform"] == "facebook_story"
    seen.clear(); s.paused = True
    runner.publish_ready(s, 1, D, CFG, {})
    assert not seen


def test_gaza_needs_recent_destruction_photos(monkeypatch):
    def page(i, title, ts, desc=""):
        return {"title": title, "index": i, "imageinfo": [{"thumburl": "u", "descriptionurl": "d", "width": 3000, "height": 2000, "timestamp": ts,
                "extmetadata": {"LicenseShortName": {"value": "CC BY 4.0"}, "Artist": {"value": "A"}, "ImageDescription": {"value": desc}}}]}
    pages = [page(1, "File:Gaza City skyline.jpg", "2025-01-01T00:00:00Z"),
             page(2, "File:Gaza destroyed buildings 2014.jpg", "2014-08-01T00:00:00Z"),
             page(3, "File:Gaza rubble of destroyed buildings.jpg", "2025-03-01T00:00:00Z")]

    class R:
        def json(self): return {"query": {"pages": pages}}
    monkeypatch.setattr(photos.requests, "get", lambda *a, **k: R())
    assert photos.search("Gaza rubble", "gaza", CFG, set(), war=True).title == "File:Gaza rubble of destroyed buildings.jpg"
    assert photos.queries("Aid reaches Gaza City", "", [])[0][0] == "Gaza City destroyed buildings"


def test_ai_art_falls_back_and_stops_when_free_allowance_is_gone(monkeypatch):
    from newsbot import ai_art
    store = type("S", (), {"d": {}, "get_setting": lambda s, k, d=None: s.d.get(k, d),
                           "set_setting": lambda s, k, v: s.d.__setitem__(k, v)})()
    cfg = {"ai_art": {"enabled": True, "max_per_day": 2}}
    assert ai_art.generate("GAZA", cfg, store)[0] is None  # no keys: brand card
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "a"); monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t")

    class R:
        status_code = 429; text = "daily free allocation"
    monkeypatch.setattr(ai_art.requests, "post", lambda *a, **k: R())
    img, note = ai_art.generate("GAZA", cfg, store)
    assert img is None and "used up" in note and store.d[ai_art._day()] == "off"
    monkeypatch.setattr(ai_art.requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call after off")))
    assert ai_art.generate("GAZA", cfg, store)[0] is None
    assert "no people" in ai_art.prompt_for("GAZA") and "Gaza" not in ai_art.prompt_for("GAZA")


def test_every_platform_caption_fits_even_with_credit_and_long_google_link():
    from newsbot import variants
    d = Draft(headline="Mohamed Salah at the centre of Turkish religious controversy", body="Reports say " + "x" * 900,
              source_urls=["https://news.google.com/rss/articles/" + "A" * 300], category="celebrity", tags=["#MohamedSalah"], source_label="Al Jazeera",
              photo_credit="AI-generated illustration (not a photo of the event)")
    for platform in ("facebook", "instagram", "threads"):
        text = template.build_caption(d, platform, cfg=CFG)
        assert len(text) <= CFG["caption_limits"][platform] and "AI-generated illustration" in text and "#HaqqReport" in text or platform == "threads"
    assert len(template.build_caption(d, "threads", cfg=CFG)) <= 480
    assert "news.google.com" not in template.build_caption(d, "threads", cfg=CFG)
    assert "Source: Al Jazeera" in template.build_caption(d, "facebook", cfg=CFG)
    assert template.build_caption(d, "instagram", cfg=CFG).count("#") <= 5


def test_platform_variants_are_checked_and_used():
    from newsbot import variants
    d = Draft(headline="Aid reaches Gaza", body="OCHA said 40 trucks crossed on Thursday.", source_urls=["https://news.un.org/x"])
    out = variants.adapt(d, lambda p: '{"facebook": "OCHA said 40 trucks crossed. What will this change for families?", '
                                      '"instagram": "Like if you care! 40 trucks crossed.", "threads": "OCHA says 90 trucks crossed."}', CFG)
    assert list(out) == ["facebook"]  # instagram = engagement bait, threads = invented number
    d.variants = out
    assert "What will this change" in template.build_caption(d, "facebook", cfg=CFG)
    assert "OCHA said 40 trucks crossed on Thursday." in template.build_caption(d, "threads", cfg=CFG)  # fallback body
    assert variants.adapt(d, lambda p: "not json", CFG) == {}
