import os
from dotenv import load_dotenv
load_dotenv()

import asyncio
import re
import requests
from bs4 import BeautifulSoup
from pyrogram import Client, filters
import logging
import shutil
from urllib.parse import urlparse
from datetime import datetime
import bot.config as config
from bot.utils.helpers import sanitize_filename, format_file_size, extract_domain
from bot import constants
from bot.utils.validators import is_valid_url, is_safe_domain, normalize_url
from bot.utils.user_agents import get_random_headers
import bot.modules.cache_manager as cache
from bot.modules import media_extractor
from bot.modules.metadata_parser import extract_metadata, format_metadata
from bot import database as db
from bot.plugins.force_subscribe import ForceSubscribe
from bot.modules.web_archiver import DownloadManager
from bot.modules.zip_packager import create_zip, get_dir_size, format_size
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from concurrent.futures import ThreadPoolExecutor

config.validate_config()
cache.init_cache()

logging.basicConfig(level=config.LOG_LEVEL, format=config.LOG_FORMAT)
logger = logging.getLogger(__name__)

download_executor = ThreadPoolExecutor(max_workers=20)

API_ID = config.API_ID
API_HASH = config.API_HASH
BOT_TOKEN = config.BOT_TOKEN

bot = Client(
    'my_bot',
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

force_subscribe = ForceSubscribe(bot, config.FORCE_SUBSCRIBE_CHANNELS)
if config.FORCE_JOIN_CHANNEL and config.FORCE_JOIN_CHANNEL not in force_subscribe.channel_ids:
    force_subscribe.add_channel(config.FORCE_JOIN_CHANNEL)

bot_start_time = datetime.now()

# ── Active crawl jobs: user_id -> WebCloner instance ──────────────
active_jobs: dict[int, object] = {}
active_progress_msgs: dict[int, int] = {}
user_settings: dict[int, dict] = {}  # user_id -> {cookies_file, subdomains}
pending_cookie_requests: dict[int, dict] = {}  # user_id -> {url, domain, subdomains, msg_id}


async def check_force_join(user_id, msg):
    usage = db.get_usage_count(user_id)
    if usage >= config.FREE_USAGE_LIMIT:
        is_subscribed, not_subscribed = await force_subscribe.check_all_subscriptions(user_id)
        if not is_subscribed:
            buttons = force_subscribe.create_join_buttons(not_subscribed)
            await msg.reply(
                force_subscribe.get_force_subscribe_message(not_subscribed),
                reply_markup=buttons
            )
            return False
    return True


# ── Callbacks ──────────────────────────────────────────────────────
@bot.on_callback_query()
async def handle_callback(bot, callback_query):
    user_id = callback_query.from_user.id
    data = callback_query.data
    msg = callback_query.message

    if data == "check_subscription":
        is_subscribed, not_subscribed = await force_subscribe.check_all_subscriptions(user_id)
        if is_subscribed:
            await callback_query.answer("Subscribed! Enjoy the bot.", show_alert=True)
            await msg.delete()
        else:
            await callback_query.answer("You haven't joined yet.", show_alert=True)

    elif data == "help":
        await callback_query.answer()
        await msg.reply(constants.HELP_MESSAGE, disable_web_page_preview=True)

    elif data == "stats":
        await callback_query.answer()
        user = db.get_user(user_id)
        total = user['total_requests'] if user else 0
        errors = user['total_errors'] if user else 0
        success_rate = ((total - errors) / total * 100) if total > 0 else 0
        await msg.reply(
            f"**Your Stats**\n\n"
            f"Requests: `{total}`\n"
            f"Errors: `{errors}`\n"
            f"Success: `{success_rate:.1f}%`"
        )

    elif data == "cancel_crawl":
        cloner = active_jobs.get(user_id)
        if cloner:
            cloner.cancel()
            await callback_query.answer("Crawl cancelled.", show_alert=True)
        else:
            await callback_query.answer("No active crawl.", show_alert=True)

    elif data.startswith("bcast_"):
        if user_id not in config.ADMIN_IDS:
            await callback_query.answer("Admins only.", show_alert=True)
            return
        await callback_query.answer()

        draft = broadcast_drafts.get(user_id, {"msg_id": msg.id})
        broadcast_drafts[user_id] = draft

        if data == "bcast_cancel":
            broadcast_drafts.pop(user_id, None)
            await bot.edit_message_text("Broadcast cancelled.", user_id, msg.id)
            return
        elif data == "bcast_target_all":
            draft["target"] = "all"
            draft["_user_ids"] = [u["user_id"] for u in db.get_all_users()]
            draft["user_count"] = len(draft["_user_ids"])
            if not draft.get("format"):
                await _bcast_show_format(bot, user_id, msg.id)
            else:
                await _bcast_prompt_content(bot, user_id, msg.id, is_button=(draft["format"] == "button"))
        elif data == "bcast_target_active":
            draft["target"] = "active"
            draft["_user_ids"] = [u["user_id"] for u in db.get_active_users(24)]
            draft["user_count"] = len(draft["_user_ids"])
            if not draft.get("format"):
                await _bcast_show_format(bot, user_id, msg.id)
            else:
                await _bcast_prompt_content(bot, user_id, msg.id, is_button=(draft["format"] == "button"))
        elif data == "bcast_fmt_plain":
            draft["format"] = "plain"
            await _bcast_prompt_content(bot, user_id, msg.id)
        elif data == "bcast_fmt_md":
            draft["format"] = "md"
            await _bcast_prompt_content(bot, user_id, msg.id)
        elif data == "bcast_fmt_html":
            draft["format"] = "html"
            await _bcast_prompt_content(bot, user_id, msg.id)
        elif data == "bcast_fmt_button":
            draft["format"] = "button"
            await _bcast_prompt_content(bot, user_id, msg.id, is_button=True)
        elif data == "bcast_back":
            if draft.get("awaiting_content") or draft.get("awaiting_buttons"):
                draft.pop("awaiting_content", None)
                draft.pop("awaiting_buttons", None)
                await _bcast_show_format(bot, user_id, msg.id)
            elif draft.get("format") and draft.get("target"):
                await _bcast_show_format(bot, user_id, msg.id)
            else:
                await _bcast_show_target(bot, user_id, msg.id)
        elif data == "bcast_confirm":
            if not draft.get("content"):
                await bot.edit_message_text("No content to send.", user_id, msg.id)
                return
            success, failed = await _bcast_send(draft)
            await bot.edit_message_text(
                f"**Broadcast Done**\n\nSent: `{success}`\nFailed: `{failed}`\nTarget: `{draft.get('target', '?')}`",
                user_id, msg.id
            )
            broadcast_drafts.pop(user_id, None)


# ── /start ─────────────────────────────────────────────────────────
@bot.on_message(filters.private & filters.command('start'))
async def start(bot, msg):
    user_id = msg.from_user.id
    first_name = msg.from_user.first_name
    db.register_user(user_id, msg.from_user.username, first_name)

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("Help", callback_data="help"),
         InlineKeyboardButton("Stats", callback_data="stats")],
        [InlineKeyboardButton("Developer", url="https://t.me/etrnkx")],
    ])

    await msg.reply(constants.WELCOME_MESSAGE.format(name=first_name), reply_markup=buttons)


