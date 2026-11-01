"""Poster for channel posts: pick the image, and optionally hide it.

Blur modes
    off      the image as it is
    spoiler  Telegram's tap-to-reveal blur (the photo is sent with has_spoiler)
    blur     the image is really blurred (Pillow Gaussian blur) and only the blurred copy is uploaded

A requested blur NEVER falls back to the sharp image. If the real blur can't be made (download failed,
Pillow missing, odd file) the original is sent as a spoiler instead; if there is no image, the post is text only.
"""
import asyncio
import io
import ipaddress
import logging
import socket
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import config
import runtime
from netsafe import is_public_ip

log = logging.getLogger("poster")

MODES = ("off", "spoiler", "blur")
MAX_BYTES = 8_000_000          # refuse to download anything bigger
MAX_SIDE = 1280                # downscale before blurring (small, fast, and enough for a poster)
MAX_PIXELS = 40_000_000        # decompression-bomb guard
_CACHE_SECONDS = 600
_cache: dict[tuple, tuple[float, bytes]] = {}      # (url, radius) -> (time, jpeg bytes): one blur serves every channel
_http = None


@dataclass
class Poster:
    url: str = ""
    data: bytes | None = None      # blurred JPEG, when we made one
    spoiler: bool = False          # send with Telegram's spoiler overlay

    def media(self):
        """What to hand to send_photo: a fresh in-memory file for blurred data, else the URL."""
        if self.data is not None:
            f = io.BytesIO(self.data)
            f.name = "poster.jpg"
            return f
        return self.url


def next_mode(current: str) -> str:
    return MODES[(MODES.index(current) + 1) % len(MODES)] if current in MODES else MODES[0]


def _is_http(url: str) -> bool:
    u = urlparse(url or "")
    return u.scheme in ("http", "https") and bool(u.hostname)


async def _public_host(url: str) -> bool:
    """SSRF guard: the poster URL comes from a third-party API, so only fetch hosts that resolve publicly."""
    host = urlparse(url).hostname or ""
    try:
        ipaddress.ip_address(host)
        return is_public_ip(host)
    except ValueError:
        pass
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    return bool(infos) and all(is_public_ip(i[4][0]) for i in infos)


async def _fetch(url: str) -> bytes:
    """Download the image (size-capped). Tests replace this function."""
    global _http
    import httpx
    if _http is None:
        _http = httpx.AsyncClient(timeout=httpx.Timeout(15, connect=8), follow_redirects=False,
                                  headers={"User-Agent": "vidbot/1.0"})
    if not await _public_host(url):
        raise ValueError("poster host is not public")
    r = await _http.get(url)
    if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
        raise ValueError(f"not an image (HTTP {r.status_code})")
    if len(r.content) > MAX_BYTES:
        raise ValueError("image too large")
    return r.content


def _blur_bytes(raw: bytes, radius: int) -> bytes:
    from PIL import Image, ImageFilter            # imported late: only needed when blurring
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    with Image.open(io.BytesIO(raw)) as im:
        im = im.convert("RGB")
        im.thumbnail((MAX_SIDE, MAX_SIDE))
        r = max(4.0, radius * max(im.size) / MAX_SIDE)       # same visual strength for small and large images
        im = im.filter(ImageFilter.GaussianBlur(r))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=82, optimize=True)
        return out.getvalue()


async def blurred(url: str, radius: int | None = None) -> bytes:
    radius = radius or config.BLUR_RADIUS
    key, now = (url, radius), time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < _CACHE_SECONDS:
        return hit[1]
    data = await asyncio.to_thread(_blur_bytes, await _fetch(url), radius)       # CPU work off the event loop
    if len(_cache) >= 32:
        _cache.pop(min(_cache, key=lambda k: _cache[k][0]), None)
    _cache[key] = (now, data)
    return data


async def prepare(url: str, mode: str) -> Poster | None:
    """The Poster to send for `url` under blur `mode`, or None when there is nothing to show."""
    if not _is_http(url):
        return None
    if mode == "spoiler":
        return Poster(url=url, spoiler=True)
    if mode == "blur":
        try:
            return Poster(data=await blurred(url))
        except Exception as e:
            log.warning("blur failed (%s): sending a spoiler instead of the sharp image", type(e).__name__)
            return Poster(url=url, spoiler=True)
    return Poster(url=url)


@runtime.on_shutdown
async def _close():
    global _http
    if _http is not None:
        await _http.aclose()
        _http = None
