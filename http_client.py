"""One small HTTP helper (httpx, imported late) and a circuit breaker shared by the TMDB and vidsrc clients."""
import logging
import time

import config
import runtime

log = logging.getLogger("http")


class Http:
    """GET -> (status, lower-cased headers, parsed JSON or None). Redirects are NOT followed, so a credential in a
    URL or header can never be forwarded to another host; responses are size-capped."""

    def __init__(self):
        self._c = None

    async def get_json(self, url: str, headers: dict | None = None, params: dict | None = None,
                       timeout: float | None = None, max_bytes: int = 2_000_000):
        import httpx
        if self._c is None:
            self._c = httpx.AsyncClient(timeout=httpx.Timeout(timeout or config.HTTP_TIMEOUT, connect=8),
                                        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                                        follow_redirects=False, headers={"User-Agent": "vidbot/1.0"})
        r = await self._c.get(url, headers=headers, params=params)
        if len(r.content) > max_bytes:
            raise ValueError("response too large")
        try:
            data = r.json()
        except ValueError:
            data = None
        return r.status_code, {k.lower(): v for k, v in r.headers.items()}, data

    async def close(self):
        if self._c is not None:
            await self._c.aclose()
            self._c = None


http = Http()
runtime.on_shutdown(http.close)


class Breaker:
    """Stops calling a dead service for `open_for` seconds after `limit` infrastructure failures in a row."""

    def __init__(self, name: str, limit: int = 5, open_for: int = 30):
        self.name, self.limit, self.open_for = name, limit, open_for
        self.fails, self.open_until = 0, 0.0
        runtime.gauge(f"vidbot_{name}_breaker_open", lambda: int(self.blocked()))

    def blocked(self) -> bool:
        return time.time() < self.open_until

    def ok(self):
        self.fails = 0

    def fail(self):
        self.fails += 1
        if self.fails >= self.limit and not self.blocked():
            self.open_until = time.time() + self.open_for
            log.warning("%s failed %d times in a row; pausing calls for %ds", self.name, self.fails, self.open_for)


class Bucket:
    """Token bucket: `per_sec` calls a second."""

    def __init__(self, per_sec: float):
        import asyncio
        self.rate, self.cap = float(per_sec), max(1.0, float(per_sec))
        self.tokens, self.t = self.cap, time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        import asyncio
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.cap, self.tokens + (now - self.t) * self.rate)
                self.t = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)