@bot.on_message(filters.private & filters.command('help'))
async def help_command(bot, msg):
    await msg.reply(constants.HELP_MESSAGE, disable_web_page_preview=True)


@bot.on_message(filters.private & filters.command('stats'))
async def stats_command(bot, msg):
    user_id = msg.from_user.id
    user = db.get_user(user_id)
    total = user['total_requests'] if user else 0
    errors = user['total_errors'] if user else 0
    success_rate = ((total - errors) / total * 100) if total > 0 else 0
    await msg.reply(
        f"**Your Stats**\n\n"
        f"Requests: `{total}`\n"
        f"Errors: `{errors}`\n"
        f"Success: `{success_rate:.1f}%`"
    )


@bot.on_message(filters.private & filters.command('version'))
async def version_command(bot, msg):
    await msg.reply(
        f"**{constants.BOT_NAME}**\n"
        f"Version: `{constants.BOT_VERSION}`\n"
        f"Engine: Playwright + Chromium"
    )


@bot.on_message(filters.private & filters.command('cancel'))
async def cancel_command(bot, msg):
    user_id = msg.from_user.id
    cloner = active_jobs.get(user_id)
    if cloner:
        cloner.cancel()
        await msg.reply("Crawl cancelled.")
    else:
        await msg.reply("No active crawl to cancel.")


