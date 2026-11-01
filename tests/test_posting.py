"""Run with:  python tests/test_posting.py

Captions, buttons, poster/blur, channel isolation (publisher.py) and the autoposter's rules (autopost.py) against
fake Telegram / MongoDB / TMDB / feed objects. No network, no credentials.
"""
import asyncio, io, re, types
from unittest import mock

import _stubs
import autopost, captions, config, media, poster, publisher, texts
from database import DEFAULTS, settings
from tmdb import Tmdb
from vidsrc import FeedError, FeedItem

config.ANNOUNCE_MIN_GAP, config.CHANNEL_MAX_FAILS = 0, 2
real_sleep = asyncio.sleep


# ── fakes ────────────────────────────────────────────────────────────────────
class Msg:
    _n = 100
    def __init__(self, chat): Msg._n += 1; self.id, self.chat = Msg._n, types.SimpleNamespace(id=chat)

class TG:
    def __init__(self): self.sent, self.fail, self.no_spoiler, self.me = [], set(), False, types.SimpleNamespace(username="vidbot")
    def _check(self, chat):
        if chat in self.fail: raise RuntimeError("CHAT_WRITE_FORBIDDEN")
    async def send_message(self, chat, text, reply_markup=None, **kw): self._check(chat); self.sent.append(("text", chat, text, reply_markup, kw)); return Msg(chat)
    async def send_photo(self, chat, photo, caption=None, reply_markup=None, **kw):
        self._check(chat)
        if kw.get("has_spoiler") and self.no_spoiler: raise TypeError("unexpected keyword has_spoiler")
        self.sent.append(("photo", chat, caption, reply_markup, {"photo": photo, **kw})); return Msg(chat)

class Col:
    async def update_one(self, *a, **k): pass

class FakeDB:
    def __init__(self): self.channels, self.seen, self.counters = {}, {}, {}
    async def channels_list(self): return [dict(v) for v in self.channels.values()]
    async def channel_get(self, chat): return dict(self.channels[int(chat)]) if int(chat) in self.channels else None
    async def channel_put(self, chat, **f): self.channels.setdefault(int(chat), {"_id": int(chat)}).update(f)
    async def channel_del(self, chat): self.channels.pop(int(chat), None)
    async def channel_result(self, chat, ok, error=""):
        c = self.channels[int(chat)]; c["fails"] = 0 if ok else c.get("fails", 0) + 1; c["last_error"] = "" if ok else error; return c["fails"]
    async def unseen(self, kind, ids): return [i for i in ids if f"{kind}:{i}" not in self.seen]
    async def mark_seen(self, kind, tid, **f): self.seen[f"{kind}:{tid}"] = f
    async def inc(self, n): self.counters[n] = self.counters.get(n, 0) + 1

class FakeFeed:
    def __init__(self): self.items, self.error, self.calls = {"movie": [], "tv": []}, None, []
    async def latest(self, kind, type_, page):
        self.calls.append((kind, type_, page))
        if self.error: raise FeedError(self.error)
        return list(self.items[kind])

V3 = "0123456789abcdef0123456789abcdef"
def movie(i, poster=True, **k): return {"id": i, "title": f"Movie {i}", "release_date": "2024-01-01", "overview": "Story.", "poster_path": "/p.jpg" if poster else None,
                                        "backdrop_path": "/b.jpg" if poster else None, "genres": [{"name": "Action"}], "vote_average": 7.5, "vote_count": 100, "runtime": 100, **k}
def show(i, **k): return {"id": i, "name": f"Show {i}", "first_air_date": "2020-01-01", "overview": "Tale.", "poster_path": "/s.jpg", "genres": [{"name": "Drama"}],
                          "number_of_seasons": 3, "number_of_episodes": 30, "episode_run_time": [45], "status": "Returning Series", **k}

def make_tmdb(details):
    calls = []
    async def transport(url, headers, params, timeout):
        path = url.split(config.TMDB_BASE)[1]
        calls.append(path)
        r = details.get(path)
        if r is None: return 404, {}, {"status_message": "not found"}
        if isinstance(r, int): return r, {}, {"status_message": "boom"}
        return 200, {}, r
    return Tmdb(transport), calls

