"""Bot constants and messages"""

BOT_VERSION = "3.0.0"
BOT_NAME = "Web Cloner Bot"

WELCOME_MESSAGE = """Welcome {name}! <tg-emoji emoji-id="5339364726612713759">💜</tg-emoji>

I clone entire websites into offline ZIP files <tg-emoji emoji-id="4902196816054846269">💻</tg-emoji>

<b>Features</b>
<tg-emoji emoji-id="4913977035174446493">ℹ️</tg-emoji> Full JS rendering with Chromium
<tg-emoji emoji-id="5339364726612713759">💜</tg-emoji> Auto-downloads videos (YouTube, TikTok, etc.)
<tg-emoji emoji-id="4902196816054846269">💻</tg-emoji> Captures lazy-loaded content
<tg-emoji emoji-id="4913977035174446493">ℹ️</tg-emoji> Cookie auth for login-protected sites
<tg-emoji emoji-id="5339364726612713759">💜</tg-emoji> Smart page prioritization
<tg-emoji emoji-id="4902196816054846269">💻</tg-emoji> Offline link rewriting

Send /help for full details.
"""

HELP_MESSAGE = """**Web Cloner Bot**

Send any URL to clone a site.

**Flags** (add after URL)
`subdomains` — Include subdomains
`cookies` — Prompt for cookies.json (for auth)

Examples:
`google.com`
`google.com subdomains`
`site.com cookies subdomains`

**Commands**
/info <url> — Page metadata
/media <url> — Download images
/cancel — Stop a clone
/settings — View your settings
/scope — Set default crawl scope
/cookies — Upload cookies globally
/stats — Your usage
/start — Welcome message

Contact [@etrnkx](https://t.me/etrnkx)
"""

ERROR_INVALID_URL = "That doesn't look like a valid URL.\n\nTry: `https://example.com`"
ERROR_TIMEOUT = "The website took too long to respond.\n\nTry again in a moment."
ERROR_CONNECTION = "Couldn't reach the website.\n\nCheck the URL and try again."
ERROR_FILE_TOO_LARGE = "This site is too large (over {max_size}MB).\n\nTry a smaller page."
ERROR_PERMISSION_DENIED = "You don't have permission to use this command."
ERROR_UNEXPECTED = "Something went wrong.\nPlease try again later."

SUCCESS_EXTRACTED = """**Clone Ready!**

Domain: `{domain}`
Size: {size}

Your ZIP is below.
"""