@bot.on_message(filters.private & filters.command('cookies'))
async def cookies_command(bot, msg):
    user_id = msg.from_user.id
    args = msg.text.split(maxsplit=1)

    if len(args) < 2 and not msg.reply_to_message:
        settings = user_settings.get(user_id, {})
        if settings.get('cookies_file'):
            await msg.reply(
                f"**Cookies active**\n\n"
                f"File: `{os.path.basename(settings['cookies_file'])}`\n\n"
                "Send `/cookies off` to disable.\n"
                "Or reply to a `.json` file with `/cookies` to set a new one."
            )
        else:
            await msg.reply(
                "**No cookies set**\n\n"
                "Reply to a `.json` cookie file with `/cookies`.\n\n"
                "Export cookies from your browser using a browser extension "
                "(e.g. \"EditThisCookie\") and save as `cookies.json`."
            )
        return

    if args[1].strip().lower() in ('off', 'disable', 'clear', 'remove'):
        if user_id in user_settings:
            old = user_settings[user_id].pop('cookies_file', None)
            if old and os.path.exists(old):
                os.remove(old)
        await msg.reply("Cookies cleared.")
        return

    reply = msg.reply_to_message
    if not reply or not reply.document:
        await msg.reply("Reply to a `.json` file with `/cookies` to set cookies.")
        return

    if not reply.document.file_name.endswith('.json'):
        await msg.reply("File must be a `.json` cookie file.")
        return

    if reply.document.file_size > 5 * 1024 * 1024:
        await msg.reply("Cookie file too large (max 5MB).")
        return

    status = await msg.reply("Downloading cookie file...")

    try:
        cookie_dir = os.path.join("user_cookies", str(user_id))
        os.makedirs(cookie_dir, exist_ok=True)
        cookie_path = os.path.join(cookie_dir, "cookies.json")

        await bot.download_media(reply.document, file_name=cookie_path)

        import json
        with open(cookie_path, 'r') as f:
            cookies = json.load(f)

        if not isinstance(cookies, list):
            await status.edit("Invalid cookie format. Expected a JSON array.")
            return

        if user_id not in user_settings:
            user_settings[user_id] = {}
        user_settings[user_id]['cookies_file'] = cookie_path

        await status.edit(
            f"**Cookies loaded**\n\n"
            f"**Count** `{len(cookies)}` cookies\n\n"
            "These will be used for your next crawl. Send `/cookies off` to disable."
        )

    except json.JSONDecodeError:
        await status.edit("Invalid JSON file. Make sure it's a valid cookies.json.")
    except Exception as e:
        await status.edit(f"Failed to load cookies: `{str(e)[:80]}`")


@bot.on_message(filters.private & filters.command('scope'))
async def scope_command(bot, msg):
    user_id = msg.from_user.id
    args = msg.text.split(maxsplit=1)

    if user_id not in user_settings:
        user_settings[user_id] = {}

    current = 'subdomains' if user_settings[user_id].get('subdomains') else 'same-domain'

    if len(args) < 2:
        other = 'subdomains' if current == 'same-domain' else 'same-domain'
        await msg.reply(
            f"**Crawl Scope**\n\n"
            f"Current: `{current}`\n\n"
            f"Use `/scope {other}` to switch.\n\n"
            f"**Same domain** — Only crawls `{msg.text.split()[0]}` pages\n"
            f"**Subdomains** — Also crawls `blog.example.com`, `shop.example.com`, etc."
        )
        return

    target = args[1].strip().lower()
    if target in ('subdomains', 'sub', 'all'):
        user_settings[user_id]['subdomains'] = True
        await msg.reply("Scope set to **subdomains**. Subsequent crawls will include subdomains.")
    elif target in ('same', 'single', 'default', 'same-domain'):
        user_settings[user_id]['subdomains'] = False
        await msg.reply("Scope set to **same-domain**. Only the main domain will be crawled.")
    else:
        await msg.reply("Usage: `/scope same-domain` or `/scope subdomains`")


@bot.on_message(filters.private & filters.command('settings'))
async def settings_command(bot, msg):
    user_id = msg.from_user.id
    settings = user_settings.get(user_id, {})

    has_cookies = bool(settings.get('cookies_file'))
    scope = 'subdomains' if settings.get('subdomains') else 'same-domain'

    await msg.reply(
        f"**Your Settings**\n\n"
        f"**Scope** `{scope}`\n"
        f"**Cookies** `{'Active' if has_cookies else 'None'}`\n\n"
        "Change with:\n"
        "`/scope subdomains` or `/scope same-domain`\n"
        "`/cookies` to upload cookie file"
    )


@bot.on_message(filters.private & filters.command('info'))
async def info_command(bot, msg):
    user_id = msg.from_user.id
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        await msg.reply("Usage: `/info <url>`\nExample: `/info https://example.com`")
        return

    url = normalize_url(parts[1].strip())
    if not is_valid_url(url):
        await msg.reply(constants.ERROR_INVALID_URL)
        return

    processing_msg = await msg.reply("Fetching page info...")

    try:
        headers = get_random_headers()
        proxies = config.get_proxies()
        response = requests.get(url, timeout=config.REQUEST_TIMEOUT, headers=headers, proxies=proxies)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')
        metadata = extract_metadata(soup)
        meta_text = format_metadata(metadata)

        await msg.reply(
            f"**Page Info**\n\nURL: `{url}`\n\n{meta_text}",
            disable_web_page_preview=True
        )
        await processing_msg.delete()

    except Exception as e:
        await msg.reply(f"Failed: `{str(e)[:80]}`")
        logger.error(f"Info error for {url}: {e}")