def setup(channels=(-1001,), details=None):
    settings._d.clear(); settings._col = Col()
    settings._d.update(tmdb_key=V3, autopost_enabled=True, autopost_max=5, autopost_backfill=0, seeded={}, require_poster=True)
    db, tg, fd = FakeDB(), TG(), FakeFeed()
    for c in channels:
        db.channels[c] = {"_id": c, "title": f"Chan {c}", "auto": True, "template": "default", "blur": "default", "backup": "", "premium": "", "fails": 0}
    t, calls = make_tmdb(details if details is not None else {})
    publisher.db = autopost.db = db
    publisher.tmdb = autopost.tmdb = t
    autopost.feed = fd
    notices = []
    async def notify(client, text): notices.append(text)
    publisher.notify_admins = notify
    return db, tg, fd, autopost.AutoPoster(), calls, notices

def labels(m): return [[b.text for b in row] for row in m.rows]
def urls(m): return [[b.url for b in row] for row in m.rows]


# ── captions ─────────────────────────────────────────────────────────────────
M = media.from_tmdb("movie", movie(1, title="Tom & <Jerry>", tagline="Run!", overview="A long story. " * 80, status="Released"))
S = media.from_tmdb("tv", show(2))

async def t_every_design_is_valid_html_and_fits_telegram():
    M.quality = "1080p"
    for m in (M, S, media.from_tmdb("movie", {})):
        for name in captions.names():
            out = captions.render(name, m)
            assert len(out) <= 1000, (name, len(out))
            assert len(re.findall(r"<blockquote[^>]*>", out)) == out.count("</blockquote>") and "</blockquote expandable>" not in out
    full = captions.render("detailed", M)
    for part in ("Tom &amp; &lt;Jerry&gt;", "(2024)", "7.5/10 (100 votes)", "Runtime:</b> 1h 40m", "Genres:</b> Action", "Status:</b> Released", "Quality:</b> 1080p", "#Action #Movie", "Run!"):
        assert part in full, part
    tv = captions.render("detailed", S)
    assert "3 seasons" in tv and "30 episodes" in tv and "~45m" in tv and "#Drama #TVShow" in tv and "📺" in tv
    assert "\n" not in captions.render("minimal", M) and captions.next_name("minimal") == "detailed"

async def t_a_very_long_story_is_trimmed_never_broken():
    out = captions.render("classic", media.from_tmdb("movie", movie(1, overview="word " * 2000)))
    assert len(out) <= 1000 and out.count("<blockquote expandable>") == 1 and out.count("</blockquote>") == 1 and "…" in out


# ── buttons, links, poster ───────────────────────────────────────────────────
async def t_a_movie_post_has_watch_details_and_the_premium_backup_links():
    db, tg, fd, ap, calls, notices = setup()
    settings._d.update(premium_link="https://t.me/shop", backup_link="https://t.me/backup")
    await publisher.publish(tg, M, [-1001])
    kind, _, caption, markup, _ = tg.sent[0]
    assert labels(markup) == [["▶️ Watch Now", "ℹ️ Details"], ["⭐ Premium", "💾 Back-up"]]
    assert urls(markup) == [["https://vidsrc.mov/embed/movie/1", "https://t.me/vidbot?start=m_1"], ["https://t.me/shop", "https://t.me/backup"]]
    db.channels[-1001]["backup"] = "https://t.me/own"
    tg.sent.clear(); await publisher.publish(tg, M, [-1001])
    assert urls(tg.sent[0][3])[1] == ["https://t.me/shop", "https://t.me/own"], "a channel's own link wins"

async def t_a_series_post_links_to_the_episode_or_s1e1_and_the_picker():
    db, tg, fd, ap, calls, notices = setup()
    await publisher.publish(tg, S, [-1001])
    assert labels(tg.sent[0][3]) == [["▶️ Watch Now", "📚 Episodes"]]
    assert urls(tg.sent[0][3])[0] == ["https://vidsrc.mov/embed/tv/2/1/1", "https://t.me/vidbot?start=t_2"]
    tg.sent.clear(); await publisher.publish(tg, S, [-1001], season=2, episode=4)
    assert labels(tg.sent[0][3])[0][0] == "▶️ Watch S2E4" and urls(tg.sent[0][3])[0][0] == "https://vidsrc.mov/embed/tv/2/2/4"

async def t_the_watch_button_can_open_as_a_telegram_mini_app():
    settings._d["use_webapp"] = True
    b = publisher.watch_button("https://vidsrc.mov/embed/movie/1")
    assert b.web_app.url == "https://vidsrc.mov/embed/movie/1" and b.url is None
    settings._d["use_webapp"] = False
    assert publisher.watch_button("https://x.example/e").url == "https://x.example/e"

