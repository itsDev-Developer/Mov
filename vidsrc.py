"""vidsrc.mov — embed links and the title feed, exactly as in the API reference you provided:

    GET {base}/embed/movie/{id}                  id = TMDB id (927085) or IMDB id (tt17048514)
    GET {base}/embed/tv/{id}/{season}/{episode}
    GET {base}/vapi/movie/{type}/{page}          -> {"status": 200, "type": "movie",
    GET {base}/vapi/tv/{type}/{page}                    "result": [{"imdb_id": "tt…", "tmdb_id": "927085", "quality": "1080p"}]}
    optional  ?sub.info={json}                   subtitles; the JSON schema belongs to the provider, so whatever
                                                 JSON you configure is passed through untouched (URL-encoded)

`type` is not enumerated in the reference (only "latest" is implied by its example), so it is a setting, not a guess.
"""
import asyncio
import json
import logging
import random
import re
from dataclasses import dataclass
from urllib.parse import quote

import config
from database import settings
from http_client import Breaker, http

log = logging.getLogger("vidsrc")
_TMDB_ID = re.compile(r"^\d{1,9}$")
_IMDB_ID = re.compile(r"^tt\d{5,10}$")
_TYPE = re.compile(r"^[a-z0-9_-]{1,32}$")
MAX_SUB = 1800


class FeedError(Exception):
    pass


def base() -> str:
    b = str(settings.get("vidsrc_base") or config.VIDSRC_BASE).rstrip("/")
    return b if b.startswith(("https://", "http://")) else config.VIDSRC_BASE


def valid_id(value) -> str:
    s = str(value).strip()
    if not (_TMDB_ID.match(s) or _IMDB_ID.match(s)):
        raise ValueError("an id is a TMDB number or an IMDB id like tt1234567")
    return s


def sub_query(raw: str | None = None) -> str:
    """'?sub.info=<url-encoded compact JSON>' for the configured subtitles JSON, or '' when unset/invalid."""
    raw = settings.get("sub_info") if raw is None else raw
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        compact = json.dumps(json.loads(raw), separators=(",", ":"), ensure_ascii=False)
    except ValueError:
        log.warning("sub_info is not valid JSON: ignoring it")
        return ""
    return "" if len(compact) > MAX_SUB else "?sub.info=" + quote(compact, safe="")


def embed_movie(movie_id) -> str:
    return f"{base()}/embed/movie/{valid_id(movie_id)}{sub_query()}"


def embed_tv(tv_id, season: int, episode: int) -> str:
    s, e = int(season), int(episode)
    if s < 0 or e < 0 or s > 999 or e > 9999:
        raise ValueError("season/episode out of range")
    return f"{base()}/embed/tv/{valid_id(tv_id)}/{s}/{e}{sub_query()}"


@dataclass(frozen=True)
class FeedItem:
    tmdb_id: int | None
    imdb_id: str | None = None
    quality: str = ""
    season: int | None = None       # only if the provider includes them for tv entries
    episode: int | None = None


def _num(v):
    return int(v) if isinstance(v, int) and not isinstance(v, bool) and v > 0 else (int(v) if isinstance(v, str) and v.isdigit() and int(v) > 0 else None)


def parse_feed(payload) -> list[FeedItem]:
    """Tolerant parser: ids may be numbers or strings, unknown fields are ignored, bad entries are dropped."""
    if not isinstance(payload, dict):
        raise FeedError("not a JSON object")
    status = payload.get("status")
    if str(status) != "200":
        raise FeedError(f"provider status {status!r}")
    result = payload.get("result")
    if not isinstance(result, list):
        return []
    out, seen = [], set()
    for e in result:
        if not isinstance(e, dict):
            continue
        tid, imdb = _num(e.get("tmdb_id")), str(e.get("imdb_id") or "")
        imdb = imdb if _IMDB_ID.match(imdb) else None
        if tid is None and imdb is None:
            continue
        key = tid or imdb
        if key in seen:
            continue
        seen.add(key)
        out.append(FeedItem(tid, imdb, str(e.get("quality") or "")[:12], _num(e.get("season")), _num(e.get("episode"))))
    return out


class Feed:
    ATTEMPTS = 3

    def __init__(self, transport=None):
        self._transport = transport or self._default_transport
        self._breaker = Breaker("vidsrc")

    @staticmethod
    async def _default_transport(url, headers, params, timeout):
        return await http.get_json(url, headers, params, timeout)

    async def latest(self, kind: str, type_: str = "latest", page: int = 1) -> list[FeedItem]:
        if kind not in ("movie", "tv"):
            raise ValueError("kind must be movie or tv")
        if not _TYPE.match(type_ or ""):
            raise ValueError("feed type may only use a-z, 0-9, - and _")
        page = max(1, min(int(page), 500))
        if self._breaker.blocked():
            raise FeedError("feed temporarily unavailable")
        url = f"{base()}/vapi/{kind}/{type_}/{page}"
        last = "no answer"
        for attempt in range(1, self.ATTEMPTS + 1):
            if attempt > 1:
                await asyncio.sleep(min(0.5 * 2 ** (attempt - 2), 4) + random.random() * 0.25)
            try:
                status, _, data = await self._transport(url, {"Accept": "application/json"}, None, config.HTTP_TIMEOUT)
            except Exception as e:
                last = type(e).__name__
                self._breaker.fail()
                continue
            if status == 200:
                self._breaker.ok()
                return parse_feed(data)
            last = f"HTTP {status}"
            if status < 500 and status != 429:
                raise FeedError(last)                  # 4xx: retrying won't help
            self._breaker.fail()
        raise FeedError(last)


feed = Feed()