@bot.on_message(filters.private & filters.command('media'))
async def media_command(bot, msg):
    user_id = msg.from_user.id
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        await msg.reply("Usage: `/media <url>`\nExample: `/media https://example.com`")
        return

    url = normalize_url(parts[1].strip())
    if not is_valid_url(url):
        await msg.reply(constants.ERROR_INVALID_URL)
        return

    processing_msg = await msg.reply("Scanning for media...")

    try:
        headers = get_random_headers()
        proxies = config.get_proxies()
        response = requests.get(url, timeout=config.REQUEST_TIMEOUT, headers=headers, proxies=proxies)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')
        domain = extract_domain(url)
        output_dir = f"media_{domain}_{user_id}"

        await processing_msg.edit("Downloading files...")
        downloaded = media_extractor.scrape_media(url, soup, output_dir, ['images'])

        if downloaded:
            await processing_msg.edit(f"Sending {len(downloaded)} files...")
            for filepath in downloaded[:5]:
                try:
                    await msg.reply_document(filepath)
                except Exception as e:
                    logger.error(f"Failed to send {filepath}: {e}")
            await msg.reply(f"Done! Downloaded `{len(downloaded)}` media files from `{domain}`")
        else:
            await msg.reply(f"No media found on `{domain}`")

        shutil.rmtree(output_dir, ignore_errors=True)
        await processing_msg.delete()
        db.increment_requests(user_id, success=True)

    except Exception as e:
        shutil.rmtree(output_dir, ignore_errors=True)
        await msg.reply(f"Failed: `{str(e)[:80]}`")
        logger.error(f"Media error for {url}: {e}")


# ── Main URL handler → Playwright crawl ───────────────────────────
@bot.on_message(filters.private & filters.regex(r"(?i)(https?://|[\w-]+\.\w{2,})"))
async def handle_url(bot, msg):
    user_id = msg.from_user.id
    raw_text = msg.text.strip()

    words = raw_text.lower().split()
    subdomains = 'subdomains' in words or 'subdomain' in words
    wants_cookies = 'cookies' in words and 'cookies' not in raw_text.replace('cookies', '', 1).lower()

    clean_url = raw_text
    for flag in ('subdomains', 'subdomain', 'cookies'):
        clean_url = re.sub(r'\b' + re.escape(flag) + r'\b', '', clean_url, flags=re.IGNORECASE).strip()
    url = normalize_url(clean_url)

    db.register_user(user_id, msg.from_user.username, msg.from_user.first_name)
    db.log_activity(user_id, "scrape_request", url)

    if db.is_banned(user_id):
        await msg.reply("You have been banned from using this bot.")
        db.record_block(user_id)
        return

    if not await check_force_join(user_id, msg):
        return
    db.increment_usage(user_id)

    if user_id in active_jobs:
        await msg.reply("You already have a running crawl. Send /cancel first.")
        return

    if not is_valid_url(url):
        await msg.reply(constants.ERROR_INVALID_URL)
        return

    domain = extract_domain(url)
    if not is_safe_domain(domain):
        await msg.reply("This domain has been blocked for safety reasons.")
        db.record_block(user_id)
        return

    settings = user_settings.get(user_id, {})
    scope = 'subdomains' if subdomains or settings.get('subdomains') else 'same-domain'
    saved_cookies = settings.get('cookies_file')
    use_cookies = saved_cookies if saved_cookies and os.path.exists(saved_cookies) else None

    if wants_cookies and not use_cookies:
        pending_cookie_requests[user_id] = {
            'url': url,
            'domain': domain,
            'scope': scope,
            'msg_id': msg.id,
        }
        await msg.reply(
            "Send me your **cookies.json** file.\n\n"
            "Export cookies from your browser using a browser extension "
            "(e.g. EditThisCookie, Cookie-Editor) and save as `cookies.json`.\n\n"
            "Send /cancel to abort."
        )
        return

    scope_label = 'Subdomains' if scope == 'subdomains' else 'Same domain'
    cookie_label = 'Yes' if use_cookies else 'No'

    logger.info(f"Crawl started by user {user_id}: {url} (scope={scope})")

    processing_msg = await msg.reply(
        f"**Starting clone**\n\n"
        f"**URL** `{url}`\n"
        f"**Scope** `{scope_label}`\n"
        f"**Cookies** `{cookie_label}`\n\n"
        "Launching Chromium...",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Cancel", callback_data="cancel_crawl")]
        ])
    )

    asyncio.create_task(
        _run_crawl(user_id, url, domain, processing_msg, bot, scope=scope, cookies_file=use_cookies)
    )


