"""Channel-post captions. Pure functions over a Media: easy to test, nothing here talks to Telegram.

    detailed  everything TMDB gives us: rating, runtime / seasons, release date, genres, status, quality, #hashtags, story
    classic   title, genres, story
    minimal   one line

render() fits the story into the room left under Telegram's 1024-character caption limit and never emits broken HTML.
"""
from fmt import esc, hashtags, quote, trunc
from media import Media

DEFAULT = "detailed"
LIMIT = 1000


def _title(m: Media) -> str:
    return f"{m.icon} <b>{esc(m.title)}</b>" + (f" ({esc(m.year)})" if m.year else "")


def _rating(m: Media) -> str:
    if not m.rating:
        return ""
    return f"⭐ <b>Rating:</b> {m.rating}/10" + (f" ({m.votes:,} votes)" if m.votes else "")


def t_detailed(m: Media):
    head = [_title(m)]
    if m.tagline:
        head.append(f"<i>{esc(trunc(m.tagline, 120))}</i>")
    rows = [r for r in (
        _rating(m),
        (f"⏱ <b>Runtime:</b> {esc(m.runtime)}" if m.kind == "movie" and m.runtime else ""),
        (f"📺 <b>{m.n_seasons} season{'s' if m.n_seasons != 1 else ''}</b>"
         + (f" · {m.n_episodes} episodes" if m.n_episodes else "") + (f" · {esc(m.runtime)}" if m.runtime else "")
         if m.kind == "tv" and m.n_seasons else ""),
        (f"📅 <b>Released:</b> {esc(m.released)}" if m.released else ""),
        (f"🎭 <b>Genres:</b> {esc(', '.join(m.genres))}" if m.genres else ""),
        (f"📌 <b>Status:</b> {esc(m.status)}" if m.status else ""),
        (f"🎞 <b>Quality:</b> {esc(m.quality)}" if m.quality else ""),
    ) if r]
    text = "\n".join(head) + (("\n\n" + quote("\n".join(rows))) if rows else "")
    return text, hashtags(m.genres + ["Movie" if m.kind == "movie" else "TVShow"]), True


def t_classic(m: Media):
    lines = [_title(m)]
    if m.genres:
        lines.append("🎭 " + esc(", ".join(m.genres)))
    if m.rating:
        lines.append(f"⭐ {m.rating}/10")
    return "\n".join(lines), "", True


def t_minimal(m: Media):
    bits = [f"<b>{esc(m.title)}</b>" + (f" ({esc(m.year)})" if m.year else ""), f"⭐ {m.rating}" if m.rating else "", m.icon]
    return " · ".join(b for b in bits if b), "", False


TEMPLATES = {"detailed": ("Detailed", t_detailed), "classic": ("Classic", t_classic), "minimal": ("Minimal", t_minimal)}


def names() -> list[str]:
    return list(TEMPLATES)


def label(name: str) -> str:
    return TEMPLATES.get(name, TEMPLATES[DEFAULT])[0]


def next_name(current: str) -> str:
    order = names()
    return order[(order.index(current) + 1) % len(order)] if current in order else order[0]


def _clip(text: str, limit: int) -> str:
    lines = text.split("\n")
    while len("\n".join(lines)) > limit and len(lines) > 1:
        lines.pop()
    return "\n".join(lines)[:limit]


def render(name: str, m: Media, limit: int = LIMIT) -> str:
    head, tail, uses_story = TEMPLATES.get(name, TEMPLATES[DEFAULT])[1](m)
    base = head + (f"\n\n{tail}" if tail else "")
    story = m.overview.strip()
    if not (uses_story and story):
        return _clip(base, limit)
    cut = limit - len(base) - len("\n\n<blockquote expandable></blockquote>")
    while cut > 40:
        text = head + "\n\n" + quote(esc(trunc(story, cut)), expandable=True) + (f"\n\n{tail}" if tail else "")
        if len(text) <= limit:
            return text
        cut -= 25
    return _clip(base, limit)
