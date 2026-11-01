"""Run with:  python tests/test_admin.py — the admin plugin: settings parsing, the TMDB key flow (checked, never echoed, never
logged), the channel manager, the "choose channels or All" post flow, admin management and the typed-input router."""
import asyncio, logging, re, types
from unittest import mock

import _stubs
import commands, config, logging_setup, publisher
from database import settings
from plugins import admin
from tmdb import TmdbError
from utils import states

P = _stubs.sys.modules["pyrogram"]
settings._d.update(admins=[2], tmdb_key="", adult=False)
async def _noop(*a, **k): return None
settings._col = types.SimpleNamespace(update_one=_noop)

OWNER, ADMIN, USER = 1, 2, 9


class FakeDB:
    def __init__(self): self.channels = {}
    async def channels_list(self): return [dict(v) for v in self.channels.values()]
    async def channel_get(self, chat): return dict(self.channels[int(chat)]) if int(chat) in self.channels else None
    async def channel_put(self, chat, **f): self.channels.setdefault(int(chat), {"_id": int(chat), "auto": True}).update(f)
    async def channel_del(self, chat): self.channels.pop(int(chat), None)
    async def channel_result(self, chat, ok, error=""): return 0
    async def user_count(self): return 42
    async def counters(self): return {"posts": 7}


class Msg:
    def __init__(self, text="", uid=OWNER, command=None):
        self.text, self.command, self.from_user, self.chat = text, command or [], types.SimpleNamespace(id=uid, first_name="A"), types.SimpleNamespace(id=uid)
        self.deleted, self.replies = False, []
    async def delete(self): self.deleted = True
    async def reply_text(self, text, **k): self.replies.append(text); return types.SimpleNamespace(edit_text=self._edit)
    async def _edit(self, text, **k): self.replies.append(("edited", text))

class CQ:
    def __init__(self, data, uid=OWNER):
        self.data, self.from_user, self.message, self.answers = data, types.SimpleNamespace(id=uid, first_name="A"), types.SimpleNamespace(chat=types.SimpleNamespace(id=uid), delete=_noop), []
    async def answer(self, text=None, show_alert=False): self.answers.append((text, show_alert))

class Client:
    def __init__(self): self.sent, self.chats = [], {}
    async def send_message(self, chat, text, **k):
        self.sent.append((chat, text))
        return types.SimpleNamespace(delete=_noop, id=1)
    async def get_chat(self, ref):
        if ref not in self.chats: raise RuntimeError("CHAT_NOT_FOUND")
        return self.chats[ref]


def setup():
    db = FakeDB()
    admin.db = publisher.db = db
    shown, toasts = [], []
    async def show(client, ev, text, markup=None, photo=None): shown.append((text, markup))
    async def done(cq, text="", alert=False): toasts.append((text, alert))
    admin.show, admin.done = show, done
    states.clear(); admin._compose.clear()
    config.OWNER_IDS = {OWNER}
    settings._d.update(admins=[ADMIN], tmdb_key="", autopost_enabled=True, premium_link="", backup_link="")
    return db, shown, toasts

def rows(markup): return [b.callback_data for r in markup.rows for b in r if b.callback_data]


def t_the_setting_parsers_accept_good_values_and_explain_bad_ones():
    f = {k: v[2] for k, v in admin.FIELDS.items()}
    assert f["tmdb_language"]("de-DE") == "de-DE" and f["tmdb_language"]("fr") == "fr"
    assert f["vidsrc_base"]("https://vidsrc.mov/") == "https://vidsrc.mov" and f["feed_tv_type"](" latest ") == "latest"
    assert f["sub_info"]('{ "a" : "b" }') == '{"a":"b"}' and f["sub_info"]("OFF") == "" and f["premium_link"]("@shop_bot") == "https://t.me/shop_bot"
    assert f["backup_link"]("off") == "" and f["log_channel"]("off") == "" and f["log_channel"]("-1001234") == "-1001234" and f["autopost_interval"]("15") == 15
    for key, bad in (("tmdb_language", "english"), ("tmdb_language", "en_us"), ("vidsrc_base", "ftp://x"), ("vidsrc_base", "https://x.com/path?a=1"),
                     ("vidsrc_base", "javascript:1"), ("feed_movie_type", "../etc"), ("feed_movie_type", "a b"), ("sub_info", "{not json"),
                     ("sub_info", '{"x":"' + "y" * 2000 + '"}'), ("autopost_interval", "0"), ("autopost_interval", "9999"), ("autopost_max", "abc"),
                     ("autopost_backfill", "-1"), ("autopost_backfill", "21"), ("premium_link", "javascript:alert(1)"), ("backup_link", "just text")):
        try:
            f[key](bad); assert False, (key, bad)
        except ValueError:
            pass

