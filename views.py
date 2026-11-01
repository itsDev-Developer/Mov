"""What the bot shows: details, seasons, episodes, search results, the latest feed. Each view returns plain data
(text, markup, image) — the Telegram handlers in plugins/ just display it, and tests can call these directly."""
import asyncio
import hashlib
import time

import captions
import publisher
import texts
import vidsrc
from buttons import B
from database import settings
from fmt import esc, trunc
from media import episode_numbers, from_tmdb, hits_from_search, merge_hits
from tmdb import TmdbError, tmdb
from utils import grid, nav_row

PER_PAGE = 10
EP_PER_PAGE = 30
_queries: dict[str, tuple[str, float]] = {}          # callback token -> (search text, time): callback data is limited to 64 bytes


def _markup(rows):
    from pyrogram.types import InlineKeyboardMarkup
    return InlineKeyboardMarkup(rows)


def menu_row() -> list:
    return [B("🏠 Menu", callback_data="m:menu")]


def kind_code(kind: str) -> str:
    return "m" if kind == "movie" else "t"


def kind_of(code: str) -> str:
    return "movie" if code == "m" else "tv"


# ── a title ──────────────────────────────────────────────────────────────────
async def title_view(kind: str, tmdb_id: int):
    m = from_tmdb(kind, await tmdb.details(kind, tmdb_id))
    m.tmdb_id = m.tmdb_id or tmdb_id
    rows = []
    if kind == "movie":
        rows.append([publisher.watch_button(vidsrc.embed_movie(m.tmdb_id))])
    else:
        row = [B("📺 Seasons", callback_data=f"ss:{m.tmdb_id}")] if m.seasons else []
        row.append(publisher.watch_button(vidsrc.embed_tv(m.tmdb_id, 1, 1), "▶️ S1E1"))
        rows.append(row)
    rows += publisher.extra_row(None)
    rows.append(menu_row())
    return captions.render("detailed", m), _markup(rows), publisher.pick_image(m)


# ── series: seasons and episodes ─────────────────────────────────────────────
async def seasons_view(tv_id: int):
    m = from_tmdb("tv", await tmdb.details("tv", tv_id))
    btns = [B(f"S{n} ({c})" if c else f"S{n}", callback_data=f"se:{tv_id}:{n}:1") for n, c, _ in m.seasons]
    text = f"📺 <b>{esc(m.title)}</b>\n<blockquote>{m.n_seasons} season{'s' if m.n_seasons != 1 else ''}"
    text += (f" · {m.n_episodes} episodes" if m.n_episodes else "") + "</blockquote>\n<i>Pick a season.</i>"
    return text, _markup(grid(btns, 4) + [[B("⬅️ Back", callback_data=f"d:t:{tv_id}")], menu_row()]), None


