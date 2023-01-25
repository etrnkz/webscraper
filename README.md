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
