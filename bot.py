"""vidbot — entry point. Start order: logging → MongoDB → TMDB images → health server → Telegram → command menu → autopost.
Stop order (SIGTERM/Ctrl-C): autopost and HTTP clients first (runtime shutdown hooks), then Telegram, then MongoDB."""
import asyncio
import logging
import signal

import config
import logging_setup

logging_setup.setup()

import runtime  # noqa: E402

log = logging.getLogger("vidbot")


async def housekeeping():
    """Once a minute: drop expired in-memory state (prompts, drafts, caches)."""
    while True:
        await asyncio.sleep(60)
        try:
            runtime.run_housekeeping()
        except Exception:
            log.exception("housekeeping failed")


async def main():
    from pyrogram import Client, enums

    import commands
    import web
    from autopost import autoposter
    from database import db, settings
    from tmdb import tmdb

    await db.init()
    key = settings.get("tmdb_key")
    if key:
        logging_setup.add_secret(key)
        await tmdb.warm()
    else:
        log.warning("No TMDB key yet: an owner can add it in /admin → 🔑 TMDB key (the bot answers 'not set up' until then)")

    runner = await web.start()
    app = Client("vidbot", api_id=config.API_ID, api_hash=config.API_HASH, bot_token=config.BOT_TOKEN,
                 workdir=config.SESSION_DIR, plugins=dict(root="plugins"), parse_mode=enums.ParseMode.HTML)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:                       # Windows
            pass

    tasks = [asyncio.create_task(housekeeping())]
    await app.start()
    try:
        me = await app.get_me()
        runtime.state["telegram"] = True
        log.info("Started as @%s", me.username)
        await commands.register(app)
        await autoposter.start(app)
        await stop.wait()
    finally:
        log.info("Shutting down…")
        runtime.state["telegram"] = False
        await runtime.run_shutdown()                      # autopost, HTTP clients
        for t in tasks:
            t.cancel()
        await app.stop()
        await runner.cleanup()
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
