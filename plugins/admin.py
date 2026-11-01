"""⚙️ /admin — everything an owner/admin changes without touching files or restarting.

  🔑 TMDB key      (owner) checked against TMDB before it is saved; your message with the key is deleted
  🌐 Settings      language · feed types · subtitles JSON · autopost interval/limits · design · blur · links · toggles
  📣 Channels      add/remove channels; per channel: autopost on/off · design · blur · ⭐ Premium and 💾 Back-up links
  📮 Post a title  /post movie|tv <tmdb id>: choose ONE channel, several, or ALL (design and blur can be overridden)
  ▶️ Run now       check the feed immediately
"""
import json
import logging
import re
import time

from pyrogram import Client, StopPropagation, filters
from pyrogram.types import InlineKeyboardMarkup as M

import captions
import commands
import logging_setup
import poster
import publisher
import runtime
import texts
import vidsrc
from autopost import autoposter, summary_text
from buttons import B, DANGER, PRIMARY, SUCCESS
from database import db, settings
from fmt import esc, plain
from media import from_tmdb
from tmdb import TmdbError, tmdb
from ui import done, guarded, show
from utils import grid, input_handlers, is_admin, is_owner, peer, set_state, states

log = logging.getLogger("admin")
COMPOSE_TTL = 1800
_compose: dict[int, dict] = {}                         # admin id -> the post being prepared
_BLUR = {"default": "channel default", "off": "off", "spoiler": "spoiler (tap to reveal)", "blur": "blurred"}


@runtime.on_housekeeping
def _prune_compose():
    now = time.time()
    for uid in [u for u, c in _compose.items() if now - c["_at"] > COMPOSE_TTL]:
        _compose.pop(uid, None)


async def _admin_f(_, __, m):
    return bool(m.from_user) and is_admin(m.from_user.id)

async def _owner_f(_, __, m):
    return bool(m.from_user) and is_owner(m.from_user.id)

async def _state_f(_, __, m):
    return bool(m.from_user) and m.from_user.id in states

admin_cmd, owner_cmd, has_state = filters.create(_admin_f), filters.create(_owner_f), filters.create(_state_f)


def _cycle(values: list, current):
    return values[(values.index(current) + 1) % len(values)] if current in values else values[0]


def _mask(key: str) -> str:
    return "not set" if not key else key[:3] + "…" + key[-4:]


def _val(v) -> str:
    return f"<code>{esc(v)}</code>" if v not in ("", None) else "<i>not set</i>"


# ── value parsers for the text settings (ValueError carries a message that is safe to show) ──
def _int_between(lo: int, hi: int):
    def parse(t: str) -> int:
        if not t.strip().isdigit() or not lo <= int(t) <= hi:
            raise ValueError(f"Send a whole number from {lo} to {hi}.")
        return int(t)
    return parse

def _lang(t: str) -> str:
    if not re.fullmatch(r"[a-z]{2}(-[A-Z]{2})?", t.strip()):
        raise ValueError("Send a language code like en-US, de-DE or fr-FR.")
    return t.strip()

def _base(t: str) -> str:
    if not re.fullmatch(r"https?://[A-Za-z0-9.-]+(:\d+)?/?", t.strip()):
        raise ValueError("Send the site address only, like https://vidsrc.mov")
    return t.strip().rstrip("/")

def _feed_type(t: str) -> str:
    if not re.fullmatch(r"[a-z0-9_-]{1,32}", t.strip()):
        raise ValueError("Use only a-z, 0-9, - and _ (for example: latest).")
    return t.strip()

def _sub(t: str) -> str:
    if t.strip().lower() in ("off", "none", "-"):
        return ""
    try:
        compact = json.dumps(json.loads(t), separators=(",", ":"), ensure_ascii=False)
    except ValueError:
        raise ValueError("That isn't valid JSON. Send it exactly as the provider documents it, or “off”.")
    if len(compact) > vidsrc.MAX_SUB:
        raise ValueError("That JSON is too long for a link.")
    return compact

