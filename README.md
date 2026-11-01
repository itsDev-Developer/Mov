# vidbot — a Telegram bot for movies & series (TMDB metadata + vidsrc links)

Users type a title, see the poster, rating and story, and tap **▶️ Watch Now**. Admins connect channels and the bot
**autoposts every new title** from the vidsrc feed (poster + details + buttons), with one-tap manual posting too.

```
vidsrc feed  ──ids──▶  TMDB (your key)  ──details + images──▶  post in your channels  ──▶  ▶️ Watch Now (vidsrc embed link)
```

## What it does

**For users**
* Type any title (or `/search`) → results mixing movies and series, most popular first, paged.
* `🆕 Movies` / `🆕 Series` → what's new on vidsrc, with TMDB titles and ratings.
* A title page: poster, year, rating, runtime, genres, status, story (in an expandable quote) and **▶️ Watch Now**.
* Series: **📺 Seasons → episode grid → episode page** (name, air date, story, still image) with Prev/Next and a Watch button per episode.

**For admins** (`/admin`)
* 🔑 **TMDB key** — you give it; it is checked against TMDB before it is saved, and your message with the key is deleted.
* 📣 **Channels** — any number. Per channel: autopost on/off · caption design · poster **blur** · ⭐ Premium and 💾 Back-up links.
* 🤖 **Autopost** — checks the feed every N minutes and posts new titles to every channel with autopost on.
* 📮 **Post a title** (`/post movie 603`) — choose **one channel, several, or All**; override design/blur for that post; see a preview.
* 🌐 **Settings** — language, feed types, subtitles JSON, interval and limits, design, blur, image source, links, toggles.

## How it uses the two APIs

**vidsrc** — exactly what your reference lists, nothing more:

| Used for | Request |
|---|---|
| movie link | `GET {base}/embed/movie/{id}` (TMDB or IMDB id) |
| episode link | `GET {base}/embed/tv/{id}/{season}/{episode}` |
| "what's new" and autopost | `GET {base}/vapi/movie/{type}/{page}` and `/vapi/tv/{type}/{page}` → `result[].tmdb_id / imdb_id / quality` |
| subtitles | `?sub.info={json}` — the JSON you configure is passed through untouched (URL-encoded) |

**TMDB** — only endpoints from the official reference (developer.themoviedb.org): `GET /configuration` (image URLs and sizes),
`/movie/{id}`, `/tv/{id}`, `/tv/{id}/season/{n}`, `/search/movie`, `/search/tv`. Auth is either a v3 `api_key` query parameter or
the v4 *API Read Access Token* as a Bearer header — paste whichever you have; the bot detects it. Images are built as
`base_url + size + file_path` from `/configuration`, as the image docs describe.

### Things the vidsrc reference doesn't say (so they are settings, not guesses)
* **Feed `{type}` values** — only `latest` is implied by your example. It is the default; change it in `/admin → Settings` if the provider has others.
* **TV feed entries** — the reference shows only a movie example. The parser accepts `tmdb_id`/`imdb_id`/`quality` and, if present, `season`/`episode`
  (then the post's button says *Watch S2E4*). Entries without a `tmdb_id` are skipped, because TMDB details need it.
* **Subtitles schema** — belongs to the provider; configure the JSON exactly as they document it.
* An embed link is a web page: Telegram can't play it inline. The button opens the browser; **📱 Open in Mini App** (a toggle) opens it inside Telegram instead — try it, not every player page behaves in a Mini App.
* Redirects are not followed (a deliberate safety choice). Give the final base URL.

## Setup

```bash
cp .env.example .env        # fill API_ID, API_HASH, BOT_TOKEN, MONGO_URI, OWNER_IDS (TMDB_KEY is optional here)
docker compose up -d --build
```
1. Open the bot → `/admin` → **🔑 TMDB key** → paste your key (or put `TMDB_KEY` in `.env`).
2. `/admin → 📣 Channels → ➕ Add channel` (make the bot an **admin** of the channel first; it proves it can post).
3. Leave autopost on. The **first check only records** what the feed already holds, so your channel isn't flooded; new titles after that are posted.
   Want the newest few posted right away? Set *First-run backfill* in Settings **before** the first check, or use `/post`.
4. `/runnow` checks immediately. `/post movie 603` posts one title by hand.

Without Docker: `pip install -r requirements.txt && python bot.py`.

## Autopost rules (so there are no surprises)
* **Oldest first**, at most `AUTOPOST_MAX_PER_CYCLE` per check (default 5); the rest wait for the next check.
* A title is remembered only when it was **dealt with**: posted, or skipped on purpose (not on TMDB; no image while *Require an image* is on).
  If TMDB is down or every channel fails, the title stays unposted and is **retried next check**.
* One check at a time — *Run now* during a running check says "busy" instead of doubling.
* A channel that fails 5 posts in a row is **paused** and the owners (or your log chat) are told; fix it and switch it back on.
* Posts are spaced 3 s apart and Telegram's flood-waits are honoured.

## Blur
Per channel or globally: **off** · **spoiler** (Telegram's tap-to-reveal) · **blur** (the bot downloads the image, Gaussian-blurs it and uploads *only* the
blurred copy; strength `BLUR_RADIUS`). A requested blur **never falls back to the sharp image**: if blurring fails, the original goes out behind a spoiler;
if spoilers aren't supported by the library, the post goes out without the image. Poster downloads refuse private/loopback addresses.

## Operations
* `GET :8080/health` (200/503: Telegram connected, MongoDB reachable) and `/metrics` (Prometheus text: uptime, TMDB calls/errors/cache hits,
  autopost totals and last run, circuit-breaker state). The Docker image has a `HEALTHCHECK`; logs can be JSON lines (`LOG_JSON=1`).
* **Secrets never reach the logs**: the bot token, API hash, Mongo password and the TMDB key (including one set later in `/admin`) are scrubbed.
  A v3 key travels in the URL query string, so HTTP client logging is silenced and error messages never include URLs.
* **TMDB client**: TTL cache (details 6 h, seasons 1 h, search 10 min), identical concurrent requests share one call, a client-side rate cap,
  retries with backoff (429 honours `Retry-After`), a circuit breaker, and a 401 is reported clearly instead of retried.
* **Graceful shutdown** (SIGTERM): autopost and HTTP clients stop first, then Telegram, then MongoDB. Memory stays bounded (capped caches, expiring prompts).
* **Run one instance.** Autopost remembers titles in MongoDB, but two instances checking at once could double-post. Scale the *bot* by giving it more RAM, not more copies.

## Credits and terms
**This product uses the TMDB API but is not endorsed or certified by TMDB.** The notice is in the bot's `/about`, as TMDB's terms ask.
TMDB's API is free for non-commercial use with attribution; if you monetise the bot, TMDB's commercial-use terms apply — check them first.
The bot only links to third-party players; you are responsible for the content you point people to.

## Tests
```
python tests/test_tmdb.py       # auth modes, caching, request sharing, retries, key secrecy, image URLs
python tests/test_vidsrc.py     # embed links from the reference, ?sub.info, feed parser/client, TMDB data model
python tests/test_posting.py    # captions, buttons, blur (fails closed), channel isolation, autopost rules, end-to-end with the reference JSON
python tests/test_views.py      # every page, paging, Telegram limits, handlers, error handling
python tests/test_admin.py      # settings, the TMDB key flow, channels, the channel picker, admin management
python tests/test_commands.py   # the "/" menu vs the commands in the code (drift guard), promotions/demotions
python tests/test_poster.py     # a real blur (Pillow), SSRF guard
```
They need no network, no database and no Telegram (those are stubbed); CI runs them on every push.
