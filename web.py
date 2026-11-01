"""A tiny HTTP server for operations: /health (Docker / uptime checks) and /metrics (Prometheus text)."""
import logging

import config
import runtime
from database import db, settings

log = logging.getLogger("web")


async def _health(request):
    from aiohttp import web
    body, ok = {"telegram": bool(runtime.state.get("telegram"))}, True
    try:
        await db.ping()
        body["mongo"] = "ok"
    except Exception as e:
        body["mongo"], ok = f"error: {type(e).__name__}", False
    body["tmdb_key"] = bool(settings.get("tmdb_key"))
    ok = ok and body["telegram"]
    body["status"] = "ok" if ok else "degraded"
    return web.json_response(body, status=200 if ok else 503)


async def _metrics(request):
    from aiohttp import web
    return web.Response(text=runtime.metrics_text(), content_type="text/plain")


async def start():
    from aiohttp import web
    app = web.Application()
    app.router.add_get("/", _health)
    app.router.add_get("/health", _health)
    app.router.add_get("/metrics", _metrics)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, "0.0.0.0", config.PORT).start()
        log.info("health + metrics on :%d", config.PORT)
    except OSError as e:
        log.warning("couldn't bind the health server on :%d (%s): continuing without it", config.PORT, e)
    return runner
