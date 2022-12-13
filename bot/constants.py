"""Bot constants and messages"""

# Bot info
BOT_VERSION = "2.3.0"
BOT_NAME = "Web Scraper Bot"

# Welcome messages
WELCOME_MESSAGE = """
╔══════════════════════════════╗
║   👋 **Welcome {name}!**       ║
╚══════════════════════════════╝

I'm a **webpage source code extractor**. Send me any URL and I'll download the entire website for you — packed in a neat `.zip` file.

▸ Use /help to explore all commands
"""

HELP_MESSAGE = """
╔══════════════════════════════╗
║      📖 **Help Center**        ║
╚══════════════════════════════╝

**How it works:**
Simply send any URL starting with `http://` or `https://` and I'll download & zip the source for you.

**User Commands:**
┌─────────────────────────────────┐
│ `/start`   — Restart the bot    │
│ `/help`    — Show this guide    │
│ `/stats`   — Your usage stats   │
│ `/version` — Bot version info   │
│ `/info`    — Extract page meta  │
│ `/media`   — Download images    │
│ `/archive` — Archive a website  │
└─────────────────────────────────┘

**Example:**
`https://www.example.com`

**Need help?** Contact: [Developer](https://t.me/e_phador)
"""

# Error messages
ERROR_INVALID_URL = "❌ **Invalid URL**\nPlease provide a valid URL starting with `http://` or `https://`."
ERROR_TIMEOUT = "⏱️ **Request timed out**\nThe website took too long to respond. Try again later."
ERROR_CONNECTION = "🔌 **Connection error**\nUnable to reach the website. Check the URL and try again."
ERROR_FILE_TOO_LARGE = "❌ **File too large**\nMaximum size is {max_size}MB."
ERROR_PERMISSION_DENIED = "⛔ **Access denied**\nYou don't have permission to use this command."
ERROR_UNEXPECTED = "❌ **Unexpected error**\nSomething went wrong. Please try again."

# Success messages
SUCCESS_EXTRACTED = "✅ **Source extracted**  \n🌐 `{domain}`  \n📦 {size}  \n🔤 {encoding}"
