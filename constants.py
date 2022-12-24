"""Bot constants and messages"""

# Bot info
BOT_VERSION = "2.0.0"
BOT_NAME = "Web Scraper Bot"

# Welcome messages
WELCOME_MESSAGE = """
👋 Hello {name}!

I am a webpage source code downloader bot. Just send me any URL and I'll extract the HTML source code for you.

Use /help to see all available commands.
"""

HELP_MESSAGE = """
**📖 How to use this bot:**

1️⃣ Send me any webpage URL (starting with http:// or https://)
2️⃣ I'll fetch and send you the HTML source code
3️⃣ Rate limit: 5 requests/minute, 15 requests/day

**Example:**
`https://www.example.com`

**Commands:**
/start - Start the bot
/help - Show this help message
/stats - Show your usage statistics
/version - Show bot version
/media <url> - Download media from webpage (images, videos, etc.)

**Need help?** Contact: [Developer](https://t.me/e_phador)
"""

# Error messages
ERROR_INVALID_URL = "❌ Invalid URL format. Please provide a valid http:// or https:// URL."
ERROR_RATE_LIMIT = "⏳ Rate limit exceeded. Please wait a minute before making more requests."
ERROR_TIMEOUT = "⏱️ Request timed out. The website took too long to respond. Please try again later."
ERROR_CONNECTION = "🔌 Connection error. Unable to reach the website. Please check the URL and try again."
ERROR_FILE_TOO_LARGE = "❌ File too large. Maximum size is {max_size}MB."
ERROR_PERMISSION_DENIED = "❌ You don't have permission to use this command."
ERROR_UNEXPECTED = "❌ An unexpected error occurred while processing your request."

# Success messages
SUCCESS_EXTRACTED = "✅ **Source code extracted**\n\n🌐 Domain: `{domain}`\n📦 Size: {size}\n🔤 Encoding: {encoding}"
