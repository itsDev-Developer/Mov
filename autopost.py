"""Autopost: watch the vidsrc feed, fetch each NEW title's details from TMDB, and post it to the autopost channels.

  • The very first run only RECORDS what the feed already contains (so your channels aren't flooded with old titles);
    set `autopost_backfill` in /admin to post the newest few of them instead.
  • At most `autopost_max` titles are posted per check, oldest first, so a channel reads in release order; the rest wait
    for the next check.
  • A title is remembered only when it was dealt with: posted, or deliberately skipped (not on TMDB, no image).
    A transient failure (TMDB down, every channel failing) leaves it unseen, so the next check retries it.
  • One cycle at a time: "Run now" while the timer fires is refused, never doubled.
"""
import asyncio
import logging
import time

import publisher
import runtime
from database import db, settings
from media import from_tmdb
from tmdb import NotFound, TmdbError, tmdb
from vidsrc import FeedError, feed

log = logging.getLogger("autopost")
KINDS = ("movie", "tv")


def summary_text(r: dict) -> str:
    if r.get("error") and not (r.get("posted") or r.get("new")):
        why = {"disabled": "autopost is switched off", "no_key": "the TMDB key is not set", "no_channels": "no channel has autopost on",
               "busy": "a check is already running"}.get(r["error"], r["error"])
        return f"⚠️ Nothing done: {why}."
    text = f"✅ Checked: {r['new']} new · {r['posted']} posted · {r['skipped']} skipped · {r['failed']} failed"
    return text + (f"\n⚠️ {r['error']}" if r.get("error") else "")


class AutoPoster:
    def __init__(self):
        self._task: asyncio.Task | None = None
        self._lock: asyncio.Lock | None = None
        self.stats = {"cycles": 0, "posted": 0, "skipped": 0, "failed": 0, "last_run": 0.0, "last_result": "never run"}
        runtime.gauge("vidbot_autopost_posted_total", lambda: self.stats["posted"])
        runtime.gauge("vidbot_autopost_failed_total", lambda: self.stats["failed"])
        runtime.gauge("vidbot_autopost_last_run_timestamp", lambda: int(self.stats["last_run"]))

    async def start(self, client):
        self._task = asyncio.create_task(self._loop(client))

    async def stop(self):
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self, client):
        await asyncio.sleep(10)                           # let start-up finish first
        while True:
            try:
                await self.run_once(client)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("autopost cycle crashed")
            await asyncio.sleep(max(1, int(settings.get("autopost_interval"))) * 60)

    async def run_once(self, client, force: bool = False) -> dict:
        """One check of the feed. `force` ignores the on/off switch (the admin's "Run now")."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        if self._lock.locked():
            return {"posted": 0, "skipped": 0, "failed": 0, "new": 0, "error": "busy"}
        async with self._lock:
            res = await self._cycle(client, force)
        self.stats["cycles"] += 1
        self.stats["last_run"] = time.time()
        self.stats["last_result"] = summary_text(res)
        for k in ("posted", "skipped", "failed"):
            self.stats[k] += res[k]
        return res

    async def _cycle(self, client, force: bool) -> dict:
        res = {"posted": 0, "skipped": 0, "failed": 0, "new": 0, "error": ""}
        if not (force or settings.get("autopost_enabled")):
            res["error"] = "disabled"
            return res
        if not tmdb.configured():
            res["error"] = "no_key"
            return res
        chats = [c["_id"] for c in await publisher.autopost_targets()]
        if not chats:
            res["error"] = "no_channels"
            return res

        budget = max(1, int(settings.get("autopost_max")))
        seeded = dict(settings.get("seeded") or {})
        for kind in KINDS:
            if budget <= 0:
                break
            try:
                items = await feed.latest(kind, settings.get(f"feed_{kind}_type"), 1)
            except (FeedError, ValueError) as e:
                res["error"] = f"{kind} feed: {e}"
                continue
            by_id = {i.tmdb_id: i for i in items if i.tmdb_id}
            new = await db.unseen(kind, list(by_id))
            if not seeded.get(kind):                      # first run: remember what is already there, post only the backfill
                keep = max(0, int(settings.get("autopost_backfill")))
                for tid in new[keep:]:
                    await db.mark_seen(kind, tid, reason="seed")
                new, seeded[kind] = new[:keep], True
                await settings.set("seeded", seeded)
            res["new"] += len(new)
            for tid in reversed(new):                     # the feed is newest-first; post oldest-first
                if budget <= 0:
                    break
                outcome = await self._one(client, kind, tid, by_id[tid], chats)
                if outcome == "abort":
                    res["error"] = "the TMDB key was rejected"
                    return res
                if outcome in res:
                    res[outcome] += 1
                if outcome == "posted":
                    budget -= 1
                elif outcome == "failed":
                    break                                 # retry next cycle; don't hammer a service that is struggling
        return res

    async def _one(self, client, kind: str, tid: int, item, chats: list[int]) -> str:
        try:
            d = await tmdb.details(kind, tid)
        except NotFound:
            await db.mark_seen(kind, tid, reason="tmdb_not_found")
            return "skipped"
        except TmdbError as e:
            log.warning("TMDB %s %s: %s", kind, tid, e.code)
            return "abort" if e.code in ("invalid_key", "no_key") else "failed"
        m = from_tmdb(kind, d)
        m.tmdb_id, m.quality = m.tmdb_id or tid, item.quality
        if settings.get("require_poster") and not (m.poster_path or m.backdrop_path):
            await db.mark_seen(kind, tid, reason="no_image", title=m.title)
            return "skipped"
        results = await publisher.publish(client, m, chats, season=item.season, episode=item.episode)
        if "posted" in results.values():
            await db.mark_seen(kind, tid, reason="posted", title=m.title)
            await db.inc("posts")
            return "posted"
        return "failed"


autoposter = AutoPoster()
runtime.on_shutdown(autoposter.stop)