async def _run_crawl(user_id, url, domain, status_msg, bot, scope='same-domain', cookies_file=None):
    from bot.core.crawler import WebCloner

    output_dir = f"clone_{domain}_{user_id}"
    zip_path = f"clone_{domain}_{user_id}.zip"

    cloner = WebCloner(
        start_url=url,
        output_dir=output_dir,
        max_pages=config.CRAWL_MAX_PAGES,
        max_depth=config.CRAWL_MAX_DEPTH,
        concurrency=config.CRAWL_CONCURRENCY,
        page_timeout=config.CRAWL_PAGE_TIMEOUT,
        asset_timeout=config.CRAWL_ASSET_TIMEOUT,
        max_time=config.CRAWL_TIMEOUT_SECONDS,
        cookies_file=cookies_file,
        crawl_scope=scope,
    )
    active_jobs[user_id] = cloner

    try:
        progress_task = asyncio.create_task(_update_progress(cloner, status_msg, bot))

        await cloner.run()

        progress_task.cancel()

        if cloner.progress.cancelled:
            await _safe_edit(status_msg, "Clone cancelled.")
            active_jobs.pop(user_id, None)
            shutil.rmtree(output_dir, ignore_errors=True)
            return

        pages = cloner.progress.pages_downloaded
        assets = cloner.progress.assets_downloaded
        videos = cloner.progress.videos_downloaded
        errors = len(cloner.progress.errors)

        if pages == 0:
            await _safe_edit(status_msg, "Couldn't download any pages from this site.")
            active_jobs.pop(user_id, None)
            shutil.rmtree(output_dir, ignore_errors=True)
            return

        packing_msg = f"Packing {pages} pages, {assets} assets"
        if videos:
            packing_msg += f", {videos} videos"
        packing_msg += " into ZIP..."
        await _safe_edit(status_msg, packing_msg)

        create_zip(output_dir, zip_path)
        zip_size = os.path.getsize(zip_path)
        size_str = format_size(zip_size)

        if zip_size > config.CRAWL_MAX_ZIP_SIZE:
            await _safe_edit(status_msg, f"ZIP too large ({size_str}). Try a smaller site.")
            active_jobs.pop(user_id, None)
            shutil.rmtree(output_dir, ignore_errors=True)
            os.remove(zip_path)
            return

        caption = (
            f"**Clone complete**\n\n"
            f"**Domain** `{domain}`\n"
            f"**Pages** `{pages}`\n"
            f"**Assets** `{assets}`\n"
        )
        if videos:
            caption += f"**Videos** `{videos}`\n"
        caption += (
            f"**Size** `{size_str}`\n"
            f"**Errors** `{errors}`\n\n"
            f"Open `index.html` to browse the site locally."
        )

        try:
            await bot.send_document(
                user_id,
                zip_path,
                caption=caption,
            )
            await status_msg.delete()
        except Exception as e:
            await _safe_edit(status_msg, f"Failed to send ZIP: `{str(e)[:80]}`")

        db.increment_requests(user_id, success=True)
        db.log_activity(user_id, "crawl_complete", f"{domain} | {pages} pages | {assets} assets")

    except Exception as e:
        logger.error(f"Crawl failed for {url}: {e}", exc_info=True)
        try:
            await status_msg.edit(f"Clone failed: `{str(e)[:100]}`")
        except Exception:
            pass
        db.increment_requests(user_id, success=False)

    finally:
        active_jobs.pop(user_id, None)
        shutil.rmtree(output_dir, ignore_errors=True)
        if os.path.exists(zip_path):
            os.remove(zip_path)


async def _update_progress(cloner, status_msg, bot):
    """Periodically update the Telegram status message with crawl progress."""
    while True:
        await asyncio.sleep(5)
        try:
            text = (
                f"**Cloning...**\n\n"
                f"{cloner.progress.to_text()}\n\n"
                f"_Updates every 5s — /cancel to stop_"
            )
            await status_msg.edit(text)
        except Exception:
            pass


async def _safe_edit(msg, text):
    try:
        await msg.edit(text)
    except Exception:
        pass


