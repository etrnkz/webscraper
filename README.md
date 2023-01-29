<div align="center">

<br>

```
██╗    ██╗███████╗██████╗ ██╗  ██╗ █████╗ ██████╗ ██╗   ██╗███████╗███████╗████████╗
██║    ██║██╔════╝██╔══██╗██║  ██║██╔══██╗██╔══██╗██║   ██║██╔════╝██╔════╝╚══██╔══╝
██║ █╗ ██║█████╗  ██████╔╝███████║███████║██████╔╝██║   ██║███████╗█████╗     ██║   
██║███╗██║██╔══╝  ██╔══██╗██╔══██║██╔══██║██╔══██╗██║   ██║╚════██║██╔══╝     ██║   
╚███╔███╔╝███████╗██████╔╝██║  ██║██║  ██║██║  ██║╚██████╔╝███████║███████╗   ██║   
 ╚══╝╚══╝ ╚══════╝╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚══════╝   ╚═╝   
                                                                                       
**WebHarvest Bot** — *Your personal web scraping army in Telegram*
```

[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Version](https://img.shields.io/badge/version-2.3.0-2B9348?style=for-the-badge&logo=semver&logoColor=white)]()
[![License](https://img.shields.io/badge/license-MIT-DA392E?style=for-the-badge&logo=opensourceinitiative&logoColor=white)](LICENSE)
[![Telegram](https://img.shields.io/badge/pyrogram-2.0-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](https://pyrogram.org)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED?style=for-the-badge&logo=docker&logoColor=white)](Dockerfile)
[![Tests](https://img.shields.io/badge/tests-13%2F13-success?style=for-the-badge&logo=pytest&logoColor=white)]()

</div>

<br>

> **WebHarvest Bot** turns Telegram into a powerful web scraping command center. Drop a URL and get the full HTML source, extract every media file, archive entire websites, or pull rich metadata — all wrapped in a sleek Telegram interface with enterprise-grade security and a full admin command suite.

---

## 📑 Table of Contents

- [Features](#-features)
- [Quick Start](#-quick-start)
- [Commands](#-commands)
- [Examples](#-examples)
- [Configuration](#%EF%B8%8F-configuration)
- [Deployment](#-deployment)
- [Architecture](#-architecture)
- [Tech Stack](#-tech-stack)
- [Contributing](#-contributing)
- [License](#-license)

---

## ✨ Features

<!-- FEATURE: CORE SCRAPING -->
<details open>
<summary><strong>🌐 Core Scraping</strong> — <em>Fetch any webpage's source with one command</em></summary>
<br>

| Capability | Description |
|:-----------|:------------|
| **HTML Source Download** | Instantly fetch and download the complete HTML source from any URL |
| **URL Validation** | Robust validation — only legitimate `http`/`https` URLs get through |
| **Domain Safety** | Automatically blocks malicious TLDs (`.tk`, `.ml`, `.ga`, `.cf`, `.gq`) & private IPs |
| **File Size Limits** | Built-in 50MB ceiling to keep things lean |
| **Encoding Detection** | Automatic charset detection via `chardet` — no more garbled text |

</details>

<!-- FEATURE: MEDIA EXTRACTION -->
<details>
<summary><strong>📸 Media Extraction</strong> — <em>Pull every image, video, CSS & JS from any page</em></summary>
<br>

| Capability | Description |
|:-----------|:------------|
| **Image Download** | Extracts all images including lazy-loaded `data-src` attributes |
| **Video Download** | Detects `<video>` tags and `<source>` elements |
| **CSS & JS Assets** | Grabs stylesheets and script files |
| **Smart Dedup** | Auto-renames duplicates so nothing gets overwritten |
| **Batched Delivery** | Sends up to 5 files per request to keep chats clean |

</details>

<!-- FEATURE: WEBSITE ARCHIVING -->
<details>
<summary><strong>📦 Website Archiving</strong> — <em>Recursively download entire websites</em></summary>
<br>

| Capability | Description |
|:-----------|:------------|
| **Recursive Crawl** | Navigates linked pages within the same domain (configurable depth) |
| **ZIP Packaging** | Compresses the whole archive into a single downloadable ZIP |
| **Crawl Limits** | Configurable max pages (50) and depth (2 levels) |
| **Rate-Limited** | Respectful delays between requests to avoid hammering servers |

</details>

<!-- FEATURE: METADATA PARSING -->
<details>
<summary><strong>ℹ️ Metadata Parsing</strong> — <em>Uncover every hidden detail of a webpage</em></summary>
<br>

| Capability | Description |
|:-----------|:------------|
| **Title & Description** | Extracts `<title>` and `<meta name="description">` |
| **Open Graph** | OG title, description & image for social previews |
| **Twitter Cards** | Twitter-specific card metadata |
| **Author & Keywords** | Content authorship and keyword tags |
| **Language Detection** | Reads the `html lang` attribute |
| **Canonical URL** | Identifies the canonical link reference |

</details>

<!-- FEATURE: ADMIN PANEL -->
<details>
<summary><strong>👑 Admin Panel</strong> — <em>Full command center for bot operators</em></summary>
<br>

| Capability | Description |
|:-----------|:------------|
| **User Monitoring** | Track first-seen, last-seen, request counts & error rates per user |
| **Ban / Unban** | Instantly restrict abusive users with automatic DM notification |
| **Admin Protection** | Cannot ban fellow admins — safety built in |
| **Broadcast** | Send announcements to *all* users or only *active* (24h) users |
| **Activity Logs** | Searchable history of every user action |
| **Cache Control** | `/clearcache` to flush the entire cache in one command |

</details>

<!-- FEATURE: MONITORING -->
<details>
<summary><strong>📊 Monitoring & Analytics</strong> — <em>Know exactly how your bot is performing</em></summary>
<br>

| Capability | Description |
|:-----------|:------------|
| **Usage Stats** | Per-user request and error counters |
| **Cache Hit Rate** | Real-time cache efficiency ratio |
| **Uptime Tracking** | Bot live time since last restart |
| **Success Rate** | Per-user + global success percentages |
| **Performance Metrics** | Execution timing for all major operations |

</details>

<!-- FEATURE: SECURITY -->
<details>
<summary><strong>🛡️ Security & Compliance</strong> — <em>Stay safe, stay legal</em></summary>
<br>

| Capability | Description |
|:-----------|:------------|
| **Robots.txt** | Automatically respects crawl policies — no accidental violations |
| **Anti-Bot Detection** | 12-user-agent rotation pool (Chrome, Firefox, Safari, Edge) |
| **Smart Headers** | Randomized `Accept`, `Accept-Language`, `Sec-Fetch-*` — looks like a real browser |
| **Proxy Support** | Route through HTTP/HTTPS proxies for anonymity |
| **Private IP Block** | Blocks `localhost`, `127.0.0.1`, `10.x`, `172.x`, `192.168.x` |
| **Malicious TLD Block** | Shuts down `.tk`, `.ml`, `.ga`, `.cf`, `.gq` domains |
| **Force Subscribe** | Optional gate requiring users to join channels before using the bot |

</details>

<!-- FEATURE: PERFORMANCE -->
<details>
<summary><strong>⚡ Performance</strong> — <em>Fast, efficient, and kind to servers</em></summary>
<br>

| Capability | Description |
|:-----------|:------------|
| **Smart Caching** | 24-hour content cache with automatic expiry |
| **Rate Limiting** | 5 req/min + 15 req/day per user (configurable) |
| **Exponential Backoff** | Retries with progressive delays on failure |
| **Sitemap Discovery** | Parses `sitemap.xml` for bulk URL discovery |

</details>

<br>

> **37+ features** packed into one Telegram bot. Every capability is built with production-grade error handling, logging, and security in mind.

---

## 🚀 Quick Start

### 📋 Prerequisites

<table>
<tr>
<td align="center">🔑</td>
<td><strong>Bot Token</strong> — Get yours from <a href="https://t.me/BotFather">@BotFather</a></td>
</tr>
<tr>
<td align="center">🆔</td>
<td><strong>API Credentials</strong> — Grab from <a href="https://my.telegram.org">my.telegram.org</a></td>
</tr>
<tr>
<td align="center">🐍</td>
<td><strong>Python 3.10+</strong></td>
</tr>
</table>

### ⚡ Install & Run

```bash
# Clone it
git clone https://github.com/yourusername/webharvest-bot.git
cd webharvest-bot

# Install deps
pip install -r requirements.txt

# Configure
cp .env.example .env
# 🔓 Populate .env with your API_ID, API_HASH & BOT_TOKEN

# Launch 🚀
python main.py
```

> **That's it.** Your bot is now live and processing URLs on Telegram.

---

## 📋 Commands

### 👤 User Commands

<table>
<tr>
<th>Command</th>
<th>What it does</th>
</tr>
<tr><td><code>/start</code></td><td>👋 Greetings & account registration</td></tr>
<tr><td><code>/help</code></td><td>📖 Full usage guide</td></tr>
<tr><td><code>/stats</code></td><td>📊 Your personal usage statistics</td></tr>
<tr><td><code>/version</code></td><td>ℹ️ Bot version info</td></tr>
<tr><td><code>/info &lt;url&gt;</code></td><td>🔍 Extract metadata from a webpage</td></tr>
<tr><td><code>/media &lt;url&gt;</code></td><td>🖼️ Download all media from a page</td></tr>
<tr><td><code>/archive &lt;url&gt;</code></td><td>📦 Recursively archive an entire site</td></tr>
</table>

### 🔧 Admin Commands

<table>
<tr>
<th>Command</th>
<th>What it does</th>
</tr>
<tr><td><code>/admin</code></td><td>🎛️ Open the admin control panel</td></tr>
<tr><td><code>/users</code></td><td>👥 List all registered users</td></tr>
<tr><td><code>/topusers</code></td><td>🏆 Top 10 by activity</td></tr>
<tr><td><code>/userinfo &lt;id&gt;</code></td><td>🔎 Deep-dive into a specific user</td></tr>
<tr><td><code>/ban &lt;id&gt;</code></td><td>🚫 Restrict an abusive user</td></tr>
<tr><td><code>/unban &lt;id&gt;</code></td><td>✅ Lift a restriction</td></tr>
<tr><td><code>/broadcast &lt;msg&gt;</code></td><td>📢 Announce to everyone</td></tr>
<tr><td><code>/broadcast active &lt;msg&gt;</code></td><td>📢 Announce to active users only</td></tr>
<tr><td><code>/logs [user_id]</code></td><td>📋 View activity logs</td></tr>
<tr><td><code>/clearcache</code></td><td>🧹 Flush cached content</td></tr>
</table>

---

## 🎯 Examples

### Scrape a webpage
```
User:  https://example.com
Bot:   ✅ Source code extracted
       🌐 Domain: example.com
       📦 Size: 45.23 KB
       🔤 Encoding: utf-8
       ⏱️ Time: 1.23s
```

### Extract metadata
```
User:  /info https://example.com
Bot:   ℹ️ Page Information
       📄 Title: Example Domain
       📝 Description: This domain is for use...
       ✍️ Author: Internet Assigned Numbers Authority
       🌐 Language: en
```

### Archive a website
```
User:  /archive https://docs.example.org
Bot:   ✅ Website Archive
       🌐 Domain: docs.example.org
       📄 Pages: 23
       📦 Size: 4.21 MB
```

---

## ⚙️ Configuration

All configuration lives in **environment variables**. Drop them in a `.env` file:

| Variable | Required | Default | Description |
|:---------|:--------:|:-------:|:------------|
| `API_ID` | ✅ | — | Telegram API ID from [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | ✅ | — | Telegram API Hash |
| `BOT_TOKEN` | ✅ | — | Bot token from [@BotFather](https://t.me/BotFather) |
| `ADMIN_IDS` | ❌ | — | Comma-separated admin user IDs |
| `LOG_LEVEL` | ❌ | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `PROXY_ENABLED` | ❌ | `false` | Toggle proxy support |
| `PROXY_HTTP` | ❌ | — | `http://user:pass@host:port` |
| `PROXY_HTTPS` | ❌ | — | `https://user:pass@host:port` |
| `REQUEST_DELAY` | ❌ | `0.5` | Delay between requests (seconds) |
| `FORCE_SUBSCRIBE_ENABLED` | ❌ | `false` | Require channel subscription |
| `FORCE_SUBSCRIBE_CHANNELS` | ❌ | — | Comma-separated channel IDs |

---

## 🐳 Deployment

### Docker (single command)

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
heroku config:set API_ID=your_api_id API_HASH=your_api_hash BOT_TOKEN=your_bot_token
git push heroku main
```

---

## 🏗️ Architecture

```
webharvest-bot/
├── main.py                 # Entry point — launches the bot
├── bot/
│   ├── __init__.py         # Package metadata (v2.3.0)
│   ├── config.py           # Environment-based configuration
│   ├── constants.py        # Messages, limits, and bot constants
│   ├── core/
│   │   └── scraper.py      # Core engine — all handlers & scraping logic
│   ├── modules/
│   │   ├── cache_manager.py    # 24h content caching layer
│   │   ├── media_extractor.py  # Image/video/CSS/JS extraction
│   │   ├── metadata_parser.py  # OG, Twitter, meta tag parsing
│   │   ├── robots_handler.py   # robots.txt compliance
│   │   ├── sitemap_crawler.py  # sitemap.xml discovery
│   │   └── web_archiver.py     # Recursive website downloader
│   ├── admin/
│   │   ├── panel.py            # Admin dashboard & user management
│   │   └── activity_tracker.py # Activity logging system
│   ├── plugins/
│   │   ├── force_subscribe.py  # Channel subscription gate
│   │   └── handlers.py         # Additional message handlers
│   ├── monitoring/
│   │   └── performance.py      # Metrics, timing, cache stats
│   └── utils/
│       ├── helpers.py          # sanitize_filename, format_file_size, extract_domain
│       ├── validators.py       # URL & domain validation
│       └── user_agents.py      # 12-user-agent rotation pool
├── tests/                  # pytest suite (13 tests)
├── pyproject.toml          # Modern Python tooling config
├── Dockerfile              # Multi-stage Docker build
├── requirements.txt        # Pinned dependencies
└── .env.example            # Configuration template
```

---

## 🛠 Tech Stack

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Pyrogram-2.0-26A5E4?style=for-the-badge&logo=telegram&logoColor=white" />
  <img src="https://img.shields.io/badge/Requests-2.28-009688?style=for-the-badge&logo=&logoColor=white" />
  <img src="https://img.shields.io/badge/BeautifulSoup-4.11-8BC34A?style=for-the-badge&logo=&logoColor=white" />
  <img src="https://img.shields.io/badge/Flask-2.2-000000?style=for-the-badge&logo=flask&logoColor=white" />
  <img src="https://img.shields.io/badge/Docker-ready-2496ED?style=for-the-badge&logo=docker&logoColor=white" />
</p>

| Layer | Technology |
|:------|:-----------|
| **Runtime** | Python 3.10+ |
| **Telegram Framework** | Pyrogram (MTProto) |
| **HTTP Client** | Requests + Session management |
| **HTML Parsing** | BeautifulSoup4 + lxml |
| **Encoding** | chardet |
| **Web Server** | Flask + Gunicorn |
| **Container** | Docker (multi-stage) |
| **Testing** | pytest + coverage |
| **Linting** | ruff |

---

## 🤝 Contributing

Contributions are welcome! Here's how to get started:

1. **Fork** the repo
2. **Create a branch** (`git checkout -b feature/amazing`)
3. **Commit** your changes (`git commit -m 'feat: add amazing feature'`)
4. **Push** (`git push origin feature/amazing`)
5. **Open a Pull Request**

Please ensure your code passes the test suite:

```bash
pytest tests/ -v
```

---

## ⭐ Show Your Support

If you find this project useful, consider:

- ⭐ **Starring** the repository on GitHub
- 📢 **Sharing** it with your Telegram bot enthusiast friends
- 🐛 **Reporting** issues or suggesting features

---

## 📄 License

```
MIT License

Copyright (c) 2022-2023 WebHarvest Bot

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files...
```

Full details in the [LICENSE](LICENSE) file.

---

<div align="center">
  <sub>Built with ❤️ and ☕ by the WebHarvest Team</sub>
  <br>
  <sub>Made in Python — powered by Pyrogram</sub>
</div>
