"""TMDB client. Uses ONLY endpoints from the official API reference (developer.themoviedb.org):

    GET /configuration                      image base URL + sizes
    GET /movie/{id}   GET /tv/{id}          details (with `language`)
    GET /tv/{id}/season/{n}                 the episodes of one season
    GET /search/movie GET /search/tv        query, page, include_adult, language

Auth, per the docs: the v3 "API Key" goes in the `api_key` query parameter, the v4 "API Read Access Token" in an
`Authorization: Bearer` header. Both are accepted; the kind is detected from the value.

Production notes: TTL cache, identical concurrent requests share ONE call, client-side rate limit, retries with
backoff (429 honours Retry-After), a circuit breaker, and the key never appears in an error message or log line.
"""
import asyncio
import logging
import random
import re
import time

import config
import runtime
from database import settings
from http_client import Breaker, Bucket, http

log = logging.getLogger("tmdb")

ATTEMPTS = 3
TTL = {"configuration": 86400, "details": 6 * 3600, "season": 3600, "search": 600}
_DEFAULT_IMAGES = {"base": "https://image.tmdb.org/t/p/", "poster": ["w342", "w500", "w780", "original"],
                   "backdrop": ["w780", "w1280", "original"], "still": ["w185", "w300", "original"]}
_PREFERRED = {"poster": "w500", "backdrop": "w780", "still": "w300"}
_PATH_OK = re.compile(r"^/[A-Za-z0-9._-]{3,120}$")


class TmdbError(Exception):
    def __init__(self, code: str, message: str = "", status: int = 0, retry_after: int = 0):
        super().__init__(message or code)
        self.code, self.message, self.status, self.retry_after = code, message or code, status, retry_after

    def user_text(self) -> str:
        return {
            "no_key": "TMDB isn't set up yet. An owner can add the key in /admin → 🔑 TMDB key.",
            "invalid_key": "The TMDB key was rejected (wrong or revoked). An owner can update it in /admin.",
            "not_found": "That title wasn't found on TMDB.",
            "rate_limited": "TMDB is busy. Please try again in a moment.",
            "unavailable": "TMDB is temporarily unavailable. Please try again shortly.",
        }.get(self.code, "TMDB couldn't answer right now. Please try again.")


class NotFound(TmdbError):
    def __init__(self, message: str = ""):
        super().__init__("not_found", message or "not found", 404)


def is_bearer(key: str) -> bool:
    """A v4 Read Access Token is a JWT (three dot-separated parts, starts with eyJ); a v3 key is 32 hex chars."""
    return key.startswith("eyJ") or key.count(".") == 2


