# Telegram Web Scraper Bot

A Telegram bot that downloads and sends webpage source code.

## Features

- 📥 Download HTML source code from any webpage
- 🖼️ Extract and download media (images, videos, CSS, JS)
- 📦 Recursive website archiving (wget-like functionality)
- ℹ️ Extract comprehensive webpage metadata
- ⚡ Smart rate limiting (5/min, 15/day)
- 💾 Intelligent caching system (24h cache)
- 🔒 File size validation (50MB max)
- 🛡️ Anti-bot detection with rotating user agents
- 🌐 Optional proxy support
- 🤖 Robots.txt compliance checking
- 📊 User statistics and performance metrics
- 🔄 Automatic retry with exponential backoff
- 🎯 URL validation and security checks
- 🗺️ Sitemap.xml parsing for URL discovery
- 👥 Comprehensive admin panel with user monitoring
- 🚫 User ban/unban system
- 📋 Activity logging and tracking
- 📢 Targeted broadcast messaging

## Commands

### User Commands
- `/start` - Start the bot
- `/help` - Show help message
- `/stats` - View your usage statistics
- `/version` - Show bot version
- `/info <url>` - Get webpage metadata
- `/media <url>` - Download media from webpage
- `/archive <url>` - Recursively download website

### Admin Commands
- `/admin` - Admin panel with statistics
- `/users` - List all users with activity
- `/topusers` - Show top 10 most active users
- `/userinfo <user_id>` - Get detailed user information
- `/ban <user_id>` - Ban a user from using the bot
- `/unban <user_id>` - Unban a previously banned user
- `/broadcast <message>` - Send message to all users
- `/broadcast active <message>` - Send to active users only
- `/logs [user_id]` - View activity logs
- `/clearcache` - Clear cached content

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
