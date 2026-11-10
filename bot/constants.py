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
