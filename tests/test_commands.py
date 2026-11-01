"""Run with:  python tests/test_commands.py — commands.py is the single list of slash commands: it fills Telegram's "/" menu
at start-up and builds /help. These checks make sure it can't drift from the commands the plugins really answer to."""
import asyncio, glob, os, re

import _stubs
import commands, config
from database import settings


def in_code() -> set[str]:
    found = set()
    for path in glob.glob(os.path.join(_stubs.ROOT, "plugins", "*.py")):
        for call in re.findall(r"filters\.command\(([^)]*)\)", open(path, encoding="utf-8").read()):
            found |= set(re.findall(r"""["']([A-Za-z0-9_]+)["']""", call))
    return found


def t_the_list_is_sane():
    names = [c for c, _, _ in commands.COMMANDS]
    assert len(names) == len(set(names)), "duplicate command"
    for name, desc, who in commands.COMMANDS:
        assert re.fullmatch(r"[a-z0-9_]{1,32}", name), name                 # Telegram's rule for command names
        assert 3 <= len(desc) <= 256, name                                   # ... and for descriptions
        assert who in ("user", "admin", "owner"), name
    assert {"start", "help", "search", "latest", "about"} <= {c for c, _ in commands.for_level("user")}

def t_every_command_in_the_code_is_registered_and_vice_versa():
    code, registry = in_code(), {c for c, _, _ in commands.COMMANDS}
    assert code - registry == set(), f"in the code but missing from commands.py: {sorted(code - registry)}"
    assert registry - code == set(), f"listed in commands.py but no plugin answers to it: {sorted(registry - code)}"

def t_each_level_sees_only_what_it_may_use():
    user, admin, owner = ({c for c, _ in commands.for_level(l)} for l in ("user", "admin", "owner"))
    assert user < admin < owner and "admin" not in user and "post" in admin and "addadmin" not in admin and "addadmin" in owner
    assert "/admin" not in commands.help_text("user") and "/post" in commands.help_text("admin") and "/addadmin" in commands.help_text("owner")
    assert "<blockquote expandable>" in commands.help_text("user") and "/start" not in commands.help_text("user")


class Client:
    def __init__(self): self.set, self.deleted = [], []
    async def set_bot_commands(self, cmds, scope=None): self.set.append(([c.command for c in cmds], scope.chat_id if scope.chat_id is not None else "default"))
    async def delete_bot_commands(self, scope=None): self.deleted.append(scope.chat_id)

async def t_start_up_registers_the_public_menu_and_each_admins_menu():
    commands._scoped.clear(); config.OWNER_IDS = {1}; settings._d["admins"] = [2, 3]
    c = Client()
    n = await commands.register(c)
    by_chat = {chat: names for names, chat in c.set}
    assert n == 3 and set(by_chat) == {"default", 1, 2, 3}
    assert "admin" not in by_chat["default"] and "post" in by_chat[2] and "addadmin" not in by_chat[2] and "addadmin" in by_chat[1]

async def t_promotions_and_demotions_follow_without_a_restart():
    commands._scoped.clear(); config.OWNER_IDS = {1}; settings._d["admins"] = []
    c = Client()
    assert await commands.sync_user(c, 50) is False and not c.set and not c.deleted, "ordinary users cost no API call"
    settings._d["admins"] = [50]
    assert await commands.sync_user(c, 50) is True and c.set[-1][1] == 50 and "admin" in c.set[-1][0]
    n = len(c.set)
    assert await commands.sync_user(c, 50) is False and len(c.set) == n, "unchanged role: nothing re-sent"
    settings._d["admins"] = []
    assert await commands.sync_user(c, 50) is True and c.deleted == [50] and await commands.sync_user(c, 50) is False

async def t_a_failing_telegram_call_never_breaks_start_up():
    class Broken(Client):
        async def set_bot_commands(self, cmds, scope=None): raise RuntimeError("flood")
    commands._scoped.clear()
    assert await commands.register(Broken()) == 0 and await commands.sync_user(Broken(), 1, force=True) is False


async def main():
    for name, fn in list(globals().items()):
        if name.startswith("t_"):
            r = fn()
            if asyncio.iscoroutine(r):
                await r
            print("ok ", name[2:].replace("_", " "))
    print("all command checks passed")

asyncio.run(main())