async def t_only_admins_open_the_panel_and_only_owners_change_where_links_point():
    db, shown, toasts = setup()
    await admin.cb_admin(Client(), CQ("a:home", USER))
    assert toasts[-1] == ("Admins only.", True) and not shown
    await admin.cb_admin(Client(), CQ("a:e:vidsrc_base", ADMIN))
    assert toasts[-1] == ("Owners only.", True) and ADMIN not in states, "the player site is owner-only"
    await admin.cb_admin(Client(), CQ("a:key", ADMIN))
    assert toasts[-1] == ("Owners only.", True)
    await admin.cb_admin(Client(), CQ("a:e:autopost_max", ADMIN))
    assert states[ADMIN]["field"] == "autopost_max" and "Max posts" in shown[-1][0]

async def t_a_typed_value_is_saved_or_refused_with_the_prompt_kept_open():
    db, shown, toasts = setup(); c = Client()
    states[ADMIN] = {"action": "field", "field": "autopost_max"}
    await admin._in_field(c, Msg("0", ADMIN), ADMIN, states[ADMIN])
    assert "❌" in c.sent[-1][1] and ADMIN in states and settings.get("autopost_max") != 0
    await admin._in_field(c, Msg("8", ADMIN), ADMIN, states[ADMIN])
    assert settings.get("autopost_max") == 8 and ADMIN not in states and "saved" in c.sent[-1][1]
    states[ADMIN] = {"action": "field", "field": "sub_info"}
    await admin._in_field(c, Msg("off", ADMIN), ADMIN, states[ADMIN])
    assert settings.get("sub_info") == "" and "cleared" in c.sent[-1][1]

async def t_the_tmdb_key_is_checked_never_echoed_and_scrubbed_from_logs():
    db, shown, toasts = setup(); c = Client()
    key = "0123456789abcdef0123456789abcdef"
    checked, warmed = [], []
    async def check_ok(k): checked.append(k); return {"images": {}}
    async def check_bad(k): raise TmdbError("invalid_key", status=401)
    async def warm(): warmed.append(1)
    states[OWNER] = {"action": "tmdb_key"}
    m = Msg("short", OWNER)
    await admin._in_key(c, m, OWNER, states[OWNER])
    assert m.deleted and "doesn't look like" in c.sent[-1][1] and OWNER in states and settings.get("tmdb_key") == ""
    with mock.patch.object(admin.tmdb, "check_key", check_bad):
        m = Msg(key, OWNER); await admin._in_key(c, m, OWNER, states[OWNER])
    assert m.deleted and "Nothing was saved" in c.sent[-1][1] and settings.get("tmdb_key") == "" and OWNER in states, "a rejected key is not saved"
    assert key not in c.sent[-1][1]
    logging_setup._redact = logging_setup.Redact(["x" * 10])
    with mock.patch.object(admin.tmdb, "check_key", check_ok), mock.patch.object(admin.tmdb, "warm", warm):
        m = Msg(key, OWNER); await admin._in_key(c, m, OWNER, states[OWNER])
    assert m.deleted and checked == [key] and settings.get("tmdb_key") == key and warmed and OWNER not in states and key not in c.sent[-1][1]
    rec = logging.LogRecord("x", logging.INFO, "f", 1, f"GET /3/movie/1?api_key={key}", None, None)
    logging_setup._redact.filter(rec)
    assert key not in rec.getMessage() and "***" in rec.getMessage(), "the key set at runtime is redacted from log lines"
    states[OWNER] = {"action": "tmdb_key"}
    await admin._in_key(c, Msg("off", OWNER), OWNER, states[OWNER])
    assert settings.get("tmdb_key") == ""

async def t_toggles_cycles_and_autopost_controls():
    db, shown, toasts = setup(); c = Client()
    await admin.cb_admin(c, CQ("a:t:adult", ADMIN)); assert settings.get("adult") is True
    await admin.cb_admin(c, CQ("a:t:adult", ADMIN)); assert settings.get("adult") is False
    for key, seq in (("post_template", ["classic", "minimal", "detailed"]), ("blur_mode", ["spoiler", "blur", "off"]), ("poster_source", ["backdrop", "poster"])):
        settings._d[key] = {"post_template": "detailed", "blur_mode": "off", "poster_source": "poster"}[key]
        got = []
        for _ in seq:
            await admin.cb_admin(c, CQ(f"a:c:{key}", ADMIN)); got.append(settings.get(key))
        assert got == seq, key
    await admin.cb_admin(c, CQ("a:ap", ADMIN)); assert settings.get("autopost_enabled") is False
    res = []
    async def run_once(client, force=False): res.append(force); return {"posted": 2, "skipped": 0, "failed": 0, "new": 2, "error": ""}
    with mock.patch.object(admin.autoposter, "run_once", run_once):
        await admin.cb_admin(c, CQ("a:run", ADMIN))
    assert res == [True] and "2 posted" in c.sent[-1][1], "Run now ignores the on/off switch and reports"

