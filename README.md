<div align="center">
  <h1>🌐 WebHarvest Bot</h1>
  <p><strong>Advanced Telegram Bot for Web Scraping, Archiving &amp; Media Extraction</strong></p>

  <p>
    <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python">
    <img src="https://img.shields.io/badge/version-2.3.0-green" alt="Version">
    <img src="https://img.shields.io/badge/license-MIT-orange" alt="License">
    <img src="https://img.shields.io/badge/telegram-pyrogram-blueviolet" alt="Pyrogram">
  </p>
</div>

---

A powerful Telegram bot for web scraping, archiving, and media extraction with advanced anti-bot detection, comprehensive admin controls, and enterprise-grade security features.

---

## ✨ Features

### 🌐 Core Scraping
- **HTML Source Download** — Fetch and download the complete HTML source code from any webpage
- **URL Validation** — Robust validation ensuring only valid `http`/`https` URLs are processed
- **Domain Safety Checks** — Blocks malicious TLDs (`.tk`, `.ml`, `.ga`, `.cf`, `.gq`) and private IP ranges
- **File Size Limits** — Configurable 50MB maximum file size protection
- **Encoding Detection** — Automatic charset detection with `chardet` for proper text rendering

### 📸 Media Extraction
- **Image Download** — Extract all images from any webpage (including lazy-loaded `data-src`)
- **Video Download** — Detect and download video files and sources
- **CSS & JS Assets** — Extract stylesheets and JavaScript files
- **Smart Deduplication** — Avoids duplicate file downloads with automatic renaming

### 📦 Website Archiving
- **Recursive Download** — Crawl entire websites with configurable depth (up to 2 levels)
- **Paginated Archiving** — Automatically discover and archive linked pages within the same domain
- **ZIP Packaging** — Compress archived pages into a single downloadable ZIP file
- **Rate-Limited Crawling** — Respectful crawling with configurable delays between requests

### ℹ️ Metadata Parsing
- **Page Titles** — Extract HTML title tags
- **Meta Descriptions** — Parse description meta tags
- **Open Graph** — Support for OG title, description, and image tags
- **Twitter Cards** — Extract Twitter card metadata
- **Author & Keywords** — Identify content authors and keyword tags
- **Language Detection** — Detect page language from `html` lang attribute
- **Canonical URLs** — Identify canonical URL references

### 👑 Admin Panel
- **User Monitoring** — Track all users with first seen, last seen, and activity metrics
- **User Ban/Unban** — Ban abusive users with automatic notification
- **Admin Protection** — Prevent banning other admin users
- **User Statistics** — Per-user request counts, error rates, and success percentages
- **Activity Logging** — Detailed activity logs for all user actions
- **Broadcast Messaging** — Send announcements to all users or active users only
- **Cache Management** — Clear cached content with a single command

### 📊 Monitoring & Analytics
- **Usage Statistics** — Per-user request and error tracking
- **Performance Metrics** — Execution timing for key operations
- **Cache Hit Rate** — Track caching efficiency with hit/miss ratio
- **Uptime Tracking** — Monitor bot uptime since last restart
- **Success Rate** — Calculate per-user and global success percentages

### 🛡️ Security & Compliance
- **Robots.txt Compliance** — Respects website crawling policies automatically
- **Anti-Bot Detection** — Randomized user agent rotation pool (Chrome, Firefox, Safari, Edge)
- **Request Header Randomization** — Full header set with modern `Sec-Fetch-*` headers
- **Proxy Support** — Optional HTTP/HTTPS proxy configuration for anonymity
- **Private IP Blocking** — Blocks requests to localhost, 127.0.0.1, and private network ranges
- **Malicious TLD Detection** — Automatically blocks known malicious free TLDs
- **Force Subscription** — Optional channel subscription requirement for bot access

### ⚡ Performance
- **Intelligent Caching** — 24-hour content caching with automatic expiry
- **Rate Limiting** — Configurable per-minute (5/min) and daily (15/day) limits
- **Exponential Backoff** — Automatic retry with progressive delays on failure
- **Sitemap Discovery** — Parse `sitemap.xml` for URL discovery and batch processing

## 🚀 Quick Start

### Prerequisites
- Python 3.10 or higher
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Telegram API ID and Hash (from [my.telegram.org](https://my.telegram.org))

### Installation

```bash
git clone https://github.com/yourusername/webharvest-bot.git
cd webharvest-bot
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your Telegram credentials
python main.py
```

## 📋 Commands

| Command | Description |
|---------|-------------|
| `/start` | Start the bot and register your account |
| `/help` | Display help message with usage instructions |
| `/stats` | View your personal usage statistics |
| `/version` | Show the current bot version |
| `/info <url>` | Extract and display webpage metadata |
| `/media <url>` | Download media files from a webpage |
| `/archive <url>` | Recursively archive an entire website |

### 🔧 Admin Commands

| Command | Description |
|---------|-------------|
| `/admin` | Open admin panel with statistics |
| `/users` | List all registered users |
| `/topusers` | Show top 10 most active users |
| `/userinfo <id>` | Get detailed information about a user |
| `/ban <id>` | Ban a user from using the bot |
| `/unban <id>` | Unban a previously banned user |
| `/broadcast <msg>` | Send message to all users |
| `/broadcast active <msg>` | Send message to active users only |
| `/logs [user_id]` | View activity logs |
| `/clearcache` | Clear all cached content |

## ⚙️ Configuration

All configuration is managed through environment variables:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `API_ID` | Yes | — | Telegram API ID from my.telegram.org |
| `API_HASH` | Yes | — | Telegram API Hash from my.telegram.org |
| `BOT_TOKEN` | Yes | — | Bot token from @BotFather |
| `ADMIN_IDS` | No | — | Comma-separated admin user IDs |
| `LOG_LEVEL` | No | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `PROXY_ENABLED` | No | `false` | Enable proxy support |
| `PROXY_HTTP` | No | — | HTTP proxy URL |
| `PROXY_HTTPS` | No | — | HTTPS proxy URL |
| `REQUEST_DELAY` | No | `0.5` | Delay between requests in seconds |
| `FORCE_SUBSCRIBE_ENABLED` | No | `false` | Enable force channel subscription |
| `FORCE_SUBSCRIBE_CHANNELS` | No | — | Comma-separated channel IDs |

## 🐳 Deployment

### Docker (Recommended)

```bash
docker build -t webharvest-bot .
docker run -d \
  --name webharvest-bot \
  --restart unless-stopped \
  --env-file .env \
  webharvest-bot
```

### Docker Compose

```yaml
version: "3.8"
services:
  bot:
    build: .
    env_file: .env
    restart: unless-stopped
```

### Heroku

```bash
heroku create webharvest-bot
heroku config:set API_ID=your_api_id
heroku config:set API_HASH=your_api_hash
heroku config:set BOT_TOKEN=your_bot_token
git push heroku main
```

## 🛠 Tech Stack

- **Runtime** — Python 3.10+
- **Framework** — Pyrogram (MTProto-based Telegram client)
- **Web Scraping** — Requests, BeautifulSoup4, lxml
- **Web Server** — Flask + Gunicorn (health checks)
- **Container** — Docker with multi-stage builds

## 📄 License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.