def _link(t: str) -> str:
    return "" if t.strip().lower() in ("off", "none", "-") else publisher.normalize_link(t)

def _chat(t: str) -> str:
    return "" if t.strip().lower() in ("off", "none", "-") else str(peer(t))


# key -> (label, prompt, parser, owner only)
FIELDS = {
    "tmdb_language": ("🗣 Language", "Language for titles and stories, e.g. <code>en-US</code>, <code>de-DE</code>.", _lang, False),
    "vidsrc_base": ("📡 Player site", "The site that provides the feed and embed links, e.g. <code>https://vidsrc.mov</code>.", _base, True),
    "feed_movie_type": ("🎬 Movie feed type", "The <code>{type}</code> in /vapi/movie/{type}/{page}. The reference shows <code>latest</code>.", _feed_type, False),
    "feed_tv_type": ("📺 Series feed type", "The <code>{type}</code> in /vapi/tv/{type}/{page}. The reference shows <code>latest</code>.", _feed_type, False),
    "sub_info": ("💬 Subtitles JSON", "The JSON for the player's <code>?sub.info=</code> parameter, exactly as the provider documents it. “off” removes it.", _sub, False),
    "autopost_interval": ("⏱ Check every (min)", "How often the feed is checked, in minutes (1-1440).", _int_between(1, 1440), False),
    "autopost_max": ("📤 Max posts per check", "Titles posted per check; the rest wait for the next one (1-50).", _int_between(1, 50), False),
    "autopost_backfill": ("🧱 First-run backfill", "On the very first check, post this many of the newest titles instead of just recording them (0-20).", _int_between(0, 20), False),
    "premium_link": ("⭐ Premium link", "Where people can get premium: <code>https://…</code>, <code>t.me/…</code> or <code>@name</code>. “off” removes it.", _link, False),
    "backup_link": ("💾 Back-up link", "Your back-up channel or bot: <code>https://…</code>, <code>t.me/…</code> or <code>@name</code>. “off” removes it.", _link, False),
    "log_channel": ("📮 Admin notices", "A chat for admin notices (like a paused channel), or “off” to DM the owners.", _chat, True),
}
TOGGLES = {"adult": "🔞 Adult in search", "require_poster": "🖼 Require an image", "use_webapp": "📱 Open in Mini App"}
CYCLES = {"post_template": ("🎨 Design", lambda v: captions.next_name(v)), "blur_mode": ("🖼 Blur", poster.next_mode),
          "poster_source": ("🖼 Image from", lambda v: "backdrop" if v == "poster" else "poster")}


# ═════════════════════════ main panel ═════════════════════════════════════
async def panel(client, ev):
    chans = await publisher.all_channels()
    s = autoposter.stats
    last = "never run" if not s["last_run"] else f"{s['last_result']} ({int((time.time() - s['last_run']) / 60)} min ago)"
    text = (
        "⚙️ <b>Admin</b>\n<blockquote>"
        f"🔑 TMDB key: {'✅ ' + esc(_mask(tmdb.key())) if tmdb.configured() else '❌ not set'}\n"
        f"📡 Feed: <code>{esc(vidsrc.base())}</code> · movie “{esc(settings.get('feed_movie_type'))}” · tv “{esc(settings.get('feed_tv_type'))}”\n"
        f"🤖 Autopost: <b>{'ON' if settings.get('autopost_enabled') else 'OFF'}</b> · every {settings.get('autopost_interval')} min · max {settings.get('autopost_max')} per check\n"
        f"📣 Channels: {len(chans)} ({sum(1 for c in chans if c.get('auto', True))} with autopost)\n"
        f"👥 Users: {await db.user_count()} · posts: {(await db.counters()).get('posts', 0)}\n"
        f"🕓 Last check: {esc(last)}</blockquote>"
    )
    rows = [[B("🔑 TMDB key", callback_data="a:key"), B("🌐 Settings", callback_data="a:set")],
            [B("📣 Channels", callback_data="ch:home", style=PRIMARY), B("📮 Post a title", callback_data="a:post")],
            [B("▶️ Run now", callback_data="a:run", style=SUCCESS),
             B("⏸ Pause autopost" if settings.get("autopost_enabled") else "▶️ Resume autopost", callback_data="a:ap")],
            [B("🏠 Menu", callback_data="m:menu")]]
    await show(client, ev, text, M(rows))


