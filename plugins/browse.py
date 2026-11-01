"""Search and browsing: type a title (or /search), open it, pick a season and episode, watch."""
import asyncio

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup as M

import texts
import views
from buttons import B
from database import db
from tmdb import tmdb
from ui import done, guarded, show


async def run_search(client, m, query: str):
    q = query.strip()
    if len(q) < 2:
        return await m.reply_text(texts.SEARCH_SHORT)
    if not tmdb.configured():
        return await m.reply_text(texts.NOT_READY)
    asyncio.create_task(db.touch_user(m.from_user.id))
    note = await m.reply_text(texts.SEARCHING)
    try:
        text, markup = await views.search_view(q)
        await note.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    except Exception:
        try:
            await note.delete()
        except Exception:
            pass
        raise


@Client.on_message(filters.command("search") & filters.private)
@guarded
async def cmd_search(client, m):
    q = " ".join(m.command[1:])
    if not q.strip():
        return await m.reply_text(texts.SEARCH_PROMPT)
    await run_search(client, m, q)


@Client.on_message(filters.command("latest") & filters.private)
@guarded
async def cmd_latest(client, m):
    await m.reply_text("🆕 <b>What's new?</b>", reply_markup=M([[B("🎬 Movies", callback_data="lt:m:1"), B("📺 Series", callback_data="lt:t:1")]]))


@Client.on_message(filters.private & filters.text & ~filters.regex(r"^/"))
@guarded
async def on_text(client, m):
    await run_search(client, m, m.text or "")


@Client.on_callback_query(filters.regex(r"^(d|ss|se|ep|sr|lt):"))
@guarded
async def cb_browse(client, cq):
    p = cq.data.split(":")
    if not tmdb.configured():
        return await done(cq, "TMDB isn't set up yet.", alert=True)
    asyncio.create_task(db.touch_user(cq.from_user.id))
    photo = None
    if p[0] == "d":
        text, markup, photo = await views.title_view(views.kind_of(p[1]), int(p[2]))
    elif p[0] == "ss":
        text, markup, photo = await views.seasons_view(int(p[1]))
    elif p[0] == "se":
        text, markup, photo = await views.episodes_view(int(p[1]), int(p[2]), int(p[3]) if len(p) > 3 else 1)
    elif p[0] == "ep":
        text, markup, photo = await views.episode_view(int(p[1]), int(p[2]), int(p[3]))
    elif p[0] == "sr":
        q = views.query_of(p[1])
        if not q:
            return await done(cq, texts.EXPIRED, alert=True)
        text, markup = await views.search_view(q, max(1, int(p[2])))
    else:
        text, markup = await views.latest_view(views.kind_of(p[1]), max(1, int(p[2])))
    await show(client, cq, text, markup, photo)
    await done(cq)
