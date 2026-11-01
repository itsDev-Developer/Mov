"""Process-wide registry: health flags, metric gauges and housekeeping callbacks.

Modules register themselves here, so the web server (health/metrics) and the main loop
(housekeeping) never need to import them, which keeps imports acyclic and tests easy.
"""
import logging
import time
from typing import Callable

log = logging.getLogger("runtime")

START = time.time()
state = {"telegram": False}          # set True once the Telegram client is connected
_gauges: dict[str, Callable[[], float]] = {}
_prunes: list[Callable[[], None]] = []
_shutdown: list[Callable] = []       # async callables run once, in order, when the bot stops


def gauge(name: str, fn: Callable[[], float]):
    """Expose `fn()` as a metric called `name` at /metrics."""
    _gauges[name] = fn


def on_housekeeping(fn: Callable[[], None]):
    """Run `fn()` roughly every minute (drop stale in-memory entries, refresh caches...)."""
    _prunes.append(fn)
    return fn


def on_shutdown(fn: Callable):
    """Register an async function to run when the bot stops (cancel tasks, close HTTP clients...)."""
    _shutdown.append(fn)
    return fn


async def run_shutdown():
    for fn in list(_shutdown):
        try:
            await fn()
        except Exception:
            log.exception("shutdown hook %s failed", getattr(fn, "__name__", fn))


def metrics_text() -> str:
    lines = [f"vidbot_uptime_seconds {int(time.time() - START)}", f"vidbot_telegram_connected {int(state['telegram'])}"]
    for name, fn in sorted(_gauges.items()):
        try:
            lines.append(f"{name} {float(fn())}")
        except Exception:
            continue
    return "\n".join(lines) + "\n"


def run_housekeeping():
    for fn in list(_prunes):
        try:
            fn()
        except Exception:
            log.exception("housekeeping task %s failed", getattr(fn, "__name__", fn))