async def settings_panel(client, ev):
    g = settings.get
    text = (
        "🌐 <b>Settings</b>\n<blockquote>"
        f"🗣 Language: {_val(g('tmdb_language'))}\n📡 Player: {_val(vidsrc.base())} · feeds: {_val(g('feed_movie_type'))} / {_val(g('feed_tv_type'))}\n"
        f"💬 Subtitles: {'set' if g('sub_info') else '<i>off</i>'}\n"
        f"⏱ Every {g('autopost_interval')} min · max {g('autopost_max')} per check · backfill {g('autopost_backfill')}\n"
        f"🎨 Design: <b>{esc(captions.label(g('post_template')))}</b> · 🖼 Blur: <b>{esc(_BLUR[g('blur_mode')] if g('blur_mode') in _BLUR else g('blur_mode'))}</b> · image from the <b>{esc(g('poster_source'))}</b>\n"
        f"⭐ Premium: {_val(g('premium_link'))}\n💾 Back-up: {_val(g('backup_link'))}\n📮 Notices: {_val(g('log_channel'))}</blockquote>"
    )
    toggles = [B(f"{label}: {'ON' if g(k) else 'off'}", callback_data=f"a:t:{k}", style=SUCCESS if g(k) else None) for k, label in TOGGLES.items()]
    cycles = [B(f"{label}: {g(k)}", callback_data=f"a:c:{k}") for k, (label, _) in CYCLES.items()]
    fields = [B(label, callback_data=f"a:e:{k}") for k, (label, _, _, _) in FIELDS.items()]
    await show(client, ev, text, M(grid(toggles, 1) + grid(cycles, 2) + grid(fields, 2) + [[B("⬅️ Admin", callback_data="a:home")]]))


@Client.on_message(filters.command("admin") & filters.private & admin_cmd)
@guarded
async def cmd_admin(client, m):
    await panel(client, m)