async def t_links_are_normalised():
    n = publisher.normalize_link
    assert n("@MyBackup") == "https://t.me/MyBackup" and n("t.me/shop") == "https://t.me/shop"
    for bad in ("", "javascript:alert(1)", "ftp://x", "just text", "@a", "https://"):
        try:
            n(bad); assert False, bad
        except ValueError:
            pass

async def t_the_image_is_the_poster_or_backdrop_and_blur_never_shows_the_sharp_one():
    db, tg, fd, ap, calls, notices = setup(channels=(-1001, -1002))
    assert publisher.pick_image(M) == "https://image.tmdb.org/t/p/w500/p.jpg"
    settings._d["poster_source"] = "backdrop"
    assert publisher.pick_image(M) == "https://image.tmdb.org/t/p/w780/b.jpg"
    only_poster = media.from_tmdb("movie", movie(5, backdrop_path=None))
    assert publisher.pick_image(only_poster).endswith("/p.jpg") and publisher.pick_image(media.from_tmdb("movie", {})) == ""
    settings._d["poster_source"] = "poster"
    db.channels[-1001]["blur"] = "spoiler"
    await publisher.publish(tg, M, [-1001, -1002])
    a, b = tg.sent
    assert a[4]["has_spoiler"] is True and not b[4].get("has_spoiler"), "per-channel blur"
    tg.sent.clear(); tg.no_spoiler = True                                   # a library without spoiler photos: text only, never sharp
    await publisher.publish(tg, M, [-1001])
    assert [s[0] for s in tg.sent] == ["text"]
    tg.sent.clear(); await publisher.publish(tg, media.from_tmdb("movie", {}), [-1002])
    assert tg.sent[0][0] == "text", "no image: a text post"
    async def broken(url): raise ValueError("download failed")
    tg.no_spoiler = False; tg.sent.clear(); db.channels[-1002]["blur"] = "blur"; poster._cache.clear()
    with mock.patch.object(poster, "_fetch", broken):
        await publisher.publish(tg, M, [-1002])
    assert tg.sent[0][4]["has_spoiler"] is True, "blur failed -> spoiler, never the sharp image"

async def t_a_designs_and_blur_can_be_overridden_for_one_post():
    db, tg, fd, ap, calls, notices = setup()
    db.channels[-1001]["template"] = "minimal"
    await publisher.publish(tg, M, [-1001])
    await publisher.publish(tg, M, [-1001], template="detailed", blur="spoiler")
    assert "\n" not in tg.sent[0][2] and "Genres" in tg.sent[1][2] and tg.sent[1][4]["has_spoiler"] is True

async def t_a_broken_channel_does_not_block_the_others_and_gets_paused():
    db, tg, fd, ap, calls, notices = setup(channels=(-1001, -1002))
    tg.fail = {-1002}
    r1 = await publisher.publish(tg, M, [-1001, -1002])
    assert r1[-1001] == "posted" and r1[-1002].startswith("failed") and db.channels[-1002]["auto"] is True
    await publisher.publish(tg, M, [-1001, -1002])
    assert db.channels[-1002]["auto"] is False and notices and "paused" in notices[0].lower()
    assert [c["_id"] for c in await publisher.autopost_targets()] == [-1001]
    assert (await publisher.publish(tg, M, [-77]))[-77] == "unknown channel"


# ── autopost ─────────────────────────────────────────────────────────────────
def feed_items(*ids, q="1080p"): return [FeedItem(i, None, q) for i in ids]
ALL = {f"/movie/{i}": movie(i) for i in range(1, 9)} | {f"/tv/{i}": show(i) for i in range(1, 9)}

async def t_the_first_run_only_records_what_the_feed_already_holds():
    db, tg, fd, ap, calls, notices = setup(details=ALL)
    fd.items["movie"] = feed_items(3, 2, 1)
    r = await ap.run_once(tg)
    assert r["posted"] == 0 and not tg.sent and set(db.seen) == {"movie:1", "movie:2", "movie:3"}
    assert all(v["reason"] == "seed" for v in db.seen.values()) and settings.get("seeded") == {"movie": True, "tv": True}
    fd.items["movie"] = feed_items(4, 3, 2, 1)                              # one new title appears
    r = await ap.run_once(tg)
    assert r["posted"] == 1 and "Movie 4" in tg.sent[0][2] and db.seen["movie:4"]["reason"] == "posted"
    assert (await ap.run_once(tg))["posted"] == 0, "never posted twice"
    assert calls.count("/movie/4") == 1