async def t_adding_and_managing_channels():
    db, shown, toasts = setup(); c = Client()
    c.chats["@news"] = types.SimpleNamespace(id=-1001, title="News <1>", type="ChatType.CHANNEL", username="news")
    c.chats["@person"] = types.SimpleNamespace(id=5, title="P", type="ChatType.PRIVATE", username=None)
    states[OWNER] = {"action": "ch_add"}
    await admin._in_ch_add(c, Msg("@nowhere"), OWNER, states[OWNER]); assert "Can't access" in c.sent[-1][1] and not db.channels
    await admin._in_ch_add(c, Msg("@person"), OWNER, states[OWNER]); assert "not a channel" in c.sent[-1][1] and not db.channels
    await admin._in_ch_add(c, Msg("@news"), OWNER, states[OWNER])
    assert db.channels[-1001]["title"] == "News <1>" and db.channels[-1001]["auto"] is True and OWNER not in states
    await admin.cb_admin(c, CQ("a:home")); await admin.cb_channels(c, CQ("ch:home"))
    assert "News &lt;1&gt;" in shown[-1][0] and "ch:v:-1001" in rows(shown[-1][1]), "channel titles are escaped"
    await admin.cb_channels(c, CQ("ch:auto:-1001")); assert db.channels[-1001]["auto"] is False
    await admin.cb_channels(c, CQ("ch:auto:-1001")); assert db.channels[-1001]["auto"] is True
    for _ in range(3): await admin.cb_channels(c, CQ("ch:tpl:-1001"))
    assert db.channels[-1001]["template"] == "minimal"
    await admin.cb_channels(c, CQ("ch:tpl:-1001")); assert db.channels[-1001]["template"] == "default"
    await admin.cb_channels(c, CQ("ch:blur:-1001")); assert db.channels[-1001]["blur"] == "off"
    await admin.cb_channels(c, CQ("ch:lnk:-1001:p")); assert states[OWNER]["kind"] == "premium"
    await admin._in_ch_link(c, Msg("javascript:1"), OWNER, states[OWNER]); assert "❌" in c.sent[-1][1] and OWNER in states
    await admin._in_ch_link(c, Msg("@shop_bot"), OWNER, states[OWNER]); assert db.channels[-1001]["premium"] == "https://t.me/shop_bot" and OWNER not in states
    sent = []
    async def fake_send(client, chat, caption, markup, pst): sent.append((chat, caption, [b.url for r in markup.rows for b in r]))
    with mock.patch.object(publisher, "send", fake_send):
        await admin.cb_channels(c, CQ("ch:test:-1001"))
    assert sent[0][0] == -1001 and "Test title" in sent[0][1] and "https://t.me/shop_bot" in sent[0][2] and toasts[-1] == ("Test post sent ✅", True)
    await admin.cb_channels(c, CQ("ch:del:-1001")); assert "Remove this channel?" in shown[-1][0] and -1001 in db.channels
    await admin.cb_channels(c, CQ("ch:delgo:-1001")); assert not db.channels
    await admin.cb_channels(c, CQ("ch:home", USER)); assert toasts[-1] == ("Admins only.", True)

