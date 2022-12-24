# Telegram Web Scraper Bot

A Telegram bot that downloads and sends webpage source code.

## Features

- 📥 Download HTML source code from any webpage
- 🖼️ Extract and download media (images, videos, CSS, JS)
- ⚡ Smart rate limiting (5/min, 15/day)
- 💾 Intelligent caching system (24h cache)
- 🔒 File size validation (50MB max)
- 🛡️ Anti-bot detection with rotating user agents
- 🌐 Optional proxy support
- 📊 User statistics tracking
- 🔄 Automatic retry with exponential backoff
- 🎯 URL validation and security checks

## Commands

- `/start` - Start the bot
- `/help` - Show help message
- `/stats` - View your usage statistics
- `/version` - Show bot version
- `/media <url>` - Download media from webpage
- `/admin` - Admin panel (admin only)
- `/broadcast` - Send announcement (admin only)

## Setup

1. Clone the repository
2. Install dependencies: `pip install -r requirements.txt`
3. Create `.env` file with your credentials (see `.env.example`)
4. Run: `python scraper.py`

## Environment Variables

```
API_ID=your_api_id
API_HASH=your_api_hash
BOT_TOKEN=your_bot_token
ADMIN_IDS=comma_separated_user_ids
LOG_LEVEL=INFO
PROXY_ENABLED=false
PROXY_HTTP=http://proxy:port
PROXY_HTTPS=https://proxy:port
```

## Deployment

### Docker
```bash
docker build -t telegram-scraper .
docker run -d telegram-scraper
```

### Heroku
```bash
heroku create
git push heroku main
```

## License

MIT
