"""Run with:  python tests/test_views.py   — the pages users see (views.py), how they are shown (ui.py) and the handlers
that connect them (plugins/start.py, plugins/browse.py), against fake TMDB data and fake chat objects."""
import asyncio, types
from unittest import mock

import _stubs
import config, texts, ui, views, vidsrc
import publisher
from database import settings
from tmdb import Tmdb, TmdbError
from vidsrc import FeedError, FeedItem

sys_types = _stubs.sys.modules["pyrogram.types"]
sys_types.InputMediaPhoto = lambda url, caption=None: ("media", url, caption)
from plugins import browse, start

V3 = "0123456789abcdef0123456789abcdef"
settings._d.update(tmdb_key=V3, premium_link="", backup_link="", use_webapp=False, poster_source="poster", adult=False)

MOVIE = {"id": 603, "title": "The <Matrix> & Co", "release_date": "1999-03-30", "overview": "A hacker learns the truth.", "genres": [{"name": "Action"}],
         "vote_average": 8.2, "vote_count": 25000, "runtime": 136, "status": "Released", "poster_path": "/m.jpg", "backdrop_path": "/mb.jpg"}
SHOW = {"id": 1399, "name": "Game of Thrones", "first_air_date": "2011-04-17", "overview": "Nine families.", "genres": [{"name": "Drama"}], "vote_average": 8.4,
        "vote_count": 20000, "number_of_seasons": 2, "number_of_episodes": 45, "episode_run_time": [55], "status": "Ended", "poster_path": "/g.jpg",
        "seasons": [{"season_number": 0, "episode_count": 2, "name": "Specials"}, {"season_number": 1, "episode_count": 35, "name": "Season 1"},
                    {"season_number": 2, "episode_count": 10, "name": "Season 2"}]}
S1 = {"episodes": [{"episode_number": n, "name": f"Episode <{n}>", "air_date": "2011-04-17", "overview": "Plot & twist.", "runtime": 55, "vote_average": 8.1,
                    "still_path": f"/still{n}.jpg"} for n in range(1, 36)]}
S2 = {"episodes": [{"episode_number": 1, "name": "Only"}]}


def tmdb_with(extra=None):
    data = {"/movie/603": MOVIE, "/tv/1399": SHOW, "/tv/1399/season/1": S1, "/tv/1399/season/2": S2, **(extra or {})}
    calls = []
    async def transport(url, headers, params, timeout):
        path = url.split(config.TMDB_BASE)[1]
        calls.append((path, dict(params)))
        r = data.get(path)
        if r is None: return 404, {}, {"status_message": "nf"}
        if isinstance(r, int): return r, {}, {}
        return 200, {}, r
    t = Tmdb(transport)
    views.tmdb = publisher.tmdb = browse.tmdb = start.tmdb = t
    return t, calls

def buttons(markup): return [b for row in markup.rows for b in row]
def cb(markup): return [b.callback_data for b in buttons(markup) if b.callback_data]
def urls(markup): return [b.url for b in buttons(markup) if b.url]


async def t_a_movie_page():
    tmdb_with()
    text, markup, photo = await views.title_view("movie", 603)
    assert "The &lt;Matrix&gt; &amp; Co" in text and "(1999)" in text and "8.2/10" in text and "2h 16m" in text
    assert urls(markup) == ["https://vidsrc.mov/embed/movie/603"] and cb(markup) == ["m:menu"]
    assert photo == "https://image.tmdb.org/t/p/w500/m.jpg"
    settings._d.update(premium_link="https://t.me/shop", backup_link="https://t.me/bk")
    try:
        assert urls((await views.title_view("movie", 603))[1])[1:] == ["https://t.me/shop", "https://t.me/bk"]
    finally:
        settings._d.update(premium_link="", backup_link="")

async def t_a_series_page_leads_to_seasons_and_can_start_at_s1e1():
    tmdb_with()
    text, markup, photo = await views.title_view("tv", 1399)
    assert "2 seasons" in text and cb(markup)[:1] == ["ss:1399"] and urls(markup) == ["https://vidsrc.mov/embed/tv/1399/1/1"]
    assert [b.text for b in buttons(markup)][:2] == ["📺 Seasons", "▶️ S1E1"]

async def t_seasons_skip_specials():
    tmdb_with()
    text, markup, _ = await views.seasons_view(1399)
    assert cb(markup) == ["se:1399:1:1", "se:1399:2:1", "d:t:1399", "m:menu"] and "S1 (35)" in [b.text for b in buttons(markup)]