async def t_backfill_posts_the_newest_few_on_the_first_run():
    db, tg, fd, ap, calls, notices = setup(details=ALL)
    settings._d["autopost_backfill"] = 2
    fd.items["movie"] = feed_items(5, 4, 3, 2, 1)
    r = await ap.run_once(tg)
    assert r["posted"] == 2 and [s[2].count("Movie 4") + s[2].count("Movie 5") for s in tg.sent] == [1, 1]
    assert {k for k, v in db.seen.items() if v["reason"] == "seed"} == {"movie:3", "movie:2", "movie:1"}

async def t_titles_go_out_oldest_first_and_at_most_max_per_check():
    db, tg, fd, ap, calls, notices = setup(details=ALL)
    settings._d.update(seeded={"movie": True, "tv": True}, autopost_max=2)
    fd.items["movie"] = feed_items(5, 4, 3, 2, 1)
    r = await ap.run_once(tg)
    assert r["posted"] == 2 and [re.search(r"Movie (\d)", s[2]).group(1) for s in tg.sent] == ["1", "2"], "release order, oldest first"
    tg.sent.clear(); await ap.run_once(tg); await ap.run_once(tg)
    assert [re.search(r"Movie (\d)", s[2]).group(1) for s in tg.sent] == ["3", "4", "5"], "the rest follow in later checks"

async def t_movies_and_series_share_the_per_check_budget():
    db, tg, fd, ap, calls, notices = setup(details=ALL)
    settings._d.update(seeded={"movie": True, "tv": True}, autopost_max=3)
    fd.items["movie"], fd.items["tv"] = feed_items(2, 1), feed_items(2, 1)
    r = await ap.run_once(tg)
    assert r["posted"] == 3 and len(tg.sent) == 3
    assert fd.calls[:2] == [("movie", "latest", 1), ("tv", "latest", 1)]

async def t_not_on_tmdb_and_no_image_are_skipped_for_good():
    db, tg, fd, ap, calls, notices = setup(details={"/movie/1": movie(1, poster=False), "/movie/3": movie(3)})
    settings._d.update(seeded={"movie": True, "tv": True})
    fd.items["movie"] = feed_items(3, 2, 1)                                  # 2 is unknown to TMDB, 1 has no image
    r = await ap.run_once(tg)
    assert (r["posted"], r["skipped"], r["failed"]) == (1, 2, 0)
    assert db.seen["movie:2"]["reason"] == "tmdb_not_found" and db.seen["movie:1"]["reason"] == "no_image"
    await ap.run_once(tg)
    assert calls.count("/movie/2") == 1, "skipped titles are not asked about again"
    settings._d["require_poster"] = False
    db.seen.clear(); fd.items["movie"] = feed_items(1)
    tg.sent.clear(); await ap.run_once(tg)
    assert tg.sent[0][0] == "text", "with require_poster off an image-less title is posted as text"

async def t_a_failure_leaves_the_title_unseen_so_the_next_check_retries_it():
    db, tg, fd, ap, calls, notices = setup(details={"/movie/1": movie(1), "/movie/2": 500})
    settings._d.update(seeded={"movie": True, "tv": True})
    fd.items["movie"] = feed_items(2, 1)                                     # oldest (1) posts, then 2 hits a TMDB outage
    with mock.patch("asyncio.sleep", _stubs.nosleep):
        r = await ap.run_once(tg)
    assert (r["posted"], r["failed"]) == (1, 1) and "movie:2" not in db.seen
    autopost.tmdb = publisher.tmdb = make_tmdb({"/movie/1": movie(1), "/movie/2": movie(2)})[0]
    assert (await ap.run_once(tg))["posted"] == 1 and "movie:2" in db.seen
    tg.fail = {-1001}                                                        # every channel failing is a failure too
    fd.items["movie"] = feed_items(3, 2, 1)
    ap3 = autopost.AutoPoster(); autopost.tmdb = publisher.tmdb = make_tmdb(ALL)[0]
    assert (await ap3.run_once(tg))["failed"] == 1 and "movie:3" not in db.seen

async def t_a_rejected_key_stops_the_check_and_marks_nothing():
    db, tg, fd, ap, calls, notices = setup(details={"/movie/1": 401})
    settings._d.update(seeded={"movie": True, "tv": True})
    fd.items["movie"] = feed_items(1)
    r = await ap.run_once(tg)
    assert r["error"] == "the TMDB key was rejected" and not db.seen and "key" in autopost.summary_text(r)

