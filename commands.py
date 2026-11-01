"""Every slash command in one place: registered with Telegram at start-up (so the "/" menu is complete), used to build
/help, and checked by tests/test_commands.py against the plugins so the list can never drift from the code."""
import logging

import config
from database import settings
from utils import is_admin, is_owner

log = logging.getLogger("commands")

# (command, description shown in Telegram's menu (<= 256 chars), who can use it)
COMMANDS: list[tuple[str, str, str]] = [
    ("start", "Open the main menu", "user"),
    ("menu", "Main menu", "user"),
    ("search", "Search movies and series", "user"),
    ("latest", "What's new", "user"),
    ("help", "How the bot works", "user"),
    ("about", "About and credits", "user"),
    ("cancel", "Cancel what you are doing", "user"),
    ("admin", "Admin panel", "admin"),
    ("channels", "Post channels, designs, links, blur", "admin"),
    ("post", "Post a title: /post movie|tv <tmdb id>", "admin"),
    ("runnow", "Check the feed and autopost now", "admin"),
    ("addadmin", "Add an admin: /addadmin <user id>", "owner"),
    ("rmadmin", "Remove an admin: /rmadmin <user id>", "owner"),
]
_RANK = {"user": 0, "admin": 1, "owner": 2}
_scoped: dict[int, int] = {}        # chat id -> the level we last registered for it (this process)


def level_of(uid: int) -> str:
    return "owner" if is_owner(uid) else ("admin" if is_admin(uid) else "user")


def for_level(level: str) -> list[tuple[str, str]]:
    return [(c, d) for c, d, who in COMMANDS if _RANK[who] <= _RANK[level]]


def help_text(level: str) -> str:
    from fmt import esc
    lines = [f"/{c} — {esc(d)}" for c, d in for_level(level) if c != "start"]
    return "<b>Commands</b>\n<blockquote expandable>" + "\n".join(lines) + "</blockquote>"


async def register(client) -> int:
    """Start-up: set the public command menu and the extra menus for known admins/owners. Never fatal."""
    from pyrogram.types import BotCommand, BotCommandScopeDefault
    try:
        await client.set_bot_commands([BotCommand(c, d) for c, d in for_level("user")], scope=BotCommandScopeDefault())
    except Exception as e:
        log.warning("couldn't register the command menu: %s", e)
        return 0
    n = 0
    for uid in set(config.OWNER_IDS) | {int(x) for x in (settings.get("admins") or []) if str(x).lstrip("-").isdigit()}:
        n += int(await sync_user(client, uid, force=True))
    log.info("command menu registered (%d public commands, %d admin chats)", len(for_level("user")), n)
    return n


async def sync_user(client, uid: int, force: bool = False) -> bool:
    """Give `uid`'s chat the right menu for their role (called on /start, so promotions and demotions follow)."""
    from pyrogram.types import BotCommand, BotCommandScopeChat
    level = level_of(uid)
    rank = _RANK[level]
    if not force and _scoped.get(uid, 0) == rank:
        return False
    if rank == 0 and uid not in _scoped:
        return False
    try:
        scope = BotCommandScopeChat(chat_id=uid)
        if rank == 0:
            await client.delete_bot_commands(scope=scope)
            _scoped.pop(uid, None)
        else:
            await client.set_bot_commands([BotCommand(c, d) for c, d in for_level(level)], scope=scope)
            _scoped[uid] = rank
        return True
    except Exception as e:
        log.debug("no command menu for %s: %s", uid, e)
        return False