@Client.on_callback_query(filters.regex(r"^a:"))
@guarded
async def cb_admin(client, cq):
    uid = cq.from_user.id
    if not is_admin(uid):
        return await done(cq, "Admins only.", alert=True)
    p = cq.data.split(":")
    cmd = p[1]
    if cmd == "home":
        states.pop(uid, None)
        await panel(client, cq)
    elif cmd == "set":
        states.pop(uid, None)
        await settings_panel(client, cq)
    elif cmd == "t" and p[2] in TOGGLES:
        await settings.set(p[2], not settings.get(p[2]))
        await settings_panel(client, cq)
    elif cmd == "c" and p[2] in CYCLES:
        await settings.set(p[2], CYCLES[p[2]][1](settings.get(p[2])))
        await settings_panel(client, cq)
    elif cmd == "e" and p[2] in FIELDS:
        label, prompt, _, owner_only = FIELDS[p[2]]
        if owner_only and not is_owner(uid):
            return await done(cq, "Owners only.", alert=True)
        set_state(uid, "field", back="a:set", field=p[2])
        cur = settings.get(p[2])
        await show(client, cq, f"✏️ <b>{label}</b>\n<blockquote>{prompt}</blockquote>\nCurrent: {_val(cur if p[2] != 'sub_info' else ('set' if cur else ''))}\n\n<i>Send the new value. /cancel to abort.</i>",
                   M([[B("✖️ Cancel", callback_data="a:set", style=DANGER)]]))
    elif cmd == "key":
        if not is_owner(uid):
            return await done(cq, "Owners only.", alert=True)
        set_state(uid, "tmdb_key", back="a:home")
        await show(client, cq, "🔑 <b>TMDB key</b>\n<blockquote>Send your TMDB credential: the <b>API Key</b> (32 characters) or the "
                   "<b>API Read Access Token</b> (long, starts with <code>eyJ</code>) from your TMDB settings page.\n"
                   "I check it with TMDB before saving, and delete your message.</blockquote>\n<i>Send “off” to remove the key. /cancel to abort.</i>",
                   M([[B("✖️ Cancel", callback_data="a:home", style=DANGER)]]))
    elif cmd == "ap":
        await settings.set("autopost_enabled", not settings.get("autopost_enabled"))
        await panel(client, cq)
    elif cmd == "run":
        await done(cq, "Checking the feed…")
        res = await autoposter.run_once(client, force=True)
        await client.send_message(cq.message.chat.id, summary_text(res))
        return await panel(client, cq)
    elif cmd == "post":
        set_state(uid, "post_ref", back="a:home")
        await show(client, cq, "📮 <b>Post a title</b>\n<blockquote>Send <code>movie 603</code> or <code>tv 1399</code> (the TMDB id).</blockquote>\n<i>/cancel to abort.</i>",
                   M([[B("✖️ Cancel", callback_data="a:home", style=DANGER)]]))
    await done(cq)


# ═════════════════════════ typed input (the router runs before the search handler) ═════════
@Client.on_message(filters.private & has_state & ~filters.regex(r"^/"), group=-5)
@guarded
async def on_input(client, m):
    uid = m.from_user.id
    st = states.get(uid)
    if not st or not is_admin(uid):
        states.pop(uid, None)
        return
    hook = input_handlers.get(st["action"])
    if hook:
        await hook(client, m, uid, st)
    raise StopPropagation


async def _in_field(client, m, uid, st):
    key = st["field"]
    label, _, parse, _ = FIELDS[key]
    try:
        value = parse(m.text or "")
    except ValueError as e:
        return await client.send_message(m.chat.id, f"❌ {esc(e)}\nTry again or /cancel.")
    states.pop(uid, None)
    await settings.set(key, value)
    await client.send_message(m.chat.id, f"✅ <b>{label}</b> saved." if value != "" else f"✅ <b>{label}</b> cleared.",
                              reply_markup=M([[B("🌐 Settings", callback_data="a:set")]]))


async def _in_key(client, m, uid, st):
    text = (m.text or "").strip()
    try:
        await m.delete()                                   # don't leave the secret sitting in the chat
    except Exception:
        pass
    if text.lower() in ("off", "none", "-"):
        states.pop(uid, None)
        await settings.set("tmdb_key", "")
        return await client.send_message(m.chat.id, "✅ TMDB key removed.", reply_markup=M([[B("⚙️ Admin", callback_data="a:home")]]))
    if len(text) < 20 or len(text) > 800 or any(c.isspace() for c in text):
        return await client.send_message(m.chat.id, "❌ That doesn't look like a TMDB key. Try again or /cancel.")
    try:
        await tmdb.check_key(text)
    except TmdbError as e:
        return await client.send_message(m.chat.id, f"❌ {esc(e.user_text())}\nNothing was saved. Try again or /cancel.")
    states.pop(uid, None)
    await settings.set("tmdb_key", text)
    logging_setup.add_secret(text)
    await tmdb.warm()
    await client.send_message(m.chat.id, "✅ TMDB key accepted and saved.", reply_markup=M([[B("⚙️ Admin", callback_data="a:home")]]))


def _parse_ref(text: str) -> tuple[str, int]:
    bits = (text or "").split()
    if len(bits) != 2 or bits[0].lower() not in ("movie", "tv") or not bits[1].isdigit() or not 0 < int(bits[1]) < 10**9:
        raise ValueError("Send it like <code>movie 603</code> or <code>tv 1399</code>.")
    return bits[0].lower(), int(bits[1])


