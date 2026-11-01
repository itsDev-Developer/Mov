"""Run with:  python tests/test_vidsrc.py   — embed links, the feed parser/client, and the TMDB data model."""
import asyncio, json
from unittest import mock
from urllib.parse import parse_qs, urlparse

import _stubs
import config
import media
import vidsrc
from database import settings
from vidsrc import Feed, FeedError, FeedItem

settings._d.update(vidsrc_base="https://vidsrc.mov", sub_info="")

# the response example from the API reference, verbatim
REFERENCE = {"status": 200, "type": "movie", "result": [{"imdb_id": "tt17048514", "tmdb_id": "927085", "quality": "1080p"}]}


def t_embed_links_match_the_reference():
    assert vidsrc.embed_movie("tt17048514") == "https://vidsrc.mov/embed/movie/tt17048514"      # IMDB example
    assert vidsrc.embed_movie(927085) == "https://vidsrc.mov/embed/movie/927085"               # TMDB example
    assert vidsrc.embed_tv(1399, 2, 5) == "https://vidsrc.mov/embed/tv/1399/2/5"
    settings._d["vidsrc_base"] = "https://other.example/"
    try:
        assert vidsrc.embed_movie(5) == "https://other.example/embed/movie/5", "trailing slash handled"
    finally:
        settings._d["vidsrc_base"] = "https://vidsrc.mov"

def t_ids_are_validated_so_nothing_odd_ends_up_in_a_url():
    for bad in ("", "abc", "12 34", "../x", "1/2", "tt12", "5?x=1", "99999999999", None):
        try:
            vidsrc.embed_movie(bad); assert False, bad
        except ValueError:
            pass
    for s, e in ((-1, 1), (1, -1), (1000, 1), (1, 10000)):
        try:
            vidsrc.embed_tv(1, s, e); assert False
        except ValueError:
            pass

def t_subtitles_json_is_passed_through_url_encoded():
    sub = {"url": "https://s.example/a.vtt", "lang": "en", "label": "English & more"}
    settings._d["sub_info"] = json.dumps(sub, indent=2)
    try:
        url = vidsrc.embed_movie(927085)
        q = parse_qs(urlparse(url).query)
        assert json.loads(q["sub.info"][0]) == sub and " " not in url.split("?")[1], "compact + encoded"
        assert "?sub.info=" in vidsrc.embed_tv(1, 1, 1)
        settings._d["sub_info"] = "{not json"
        assert vidsrc.embed_movie(5) == "https://vidsrc.mov/embed/movie/5", "invalid JSON is ignored, never sent"
        settings._d["sub_info"] = json.dumps({"x": "y" * 2000})
        assert "?" not in vidsrc.embed_movie(5), "oversized JSON is dropped"
    finally:
        settings._d["sub_info"] = ""

def t_the_reference_feed_response_parses():
    assert vidsrc.parse_feed(REFERENCE) == [FeedItem(927085, "tt17048514", "1080p")]

def t_the_parser_is_tolerant_but_strict_about_failure():
    p = vidsrc.parse_feed
    assert p({"status": "200", "result": [{"tmdb_id": 5}, {"tmdb_id": "5"}, {"tmdb_id": "6", "imdb_id": "nope"}]}) == [FeedItem(5), FeedItem(6)]
    assert p({"status": 200, "result": [{"imdb_id": "tt1234567"}]}) == [FeedItem(None, "tt1234567")]
    assert p({"status": 200, "result": [{}, {"tmdb_id": True}, {"tmdb_id": 0}, {"tmdb_id": -3}, {"tmdb_id": "x"}, "junk", None, {"tmdb_id": 9}]}) == [FeedItem(9)]
    assert p({"status": 200, "type": "tv", "result": [{"tmdb_id": 7, "season": "2", "episode": 4}]}) == [FeedItem(7, None, "", 2, 4)]
    assert p({"status": 200}) == [] and p({"status": 200, "result": "x"}) == []
    for bad in ([], None, "x", {"status": 500, "result": []}, {"result": []}):
        try:
            p(bad); assert False, bad
        except FeedError:
            pass

def make_feed(handler):
    calls = []
    async def transport(url, headers, params, timeout):
        calls.append(url); return handler(len(calls))
    return Feed(transport), calls

async def t_the_feed_client_builds_the_documented_urls():
    f, calls = make_feed(lambda n: (200, {}, REFERENCE))
    assert await f.latest("movie") == [FeedItem(927085, "tt17048514", "1080p")]
    await f.latest("tv", "latest", 3); await f.latest("movie", "latest", 99999); await f.latest("movie", "latest", 0)
    assert calls == ["https://vidsrc.mov/vapi/movie/latest/1", "https://vidsrc.mov/vapi/tv/latest/3",
                     "https://vidsrc.mov/vapi/movie/latest/500", "https://vidsrc.mov/vapi/movie/latest/1"]
    for kind, typ in (("music", "latest"), ("movie", "../etc"), ("movie", "a b"), ("movie", "")):
        try:
            await f.latest(kind, typ); assert False
        except ValueError:
            pass

