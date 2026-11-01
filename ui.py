"""Rendering helpers: guarded handlers, pages that edit-or-send (with an optional photo), callback answers."""
import functools
import logging

import texts
from pyrogram import StopPropagation
from pyrogram.errors import MessageNotModified
from tmdb import TmdbError
from vidsrc import FeedError

log = logging.getLogger("ui")


def _is_callback(ev) -> bool:
    return hasattr(ev, "data") and hasattr(ev, "answer")


async def done(cq, text: str = "", alert: bool = False):
    """Answer a callback query (a toast, or an alert box). Never raises: an expired query is not an error."""
    try:
        await cq.answer(text or None, show_alert=alert)
    except Exception:
        pass


async def _tell(client, ev, text: str):
    if _is_callback(ev):
        return await done(ev, text[:190], alert=True)
    try:
        await client.send_message(ev.chat.id, text)
    except Exception:
        pass


def guarded(fn):
    """A handler that fails must never leave the user staring at nothing, and must never take the bot down."""
    @functools.wraps(fn)
    async def wrapper(client, ev, *a, **k):
        try:
            return await fn(client, ev, *a, **k)
        except StopPropagation:
            raise
        except TmdbError as e:
            log.info("%s: TMDB %s", fn.__name__, e.code)
            await _tell(client, ev, e.user_text())
        except FeedError as e:
            log.info("%s: feed %s", fn.__name__, e)
            await _tell(client, ev, texts.NOTHING_NEW)
        except Exception:
            log.exception("handler %s failed", fn.__name__)
            await _tell(client, ev, texts.ERR_GENERIC)
    return wrapper


async def show(client, ev, text: str, markup=None, photo: str | None = None):
    """Show a page. A callback edits the message it came from (switching between photo and text pages by replacing
    the message); a command sends a new one. A bad image URL falls back to a text page."""
    if _is_callback(ev) and ev.message is not None:
        msg = ev.message
        try:
            if photo and msg.photo:
                from pyrogram.types import InputMediaPhoto
                await msg.edit_media(InputMediaPhoto(photo, caption=text), reply_markup=markup)
                return
            if not photo and not msg.photo:
                await msg.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
                return
        except MessageNotModified:
            return
        except Exception as e:
            log.debug("edit failed (%s), sending a new message", type(e).__name__)
        try:
            await msg.delete()                           # photo <-> text pages can't be edited into each other
        except Exception:
            pass
        chat_id = msg.chat.id
    else:
        chat_id = ev.chat.id
    if photo:
        try:
            return await client.send_photo(chat_id, photo, caption=text, reply_markup=markup)
        except Exception as e:
            log.info("photo failed (%s), sending text", type(e).__name__)
    return await client.send_message(chat_id, text, reply_markup=markup, disable_web_page_preview=True)