async def t_episodes_are_paged_and_stay_within_telegrams_limits():
    tmdb_with()
    text, m1, _ = await views.episodes_view(1399, 1, 1)
    eps = [c for c in cb(m1) if c.startswith("ep:")]
    assert len(eps) == 30 and eps[0] == "ep:1399:1:1" and "se:1399:1:2" in cb(m1) and "35 episodes" in text
    _, m2, _ = await views.episodes_view(1399, 1, 2)
    assert len([c for c in cb(m2) if c.startswith("ep:")]) == 5
    _, m9, _ = await views.episodes_view(1399, 1, 99)
    assert len([c for c in cb(m9) if c.startswith("ep:")]) == 5, "page is clamped"
    text, m3, _ = await views.episodes_view(1399, 2, 1)
    assert "1 episode<" in text.replace("</blockquote>", "<")  # singular
    for markup in (m1, m2, m3):
        assert all(len(c.encode()) <= 64 for c in cb(markup)), "callback data is limited to 64 bytes"

async def t_an_episode_page():
    tmdb_with()
    text, markup, photo = await views.episode_view(1399, 1, 2)
    assert "S1E2" in text and "Episode &lt;2&gt;" in text and "Plot &amp; twist." in text and "55m" in text and "8.1/10" in text
    assert urls(markup) == ["https://vidsrc.mov/embed/tv/1399/1/2"] and photo == "https://image.tmdb.org/t/p/w300/still2.jpg"
    assert {"ep:1399:1:1", "ep:1399:1:3", "ss:1399", "se:1399:1:1"} <= set(cb(markup))
    _, first, _ = await views.episode_view(1399, 1, 1)
    assert "ep:1399:1:0" not in cb(first) and "ep:1399:1:2" in cb(first)
    _, last, _ = await views.episode_view(1399, 1, 35)
    assert "ep:1399:1:36" not in cb(last) and "se:1399:1:2" in cb(last), "the back button returns to the page that holds this episode"
    try:
        await views.episode_view(1399, 1, 99); assert False
    except TmdbError as e:
        assert e.code == "not_found"

SEARCH_M = {"results": [{"id": 603, "title": "The Matrix", "release_date": "1999-03-30", "popularity": 80, "vote_average": 8.2}], "total_pages": 3}
SEARCH_T = {"results": [{"id": 1399, "name": "Game of Thrones", "first_air_date": "2011-04-17", "popularity": 200, "vote_average": 8.4}], "total_pages": 1}

async def t_search_merges_movies_and_series_by_popularity():
    t, calls = tmdb_with({"/search/movie": SEARCH_M, "/search/tv": SEARCH_T})
    text, markup = await views.search_view("matrix")
    assert text.index("Game of Thrones") < text.index("The Matrix") and "🔍 <b>matrix</b>" in text
    assert cb(markup)[:2] == ["d:t:1399", "d:m:603"]
    nav = [c for c in cb(markup) if c.startswith("sr:")]
    assert len(nav) == 1 and nav[0].endswith(":2"), "pages: TMDB says 3"
    token = nav[0].split(":")[1]
    assert views.query_of(token) == "matrix" and views.query_of("nope") is None
    assert {p["query"] for path, p in calls if path.startswith("/search")} == {"matrix"}
    _, markup2 = await views.search_view("matrix", 2)
    assert any(c.endswith(":1") for c in cb(markup2) if c.startswith("sr:")) and any(c.endswith(":3") for c in cb(markup2) if c.startswith("sr:"))

async def t_search_survives_one_half_failing_and_reports_when_both_fail():
    tmdb_with({"/search/movie": SEARCH_M, "/search/tv": 500})
    with mock.patch("asyncio.sleep", _stubs.nosleep):
        text, markup = await views.search_view("matrix")
    assert "The Matrix" in text and "Game" not in text
    tmdb_with({"/search/movie": 500, "/search/tv": 500})
    with mock.patch("asyncio.sleep", _stubs.nosleep):
        try:
            await views.search_view("matrix"); assert False
        except TmdbError:
            pass
    tmdb_with({"/search/movie": {"results": []}, "/search/tv": {"results": []}})
    assert (await views.search_view("zzzz"))[0] == texts.NO_RESULTS

class FakeFeed:
    def __init__(self, items=None, error=None): self.items, self.error, self.calls = items or [], error, []
    async def latest(self, kind, type_, page):
        self.calls.append((kind, type_, page))
        if self.error: raise FeedError(self.error)
        return self.items