async def t_posting_by_hand_asks_which_channel_or_all():
    db, shown, toasts = setup(); c = Client()
    db.channels = {-1001: {"_id": -1001, "title": "A", "auto": True}, -1002: {"_id": -1002, "title": "B", "auto": True}}
    assert admin._parse_ref("movie 603") == ("movie", 603) and admin._parse_ref("TV 1399") == ("tv", 1399)
    for bad in ("", "movie", "603", "film 1", "movie x", "movie 0", "movie 1 2", "movie 99999999999"):
        try:
            admin._parse_ref(bad); assert False, bad
        except ValueError:
            pass
    async def details(kind, tid): return {"id": tid, "title": "The <Matrix>", "release_date": "1999-01-01", "overview": "Story & more."}
    posted = []
    async def publish(client, m, chats, tpl, blur): posted.append((m.title, chats, tpl, blur)); return {ch: "posted" for ch in chats}
    settings._d["tmdb_key"] = "0123456789abcdef0123456789abcdef"
    with mock.patch.object(admin.tmdb, "details", details), mock.patch.object(publisher, "publish", publish):
        await admin.cmd_post(c, Msg(command=["post", "movie", "603"]))
        text, markup = shown[-1]
        assert "The &lt;Matrix&gt;" in text and "nothing selected" in text and rows(markup)[:3] == ["pp:t:-1001", "pp:t:-1002", "pp:all"]
        assert "<b>" not in text.split("Preview")[1], "the preview is plain text"
        await admin.cb_post(c, CQ("pp:go")); assert toasts[-1][0].startswith("Pick at least one") and not posted
        await admin.cb_post(c, CQ("pp:t:-1002")); assert admin._compose[OWNER]["sel"] == {-1002}
        await admin.cb_post(c, CQ("pp:all")); assert admin._compose[OWNER]["sel"] == {-1001, -1002}
        await admin.cb_post(c, CQ("pp:all")); assert admin._compose[OWNER]["sel"] == set()
        await admin.cb_post(c, CQ("pp:t:-1001")); await admin.cb_post(c, CQ("pp:tpl")); await admin.cb_post(c, CQ("pp:blur"))
        await admin.cb_post(c, CQ("pp:go"))
        assert posted == [("The <Matrix>", [-1001], "detailed", "off")] and "Done" in shown[-1][0] and OWNER not in admin._compose
        await admin.cb_post(c, CQ("pp:t:-1001")); assert toasts[-1][0].startswith("This screen expired")
        await admin.cb_post(c, CQ("pp:all", USER)); assert toasts[-1] == ("Admins only.", True)
        db.channels.clear(); await admin.cmd_post(c, Msg(command=["post", "movie", "603"]))
        assert "Add a channel first" in c.sent[-1][1]
        m = Msg(command=["post", "nonsense"]); await admin.cmd_post(c, m); assert m.replies and "Usage" in m.replies[-1]

async def t_admin_management_and_the_input_router():
    db, shown, toasts = setup(); c = Client()
    synced = []
    async def sync(client, uid, force=False): synced.append(uid)
    m = Msg(command=["addadmin", "77"], uid=OWNER)
    with mock.patch.object(commands, "sync_user", sync):
        await admin.cmd_addadmin(c, m); assert 77 in settings.get("admins") and synced == [77] and "now an admin" in m.replies[-1]
        await admin.cmd_addadmin(c, m); assert settings.get("admins").count(77) == 1, "adding twice does not duplicate"
        m = Msg(command=["rmadmin", "77"]); await admin.cmd_rmadmin(c, m); assert 77 not in settings.get("admins") and "no longer" in m.replies[-1]
        m = Msg(command=["addadmin", "abc"]); await admin.cmd_addadmin(c, m); assert "Usage" in m.replies[-1]
    calls = []
    async def hook(client, m, uid, st): calls.append(uid)
    admin.input_handlers["x"] = hook
    states[ADMIN] = {"action": "x"}
    try:
        await admin.on_input(c, Msg("hello", ADMIN)); assert False
    except P.StopPropagation:
        assert calls == [ADMIN], "an admin's reply goes to their prompt (and stops the search handler)"
    states[USER] = {"action": "x"}
    await admin.on_input(c, Msg("hello", USER))
    assert calls == [ADMIN] and USER not in states, "a non-admin can never answer an admin prompt"

async def t_every_panel_renders_with_balanced_quotes_and_escaped_values():
    db, shown, toasts = setup(); c = Client()
    db.channels = {-1001: {"_id": -1001, "title": "<b>Bad</b>", "auto": True, "fails": 2, "last_error": "boom <x>", "premium": "https://t.me/p?x=<y>"}}
    settings._d.update(sub_info='{"a":1}', log_channel="@notices")
    ev = types.SimpleNamespace(chat=types.SimpleNamespace(id=1))
    for coro in (admin.panel(c, ev), admin.settings_panel(c, ev), admin.channels_panel(c, ev), admin.channel_detail(c, ev, -1001)):
        await coro
        text = shown[-1][0]
        assert len(re.findall(r"<blockquote[^>]*>", text)) == text.count("</blockquote>")
        assert "<b>Bad</b>" not in text and "boom <x>" not in text and "<y>" not in text, "unescaped value"
        assert all(len(d.encode()) <= 64 for d in rows(shown[-1][1])), "callback data is limited to 64 bytes"


async def main():
    for name, fn in list(globals().items()):
        if name.startswith("t_"):
            r = fn()
            if asyncio.iscoroutine(r):
                await r
            print("ok ", name[2:].replace("_", " "))
    print("all admin checks passed")

asyncio.run(main())