async def episodes_view(tv_id: int, season: int, page: int = 1):
    show_d, season_d = await asyncio.gather(tmdb.details("tv", tv_id), tmdb.season(tv_id, season))
    title = from_tmdb("tv", show_d).title
    eps = episode_numbers(season_d)
    pages = max(1, -(-len(eps) // EP_PER_PAGE))
    page = max(1, min(page, pages))
    chunk = eps[(page - 1) * EP_PER_PAGE: page * EP_PER_PAGE]
    rows = grid([B(str(n), callback_data=f"ep:{tv_id}:{season}:{n}") for n, _ in chunk], 6)
    if pages > 1:
        rows.append(nav_row(f"se:{tv_id}:{season}", page, pages))
    rows += [[B("⬅️ Seasons", callback_data=f"ss:{tv_id}")], menu_row()]
    text = f"📺 <b>{esc(title)}</b> — Season {season}\n<blockquote>{len(eps)} episode{'s' if len(eps) != 1 else ''}</blockquote>\n<i>Pick an episode.</i>"
    return (text if eps else text.replace("<i>Pick an episode.</i>", "<i>No episodes listed yet.</i>")), _markup(rows), None


async def episode_view(tv_id: int, season: int, episode: int):
    show_d, season_d = await asyncio.gather(tmdb.details("tv", tv_id), tmdb.season(tv_id, season))
    m = from_tmdb("tv", show_d)
    by_num = {int(e["episode_number"]): e for e in (season_d.get("episodes") or []) if isinstance(e, dict) and str(e.get("episode_number", "")).isdigit()}
    ep = by_num.get(episode)
    if ep is None:
        raise TmdbError("not_found", status=404)
    bits = [f"<b>{esc(ep.get('name') or f'Episode {episode}')}</b>"]
    if ep.get("air_date"):
        bits.append(f"📅 {esc(ep['air_date'])}")
    if ep.get("runtime"):
        bits.append(f"⏱ {int(ep['runtime'])}m")
    if ep.get("vote_average"):
        bits.append(f"⭐ {round(float(ep['vote_average']), 1)}/10")
    text = f"📺 <b>{esc(m.title)}</b> — S{season}E{episode}\n<blockquote>{chr(10).join(bits)}</blockquote>"
    if str(ep.get("overview") or "").strip():
        text += f"\n<blockquote expandable>{esc(trunc(ep['overview'], 700))}</blockquote>"
    nums = sorted(by_num)
    nav = []
    if episode - 1 in by_num:
        nav.append(B("⏮ Prev", callback_data=f"ep:{tv_id}:{season}:{episode - 1}"))
    if episode + 1 in by_num:
        nav.append(B("Next ⏭", callback_data=f"ep:{tv_id}:{season}:{episode + 1}"))
    rows = [[publisher.watch_button(vidsrc.embed_tv(tv_id, season, episode), f"▶️ Watch S{season}E{episode}")]]
    rows += [nav] if nav else []
    rows += publisher.extra_row(None)
    rows += [[B("⬅️ Episodes", callback_data=f"se:{tv_id}:{season}:{max(1, -(-(nums.index(episode) + 1) // EP_PER_PAGE))}"), B("📚 Seasons", callback_data=f"ss:{tv_id}")], menu_row()]
    image = tmdb.image_url(ep.get("still_path"), "still") or tmdb.image_url(m.poster_path, "poster")
    return text, _markup(rows), image


# ── lists: search results and the latest feed ────────────────────────────────
def _line(i: int, h) -> str:
    return f"{i}. {'🎬' if h.kind == 'movie' else '📺'} <b>{esc(h.title)}</b>" + (f" ({esc(h.year)})" if h.year else "") + (f" ⭐{h.rating}" if h.rating else "")


def _list(head: str, hits, nav_rows: list):
    lines = "\n".join(_line(i, h) for i, h in enumerate(hits, 1))
    btns = [B(str(i), callback_data=f"d:{kind_code(h.kind)}:{h.tmdb_id}") for i, h in enumerate(hits, 1)]
    return f"{head}\n<blockquote>{lines}</blockquote>\n<i>Tap a number to open it.</i>", _markup(grid(btns, 5) + nav_rows + [menu_row()])


def query_token(query: str) -> str:
    tok = hashlib.sha1(query.lower().encode()).hexdigest()[:10]
    if len(_queries) > 500:
        for k in sorted(_queries, key=lambda k: _queries[k][1])[:200]:
            _queries.pop(k, None)
    _queries[tok] = (query, time.time())
    return tok


def query_of(token: str) -> str | None:
    hit = _queries.get(token)
    return hit[0] if hit else None


async def search_view(query: str, page: int = 1):
    q = query.strip()[:120]
    results = await asyncio.gather(tmdb.search("movie", q, page), tmdb.search("tv", q, page), return_exceptions=True)
    if all(isinstance(r, Exception) for r in results):
        raise results[0]
    movies, shows = [r if isinstance(r, dict) else {} for r in results]
    hits = merge_hits(hits_from_search("movie", movies), hits_from_search("tv", shows), PER_PAGE)
    if not hits:
        return texts.NO_RESULTS, _markup([menu_row()])
    pages = max(int(movies.get("total_pages") or 1), int(shows.get("total_pages") or 1), 1)
    nav = [nav_row(f"sr:{query_token(q)}", page, min(pages, 500))] if pages > 1 else []
    return _list(f"🔍 <b>{esc(q)}</b>", hits, nav)


async def latest_view(kind: str, page: int = 1):
    from media import Hit
    from vidsrc import feed
    items = [i for i in await feed.latest(kind, settings.get(f"feed_{kind}_type"), page) if i.tmdb_id][:PER_PAGE]
    sem = asyncio.Semaphore(4)

    async def one(i):
        async with sem:
            try:
                m = from_tmdb(kind, await tmdb.details(kind, i.tmdb_id))
            except TmdbError:
                return None
            return Hit(kind, m.tmdb_id or i.tmdb_id, m.title, m.year, m.rating, 0.0)

    hits = [h for h in await asyncio.gather(*[one(i) for i in items]) if h]
    if not hits:
        return texts.NOTHING_NEW, _markup([menu_row()])
    nav = [B("◀️", callback_data=f"lt:{kind_code(kind)}:{page - 1}")] if page > 1 else []
    nav.append(B(f"{page}", callback_data="noop"))
    nav.append(B("▶️", callback_data=f"lt:{kind_code(kind)}:{page + 1}"))
    return _list(f"🆕 <b>Latest {'movies' if kind == 'movie' else 'series'}</b>", hits, [nav])