class Tmdb:
    def __init__(self, transport=None):
        self._transport = transport or self._default_transport
        self._cache: dict = {}
        self._inflight: dict = {}
        self._bucket: Bucket | None = None
        self._breaker = Breaker("tmdb")
        self._images = dict(_DEFAULT_IMAGES)
        self.calls = self.errors = self.cache_hits = 0
        runtime.gauge("vidbot_tmdb_requests", lambda: self.calls)
        runtime.gauge("vidbot_tmdb_errors", lambda: self.errors)
        runtime.gauge("vidbot_tmdb_cache_hits", lambda: self.cache_hits)

    @staticmethod
    async def _default_transport(url, headers, params, timeout):
        return await http.get_json(url, headers, params, timeout)

    # ── configuration ────────────────────────────────────────────────────────
    @staticmethod
    def key() -> str:
        return str(settings.get("tmdb_key") or "").strip()

    def configured(self) -> bool:
        return bool(self.key())

    # ── core ─────────────────────────────────────────────────────────────────
    async def _get(self, path: str, params: dict | None = None, *, ttl: int = 0, key: str = ""):
        key = key or self.key()
        if not key:
            raise TmdbError("no_key")
        params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        ck = (path, tuple(sorted(params.items())), key[-6:])
        hit = self._cache.get(ck) if ttl else None
        if hit and hit[0] > time.time():
            self.cache_hits += 1
            return hit[1]
        if ck in self._inflight:                           # someone is already asking exactly this: share the answer
            return await asyncio.shield(self._inflight[ck])
        fut = asyncio.get_running_loop().create_future()
        self._inflight[ck] = fut
        try:
            data = await self._fetch(path, params, key)
            if ttl:
                if len(self._cache) >= 600:
                    for k in sorted(self._cache, key=lambda k: self._cache[k][0])[:150]:
                        self._cache.pop(k, None)
                self._cache[ck] = (time.time() + ttl, data)
            fut.set_result(data)
            return data
        except Exception as e:
            fut.set_exception(e)
            fut.exception()                                # mark retrieved: nobody else may be waiting
            raise
        finally:
            self._inflight.pop(ck, None)

    async def _fetch(self, path: str, params: dict, key: str):
        if self._breaker.blocked():
            raise TmdbError("unavailable", status=503)
        if self._bucket is None:
            self._bucket = Bucket(config.TMDB_RATE_PER_SEC)
        headers = {"Accept": "application/json"}
        if is_bearer(key):
            headers["Authorization"] = f"Bearer {key}"
        else:
            params = {**params, "api_key": key}
        url = f"{config.TMDB_BASE}{path}"
        last: TmdbError | None = None
        for attempt in range(1, ATTEMPTS + 1):
            if attempt > 1:
                await asyncio.sleep(min(0.4 * 2 ** (attempt - 2), 3) + random.random() * 0.2)
                if self._breaker.blocked():
                    break
            await self._bucket.acquire()
            self.calls += 1
            try:
                status, hdrs, data = await self._transport(url, headers, params, config.HTTP_TIMEOUT)
            except Exception as e:                         # never include `e`'s text: it can echo the URL (and the key)
                last = TmdbError("network_error", type(e).__name__)
                self._breaker.fail()
                continue
            if status == 200 and isinstance(data, dict):
                self._breaker.ok()
                return data
            msg = str(data.get("status_message") or "") if isinstance(data, dict) else ""
            if status == 401:
                self.errors += 1
                raise TmdbError("invalid_key", msg or "invalid key", 401)
            if status == 404:
                self._breaker.ok()
                raise NotFound(msg)
            if status == 429:
                ra = str(hdrs.get("retry-after", ""))
                last = TmdbError("rate_limited", msg, 429, int(ra) if ra.isdigit() else 0)
                if attempt < ATTEMPTS:
                    await asyncio.sleep(min(last.retry_after or 1, 10))
                continue
            if status >= 500:
                last = TmdbError("unavailable", msg, status)
                self._breaker.fail()
                continue
            self.errors += 1
            raise TmdbError("http_error", msg, status)
        self.errors += 1
        raise last or TmdbError("network_error")

    # ── endpoints ────────────────────────────────────────────────────────────
    def _lang(self) -> str:
        return str(settings.get("tmdb_language") or config.TMDB_LANGUAGE)

    async def check_key(self, key: str) -> dict:
        """Validate a credential WITHOUT saving it (a cheap documented call)."""
        return await self._get("/configuration", key=key)

    async def warm(self):
        """Load the image base URL and sizes once (the docs say to take them from /configuration)."""
        try:
            img = (await self._get("/configuration", ttl=TTL["configuration"])).get("images") or {}
            base = img.get("secure_base_url") or _DEFAULT_IMAGES["base"]
            self._images = {"base": base if base.startswith("https://") else _DEFAULT_IMAGES["base"],
                            "poster": img.get("poster_sizes") or _DEFAULT_IMAGES["poster"],
                            "backdrop": img.get("backdrop_sizes") or _DEFAULT_IMAGES["backdrop"],
                            "still": img.get("still_sizes") or _DEFAULT_IMAGES["still"]}
        except TmdbError as e:
            log.info("using default image settings (%s)", e.code)

    def image_url(self, path: str | None, kind: str = "poster", size: str | None = None) -> str:
        """base + size + file_path, exactly as the image docs describe. '' when there is no (valid) path."""
        if not path or not _PATH_OK.match(path):
            return ""
        sizes = self._images.get(kind, [])
        size = size if size in sizes else (_PREFERRED.get(kind) if _PREFERRED.get(kind) in sizes else "original")
        return f"{self._images['base']}{size}{path}"

    async def details(self, kind: str, tmdb_id: int) -> dict:
        if kind not in ("movie", "tv"):
            raise ValueError("kind must be movie or tv")
        return await self._get(f"/{kind}/{int(tmdb_id)}", {"language": self._lang()}, ttl=TTL["details"])

    async def season(self, tv_id: int, number: int) -> dict:
        return await self._get(f"/tv/{int(tv_id)}/season/{int(number)}", {"language": self._lang()}, ttl=TTL["season"])

    async def search(self, kind: str, query: str, page: int = 1) -> dict:
        if kind not in ("movie", "tv"):
            raise ValueError("kind must be movie or tv")
        return await self._get(f"/search/{kind}", {"query": query[:120], "page": max(1, min(int(page), 500)),
                                                  "include_adult": "true" if settings.get("adult") else "false",
                                                  "language": self._lang()}, ttl=TTL["search"])


tmdb = Tmdb()