async def _in_post(client, m, uid, st):
    try:
        kind, tid = _parse_ref(m.text)
    except ValueError as e:
        return await client.send_message(m.chat.id, f"❌ {e}")
    states.pop(uid, None)
    await _start_post(client, m, kind, tid)


input_handlers.update({"field": _in_field, "tmdb_key": _in_key, "post_ref": _in_post})


# ═════════════════════════ channels ════════════════════════════════════════
def _on(ch: dict) -> str:
    return "✅ autopost on" if ch.get("auto", True) else ("⚠️ paused after errors" if ch.get("fails") else "⏸ autopost off")


async def channels_panel(client, ev):
    chans = await publisher.all_channels()
    lines = [f"{'✅' if c.get('auto', True) else '⏸'} <b>{esc(c.get('title') or c['_id'])}</b> · {esc(captions.label(publisher.effective_template(c)))}" for c in chans] or ["<i>No channels yet. Add one.</i>"]
    text = (f"📣 <b>Post channels</b>\n<blockquote>{chr(10).join(lines)}</blockquote>\n"
            "<i>Autopost sends every new title to the channels with autopost on. Use 📮 Post a title to choose channels yourself.</i>")
    rows = [[B(f"{'✅' if c.get('auto', True) else '⏸'} {(c.get('title') or str(c['_id']))[:28]}", callback_data=f"ch:v:{c['_id']}")] for c in chans]
    rows += [[B("➕ Add channel", callback_data="ch:add", style=SUCCESS)], [B("⬅️ Admin", callback_data="a:home")]]
    await show(client, ev, text, M(rows))


async def channel_detail(client, ev, chat: int):
    ch = await publisher.get(chat)
    if not ch:
        return await channels_panel(client, ev)
    text = (f"📣 <b>{esc(ch.get('title') or chat)}</b> <code>{chat}</code>\n<blockquote>{_on(ch)}\n"
            f"🎨 Design: <b>{esc(captions.label(publisher.effective_template(ch)))}</b>{' (default)' if ch.get('template', 'default') == 'default' else ''}\n"
            f"🖼 Blur: <b>{esc(_BLUR.get(ch.get('blur', 'default')))}</b>\n"
            f"⭐ Premium: {_val(ch.get('premium'))}{'' if ch.get('premium') else ' → default'}\n"
            f"💾 Back-up: {_val(ch.get('backup'))}{'' if ch.get('backup') else ' → default'}"
            + (f"\n⚠️ Last error: <code>{esc(ch['last_error'])}</code>" if ch.get("last_error") else "") + "</blockquote>")
    rows = [[B(f"🔔 Autopost: {'on' if ch.get('auto', True) else 'off'}", callback_data=f"ch:auto:{chat}")],
            [B("🎨 Design ▶", callback_data=f"ch:tpl:{chat}"), B("🖼 Blur ▶", callback_data=f"ch:blur:{chat}")],
            [B("⭐ Premium link", callback_data=f"ch:lnk:{chat}:p"), B("💾 Back-up link", callback_data=f"ch:lnk:{chat}:b")],
            [B("🧪 Test post", callback_data=f"ch:test:{chat}"), B("🗑 Remove", callback_data=f"ch:del:{chat}", style=DANGER)],
            [B("⬅️ Channels", callback_data="ch:home")]]
    await show(client, ev, text, M(rows))


@Client.on_message(filters.command("channels") & filters.private & admin_cmd)
@guarded
async def cmd_channels(client, m):
    await channels_panel(client, m)


def _link_prompt(kind: str, where: str) -> str:
    name = "⭐ Premium" if kind == "premium" else "💾 Back-up"
    return (f"{name} <b>link</b> for {where}\n<blockquote>Send <code>https://…</code>, <code>t.me/…</code> or <code>@username</code>. "
            "Send <code>off</code> to remove it.</blockquote>\n<i>/cancel to abort.</i>")