# ── /admin ─────────────────────────────────────────────────────────
@bot.on_message(filters.private & filters.command('admin'))
async def admin_command(bot, msg):
    user_id = msg.from_user.id
    if user_id not in config.ADMIN_IDS:
        await msg.reply(constants.ERROR_PERMISSION_DENIED)
        return

    stats = db.get_statistics()
    uptime = datetime.now() - bot_start_time
    uptime_str = str(uptime).split('.')[0]

    from bot.monitoring.performance import get_performance_stats
    perf_stats = get_performance_stats()

    admin_text = f"""
**Admin Panel**

Users: `{stats['total_users']}`
Requests: `{stats['total_requests']}`
Errors: `{stats['total_errors']}`
Avg/User: `{(stats['total_requests'] / stats['total_users']) if stats['total_users'] > 0 else 0:.2f}`
Uptime: `{uptime_str}`
Cache hit: `{perf_stats['cache_hit_rate']:.1f}%`
Active crawls: `{len(active_jobs)}`

**Commands:**
/broadcast — Broadcast to all users
/clearcache — Clear cached content
/users — List all users
/topusers — Top 10 users
/userinfo — User details
/ban — Ban a user
/unban — Unban a user
/promote — Send promotional broadcast
/logs — View activity logs
"""
    await msg.reply(admin_text)


# ── Broadcast system ───────────────────────────────────────────────
broadcast_drafts = {}


async def _bcast_show_target(bot, user_id, edit_msg_id):
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("All Users", callback_data="bcast_target_all")],
        [InlineKeyboardButton("Active (24h)", callback_data="bcast_target_active")],
        [InlineKeyboardButton("Cancel", callback_data="bcast_cancel")],
    ])
    await bot.edit_message_text("Broadcast — Target audience:", user_id, edit_msg_id, reply_markup=buttons)


async def _bcast_show_format(bot, user_id, edit_msg_id):
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("Plain Text", callback_data="bcast_fmt_plain")],
        [InlineKeyboardButton("Markdown", callback_data="bcast_fmt_md")],
        [InlineKeyboardButton("HTML", callback_data="bcast_fmt_html")],
        [InlineKeyboardButton("With Buttons", callback_data="bcast_fmt_button")],
        [InlineKeyboardButton("Back", callback_data="bcast_back")],
    ])
    await bot.edit_message_text("Broadcast — Message format:", user_id, edit_msg_id, reply_markup=buttons)


async def _bcast_prompt_content(bot, user_id, edit_msg_id, is_button=False):
    hint = (
        "Send me the **message content** now.\n"
        "You can use any Telegram formatting. I'll wait up to 2 minutes."
    ) if not is_button else (
        "Send me the **message content** first, then I'll ask for button config."
    )
    await bot.edit_message_text(
        f"Broadcast — Content\n\n{hint}\n\n_Send your message as a reply to this._",
        user_id, edit_msg_id
    )
    broadcast_drafts[user_id]["awaiting_content"] = True


async def _bcast_prompt_buttons(bot, user_id, edit_msg_id):
    await bot.edit_message_text(
        "Button Configuration\n\n"
        "Send button lines (one per line):\n"
        "`Button Label | https://url.com`\n\n"
        "Send `/done` when finished, or `/skip` for no buttons.",
        user_id, edit_msg_id
    )
    broadcast_drafts[user_id]["awaiting_buttons"] = True


async def _bcast_show_preview(bot, user_id, edit_msg_id):
    draft = broadcast_drafts.get(user_id)
    if not draft:
        return
    target_label = "All Users" if draft["target"] == "all" else "Active Users (24h)"
    fmt_label = {"plain": "Plain", "md": "Markdown", "html": "HTML", "button": "With Buttons"}.get(draft["format"], "Plain")
    preview = draft["content"] or "_(empty)_"
    buttons = draft.get("buttons", [])
    btn_preview = ""
    if buttons:
        rows = [f"`{b.split('|',1)[0].strip()}`" for b in buttons]
        btn_preview = "\nButtons: " + ", ".join(rows)

    text = (
        f"**Broadcast Preview**\n\n"
        f"Target: `{target_label}`\n"
        f"Format: `{fmt_label}`\n"
        f"Users: `{draft['user_count']}`\n\n"
        f"Message:\n{preview[:500]}{btn_preview}\n\n"
        f"Ready to send?"
    )
    btns = [
        [InlineKeyboardButton("Confirm", callback_data="bcast_confirm"),
         InlineKeyboardButton("Cancel", callback_data="bcast_cancel")],
        [InlineKeyboardButton("Back", callback_data="bcast_back")],
    ]
    await bot.edit_message_text(text, user_id, edit_msg_id, reply_markup=InlineKeyboardMarkup(btns))


