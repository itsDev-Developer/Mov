"""Posting titles to channels: the channel list, the post (poster · caption · buttons) and the sending.

Per channel: autopost on/off · caption design · poster blur · ⭐ Premium and 💾 Back-up links. Anything left on
"default" uses the global value from /admin. A channel that fails CHANNEL_MAX_FAILS posts in a row is paused, so one
broken channel never blocks the others. Posts are spaced ANNOUNCE_MIN_GAP seconds apart and FloodWait is honoured.
A requested blur NEVER falls back to the sharp image (see poster.py).
"""
import asyncio
import logging
import re
import time

import captions
import config
import poster
import texts
import vidsrc
from buttons import B, PRIMARY
from database import db, settings
from media import Media
from tmdb import tmdb
from utils import grid, notify_admins, peer

log = logging.getLogger("publisher")
DEFAULT = "default"
_gate: asyncio.Lock | None = None
_last_send = 0.0


# ── the channel list ─────────────────────────────────────────────────────────
def normalize_link(text: str) -> str:
    """Accept https://…, t.me/… or @username; return an https URL or raise ValueError."""
    t = (text or "").strip()
    if re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{3,31}", t):
        return f"https://t.me/{t[1:]}"
    if t.lower().startswith(("t.me/", "telegram.me/")):
        t = "https://" + t
    if not re.fullmatch(r"https?://[^\s]{4,480}", t):
        raise ValueError("Send a link that starts with https://, or t.me/…, or @username. Send “off” to remove it.")
    return t


async def all_channels() -> list[dict]:
    return await db.channels_list()


async def autopost_targets() -> list[dict]:
    return [c for c in await db.channels_list() if c.get("auto", True)]


async def get(chat: int):
    return await db.channel_get(chat)


async def add(chat_id: int, title: str, username: str = ""):
    await db.channel_put(chat_id, title=title or str(chat_id), username=username or "", auto=True, template=DEFAULT,
                         blur=DEFAULT, backup="", premium="", fails=0, last_error="")


async def remove(chat: int):
    await db.channel_del(chat)


async def update(chat: int, **fields):
    await db.channel_put(chat, **fields)


def effective_template(ch: dict | None) -> str:
    t = (ch or {}).get("template", DEFAULT)
    if t in captions.TEMPLATES:
        return t
    g = settings.get("post_template")
    return g if g in captions.TEMPLATES else captions.DEFAULT


def effective_blur(ch: dict | None) -> str:
    b = (ch or {}).get("blur", DEFAULT)
    if b in poster.MODES:
        return b
    g = settings.get("blur_mode")
    return g if g in poster.MODES else "off"


def effective_link(ch: dict | None, kind: str) -> str:
    return str((ch or {}).get(kind) or settings.get(f"{kind}_link") or "")


async def record_result(chat: int, ok: bool, error: str = "") -> bool:
    """Count consecutive failures. True when this failure just paused the channel."""
    streak = await db.channel_result(chat, ok, error)
    if not ok and streak >= config.CHANNEL_MAX_FAILS:
        ch = await db.channel_get(chat)
        if ch and ch.get("auto", True):
            await db.channel_put(chat, auto=False)
            return True
    return False


# ── building a post ──────────────────────────────────────────────────────────
def watch_url(m: Media, season: int | None = None, episode: int | None = None) -> str:
    if m.kind == "movie":
        return vidsrc.embed_movie(m.tmdb_id)
    return vidsrc.embed_tv(m.tmdb_id, season or 1, episode or 1)


def watch_button(url: str, label: str = texts.BTN_WATCH):
    """URL button (opens the browser) — or a Telegram Mini App button when the admin turned that on."""
    if settings.get("use_webapp"):
        from pyrogram.types import InlineKeyboardButton, WebAppInfo
        return InlineKeyboardButton(label, web_app=WebAppInfo(url=url))
    return B(label, url=url, style=PRIMARY)


