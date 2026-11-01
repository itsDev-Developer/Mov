"""Roles, small keyboard helpers, admin notices and the conversation-state registry."""
import logging
import re
import time

import config
import runtime
from buttons import B
from database import settings

log = logging.getLogger("utils")

states: dict[int, dict] = {}        # admin id -> the prompt they are answering
STATE_TTL = 900                     # an unanswered prompt stops capturing messages after 15 minutes
input_handlers: dict = {}           # action -> async handler(client, message, uid, state); plugins register here


def is_owner(uid: int) -> bool:
    return uid in config.OWNER_IDS


def is_admin(uid: int) -> bool:
    return uid in config.OWNER_IDS or uid in (settings.get("admins") or [])


def set_state(uid: int, action: str, back: str = "a:home", **extra):
    states[uid] = {"action": action, "back": back, "_at": time.time(), **extra}


@runtime.on_housekeeping
def _prune_states():
    now = time.time()
    for uid in [u for u, st in states.items() if now - st.get("_at", now) > STATE_TTL]:
        states.pop(uid, None)


def peer(v) -> int | str:
    """A chat reference from what an admin types: -1001234 / 12345 / @name / t.me/name."""
    s = str(v).strip()
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    s = re.sub(r"^(https?://)?(t\.me|telegram\.me)/", "", s).lstrip("@").split("/")[0]
    return "@" + s


def grid(items: list, per_row: int) -> list[list]:
    return [items[i:i + per_row] for i in range(0, len(items), per_row)]


def nav_row(prefix: str, page: int, pages: int) -> list:
    row = []
    if page > 1:
        row.append(B("◀️", callback_data=f"{prefix}:{page - 1}"))
    row.append(B(f"{page}/{pages}", callback_data="noop"))
    if page < pages:
        row.append(B("▶️", callback_data=f"{prefix}:{page + 1}"))
    return row


async def notify_admins(client, text: str):
    """Tell the admins something happened: the log channel if one is set, else a DM to every owner."""
    chan = str(settings.get("log_channel") or "").strip()
    targets = [peer(chan)] if chan else sorted(config.OWNER_IDS)
    for t in targets:
        try:
            await client.send_message(t, text)
        except Exception as e:
            log.debug("couldn't notify %s: %s", t, e)