async def _bcast_send(draft):
    target_users = draft.get("_user_ids", [])
    content = draft["content"]
    buttons = draft.get("buttons", [])
    fmt = draft["format"]

    parse_mode = None
    if fmt == "md":
        parse_mode = "markdown"
    elif fmt == "html":
        parse_mode = "html"

    reply_markup = None
    if buttons:
        kb = [[InlineKeyboardButton(label.strip(), url=url.strip())]
              for label, url in (b.split("|", 1) for b in buttons)]
        reply_markup = InlineKeyboardMarkup(kb)

    success, failed = 0, 0

    for uid in target_users:
        try:
            await bot.send_message(uid, content, parse_mode=parse_mode,
                                   reply_markup=reply_markup, disable_web_page_preview=True)
            success += 1
        except Exception:
            failed += 1

    return success, failed


@bot.on_message(filters.private & filters.command('broadcast'))
async def broadcast_command(bot, msg):
    user_id = msg.from_user.id
    if user_id not in config.ADMIN_IDS:
        await msg.reply(constants.ERROR_PERMISSION_DENIED)
        return
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("All Users", callback_data="bcast_target_all")],
        [InlineKeyboardButton("Active (24h)", callback_data="bcast_target_active")],
        [InlineKeyboardButton("Cancel", callback_data="bcast_cancel")],
    ])
    m = await msg.reply("Broadcast — Target audience:", reply_markup=buttons)
    broadcast_drafts[user_id] = {"msg_id": m.id}


@bot.on_message(filters.private & filters.command('promote'))
async def promote_command(bot, msg):
    user_id = msg.from_user.id
    if user_id not in config.ADMIN_IDS:
        await msg.reply(constants.ERROR_PERMISSION_DENIED)
        return
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("All Users", callback_data="bcast_target_all")],
        [InlineKeyboardButton("Active (24h)", callback_data="bcast_target_active")],
        [InlineKeyboardButton("Cancel", callback_data="bcast_cancel")],
    ])
    m = await msg.reply("Promotional Broadcast — Target:", reply_markup=buttons)
    broadcast_drafts[user_id] = {"msg_id": m.id, "format": "button"}
    await _bcast_show_target(bot, user_id, m.id)


@bot.on_message(filters.private & filters.command('clearcache'))
async def clearcache_command(bot, msg):
    user_id = msg.from_user.id
    if user_id not in config.ADMIN_IDS:
        await msg.reply(constants.ERROR_PERMISSION_DENIED)
        return
    try:
        cache.clear_expired_cache()
        await msg.reply("Cache cleared.")
    except Exception as e:
        await msg.reply(f"Failed: {e}")


@bot.on_message(filters.private & filters.command('users'))
async def users_command(bot, msg):
    user_id = msg.from_user.id
    if user_id not in config.ADMIN_IDS:
        await msg.reply(constants.ERROR_PERMISSION_DENIED)
        return
    stats = db.get_statistics()
    all_users = db.get_all_users()
    lines = []
    for i, u in enumerate(all_users[:20], 1):
        name = u['first_name'] or u['username'] or f"User {u['user_id']}"
        status = "Banned" if not u['is_active'] else "Active"
        lines.append(f"{i}. [{status}] `{u['user_id']}` {name} - {u['total_requests']} req")

    await msg.reply(
        f"**User Management**\n\n"
        f"Total: `{stats['total_users']}` | Active (24h): `{stats['active_24h']}`\n"
        f"Requests: `{stats['total_requests']}` | Errors: `{stats['total_errors']}`\n\n"
        f"**Recent:**\n{chr(10).join(lines) if lines else 'No users'}"
    )


@bot.on_message(filters.private & filters.command('topusers'))
async def topusers_command(bot, msg):
    user_id = msg.from_user.id
    if user_id not in config.ADMIN_IDS:
        await msg.reply(constants.ERROR_PERMISSION_DENIED)
        return
    top = db.get_top_users(10)
    lines = []
    for i, u in enumerate(top, 1):
        name = u['first_name'] or u['username'] or f"User {u['user_id']}"
        lines.append(f"{i}. `{u['user_id']}` {name} - {u['total_requests']} req ({u['total_errors']} err)")
    await msg.reply(f"**Top 10 Users**\n\n{chr(10).join(lines) if lines else 'No data'}")


@bot.on_message(filters.private & filters.command('userinfo'))
async def userinfo_command(bot, msg):
    user_id = msg.from_user.id
    if user_id not in config.ADMIN_IDS:
        await msg.reply(constants.ERROR_PERMISSION_DENIED)
        return
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        await msg.reply("Usage: /userinfo <user_id>")
        return
    try:
        target = int(parts[1])
        u = db.get_user(target)
        if not u or not u['first_seen']:
            await msg.reply(f"User {target} not found.")
            return
        name = u['first_name'] or u['username'] or f"User {target}"
        status = "Banned" if not u['is_active'] else "Active"
        total, errs = u['total_requests'], u['total_errors']
        rate = ((total - errs) / total * 100) if total > 0 else 0.0
        await msg.reply(
            f"**User Info**\n\n"
            f"ID: `{target}`\nName: {name}\nUsername: @{u['username'] or 'N/A'}\nStatus: {status}\n\n"
            f"Requests: `{total}` | Errors: `{errs}` | Blocks: `{u['blocked_count']}`\n"
            f"Success: `{rate:.1f}%`\n"
            f"First seen: `{u['first_seen']}` | Last seen: `{u['last_seen']}`"
        )
    except ValueError:
        await msg.reply("Invalid ID.")