async def t_the_latest_page_lists_titles_from_the_feed():
    tmdb_with({"/movie/1": {**MOVIE, "id": 1, "title": "One"}, "/movie/2": 404, "/movie/3": {**MOVIE, "id": 3, "title": "Three"}})
    vidsrc.feed = FakeFeed([FeedItem(1), FeedItem(2), FeedItem(None, "tt1234567"), FeedItem(3)])
    settings._d["feed_movie_type"] = "latest"
    text, markup = await views.latest_view("movie", 2)
    assert "One" in text and "Three" in text and "movie/2" not in text and text.count("<b>") >= 3
    assert cb(markup)[:2] == ["d:m:1", "d:m:3"] and "lt:m:1" in cb(markup) and "lt:m:3" in cb(markup)
    assert vidsrc.feed.calls == [("movie", "latest", 2)]
    vidsrc.feed = FakeFeed([])
    assert (await views.latest_view("tv", 1))[0] == texts.NOTHING_NEW
    vidsrc.feed = FakeFeed(error="HTTP 500")
    try:
        await views.latest_view("tv", 1); assert False
    except FeedError:
        pass


# ── ui.py ────────────────────────────────────────────────────────────────────
class Msg:
    def __init__(self, photo=False, chat=5): self.photo, self.chat, self.log = photo, types.SimpleNamespace(id=chat), []
    async def edit_text(self, text, reply_markup=None, **k): self.log.append(("edit_text", text))
    async def edit_media(self, media, reply_markup=None): self.log.append(("edit_media", media[1]))
    async def delete(self): self.log.append(("delete",))

class CQ:
    def __init__(self, message, uid=5): self.data, self.message, self.from_user, self.answers = "x", message, types.SimpleNamespace(id=uid, first_name="Ann"), []
    async def answer(self, text=None, show_alert=False): self.answers.append((text, show_alert))

class Client:
    def __init__(self, fail_photo=False): self.sent, self.fail_photo = [], fail_photo
    async def send_message(self, chat, text, **k): self.sent.append(("text", chat, text)); return types.SimpleNamespace(id=1)
    async def send_photo(self, chat, photo, caption=None, **k):
        if self.fail_photo: raise RuntimeError("WEBPAGE_CURL_FAILED")
        self.sent.append(("photo", chat, caption)); return types.SimpleNamespace(id=1)

async def t_pages_edit_in_place_and_switch_between_photo_and_text():
    c = Client()
    m = Msg(photo=False); await ui.show(c, CQ(m), "t", None, None); assert m.log == [("edit_text", "t")]
    m = Msg(photo=True); await ui.show(c, CQ(m), "t", None, "http://img"); assert m.log == [("edit_media", "http://img")]
    m = Msg(photo=False); await ui.show(c, CQ(m), "t", None, "http://img")
    assert m.log == [("delete",)] and c.sent[-1][0] == "photo", "a text page can't become a photo: replaced"
    m = Msg(photo=True); await ui.show(c, CQ(m), "t", None, None)
    assert m.log == [("delete",)] and c.sent[-1][0] == "text"
    cmd = types.SimpleNamespace(chat=types.SimpleNamespace(id=9))
    await ui.show(c, cmd, "hello", None, None); assert c.sent[-1] == ("text", 9, "hello")
    bad = Client(fail_photo=True)
    await ui.show(bad, cmd, "caption", None, "http://broken"); assert bad.sent == [("text", 9, "caption")], "a bad image URL falls back to text"

async def t_a_failing_handler_always_answers_the_user():
    c = Client()
    msg = types.SimpleNamespace(chat=types.SimpleNamespace(id=9))
    @ui.guarded
    async def tmdb_down(client, ev): raise TmdbError("rate_limited")
    @ui.guarded
    async def broken(client, ev): raise ZeroDivisionError("x")
    @ui.guarded
    async def stop(client, ev): raise _stubs.sys.modules["pyrogram"].StopPropagation
    @ui.guarded
    async def feed_down(client, ev): raise FeedError("x")
    await tmdb_down(c, msg); await broken(c, msg); await feed_down(c, msg)
    assert [s[2] for s in c.sent] == [TmdbError("rate_limited").user_text(), texts.ERR_GENERIC, texts.NOTHING_NEW]
    q = CQ(Msg()); await broken(c, q)
    assert q.answers[0][1] is True, "callbacks get an alert box"
    try:
        await stop(c, msg); assert False
    except _stubs.sys.modules["pyrogram"].StopPropagation:
        pass


# ── handlers ─────────────────────────────────────────────────────────────────
class Reply:
    def __init__(self, text="", command=None, uid=5, name="Ann"):
        self.text, self.command, self.from_user, self.chat, self.replies = text, command or [], types.SimpleNamespace(id=uid, first_name=name), types.SimpleNamespace(id=uid), []
    async def reply_text(self, text, **k):
        self.replies.append(text)
        return types.SimpleNamespace(edit_text=self._edit, delete=self._del)
    async def _edit(self, text, **k): self.replies.append(("edited", text))
    async def _del(self): self.replies.append("deleted")

class DB:
    async def touch_user(self, uid): return False

