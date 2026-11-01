"""Environment configuration. Everything an admin may want to change later also lives in /admin (MongoDB)."""
import os

from dotenv import load_dotenv

load_dotenv()


def _req(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name} (see .env.example)")
    return value


def _int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return max(minimum, int(raw)) if raw else default
    except ValueError:
        raise SystemExit(f"{name} must be a whole number, got {raw!r}")


def _url(name: str, default: str) -> str:
    v = (os.getenv(name, "").strip() or default).rstrip("/")
    if not v.startswith(("https://", "http://")):
        raise SystemExit(f"{name} must start with https:// (got {v!r})")
    return v


# ── Telegram + storage ───────────────────────────────────────────────────────
API_ID = int(_req("API_ID"))
API_HASH = _req("API_HASH")
BOT_TOKEN = _req("BOT_TOKEN")
MONGO_URI = _req("MONGO_URI")
DB_NAME = os.getenv("DB_NAME", "vidbot").strip() or "vidbot"
OWNER_IDS = {int(x) for x in os.getenv("OWNER_IDS", "").replace(" ", "").split(",") if x}
if not OWNER_IDS:
    raise SystemExit("OWNER_IDS must contain at least one Telegram user ID")
SESSION_DIR = os.getenv("SESSION_DIR", ".").strip() or "."

# ── TMDB (metadata) and vidsrc (feed + embed links) ──────────────────────────
# The TMDB key can also be set later by an owner in /admin -> TMDB key (it is checked before it is saved).
# Either credential from your TMDB settings page works: the v3 "API Key" or the v4 "API Read Access Token".
TMDB_KEY = os.getenv("TMDB_KEY", "").strip()
TMDB_LANGUAGE = os.getenv("TMDB_LANGUAGE", "en-US").strip() or "en-US"
TMDB_BASE = _url("TMDB_BASE", "https://api.themoviedb.org/3")
TMDB_RATE_PER_SEC = _int("TMDB_RATE_PER_SEC", 20, 1)     # polite client-side cap
VIDSRC_BASE = _url("VIDSRC_BASE", "https://vidsrc.mov")
HTTP_TIMEOUT = _int("HTTP_TIMEOUT", 15, 3)

# ── autopost ─────────────────────────────────────────────────────────────────
AUTOPOST_INTERVAL_MIN = _int("AUTOPOST_INTERVAL_MIN", 15, 1)   # how often the feed is checked
AUTOPOST_MAX_PER_CYCLE = _int("AUTOPOST_MAX_PER_CYCLE", 5, 1)  # new titles posted per check (the rest wait for the next one)
ANNOUNCE_MIN_GAP = _int("ANNOUNCE_MIN_GAP", 3, 0)              # seconds between posts to Telegram (flood limits)
CHANNEL_MAX_FAILS = _int("CHANNEL_MAX_FAILS", 5, 1)            # a channel failing this many posts in a row is paused
BLUR_RADIUS = _int("BLUR_RADIUS", 18, 1)                       # strength of the "blur" poster mode

# ── operations ───────────────────────────────────────────────────────────────
PORT = _int("PORT", 8080, 1)                                   # /health and /metrics
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()
LOG_JSON = os.getenv("LOG_JSON", "").strip().lower() in ("1", "true", "yes")
