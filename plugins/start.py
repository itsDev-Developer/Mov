"""Start, menu, help, about, cancel — and the deep links from channel posts (/start m_<id> or t_<id>)."""
import asyncio
import re

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup as M

import commands
import texts
from buttons import B, PRIMARY
from database import db
from fmt import esc
from tmdb import tmdb
from ui import done, guarded, show
from utils import states
import views

DEEP = re.compile(r"^([mt])_(\d{1,9})$")


def main_kb() -> M:
    return M([[B("🔍 Search", callback_data="m:search", style=PRIMARY)],
              [B("🆕 Movies", callback_data="lt:m:1"), B("🆕 Series", callback_data="lt:t:1")],
              [B("❓ Help", callback_data="m:help"), B("ℹ️ About", callback_data="m:about")]])


BACK = M([[B("🏠 Menu", callback_data="m:menu")]])


def _help(uid: int) -> str:
    return texts.HELP.format(commands=commands.help_text(commands.level_of(uid)))


@Client.on_message(filters.command(["start", "menu"]) & filters.private)
@guarded
async def cmd_start(client, m):
    uid = m.from_user.id
    states.pop(uid, None)
    asyncio.create_task(commands.sync_user(client, uid))      # keep their "/" menu in step with their role
    await db.touch_user(uid)
    if len(m.command) > 1 and (hit := DEEP.match(m.command[1])):
        if not tmdb.configured():
            return await m.reply_text(texts.NOT_READY)
        text, markup, photo = await views.title_view(views.kind_of(hit.group(1)), int(hit.group(2)))
        return await show(client, m, text, markup, photo)
    await show(client, m, texts.WELCOME.format(name=esc(m.from_user.first_name or "there")), main_kb())


@Client.on_message(filters.command("help") & filters.private)
@guarded
async def cmd_help(client, m):
    await show(client, m, _help(m.from_user.id), BACK)


@Client.on_message(filters.command("about") & filters.private)
@guarded
async def cmd_about(client, m):
    await show(client, m, texts.ABOUT, BACK)


@Client.on_message(filters.command("cancel") & filters.private)
@guarded
async def cmd_cancel(client, m):
    had = states.pop(m.from_user.id, None)
    try:                                                      # the admin plugin keeps its own "post" drafts
        from plugins import admin
        had = admin._compose.pop(m.from_user.id, None) or had
    except Exception:
        pass
    await m.reply_text("Cancelled." if had else "Nothing to cancel.")


@Client.on_callback_query(filters.regex(r"^m:(menu|help|about|search)$"))
@guarded
async def cb_menu(client, cq):
    what = cq.data.split(":")[1]
    if what == "menu":
        await show(client, cq, texts.WELCOME.format(name=esc(cq.from_user.first_name or "there")), main_kb())
    elif what == "help":
        await show(client, cq, _help(cq.from_user.id), BACK)
    elif what == "about":
        await show(client, cq, texts.ABOUT, BACK)
    else:
        await show(client, cq, texts.SEARCH_PROMPT, BACK)
    await done(cq)


@Client.on_callback_query(filters.regex(r"^noop$"))
async def cb_noop(client, cq):
    await done(cq)
