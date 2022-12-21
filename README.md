# Telegram Web Scraper Bot

A Telegram bot that downloads and sends webpage source code.

## Features

- 📥 Download HTML source code from any webpage
- ⚡ Rate limiting (5 requests per minute)
- 🔒 File size validation (50MB max)
- 📊 User statistics tracking
- 🛡️ URL validation and error handling

## Commands

- `/start` - Start the bot
- `/help` - Show help message
- `/stats` - View your usage statistics
- `/version` - Show bot version
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