@Client.on_callback_query(filters.regex(r"^ch:"))
@guarded
async def cb_channels(client, cq):
    uid = cq.from_user.id
    if not is_admin(uid):
        return await done(cq, "Admins only.", alert=True)
    p = cq.data.split(":")
    cmd = p[1]
    chat = int(p[2]) if len(p) > 2 and p[2].lstrip("-").isdigit() else None
    if cmd == "home":
        states.pop(uid, None)
        await channels_panel(client, cq)
    elif cmd == "add":
        set_state(uid, "ch_add", back="ch:home")
        await show(client, cq, "➕ <b>Add a channel</b>\n<blockquote>Send the channel as <code>@username</code> or its numeric ID (<code>-100…</code>). "
                   "The bot must be an <b>admin</b> there and allowed to post.</blockquote>\n<i>/cancel to abort.</i>", M([[B("✖️ Cancel", callback_data="ch:home", style=DANGER)]]))
    elif cmd == "v" and chat is not None:
        await channel_detail(client, cq, chat)
    elif cmd == "auto" and chat is not None:
        ch = await publisher.get(chat)
        if ch:
            await publisher.update(chat, auto=not ch.get("auto", True), fails=0, last_error="")
        await channel_detail(client, cq, chat)
    elif cmd == "tpl" and chat is not None:
        ch = await publisher.get(chat) or {}
        await publisher.update(chat, template=_cycle(["default"] + captions.names(), ch.get("template", "default")))
        await channel_detail(client, cq, chat)
    elif cmd == "blur" and chat is not None:
        ch = await publisher.get(chat) or {}
        await publisher.update(chat, blur=_cycle(["default"] + list(poster.MODES), ch.get("blur", "default")))
        await channel_detail(client, cq, chat)
    elif cmd == "lnk" and len(p) == 4:
        kind = {"p": "premium", "b": "backup"}.get(p[3])
        set_state(uid, "ch_link", back=f"ch:v:{chat}", chat=chat, kind=kind)
        await show(client, cq, _link_prompt(kind, "this channel"), M([[B("✖️ Cancel", callback_data=f"ch:v:{chat}", style=DANGER)]]))
    elif cmd == "test" and chat is not None:
        return await _test_post(client, cq, chat)
    elif cmd == "del" and chat is not None:
        await show(client, cq, "🗑 <b>Remove this channel?</b>\n<blockquote>The bot stops posting there. Existing posts stay.</blockquote>",
                   M([[B("✅ Yes, remove", callback_data=f"ch:delgo:{chat}", style=DANGER), B("✖️ No", callback_data=f"ch:v:{chat}")]]))
    elif cmd == "delgo" and chat is not None:
        await publisher.remove(chat)
        await channels_panel(client, cq)
    await done(cq)


async def _test_post(client, cq, chat: int):
    ch = await publisher.get(chat)
    m = from_tmdb("movie", {"id": 603, "title": "Test title", "release_date": "2025-01-01", "overview": "This is a test post from the bot. Nothing is wrong.",
                            "genres": [{"name": "Action"}, {"name": "Drama"}], "vote_average": 8.1, "vote_count": 1000, "runtime": 120, "status": "Released"})
    rows = [[B(texts.BTN_WATCH, url="https://t.me/")]] + publisher.extra_row(ch)
    try:
        await publisher.send(client, chat, captions.render(publisher.effective_template(ch), m), M(rows), None)
        await publisher.record_result(chat, True)
        await done(cq, "Test post sent ✅", alert=True)
    except Exception as e:
        await publisher.record_result(chat, False, f"{type(e).__name__}: {e}")
        await done(cq, f"Couldn't post there: {type(e).__name__}. Is the bot an admin?", alert=True)