async def t_feed_failures_are_retried_only_when_that_can_help():
    seq = [(500, {}, None), (429, {}, None), (200, {}, REFERENCE)]
    f, calls = make_feed(lambda n: seq[n - 1])
    with mock.patch("asyncio.sleep", _stubs.nosleep):
        assert len(await f.latest("movie")) == 1 and len(calls) == 3
        f2, calls2 = make_feed(lambda n: (404, {}, None))
        try:
            await f2.latest("movie"); assert False
        except FeedError as e:
            assert "404" in str(e) and len(calls2) == 1, "a 404 is not retried"
        async def boom(url, headers, params, timeout): raise OSError("down")
        f3 = Feed(boom)
        for _ in range(2):
            try:
                await f3.latest("movie")
            except FeedError:
                pass
        try:
            await f3.latest("movie"); assert False
        except FeedError as e:
            assert "unavailable" in str(e), "breaker open after repeated failures"


# ── the TMDB data model ──────────────────────────────────────────────────────
MOVIE = {"id": 927085, "title": "Heart of Stone", "release_date": "2023-08-09", "overview": " A spy. ", "tagline": "Trust no one.",
         "genres": [{"id": 28, "name": "Action"}, {"id": 53, "name": "Thriller"}], "vote_average": 6.7654, "vote_count": 1234, "runtime": 125,
         "status": "Released", "poster_path": "/p.jpg", "backdrop_path": "/b.jpg"}
SHOW = {"id": 1399, "name": "Game of Thrones", "first_air_date": "2011-04-17", "overview": "Nine noble families.", "genres": [{"name": "Drama"}],
        "vote_average": 8.4, "vote_count": 20000, "episode_run_time": [], "status": "Ended", "number_of_seasons": 8, "number_of_episodes": 73,
        "seasons": [{"season_number": 0, "episode_count": 3, "name": "Specials"}, {"season_number": 2, "episode_count": 10, "name": "Season 2"},
                    {"season_number": 1, "episode_count": 10, "name": "Season 1"}], "poster_path": "/g.jpg"}

def t_movies_and_series_map_to_media():
    m = media.from_tmdb("movie", MOVIE)
    assert (m.title, m.year, m.rating, m.runtime, m.genres, m.overview, m.icon) == ("Heart of Stone", "2023", 6.8, "2h 5m", ["Action", "Thriller"], "A spy.", "🎬")
    s = media.from_tmdb("tv", SHOW)
    assert (s.title, s.year, s.n_seasons, s.n_episodes, s.icon, s.runtime) == ("Game of Thrones", "2011", 8, 73, "📺", "")
    assert s.seasons == [(1, 10, "Season 1"), (2, 10, "Season 2")], "sorted, and 'Specials' (season 0) left out"
    bare = media.from_tmdb("movie", {})
    assert bare.title == "Untitled" and bare.rating == 0 and bare.genres == [] and bare.poster_path == ""
    assert media.from_tmdb("tv", {"name": "X", "episode_run_time": [0, 45]}).runtime == "~45m"

def t_search_results_are_merged_by_popularity():
    mv = media.hits_from_search("movie", {"results": [{"id": 1, "title": "A", "release_date": "2020-01-01", "popularity": 5, "vote_average": 7},
                                                      {"id": 0, "title": "bad"}, {"id": 2}, "junk"]})
    tv = media.hits_from_search("tv", {"results": [{"id": 3, "name": "B", "first_air_date": "", "popularity": 50}, {"id": 4, "name": "C", "popularity": 1}]})
    assert [(h.kind, h.tmdb_id) for h in mv] == [("movie", 1)] and mv[0].year == "2020"
    assert [(h.kind, h.tmdb_id) for h in media.merge_hits(mv, tv)] == [("tv", 3), ("movie", 1), ("tv", 4)]
    assert len(media.merge_hits(mv * 20, tv * 20, limit=7)) == 7 and media.hits_from_search("movie", None) == []

def t_season_payloads_give_episode_numbers():
    eps = media.episode_numbers({"episodes": [{"episode_number": 2, "name": "Two"}, {"episode_number": 1, "name": "One"}, {"name": "x"}, "junk", {"episode_number": 0}]})
    assert eps == [(1, "One"), (2, "Two")] and media.episode_numbers(None) == []


async def main():
    for name, fn in list(globals().items()):
        if name.startswith("t_"):
            r = fn()
            if asyncio.iscoroutine(r):
                await r
            print("ok ", name[2:].replace("_", " "))
    print("all vidsrc + media checks passed")

asyncio.run(main())