@bot.on_message(filters.private & filters.command('ban'))
async def ban_command(bot, msg):
    user_id = msg.from_user.id
    if user_id not in config.ADMIN_IDS:
        await msg.reply(constants.ERROR_PERMISSION_DENIED)
        return
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        await msg.reply("Usage: /ban <user_id>")
        return
    try:
        target = int(parts[1])
        if target in config.ADMIN_IDS:
            await msg.reply("Cannot ban an admin.")
            return
        db.ban_user(target)
        await msg.reply(f"User {target} banned.")
        try:
            await bot.send_message(target, "You have been banned.")
        except Exception:
            pass
    except ValueError:
        await msg.reply("Invalid ID.")


@bot.on_message(filters.private & filters.command('unban'))
async def unban_command(bot, msg):
    user_id = msg.from_user.id
    if user_id not in config.ADMIN_IDS:
        await msg.reply(constants.ERROR_PERMISSION_DENIED)
        return
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        await msg.reply("Usage: /unban <user_id>")
        return
    try:
        target = int(parts[1])
        db.unban_user(target)
        await msg.reply(f"User {target} unbanned.")
        try:
            await bot.send_message(target, "You have been unbanned.")
        except Exception:
            pass
    except ValueError:
        await msg.reply("Invalid ID.")


@bot.on_message(filters.private & filters.command('logs'))
async def logs_command(bot, msg):
    user_id = msg.from_user.id
    if user_id not in config.ADMIN_IDS:
        await msg.reply(constants.ERROR_PERMISSION_DENIED)
        return
    parts = msg.text.split(maxsplit=1)
    if len(parts) >= 2:
        try:
            target = int(parts[1])
            rows = db.get_user_activities(target, 20)
            lines = [f"{r['timestamp'][:19]} | {r['action']} | {r['details'] or ''}" for r in rows]
            await msg.reply(f"**Logs for User {target}:**\n\n```\n{chr(10).join(lines) or 'No logs'}\n```")
        except ValueError:
            await msg.reply("Invalid ID.")
    else:
        rows = db.get_recent_activities(30)
        lines = [f"{r['timestamp'][:19]} | U{r['user_id']} | {r['action']}" for r in rows]
        await msg.reply(f"**Recent Logs:**\n\n```\n{chr(10).join(lines) or 'No logs'}\n```")


# ── Broadcast content handler (must come after command handlers) ──
@bot.on_message(filters.private & filters.text)
async def broadcast_content_handler(bot, msg):
    user_id = msg.from_user.id
    draft = broadcast_drafts.get(user_id)
    if not draft:
        return

    if draft.get("awaiting_buttons"):
        text = msg.text.strip()
        if text == "/done":
            draft["awaiting_buttons"] = False
            await _bcast_show_preview(bot, user_id, draft["msg_id"])
        elif text == "/skip":
            draft["buttons"] = []
            draft["awaiting_buttons"] = False
            await _bcast_show_preview(bot, user_id, draft["msg_id"])
        else:
            lines = text.split("\n")
            buttons = []
            for line in lines:
                line = line.strip()
                if "|" in line:
                    label, url = line.split("|", 1)
                    buttons.append(f"{label.strip()}|{url.strip()}")
            draft["buttons"] = buttons
            draft["awaiting_buttons"] = False
            await _bcast_show_preview(bot, user_id, draft["msg_id"])
        return True

    if draft.get("awaiting_content"):
        draft["content"] = msg.text
        draft["awaiting_content"] = False
        if draft.get("format") == "button":
            await _bcast_prompt_buttons(bot, user_id, draft["msg_id"])
        else:
            await _bcast_show_preview(bot, user_id, draft["msg_id"])
        return True


@bot.on_message(filters.private & filters.text)
async def fallback_handler(bot, msg):
    if broadcast_drafts.get(msg.from_user.id):
        return
    if msg.text.startswith("/"):
        return
    if is_valid_url(msg.text.strip()):
        return
    await msg.reply(
        "Send me a URL to clone!\n\n"
        "Example: `https://example.com`\n\n"
        "Type /help for more info.",
        disable_web_page_preview=True,
        quote=True
    )