async def _in_ch_add(client, m, uid, st):
    try:
        chat = await client.get_chat(peer(m.text or ""))
    except Exception as e:
        return await client.send_message(m.chat.id, f"❌ Can't access that chat: {esc(e)}\nTry again or /cancel.")
    if str(chat.type).lower().endswith(("private", "bot")):
        return await client.send_message(m.chat.id, "❌ That is not a channel or group. Try again or /cancel.")
    try:                                                    # prove the bot can post there
        probe = await client.send_message(chat.id, "✅ Channel connected.")
        await probe.delete()
    except Exception as e:
        return await client.send_message(m.chat.id, f"❌ I can't post there: {esc(e)}\nMake the bot an <b>admin</b> with permission to post, then try again, or /cancel.")
    states.pop(uid, None)
    await publisher.add(chat.id, chat.title or str(chat.id), getattr(chat, "username", "") or "")
    await client.send_message(m.chat.id, f"✅ <b>{esc(chat.title)}</b> added. Autopost is on for it.", reply_markup=M([[B("📣 Channels", callback_data="ch:home")]]))


async def _in_ch_link(client, m, uid, st):
    try:
        link = _link(m.text or "")
    except ValueError as e:
        return await client.send_message(m.chat.id, f"❌ {esc(e)}")
    states.pop(uid, None)
    await publisher.update(st["chat"], **{st["kind"]: link})
    await client.send_message(m.chat.id, "✅ Saved." if link else "✅ Link removed.", reply_markup=M([[B("⬅️ Back", callback_data=st["back"])]]))


input_handlers.update({"ch_add": _in_ch_add, "ch_link": _in_ch_link})


# ═════════════════════════ manual post: choose channels ════════════════════
async def _start_post(client, ev, kind: str, tid: int):
    chat_id = ev.message.chat.id if hasattr(ev, "data") else ev.chat.id
    if not tmdb.configured():
        return await client.send_message(chat_id, texts.NOT_READY)
    uid = ev.from_user.id
    chans = await publisher.all_channels()
    if not chans:
        return await client.send_message(chat_id, "Add a channel first: /channels")
    m = from_tmdb(kind, await tmdb.details(kind, tid))
    m.tmdb_id = m.tmdb_id or tid
    _compose[uid] = {"m": m, "sel": set(), "tpl": None, "blur": None, "_at": time.time(), "chans": {c["_id"]: c for c in chans}}
    await _compose_view(client, ev, uid)


def _compose_text(e: dict) -> str:
    sel = e["sel"]
    first = e["chans"].get(next(iter(sel), next(iter(e["chans"]))))
    tpl = e["tpl"] or publisher.effective_template(first)
    chosen = ", ".join(esc(e["chans"][c].get("title") or c) for c in sel) or "<i>nothing selected</i>"
    return (f"📮 <b>Post a title</b>\n<blockquote>{e['m'].icon} <b>{esc(e['m'].title)}</b> {esc(e['m'].year)}</blockquote>\n<b>Post to:</b> {chosen}\n"
            f"🎨 Design: <b>{esc(captions.label(tpl))}</b> · 🖼 Blur: <b>{esc(_BLUR.get(e['blur'] or 'default'))}</b>\n\n"
            f"<b>Preview</b>\n<blockquote expandable>{esc(plain(captions.render(tpl, e['m'])))}</blockquote>")


def _compose_kb(e: dict) -> M:
    allsel = set(e["chans"]) == e["sel"]
    rows = grid([B(f"{'✅' if c in e['sel'] else '⬜'} {(ch.get('title') or str(c))[:22]}", callback_data=f"pp:t:{c}") for c, ch in e["chans"].items()], 2)
    rows += [[B(f"{'✅' if allsel else '⬜'} All channels", callback_data="pp:all")],
             [B("🎨 Design ▶", callback_data="pp:tpl"), B("🖼 Blur ▶", callback_data="pp:blur")],
             [B(f"🚀 Post to {len(e['sel'])} channel(s)" if e["sel"] else "🚀 Post", callback_data="pp:go", style=PRIMARY if e["sel"] else None),
              B("✖️ Cancel", callback_data="pp:x", style=DANGER)]]
    return M(rows)