async def t_start_opens_the_menu_or_a_title_from_a_channel_deep_link():
    tmdb_with()
    shown = []
    async def show(client, ev, text, markup=None, photo=None): shown.append((text, photo))
    with mock.patch.object(start, "show", show), mock.patch.object(start, "db", DB()):
        await start.cmd_start(Client(), Reply(command=["start"], name="<Ann>"))
        assert "Welcome, &lt;Ann&gt;!" in shown[-1][0] and shown[-1][1] is None
        await start.cmd_start(Client(), Reply(command=["start", "t_1399"]))
        assert "Game of Thrones" in shown[-1][0] and shown[-1][1].endswith("/g.jpg")
        await start.cmd_start(Client(), Reply(command=["start", "m_603"]))
        assert "Matrix" in shown[-1][0]
        n = len(shown)
        await start.cmd_start(Client(), Reply(command=["start", "x_1; DROP"]))
        assert len(shown) == n + 1 and "Welcome" in shown[-1][0], "an invalid payload just opens the menu"
        settings._d["tmdb_key"] = ""
        r = Reply(command=["start", "m_603"]); await start.cmd_start(Client(), r)
        settings._d["tmdb_key"] = V3
        assert r.replies == [texts.NOT_READY]

async def t_help_and_about_carry_the_required_tmdb_notice():
    shown = []
    async def show(client, ev, text, markup=None, photo=None): shown.append(text)
    with mock.patch.object(start, "show", show):
        await start.cmd_about(Client(), Reply())
        await start.cmd_help(Client(), Reply())
    assert "This product uses the TMDB API but is not endorsed or certified by TMDB." in shown[0]
    assert "/search" in shown[1] and "/admin" not in shown[1], "users don't see admin commands"

async def t_typing_a_title_searches_and_short_text_is_refused():
    tmdb_with({"/search/movie": SEARCH_M, "/search/tv": SEARCH_T})
    with mock.patch.object(browse, "db", DB()):
        r = Reply("x"); await browse.on_text(Client(), r); assert r.replies == [texts.SEARCH_SHORT]
        r = Reply("matrix"); await browse.on_text(Client(), r)
        assert r.replies[0] == texts.SEARCHING and r.replies[1][0] == "edited" and "Game of Thrones" in r.replies[1][1]
        r = Reply(command=["search"]); await browse.cmd_search(Client(), r); assert r.replies == [texts.SEARCH_PROMPT]
        settings._d["tmdb_key"] = ""
        r = Reply("matrix"); await browse.on_text(Client(), r)
        settings._d["tmdb_key"] = V3
        assert r.replies == [texts.NOT_READY]
    tmdb_with({"/search/movie": 500, "/search/tv": 500})
    with mock.patch.object(browse, "db", DB()), mock.patch("asyncio.sleep", _stubs.nosleep):
        r = Reply("matrix")
        try:
            await browse.run_search(Client(), r, "matrix"); assert False
        except TmdbError:
            pass
        assert r.replies[-1] == "deleted", "the 'Searching…' note is removed when the search fails"

async def t_every_browse_button_leads_somewhere():
    tmdb_with({"/search/movie": SEARCH_M, "/search/tv": SEARCH_T})
    shown, toasts = [], []
    async def show(client, ev, text, markup=None, photo=None): shown.append((text, photo))
    async def done(cq, text="", alert=False): toasts.append((text, alert))
    def q(data):
        c = CQ(Msg()); c.data = data; return c
    vidsrc.feed = FakeFeed([FeedItem(603)])
    with mock.patch.object(browse, "show", show), mock.patch.object(browse, "done", done), mock.patch.object(browse, "db", DB()):
        for data, expect in (("d:m:603", "Matrix"), ("d:t:1399", "Game of Thrones"), ("ss:1399", "Pick a season"), ("se:1399:1:1", "Pick an episode"),
                             ("se:1399:2", "1 episode"), ("ep:1399:1:3", "S1E3"), ("lt:m:1", "Latest movies")):
            await browse.cb_browse(Client(), q(data))
            assert expect in shown[-1][0], (data, shown[-1][0][:80])
        token = views.query_token("matrix")
        await browse.cb_browse(Client(), q(f"sr:{token}:1")); assert "Game of Thrones" in shown[-1][0]
        n = len(shown)
        await browse.cb_browse(Client(), q("sr:deadbeef00:1"))
        assert toasts[-1] == (texts.EXPIRED, True) and len(shown) == n, "an expired search button explains itself"
        settings._d["tmdb_key"] = ""
        await browse.cb_browse(Client(), q("d:m:603"))
        settings._d["tmdb_key"] = V3
        assert toasts[-1][1] is True and "TMDB" in toasts[-1][0]


async def main():
    for name, fn in list(globals().items()):
        if name.startswith("t_"):
            await fn()
            print("ok ", name[2:].replace("_", " "))
    print("all view / ui / handler checks passed")

asyncio.run(main())
