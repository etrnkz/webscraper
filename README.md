<div align="center">

<br>

```
██████╗ ██╗    ██╗███████╗██████╗ ███████╗ ██████╗██████╗  █████╗ ██████╗ ███████╗██████╗ 
██╔══██╗██║    ██║██╔════╝██╔══██╗██╔════╝██╔════╝██╔══██╗██╔══██╗██╔══██╗██╔════╝██╔══██╗
██████╔╝██║ █╗ ██║█████╗  ██████╔╝███████╗██║     ██████╔╝███████║██████╔╝█████╗  ██████╔╝
██╔═══╝ ██║███╗██║██╔══╝  ██╔══██╗╚════██║██║     ██╔══██╗██╔══██║██╔══██╗██╔══╝  ██╔══██╗
██║     ╚███╔███╔╝███████╗██║  ██║███████║╚██████╗██║  ██║██║  ██║██║  ██║███████╗██║  ██║
╚═╝      ╚══╝╚══╝ ╚══════╝╚═╝  ╚═╝╚══════╝ ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝
```

**WebScraper Bot** — Full website extraction, right inside Telegram

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![Pyrogram](https://img.shields.io/badge/Pyrogram-2.0-26A5E4?style=flat-square&logo=telegram&logoColor=white)](https://pyrogram.org)
[![Version](https://img.shields.io/badge/version-2.3.0-2B9348?style=flat-square&logo=semver&logoColor=white)]()
[![License](https://img.shields.io/badge/license-MIT-DA392E?style=flat-square&logo=opensourceinitiative&logoColor=white)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED?style=flat-square&logo=docker&logoColor=white)](Dockerfile)

<br>

</div>

A Telegram bot that scrapes entire websites, extracts media, archives pages, and pulls rich metadata — all delivered as clean ZIP files. Built with Pyrogram for speed, with a full admin suite, SQLite persistence, and multi-format broadcasts.

---

## Quick Start

```bash
git clone https://github.com/yourusername/webscraper-bot
cd webscraper-bot
pip install -r requirements.txt
cp .env.example .env   # add API_ID, API_HASH, BOT_TOKEN
python main.py
```

Or with Docker:

```bash
docker build -t webscraper-bot . && docker run -d --env-file .env webscraper-bot
```

---

## Commands

| | Command | Description |
|---|---------|-------------|
| 👤 | `send a URL` | Download the full website as ZIP |
| 👤 | `/help` | Usage guide |
| 👤 | `/stats` | Your usage statistics |
| 👤 | `/info <url>` | Page metadata |
| 👤 | `/media <url>` | Extract images & assets |
| 👤 | `/archive <url>` | Recursive site archive |
| 🔧 | `/admin` | Admin panel |
| 🔧 | `/broadcast` | Send messages (text/markdown/html/buttons) |
| 🔧 | `/promote` | Quick promotional broadcast |
| 🔧 | `/users` | List & manage users |
| 🔧 | `/ban` / `/unban` | User restrictions |
| 🔧 | `/logs` | Activity logs |

---

## Features

**Website Scraping** — Drop any URL and the bot recursively downloads every linked page on the domain, packages them into a ZIP, and sends it straight to your chat.

**Media Extraction** — Pull images, videos, CSS, and JS assets from any page. Automatically handles lazy-loaded attributes and deduplicates files.

**Website Archiving** — Recursively crawl up to 50 pages (configurable depth) and get a complete site backup as a ZIP archive.

**Metadata Parsing** — Extract title, description, Open Graph tags, Twitter Cards, author, keywords, language, and canonical URLs from any page.

**Admin Suite** — Full user management with ban/unban, usage stats, activity logs, cache control, and a powerful broadcast system with inline button support.

**Security** — Blocks malicious TLDs, private IPs, and unsafe domains. Rotates user agents and headers to avoid bot detection. Optional force-subscribe channel gate.

---

## Configuration

| Variable | Required | Default | Description |
|----------|:--------:|:-------:|-------------|
| `API_ID` | ✅ | — | From [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | ✅ | — | From [my.telegram.org](https://my.telegram.org) |
| `BOT_TOKEN` | ✅ | — | From [@BotFather](https://t.me/BotFather) |
| `ADMIN_IDS` | ❌ | — | Comma-separated admin user IDs |
| `LOG_LEVEL` | ❌ | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `FORCE_SUBSCRIBE_CHANNELS` | ❌ | — | Channel IDs for subscription gate |



## Tech Stack

| | |
|---|------|
| **Language** | Python 3.10+ |
| **Framework** | Pyrogram 2.0 (MTProto) |
| **Database** | SQLite |
| **HTTP** | Requests + Session pooling |
| **Parsing** | BeautifulSoup4 |
| **Container** | Docker |
| **Testing** | pytest |

---

## License

MIT &mdash; see [LICENSE](LICENSE) for details.

<div align="center">
  <br>
  <sub>Built with Python & Pyrogram</sub>
</div>