async def _compose_view(client, ev, uid: int):
    e = _compose[uid]
    await show(client, ev, _compose_text(e), _compose_kb(e))


@Client.on_message(filters.command("post") & filters.private & admin_cmd)
@guarded
async def cmd_post(client, m):
    try:
        kind, tid = _parse_ref(" ".join(m.command[1:]))
    except ValueError as e:
        return await m.reply_text(f"Usage: {e}")
    await _start_post(client, m, kind, tid)


@Client.on_callback_query(filters.regex(r"^pp:"))
@guarded
async def cb_post(client, cq):
    uid = cq.from_user.id
    if not is_admin(uid):
        return await done(cq, "Admins only.", alert=True)
    e = _compose.get(uid)
    if not e:
        return await done(cq, "This screen expired. Start again.", alert=True)
    e["_at"] = time.time()
    p = cq.data.split(":")
    if p[1] == "x":
        _compose.pop(uid, None)
        await done(cq, "Cancelled.")
        try:
            return await cq.message.delete()
        except Exception:
            return
    if p[1] == "t":
        e["sel"] ^= {int(p[2])}
    elif p[1] == "all":
        e["sel"] = set() if set(e["chans"]) == e["sel"] else set(e["chans"])
    elif p[1] == "tpl":
        e["tpl"] = _cycle([None] + captions.names(), e["tpl"])
    elif p[1] == "blur":
        e["blur"] = _cycle([None] + list(poster.MODES), e["blur"])
    elif p[1] == "go":
        if not e["sel"]:
            return await done(cq, "Pick at least one channel (or All).", alert=True)
        await done(cq, "Posting…")
        chats = [c for c in e["chans"] if c in e["sel"]]
        res = await publisher.publish(client, e["m"], chats, e["tpl"], e["blur"])
        _compose.pop(uid, None)
        lines = [f"{'✅' if r == 'posted' else '❌'} {esc(e['chans'][c].get('title') or c)} — {esc(r)}" for c, r in res.items()]
        return await show(client, cq, "📮 <b>Done</b>\n<blockquote>" + "\n".join(lines) + "</blockquote>", M([[B("📣 Channels", callback_data="ch:home")]]))
    await _compose_view(client, cq, uid)
    await done(cq)


# ═════════════════════════ commands: run now, admins ═══════════════════════
@Client.on_message(filters.command("runnow") & filters.private & admin_cmd)
@guarded
async def cmd_runnow(client, m):
    note = await m.reply_text("🔄 Checking the feed…")
    await note.edit_text(summary_text(await autoposter.run_once(client, force=True)))


async def _admin_change(client, m, add: bool):
    arg = " ".join(m.command[1:]).strip()
    if not arg.isdigit() or int(arg) <= 0:
        return await m.reply_text(f"Usage: <code>/{'addadmin' if add else 'rmadmin'} &lt;user id&gt;</code>")
    uid = int(arg)
    admins = list(settings.get("admins") or [])
    if add and uid not in admins:
        admins.append(uid)
    elif not add:
        admins = [a for a in admins if a != uid]
    await settings.set("admins", admins)
    await commands.sync_user(client, uid, force=True)
    await m.reply_text(f"✅ <code>{uid}</code> is {'now an admin' if add else 'no longer an admin'}.")


@Client.on_message(filters.command("addadmin") & filters.private & owner_cmd)
@guarded
async def cmd_addadmin(client, m):
    await _admin_change(client, m, True)


@Client.on_message(filters.command("rmadmin") & filters.private & owner_cmd)
@guarded
async def cmd_rmadmin(client, m):
    await _admin_change(client, m, False)
