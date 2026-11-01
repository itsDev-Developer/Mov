"""Run with:  python tests/test_tmdb.py   — the TMDB client against a fake transport (no network, no real key)."""
import asyncio
from unittest import mock

import _stubs
import config
import tmdb as tmdb_mod
from database import settings
from tmdb import NotFound, Tmdb, TmdbError

V3 = "0123456789abcdef0123456789abcdef"
V4 = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJ4In0.signaturepart123"
settings._d.update(tmdb_key=V3, tmdb_language="en-US", adult=False)


def make(handler):
    calls = []
    async def transport(url, headers, params, timeout):
        calls.append({"url": url, "headers": dict(headers), "params": dict(params)})
        return handler(len(calls), url, params)
    return Tmdb(transport), calls


def ok(**d): return 200, {}, d


async def t_a_v3_key_goes_in_the_query_and_a_v4_token_in_a_bearer_header():
    t, calls = make(lambda n, u, p: ok(id=1))
    await t.details("movie", 1)
    assert calls[0]["params"]["api_key"] == V3 and "Authorization" not in calls[0]["headers"]
    settings._d["tmdb_key"] = V4
    t2, calls2 = make(lambda n, u, p: ok(id=1))
    await t2.details("movie", 1)
    assert calls2[0]["headers"]["Authorization"] == f"Bearer {V4}" and "api_key" not in calls2[0]["params"]
    settings._d["tmdb_key"] = V3
    assert tmdb_mod.is_bearer(V4) and not tmdb_mod.is_bearer(V3)

async def t_no_key_means_no_request():
    t, calls = make(lambda n, u, p: ok())
    settings._d["tmdb_key"] = ""
    try:
        await t.details("movie", 1); assert False
    except TmdbError as e:
        assert e.code == "no_key" and "TMDB" in e.user_text()
    finally:
        settings._d["tmdb_key"] = V3
    assert not calls

async def t_details_use_the_documented_paths_and_language_and_are_cached():
    t, calls = make(lambda n, u, p: ok(id=927085, title="X"))
    settings._d["tmdb_language"] = "de-DE"
    try:
        await t.details("movie", 927085); await t.details("movie", 927085)
        assert len(calls) == 1 and calls[0]["url"] == f"{config.TMDB_BASE}/movie/927085" and calls[0]["params"]["language"] == "de-DE"
        await t.details("tv", 5); await t.season(5, 2)
        assert [c["url"].split(config.TMDB_BASE)[1] for c in calls[1:]] == ["/tv/5", "/tv/5/season/2"]
        settings._d["tmdb_language"] = "fr-FR"
        await t.details("movie", 927085)
        assert len(calls) == 4, "another language is a different cache entry"
    finally:
        settings._d["tmdb_language"] = "en-US"
    try:
        await t.details("person", 1); assert False
    except ValueError:
        pass
    assert t.cache_hits == 1

async def t_identical_concurrent_requests_share_one_call():
    async def slow(url, headers, params, timeout):
        calls.append(1); await asyncio.sleep(0.05); return ok(id=1)
    calls = []
    t = Tmdb(slow)
    results = await asyncio.gather(*[t.details("movie", 1) for _ in range(6)])
    assert len(calls) == 1 and all(r == {"id": 1} for r in results)

async def t_errors_are_classified_and_not_retried_when_retrying_cannot_help():
    t, calls = make(lambda n, u, p: (404, {}, {"status_code": 34, "status_message": "The resource you requested could not be found."}))
    try:
        await t.details("movie", 1); assert False
    except NotFound as e:
        assert e.code == "not_found" and e.status == 404
    assert len(calls) == 1
    t, calls = make(lambda n, u, p: (401, {}, {"status_code": 7, "status_message": "Invalid API key"}))
    try:
        await t.details("movie", 1); assert False
    except TmdbError as e:
        assert e.code == "invalid_key" and "TMDB key" in e.user_text()
    assert len(calls) == 1
    t, calls = make(lambda n, u, p: (404, {}, None))
    for _ in range(2):
        try:
            await t.details("movie", 1)
        except NotFound:
            pass
    assert len(calls) == 2, "a failure is never cached"

