"""TMDB JSON -> the few fields the bot needs. Pure functions; every field may be missing."""
from dataclasses import dataclass, field

from fmt import minutes


@dataclass
class Media:
    kind: str                                   # "movie" | "tv"
    tmdb_id: int
    title: str
    year: str = ""
    overview: str = ""
    tagline: str = ""
    genres: list = field(default_factory=list)
    rating: float = 0.0
    votes: int = 0
    runtime: str = ""                           # "2h 5m" (movie) or "~45m" (tv episode length)
    status: str = ""
    released: str = ""
    poster_path: str = ""
    backdrop_path: str = ""
    seasons: list = field(default_factory=list)     # [(season_number, episode_count, name)], without "Specials" (season 0)
    n_seasons: int = 0
    n_episodes: int = 0
    quality: str = ""                           # filled from the vidsrc feed when known

    @property
    def icon(self) -> str:
        return "🎬" if self.kind == "movie" else "📺"


def _int(v, default=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def from_tmdb(kind: str, d: dict) -> Media:
    d = d or {}
    movie = kind == "movie"
    title = (d.get("title") if movie else d.get("name")) or d.get("original_title") or d.get("original_name") or "Untitled"
    released = str((d.get("release_date") if movie else d.get("first_air_date")) or "")
    run = d.get("runtime") if movie else next((m for m in (d.get("episode_run_time") or []) if m), None)
    seasons = [(_int(s.get("season_number")), _int(s.get("episode_count")), str(s.get("name") or ""))
               for s in (d.get("seasons") or []) if isinstance(s, dict) and _int(s.get("season_number")) > 0]
    seasons.sort()
    return Media(
        kind=kind, tmdb_id=_int(d.get("id")), title=str(title), year=released[:4], overview=str(d.get("overview") or "").strip(),
        tagline=str(d.get("tagline") or "").strip(), genres=[g["name"] for g in d.get("genres", []) if isinstance(g, dict) and g.get("name")],
        rating=round(float(d.get("vote_average") or 0), 1), votes=_int(d.get("vote_count")),
        runtime=(("" if movie else "~") + minutes(run)) if minutes(run) else "", status=str(d.get("status") or ""), released=released,
        poster_path=str(d.get("poster_path") or ""), backdrop_path=str(d.get("backdrop_path") or ""),
        seasons=seasons, n_seasons=_int(d.get("number_of_seasons")) or len(seasons), n_episodes=_int(d.get("number_of_episodes")),
    )


@dataclass(frozen=True)
class Hit:
    kind: str
    tmdb_id: int
    title: str
    year: str
    rating: float
    popularity: float


def hits_from_search(kind: str, payload: dict) -> list[Hit]:
    out = []
    for r in (payload or {}).get("results", []) or []:
        if not isinstance(r, dict) or not _int(r.get("id")):
            continue
        title = (r.get("title") if kind == "movie" else r.get("name")) or r.get("original_title") or r.get("original_name")
        if not title:
            continue
        date = str((r.get("release_date") if kind == "movie" else r.get("first_air_date")) or "")
        out.append(Hit(kind, _int(r["id"]), str(title), date[:4], round(float(r.get("vote_average") or 0), 1), float(r.get("popularity") or 0)))
    return out


def merge_hits(movies: list[Hit], shows: list[Hit], limit: int = 10) -> list[Hit]:
    """Movies and series together, most popular first."""
    return sorted(movies + shows, key=lambda h: h.popularity, reverse=True)[:limit]


def episode_numbers(season_payload: dict) -> list[tuple[int, str]]:
    """[(episode_number, name)] from a TMDB season-details response, in order."""
    eps = []
    for e in (season_payload or {}).get("episodes", []) or []:
        n = _int(e.get("episode_number")) if isinstance(e, dict) else 0
        if n > 0:
            eps.append((n, str(e.get("name") or "")))
    return sorted(eps)
