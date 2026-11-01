"""MongoDB access and the runtime settings cache (everything an admin can change without a restart)."""
import logging
import time
from datetime import datetime, timezone

import config

log = logging.getLogger("db")

DEFAULTS = {
    "tmdb_key": config.TMDB_KEY,          # TMDB v3 API key or v4 read access token (owners set it in /admin)
    "tmdb_language": config.TMDB_LANGUAGE,
    "vidsrc_base": config.VIDSRC_BASE,
    "feed_movie_type": "latest",          # the {type} in /vapi/movie/{type}/{page}
    "feed_tv_type": "latest",             # the {type} in /vapi/tv/{type}/{page}
    "sub_info": "",                       # JSON passed through as ?sub.info= on embed links ("" = none)
    "adult": False,                       # include adult titles in search results
    "require_poster": True,               # autopost skips titles TMDB has no image for
    "use_webapp": False,                  # open "Watch" inside Telegram (Mini App) instead of the browser
    "autopost_enabled": True,
    "autopost_interval": config.AUTOPOST_INTERVAL_MIN,
    "autopost_max": config.AUTOPOST_MAX_PER_CYCLE,
    "autopost_backfill": 0,               # on the very first run, post this many of the newest titles (0 = none)
    "seeded": {},                         # {"movie": True, "tv": True}: the feed's existing titles were recorded
    "post_template": "detailed",          # detailed | classic | minimal
    "blur_mode": "off",                   # off | spoiler | blur
    "poster_source": "poster",            # poster | backdrop
    "premium_link": "",
    "backup_link": "",
    "log_channel": "",                    # where admin notices go; empty = DM the owners
    "admins": [],                         # extra admins (owners come from OWNER_IDS)
}


class Settings:
    def __init__(self):
        self._d: dict = {}
        self._col = None

    async def load(self, col):
        self._col = col
        async for doc in col.find({}):
            if doc["_id"] in DEFAULTS:
                self._d[doc["_id"]] = doc["v"]

    def get(self, key: str):
        return self._d.get(key, DEFAULTS[key])          # an unknown key is a bug: KeyError, not a silent None

    async def set(self, key: str, value):
        if key not in DEFAULTS:
            raise KeyError(key)
        self._d[key] = value
        await self._col.update_one({"_id": key}, {"$set": {"v": value}}, upsert=True)


settings = Settings()


class Database:
    def __init__(self):
        self.client = None
        self.users = self.seen = self.channels = self.stats = None
        self._touched: dict[int, float] = {}

    async def init(self):
        from pymongo import AsyncMongoClient
        self.client = AsyncMongoClient(config.MONGO_URI, serverSelectionTimeoutMS=8000)
        d = self.client[config.DB_NAME]
        self.users, self.seen, self.channels, self.stats = d.users, d.seen, d.channels, d.stats
        await self.client.admin.command("ping")
        await self.seen.create_index("at")
        await settings.load(d.settings)
        log.info("MongoDB connected (%d settings overridden)", len(settings._d))

    async def ping(self) -> bool:
        await self.client.admin.command("ping")
        return True

    async def close(self):
        if self.client is not None:
            await self.client.close()

    # ── users / counters ─────────────────────────────────────────────────────
    async def touch_user(self, uid: int) -> bool:
        """Record activity (at most once per 10 minutes per user). True the first time we ever see them."""
        now = time.time()
        if now - self._touched.get(uid, 0) < 600:
            return False
        if len(self._touched) > 20000:
            self._touched.clear()
        self._touched[uid] = now
        try:
            r = await self.users.update_one({"_id": uid}, {"$set": {"last": datetime.now(timezone.utc)},
                                                           "$setOnInsert": {"first": datetime.now(timezone.utc)}}, upsert=True)
            return r.upserted_id is not None
        except Exception as e:
            log.debug("touch_user failed: %s", e)
            return False

    async def inc(self, name: str, n: int = 1):
        try:
            await self.stats.update_one({"_id": name}, {"$inc": {"n": n}}, upsert=True)
        except Exception:
            pass                                          # statistics must never break the bot

    async def counters(self) -> dict:
        return {d["_id"]: d["n"] async for d in self.stats.find({})}

    async def user_count(self) -> int:
        return await self.users.estimated_document_count()

    # ── titles the autoposter has dealt with ─────────────────────────────────
    async def unseen(self, kind: str, ids: list[int]) -> list[int]:
        """The ids (in the given order) that have not been handled yet."""
        if not ids:
            return []
        found = {d["_id"] async for d in self.seen.find({"_id": {"$in": [f"{kind}:{i}" for i in ids]}}, {"_id": 1})}
        return [i for i in ids if f"{kind}:{i}" not in found]

    async def mark_seen(self, kind: str, tmdb_id: int, **fields):
        await self.seen.update_one({"_id": f"{kind}:{tmdb_id}"}, {"$set": {"at": datetime.now(timezone.utc), **fields}}, upsert=True)

    # ── the channels we post to ──────────────────────────────────────────────
    async def channels_list(self) -> list[dict]:
        return await self.channels.find({}).sort("at", 1).to_list(length=100)

    async def channel_get(self, chat: int):
        return await self.channels.find_one({"_id": int(chat)})

    async def channel_put(self, chat: int, **fields):
        await self.channels.update_one({"_id": int(chat)}, {"$set": dict(fields), "$setOnInsert": {"at": datetime.now(timezone.utc)}}, upsert=True)

    async def channel_del(self, chat: int):
        await self.channels.delete_one({"_id": int(chat)})

    async def channel_result(self, chat: int, ok: bool, error: str = "") -> int:
        """Count consecutive failures so a broken channel can be paused. Returns the current streak."""
        if ok:
            await self.channels.update_one({"_id": int(chat)}, {"$set": {"fails": 0, "last_error": ""}})
            return 0
        d = await self.channels.find_one_and_update({"_id": int(chat)}, {"$inc": {"fails": 1}, "$set": {"last_error": error[:200]}},
                                                    return_document=True)
        return int((d or {}).get("fails", 1))


db = Database()