async def t_transient_failures_are_retried():
    seq = [(429, {"retry-after": "0"}, {}), (503, {}, {}), ok(id=7)]
    t, calls = make(lambda n, u, p: seq[n - 1])
    with mock.patch("asyncio.sleep", _stubs.nosleep):
        assert (await t.details("movie", 7))["id"] == 7
    assert len(calls) == 3

async def t_the_key_never_leaks_and_a_dead_service_trips_the_breaker():
    async def boom(url, headers, params, timeout):
        calls.append(1); raise OSError(f"connect failed {url}?api_key={params.get('api_key')} {headers.get('Authorization')}")
    calls = []
    t = Tmdb(boom)
    with mock.patch("asyncio.sleep", _stubs.nosleep):
        for i in range(3):
            try:
                await t.details("movie", i + 1); assert False
            except TmdbError as e:
                assert V3 not in str(e) and V3 not in e.user_text() and e.code in ("network_error", "unavailable")
        before = len(calls)
        try:
            await t.details("movie", 99); assert False
        except TmdbError as e:
            assert e.code == "unavailable"
    assert len(calls) == before, "breaker open: no request is made"

async def t_search_sends_the_documented_parameters():
    t, calls = make(lambda n, u, p: ok(results=[]))
    await t.search("tv", "Breaking Bad" + "x" * 300, page=999)
    p = calls[0]["params"]
    assert calls[0]["url"].endswith("/search/tv") and len(p["query"]) == 120 and p["page"] == 500 and p["include_adult"] == "false"
    settings._d["adult"] = True
    try:
        await t.search("movie", "x", page=0)
    finally:
        settings._d["adult"] = False
    assert calls[1]["params"]["include_adult"] == "true" and calls[1]["params"]["page"] == 1

async def t_image_urls_follow_the_configuration_endpoint():
    cfg = {"images": {"secure_base_url": "https://img.example/t/p/", "poster_sizes": ["w154", "w500", "original"],
                      "backdrop_sizes": ["w300", "w780", "original"], "still_sizes": ["w92", "original"]}}
    t, calls = make(lambda n, u, p: ok(**cfg))
    assert t.image_url("/abc.jpg") == "https://image.tmdb.org/t/p/w500/abc.jpg", "sensible default before /configuration is read"
    await t.warm()
    assert calls[0]["url"].endswith("/configuration")
    assert t.image_url("/abc.jpg") == "https://img.example/t/p/w500/abc.jpg"
    assert t.image_url("/abc.jpg", "backdrop") == "https://img.example/t/p/w780/abc.jpg"
    assert t.image_url("/abc.jpg", "still") == "https://img.example/t/p/original/abc.jpg", "w300 isn't offered -> original"
    assert t.image_url("/abc.jpg", "poster", "w154") == "https://img.example/t/p/w154/abc.jpg"
    assert t.image_url("/abc.jpg", "poster", "w999") == "https://img.example/t/p/w500/abc.jpg"
    for bad in ("", None, "abc.jpg", "/../etc/passwd", "/a/b.jpg", "/x y.jpg"):
        assert t.image_url(bad) == "", bad
    t2, _ = make(lambda n, u, p: (500, {}, None))
    with mock.patch("asyncio.sleep", _stubs.nosleep):
        await t2.warm()
    assert t2.image_url("/abc.jpg").startswith("https://image.tmdb.org/t/p/"), "a failing /configuration falls back to defaults"

async def t_a_key_can_be_checked_without_saving_it():
    t, calls = make(lambda n, u, p: ok(images={}))
    settings._d["tmdb_key"] = ""
    try:
        await t.check_key(V3)
    finally:
        settings._d["tmdb_key"] = V3
    assert calls[0]["params"]["api_key"] == V3 and calls[0]["url"].endswith("/configuration")


async def main():
    for name, fn in list(globals().items()):
        if name.startswith("t_"):
            await fn()
            print("ok ", name[2:].replace("_", " "))
    print("all TMDB client checks passed")

asyncio.run(main())
