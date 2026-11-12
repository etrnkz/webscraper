"""Bot constants and messages"""

BOT_VERSION = "3.0.0"
BOT_NAME = "Web Cloner Bot"

# NOTE: The welcome message is built at runtime by
# bot.utils.premium_text.PremiumText in the /start handler so that premium
# emojis render as real CUSTOM_EMOJI entities. Do not reintroduce a static
# HTML/<tg-emoji> variant here — those render literally over MTProto.

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

# Queue / admission control messages
QUEUE_WAITING = """**Queued — {position} in line**

Estimated wait: ~{wait_min} min

The server is busy. Your clone will start when resources are available.

[Cancel]
"""

QUEUE_FULL = """**Server at capacity**

Too many users are waiting. Please try again later.

Your usage was **not** charged.
"""

SERVER_BUSY_RETRY = """**Server busy — retrying**

Chromium could not launch (resource pressure). Retrying in {delay}s…

Attempt {attempt} of {max_attempts}.
"""

DEFERRED = """**Server busy — waiting for resources**

Capacity dropped before launch. Re-queued at your position.
"""

CANCELLED_QUEUED = """**Clone cancelled**

Your request was removed from the queue. No browser was launched.

Usage was **not** charged.
"""

CANCELLED_RUNNING = """**Clone cancelled**

The crawl was stopped and temporary files were cleaned up.

Usage **was** charged (browser had launched).
"""

TOO_LATE_TO_CANCEL = """**Cannot cancel**

The archive is already being uploaded. Deleting it mid-transfer
would corrupt the file.

Your clone is on its way!
"""

RESTART_NOTICE = """**Server restarted**

The server restarted while your clone was queued.
Please send the URL again.
"""

EXPIRED = """**Queue wait expired**

Your request waited too long and was removed from the queue.

Usage was **not** charged. Please try again.
"""