async def t_feed_trouble_is_reported_and_one_feed_does_not_block_the_other():
    db, tg, fd, ap, calls, notices = setup(details=ALL)
    settings._d.update(seeded={"movie": True, "tv": True})
    fd.error = "HTTP 500"
    r = await ap.run_once(tg)
    assert "feed" in r["error"] and r["posted"] == 0
    class OnlyMovies(FakeFeed):
        async def latest(self, kind, type_, page):
            if kind == "movie": raise FeedError("down")
            return feed_items(1)
    autopost.feed = OnlyMovies()
    r = await ap.run_once(tg)
    assert r["posted"] == 1 and "movie feed" in r["error"]

async def t_it_refuses_to_run_when_it_cannot_do_anything_useful():
    db, tg, fd, ap, calls, notices = setup(details=ALL)
    settings._d["autopost_enabled"] = False
    assert (await ap.run_once(tg))["error"] == "disabled" and not fd.calls
    fd.items["movie"] = feed_items(1)
    assert (await ap.run_once(tg, force=True))["error"] == "", "Run now ignores the switch"
    settings._d.update(autopost_enabled=True, tmdb_key="")
    assert (await ap.run_once(tg))["error"] == "no_key"
    settings._d["tmdb_key"] = V3; db.channels.clear()
    assert (await ap.run_once(tg))["error"] == "no_channels"
    assert "Nothing done" in autopost.summary_text({"error": "no_channels", "posted": 0, "new": 0})

async def t_two_checks_never_run_at_once():
    db, tg, fd, ap, calls, notices = setup(details=ALL)
    gate = asyncio.Event()
    class SlowFeed(FakeFeed):
        async def latest(self, kind, type_, page): await gate.wait(); return []
    autopost.feed = SlowFeed()
    first = asyncio.create_task(ap.run_once(tg))
    await real_sleep(0.05)
    assert (await ap.run_once(tg))["error"] == "busy"
    gate.set(); await first

async def t_posts_go_to_every_autopost_channel_and_skip_paused_ones():
    db, tg, fd, ap, calls, notices = setup(channels=(-1001, -1002, -1003), details=ALL)
    db.channels[-1003]["auto"] = False
    settings._d.update(seeded={"movie": True, "tv": True})
    fd.items["movie"] = feed_items(1)
    await ap.run_once(tg)
    assert sorted(s[1] for s in tg.sent) == [-1002, -1001] and len(tg.sent) == 2, "both autopost channels, not the paused one"
    assert calls.count("/movie/1") == 1, "one TMDB call serves every channel"

async def t_end_to_end_with_the_reference_feed_response():
    """The exact JSON from the vidsrc reference, through the real feed client + parser, TMDB client and publisher."""
    import vidsrc
    reference = {"status": 200, "type": "movie", "result": [{"imdb_id": "tt17048514", "tmdb_id": "927085", "quality": "1080p"}]}
    urls_seen = []
    async def feed_transport(url, headers, params, timeout):
        urls_seen.append(url); return (200, {}, reference) if "/movie/" in url else (200, {}, {"status": 200, "type": "tv", "result": []})
    db, tg, fd, ap, calls, notices = setup(details={"/movie/927085": movie(927085, title="Heart of Stone")})
    settings._d.update(seeded={"movie": True, "tv": True})
    autopost.feed = vidsrc.Feed(feed_transport)
    r = await ap.run_once(tg)
    assert r["posted"] == 1 and urls_seen == ["https://vidsrc.mov/vapi/movie/latest/1", "https://vidsrc.mov/vapi/tv/latest/1"]
    kind, chat, caption, markup, kw = tg.sent[0]
    assert kind == "photo" and kw["photo"] == "https://image.tmdb.org/t/p/w500/p.jpg" and chat == -1001
    assert "Heart of Stone" in caption and "Quality:</b> 1080p" in caption
    assert urls(markup)[0][0] == "https://vidsrc.mov/embed/movie/927085", "the embed link from the reference"
    assert db.seen["movie:927085"]["reason"] == "posted" and calls == ["/movie/927085"]


async def main():
    for name, fn in list(globals().items()):
        if name.startswith("t_"):
            await fn()
            print("ok ", name[2:].replace("_", " "))
    print("all posting checks passed")

asyncio.run(main())
