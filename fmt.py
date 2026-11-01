"""Small text helpers. Everything the bot sends is HTML, so anything that came from outside goes through esc()."""
import html
import re


def esc(v) -> str:
    return html.escape(str(v if v is not None else ""), quote=False)


def trunc(s: str, n: int) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    cut = s[: max(n - 1, 1)]
    return (cut.rsplit(" ", 1)[0] if " " in cut[n // 2:] else cut).rstrip(" ,.;:-") + "…"


def quote(body: str, expandable: bool = False) -> str:
    return f"<blockquote{' expandable' if expandable else ''}>{body}</blockquote>"      # the closing tag never has attributes


def hashtags(names, limit: int = 6) -> str:
    tags = []
    for g in list(names)[:limit]:
        t = re.sub(r"\W+", "_", str(g), flags=re.U).strip("_")
        if t:
            tags.append("#" + t)
    return " ".join(tags)


def plain(html_text: str) -> str:
    return re.sub(r"</?[a-z][^>]*>", "", html_text or "")


def minutes(m) -> str:
    try:
        m = int(m)
    except (TypeError, ValueError):
        return ""
    if m <= 0:
        return ""
    return f"{m // 60}h {m % 60}m" if m >= 60 else f"{m}m"