async def bot_username(client) -> str | None:
    u = getattr(getattr(client, "me", None), "username", None)
    if u:
        return u
    try:
        return (await client.get_me()).username
    except Exception as e:
        log.warning("can't resolve the bot username: %s", e)
        return None


def extra_row(ch: dict | None) -> list:
    row = []
    prem, back = effective_link(ch, "premium"), effective_link(ch, "backup")
    if prem:
        row.append(B(texts.BTN_PREMIUM, url=prem))
    if back:
        row.append(B(texts.BTN_BACKUP, url=back))
    return [row] if row else []


async def build_markup(client, m: Media, ch: dict | None, season: int | None = None, episode: int | None = None):
    from pyrogram.types import InlineKeyboardMarkup as M
    label = texts.BTN_WATCH if m.kind == "movie" or not season else f"▶️ Watch S{season}E{episode or 1}"
    row = [watch_button(watch_url(m, season, episode), label)]
    user = await bot_username(client)
    if user:                                                   # open the title inside this bot (details / episode picker)
        row.append(B("📚 Episodes" if m.kind == "tv" else "ℹ️ Details", url=f"https://t.me/{user}?start={'m' if m.kind == 'movie' else 't'}_{m.tmdb_id}"))
    return M([row] + extra_row(ch))


def pick_image(m: Media) -> str:
    """The image for a post: the poster or the backdrop (admin setting), falling back to whichever exists."""
    poster_url, back_url = tmdb.image_url(m.poster_path, "poster"), tmdb.image_url(m.backdrop_path, "backdrop")
    return (back_url or poster_url) if settings.get("poster_source") == "backdrop" else (poster_url or back_url)


# ── sending ──────────────────────────────────────────────────────────────────
async def _throttle():
    global _gate, _last_send
    if _gate is None:
        _gate = asyncio.Lock()
    async with _gate:
        wait = config.ANNOUNCE_MIN_GAP - (time.monotonic() - _last_send)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_send = time.monotonic()


async def _tg(call):
    from pyrogram.errors import FloodWait
    for attempt in (1, 2):
        await _throttle()
        try:
            return await call()
        except FloodWait as e:
            if attempt == 2:
                raise
            await asyncio.sleep(min(e.value + 1, 120))


async def send(client, chat, caption: str, markup, pst):
    """Photo (maybe spoilered/blurred) with caption, else text only. A hidden poster is never sent sharp."""
    target = chat if isinstance(chat, int) else peer(str(chat))
    if pst is not None:
        try:
            kw = {"has_spoiler": True} if pst.spoiler else {}
            return await _tg(lambda: client.send_photo(target, pst.media(), caption=caption, reply_markup=markup, **kw))
        except TypeError:
            if not pst.spoiler:
                raise
            log.warning("this Pyrogram version has no spoiler photos: posting text only instead of a sharp poster")
        except Exception as e:
            log.info("poster failed (%s), sending text only", type(e).__name__)
    return await _tg(lambda: client.send_message(target, caption, reply_markup=markup))


async def publish(client, m: Media, chats: list[int], template: str | None = None, blur: str | None = None,
                  season: int | None = None, episode: int | None = None) -> dict:
    """Post `m` to each chat now. Returns {chat: 'posted' | reason}. `template` / `blur` override the channels' own."""
    results = {}
    image = pick_image(m)
    for chat in chats:
        ch = await get(chat)
        if not ch:
            results[chat] = "unknown channel"
            continue
        try:
            caption = captions.render(template or effective_template(ch), m)
            pst = await poster.prepare(image, blur or effective_blur(ch))
            await send(client, chat, caption, await build_markup(client, m, ch, season, episode), pst)
            await record_result(chat, True)
            results[chat] = "posted"
        except Exception as e:
            log.warning("post to %s failed: %s", chat, e)
            results[chat] = f"failed ({type(e).__name__})"
            if await record_result(chat, False, f"{type(e).__name__}: {e}"):
                await notify_admins(client, texts.CHANNEL_PAUSED.format(title=ch.get("title") or chat, n=config.CHANNEL_MAX_FAILS))
    return results
