"""Logging: plain or JSON lines, quiet third-party libraries, and secrets never written to logs.

A TMDB v3 key travels in the URL query string, so it is scrubbed here too (add_secret() covers a key an admin
sets later from inside the bot)."""
import json
import logging
import sys
import time

import config

_NOISY = ("pyrogram", "httpx", "httpcore", "pymongo", "aiohttp.access", "asyncio")


class Redact(logging.Filter):
    """Replace secrets (bot token, API hash, TMDB key, Mongo password) with *** in every message."""

    def __init__(self, secrets):
        super().__init__()
        self.secrets = []
        for s in secrets:
            self.add(s)

    def add(self, secret: str):
        if secret and len(secret) >= 6 and secret not in self.secrets:
            self.secrets = sorted([*self.secrets, secret], key=len, reverse=True)

    def scrub(self, text: str) -> str:
        for s in self.secrets:
            text = text.replace(s, "***")
        return text

    def filter(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            return True
        scrubbed = self.scrub(msg)
        if scrubbed != msg:
            record.msg, record.args = scrubbed, ()
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self.scrub(record.exc_text)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record):
        out = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info or record.exc_text:
            out["exc"] = record.exc_text or self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False)


def _mongo_password(uri: str) -> str:
    try:
        creds = uri.split("://", 1)[1].split("@", 1)[0]
        return creds.split(":", 1)[1] if ":" in creds and "@" in uri else ""
    except IndexError:
        return ""


_redact: Redact | None = None


def add_secret(secret: str):
    """Start scrubbing `secret` from all log output (e.g. a TMDB key set at runtime)."""
    if _redact is not None:
        _redact.add(secret)


def setup():
    global _redact
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if config.LOG_JSON else
                         logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    _redact = Redact([config.BOT_TOKEN, config.API_HASH, config.TMDB_KEY, _mongo_password(config.MONGO_URI)])
    handler.addFilter(_redact)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))
    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)
