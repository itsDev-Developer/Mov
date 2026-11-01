"""Every inline button in the bot is built here, so Telegram's native colours are used consistently.

    B("Download", url=...)                      plain
    B("Download", url=..., style=SUCCESS)       green   (PRIMARY = blue, DANGER = red)

Colours need a Pyrogram build whose InlineKeyboardButton has a `style` argument (Kurigram >= 2.2.x
on a recent TL layer) and Telegram apps from Feb 2026 or later. On anything older the button simply
renders plain, nothing breaks. Set BUTTON_STYLES=off to switch colours off.
"""
import inspect
import logging
import os

from pyrogram import enums
from pyrogram.types import InlineKeyboardButton as _Button

log = logging.getLogger("buttons")

PRIMARY, SUCCESS, DANGER = "primary", "success", "danger"


def make_factory(button_cls, enums_mod, enabled: bool = True):
    """Build B(). Split out so tests can pass fake classes."""
    supported = False
    if enabled:
        try:
            supported = "style" in inspect.signature(button_cls.__init__).parameters
        except (TypeError, ValueError):
            supported = False
    style_enum = getattr(enums_mod, "ButtonStyle", None)

    def B(text, *args, style=None, **kwargs):
        if style and supported:
            value = getattr(style_enum, str(style).upper(), style) if style_enum is not None else style
            try:
                return button_cls(text, *args, style=value, **kwargs)
            except Exception as e:           # unknown value for this version: fall back to plain
                log.debug("button style %r rejected: %s", style, e)
        return button_cls(text, *args, **kwargs)

    B.styles_active = supported
    return B


B = make_factory(_Button, enums, os.getenv("BUTTON_STYLES", "on").strip().lower() not in ("off", "0", "false", "no"))
styles_active = B.styles_active
