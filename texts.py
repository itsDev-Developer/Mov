"""All user-facing wording (HTML). House style: bold heading with an emoji, body in a <blockquote>, hints in <i>."""

WELCOME = ("👋 <b>Welcome, {name}!</b>\n\n"
           "<blockquote>🔎 <b>Type a title</b> to search movies and series\n"
           "🆕 or browse what's new</blockquote>\n"
           "<i>Tap a title to see details and watch.</i>")

HELP = ("❓ <b>How it works</b>\n\n"
        "<blockquote>1️⃣ Type a title, or tap 🆕 for the latest\n"
        "2️⃣ Open it to see the poster, rating and story\n"
        "3️⃣ Tap ▶️ Watch Now. For series pick a season and episode first</blockquote>\n\n"
        "{commands}")

# TMDB's terms ask for this notice in an About/credits section
ABOUT = ("ℹ️ <b>About</b>\n\n"
         "<blockquote>Titles, posters and details come from TMDB.\n"
         "<b>This product uses the TMDB API but is not endorsed or certified by TMDB.</b></blockquote>\n"
         "<i>Streaming links are provided by a third-party player.</i>")

SEARCH_PROMPT = "🔍 <b>Search</b>\n\n<blockquote>Type the title you're looking for and send it.</blockquote>"
SEARCH_SHORT = "Please type at least 2 characters."
SEARCHING = "🔎 <i>Searching…</i>"
NO_RESULTS = "😕 <b>No results</b>\n\n<blockquote>Try a shorter or different spelling.</blockquote>"
EXPIRED = "This button expired. Please search again."
ERR_GENERIC = "⚠️ <b>Something went wrong</b>\n\n<blockquote>Please try again.</blockquote>"
NOT_READY = "🛠 <b>Not set up yet</b>\n\n<blockquote>The owner still has to add the TMDB key.</blockquote>"
NOTHING_NEW = "<i>Nothing new right now. Try again later.</i>"

BTN_WATCH = "▶️ Watch Now"
BTN_PREMIUM = "⭐ Premium"
BTN_BACKUP = "💾 Back-up"

CHANNEL_PAUSED = ("⏸ <b>Channel paused</b>\n<blockquote><b>{title}</b> failed {n} posts in a row, so autopost to it was "
                  "switched off. Fix the problem (is the bot still an admin there?) and turn it back on in /admin → Channels.</blockquote>")
