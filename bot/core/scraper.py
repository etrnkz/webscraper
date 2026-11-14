import os
from dotenv import load_dotenv
load_dotenv()

import asyncio
import re
import requests
import logging
import shutil
import time
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
from hydrogram import Client, filters
from hydrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from hydrogram.types import Message
from concurrent.futures import ThreadPoolExecutor
from bot.services.botapi import upgrade_premium_markup

# Import queue system
from bot.errors import BrowserLaunchError
from bot.queue.crawl_queue import CrawlQueue, CrawlJob, Outcome, JobState
from bot.queue.notifier import JobNotifier
from bot.queue.resources import read_resources, derive_capacity

config.validate_config()
cache.init_cache()

logging.basicConfig(level=config.LOG_LEVEL, format=config.LOG_FORMAT)
logger = logging.getLogger(__name__)

API_ID = config.API_ID
API_HASH = config.API_HASH
BOT_TOKEN = config.BOT_TOKEN

bot = Client(
    'my_bot',
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    ipv6=False
)

force_subscribe = ForceSubscribe(bot, config.FORCE_SUBSCRIBE_CHANNELS)
if config.FORCE_JOIN_CHANNEL and config.FORCE_JOIN_CHANNEL not in force_subscribe.channel_ids:
    force_subscribe.add_channel(config.FORCE_JOIN_CHANNEL)

bot_start_time = datetime.now()

# User settings (persist across restarts)
user_settings: dict[int, dict] = {}  # user_id -> {cookies_file, subdomains}

# Queue and notifier (initialized at module level)
def _make_runner():
    """Create runner function that wraps _run_crawl in a task."""
    async def runner(job: CrawlJob):
        return asyncio.create_task(_run_crawl(job))
    return runner

def _charge_usage(user_id: int) -> None:
    db.increment_usage(user_id)

def _get_usage(user_id: int) -> int:
    return db.get_usage_count(user_id)

crawl_queue = CrawlQueue(
    runner=_make_runner(),
    charge=_charge_usage,
    usage_of=_get_usage,
    max_queue_size=config.CRAWL_MAX_QUEUE_SIZE,
    max_concurrent=config.CRAWL_MAX_CONCURRENT,
    memory_budget_mb=config.CRAWL_MEMORY_BUDGET_MB,
    memory_reserve_mb=config.CRAWL_MEMORY_RESERVE_MB,
    retry_limit=config.CRAWL_BROWSER_RETRY_LIMIT,
    retry_base_seconds=config.CRAWL_BROWSER_RETRY_BASE_SECONDS,
    launch_failure_threshold=config.CRAWL_LAUNCH_FAILURE_THRESHOLD,
    launch_suspend_seconds=config.CRAWL_LAUNCH_SUSPEND_SECONDS,
    max_queue_age_minutes=config.CRAWL_MAX_QUEUE_AGE_MINUTES,
    estimate_minutes_per_crawl=config.CRAWL_ESTIMATE_MINUTES_PER_CRAWL,
    position_refresh_seconds=config.CRAWL_POSITION_REFRESH_SECONDS,
    position_refresh_seconds_long=config.CRAWL_POSITION_REFRESH_SECONDS_LONG,
    position_refresh_top_n=config.CRAWL_POSITION_REFRESH_TOP_N,
)

telegram_notifier = TelegramNotifier(bot)
crawl_queue.set_notifier(telegram_notifier)

# Background tasks
_background_tasks: list[asyncio.Task] = []


class TelegramNotifier:
    """Telegram implementation of JobNotifier.

    Edits the job's existing status message rather than sending new ones.
    """

    def __init__(self, bot_client: Client):
        self.bot = bot_client

    async def _edit(self, chat_id: int, message_id: int | None, text: str, reply_markup=None) -> None:
        if message_id is None:
            return
        try:
            await self.bot.edit_message_text(chat_id, message_id, text, reply_markup=reply_markup)
        except Exception as e:
            logger.debug("Failed to edit message %s/%s: %s", chat_id, message_id, e)

    async def on_queued(self, job: CrawlJob, position: int, wait_seconds: int) -> None:
        wait_min = max(1, (wait_seconds + 59) // 60)
        text = constants.QUEUE_WAITING.format(position=position, wait_min=wait_min)
        await self._edit(job.chat_id, job.message_id, text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Cancel", callback_data="cancel_crawl")]
        ]))

    async def on_started(self, job: CrawlJob) -> None:
        scope_label = 'Subdomains' if job.scope == 'subdomains' else 'Same domain'
        cookie_label = 'Yes' if job.cookies_file else 'No'
        text = (
            f"**Starting clone**\n\n"
            f"**URL** `{job.url}`\n"
            f"**Scope** `{scope_label}`\n"
            f"**Cookies** `{cookie_label}`\n\n"
            "Launching Chromium..."
        )
        await self._edit(job.chat_id, job.message_id, text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Cancel", callback_data="cancel_crawl")]
        ]))

    async def on_retry(self, job: CrawlJob, attempt: int, delay_seconds: int, kind: str) -> None:
        text = constants.SERVER_BUSY_RETRY.format(
            delay=delay_seconds,
            attempt=attempt,
            max_attempts=config.CRAWL_BROWSER_RETRY_LIMIT
        )
        await self._edit(job.chat_id, job.message_id, text)

    async def on_deferred(self, job: CrawlJob) -> None:
        text = constants.DEFERRED
        await self._edit(job.chat_id, job.message_id, text)

    async def on_failed(self, job: CrawlJob, reason: str) -> None:
        text = f"Clone failed: `{reason[:100]}`"
        await self._edit(job.chat_id, job.message_id, text)

    async def on_cancelled(self, job: CrawlJob, charged: bool) -> None:
        if charged:
            text = constants.CANCELLED_RUNNING
        else:
            text = constants.CANCELLED_QUEUED
        await self._edit(job.chat_id, job.message_id, text)

    async def on_expired(self, job: CrawlJob) -> None:
        text = constants.EXPIRED
        await self._edit(job.chat_id, job.message_id, text)


async def check_force_join(user_id: int, msg: Message) -> bool:
    """Check if user must join required channels.

    Only applies to users who have exhausted their free allowance.
    """
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
        if crawl_queue is None:
            await callback_query.answer("Queue not ready.", show_alert=True)
            return
        state, charged = crawl_queue.cancel(user_id)
        if state == "none":
            await callback_query.answer("No active crawl.", show_alert=True)
        else:
            await callback_query.answer(f"Cancelled ({state}).", show_alert=True)

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
    logger.info("START_HANDLER | user_id=%s | username=%s | first_name=%s", user_id, msg.from_user.username, first_name)
    db.register_user(user_id, msg.from_user.username, first_name)

    # Premium welcome via entities (like AltaMovies)
    from bot.utils.premium_text import PremiumText
    pt = PremiumText()
    pt.add(f"Welcome {first_name}! ").emoji("WEB").add("\n\n")
    pt.add("I clone entire websites into offline ZIP files\n\n")
    pt.bold("Features").add("\n")
    pt.emoji("STAR").add(" Full JS rendering with Chromium\n")
    pt.emoji("STAR").add(" Auto-downloads videos (YouTube, TikTok, etc.)\n")
    pt.emoji("STAR").add(" Captures lazy-loaded content\n")
    pt.emoji("STAR").add(" Cookie auth for login-protected sites\n")
    pt.emoji("STAR").add(" Smart page prioritization\n")
    pt.emoji("STAR").add(" Offline link rewriting\n")
    pt.add("\nSend /help for full details.")
    text, entities = pt.build()

    # Buttons with premium icons
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("ℹ️ Help", callback_data="help"),
         InlineKeyboardButton("💜 Stats", callback_data="stats")],
        [InlineKeyboardButton("💻 Developer", url="https://t.me/etrnkx")],
    ])

    sent = await msg.reply_text(text, entities=entities, reply_markup=buttons)
    try:
        await upgrade_premium_markup(sent, buttons)
    except Exception:
        pass


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
    if crawl_queue is None:
        await msg.reply("Queue not ready.")
        return
    state, charged = crawl_queue.cancel(user_id)
    if state == "none":
        await msg.reply("No active crawl to cancel.")
    else:
        await msg.reply(f"Cancelled ({state}).")


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

    # Force-join gate (only for users past free allowance)
    if not await check_force_join(user_id, msg):
        return

    # Validate URL and domain BEFORE queue submission
    if not is_valid_url(url):
        await msg.reply(constants.ERROR_INVALID_URL)
        return

    domain = extract_domain(url)
    if not is_safe_domain(domain):
        await msg.reply("This domain has been blocked for safety reasons.")
        db.record_block(user_id)
        return

    # Resolve settings
    settings = user_settings.get(user_id, {})
    scope = 'subdomains' if subdomains or settings.get('subdomains') else 'same-domain'
    saved_cookies = settings.get('cookies_file')
    use_cookies = saved_cookies if saved_cookies and os.path.exists(saved_cookies) else None

    # Cookie prompt (before queue)
    if wants_cookies and not use_cookies:
        await msg.reply(
            "Send me your **cookies.json** file.\n\n"
            "Export cookies from your browser using a browser extension "
            "(e.g. EditThisCookie, Cookie-Editor) and save as `cookies.json`.\n\n"
            "Send /cancel to abort."
        )
        # Note: cookie flow is not implemented for queued jobs; user must re-send URL with cookies
        return

    # Build job
    import uuid
    job = CrawlJob(
        job_id=str(uuid.uuid4())[:8],
        user_id=user_id,
        url=url,
        domain=domain,
        scope=scope,
        cookies_file=use_cookies,
        chat_id=msg.chat.id,
        message_id=None,  # Will be set after first message
        sequence=int(datetime.now().timestamp() * 1000) % 1_000_000_000,
        enqueued_at=0.0,
    )

    # Submit to queue
    if crawl_queue is None:
        await msg.reply("Server starting up, please try again in a moment.")
        return

    admission = crawl_queue.submit(job)

    # Handle admission outcomes
    if admission.outcome == Outcome.REJECTED_DUPLICATE:
        existing = crawl_queue._by_user.get(user_id)
        if existing and existing.message_id:
            await msg.reply(
                "You already have a crawl in progress or queued. "
                "Use /cancel to stop it.",
                reply_to_message_id=msg.id
            )
        else:
            await msg.reply("You already have a crawl in progress or queued. Use /cancel first.")
        return

    if admission.outcome == Outcome.REJECTED_QUEUE_FULL:
        await msg.reply(constants.QUEUE_FULL)
        return

    if admission.outcome == Outcome.REJECTED_USAGE:
        # Should not happen: force-join gate runs first
        await msg.reply("Usage limit reached. Join the required channel to continue.")
        return

    # Send initial status message
    if admission.outcome == Outcome.ADMITTED:
        scope_label = 'Subdomains' if scope == 'subdomains' else 'Same domain'
        cookie_label = 'Yes' if use_cookies else 'No'
        text = (
            f"**Starting clone**\n\n"
            f"**URL** `{url}`\n"
            f"**Scope** `{scope_label}`\n"
            f"**Cookies** `{cookie_label}`\n\n"
            "Launching Chromium..."
        )
    else:  # QUEUED
        wait_min = max(1, (admission.estimated_wait_seconds + 59) // 60)
        text = constants.QUEUE_WAITING.format(
            position=admission.position,
            wait_min=wait_min
        )

    processing_msg = await msg.reply(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Cancel", callback_data="cancel_crawl")]
        ])
    )

    # Record message_id and launch
    job.message_id = processing_msg.id
    crawl_queue.launch(job)

    # For admitted jobs, notify started
    if admission.outcome == Outcome.ADMITTED:
        await telegram_notifier.on_started(job)


# ── Crawl runner ───────────────────────────────────────────────────

async def _run_crawl(job: CrawlJob):
    """Queue runner: launches crawler, handles retries, charges usage, uploads."""
    from bot.core.crawler import WebCloner

    # Wait for status message to exist
    await job.status_ready.wait()

    # Check if cancelled before we even start
    if job.cancel_requested:
        crawl_queue.complete(job)
        return

    # Provisional admission check (capacity may have dropped)
    if not crawl_queue.may_start(job):
        crawl_queue.defer(job)
        await telegram_notifier.on_deferred(job)
        return

    # Resolve concurrency from queue's current capacity
    workers_per_crawl = crawl_queue.get_workers_per_crawl()

    # Unique output paths per job
    output_dir = f"clone_{job.domain}_{job.user_id}_{job.job_id}"
    zip_path = f"clone_{job.domain}_{job.user_id}_{job.job_id}.zip"

    cloner = WebCloner(
        start_url=job.url,
        output_dir=output_dir,
        max_pages=config.CRAWL_MAX_PAGES,
        max_depth=config.CRAWL_MAX_DEPTH,
        concurrency=workers_per_crawl,
        page_timeout=config.CRAWL_PAGE_TIMEOUT,
        asset_timeout=config.CRAWL_ASSET_TIMEOUT,
        max_time=config.CRAWL_TIMEOUT_SECONDS,
        cookies_file=job.cookies_file,
        crawl_scope=job.scope,
    )

    # Set cancel callback
    job.cancel_running = cloner.cancel

    # Track progress task for cleanup
    progress_task: asyncio.Task | None = None

    try:
        # Notify started
        await telegram_notifier.on_started(job)

        # Progress updater
        async def _progress_updater():
            while True:
                await asyncio.sleep(5)
                try:
                    if cloner.progress.cancelled:
                        break
                    text = (
                        f"**Cloning...**\n\n"
                        f"{cloner.progress.to_text()}\n\n"
                        f"_Updates every 5s — /cancel to stop_"
                    )
                    await bot.edit_message_text(job.chat_id, job.message_id, text)
                except Exception:
                    break

        progress_task = asyncio.create_task(_progress_updater())

        # Run crawl
        await cloner.run()

        # Check for cancellation after crawl
        if cloner.progress.cancelled:
            await telegram_notifier.on_cancelled(job, charged=job.charged)
            crawl_queue.complete(job)
            # Cleanup off-loop
            await asyncio.to_thread(shutil.rmtree, output_dir, ignore_errors=True)
            if os.path.exists(zip_path):
                await asyncio.to_thread(os.remove, zip_path)
            return

        # Crawl succeeded - record duration (held window: admission to archive built)
        duration = time.monotonic() - job.enqueued_at if job.enqueued_at else 0
        crawl_queue.record_duration(job, duration)

        # Get stats
        pages = cloner.progress.pages_downloaded
        assets = cloner.progress.assets_downloaded
        videos = cloner.progress.videos_downloaded
        errors = len(cloner.progress.errors)

        if pages == 0:
            await telegram_notifier.on_failed(job, "Couldn't download any pages")
            crawl_queue.complete(job)
            await asyncio.to_thread(shutil.rmtree, output_dir, ignore_errors=True)
            return

        # Release slot BEFORE packing/upload (packing is I/O, not CPU/RAM bound)
        crawl_queue.release_slot(job)

        # Pack ZIP off the event loop
        await asyncio.to_thread(create_zip, output_dir, zip_path)
        zip_size = await asyncio.to_thread(os.path.getsize, zip_path)
        size_str = format_size(zip_size)

        if zip_size > config.CRAWL_MAX_ZIP_SIZE:
            await telegram_notifier.on_failed(job, f"ZIP too large ({size_str})")
            crawl_queue.complete(job)
            await asyncio.to_thread(shutil.rmtree, output_dir, ignore_errors=True)
            await asyncio.to_thread(os.remove, zip_path)
            return

        # Upload
        caption = (
            f"**Clone complete**\n\n"
            f"**Domain** `{job.domain}`\n"
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
                job.user_id,
                zip_path,
                caption=caption,
            )
            # Delete status message after successful delivery
            try:
                await bot.delete_messages(job.chat_id, job.message_id)
            except Exception:
                pass

            crawl_queue.complete(job)
            db.log_activity(job.user_id, "crawl_complete", f"{job.domain} | {pages} pages | {assets} assets")

        except Exception as e:
            await telegram_notifier.on_failed(job, f"Failed to send ZIP: {str(e)[:80]}")
            crawl_queue.complete(job)

    except BrowserLaunchError as e:
        # Launch failed - record and let queue handle retry
        crawl_queue.record_launch_failure(job, e.kind)
        job.launch_attempts += 1

        if job.launch_attempts >= config.CRAWL_BROWSER_RETRY_LIMIT:
            # Retry budget exhausted
            kind_name = e.kind.value.replace('_', ' ')
            await telegram_notifier.on_failed(job, f"Launch failed after {job.launch_attempts} attempts ({kind_name})")
            crawl_queue.complete(job)
            # Cleanup off-loop
            await asyncio.to_thread(shutil.rmtree, output_dir, ignore_errors=True)
            if os.path.exists(zip_path):
                await asyncio.to_thread(os.remove, zip_path)
        else:
            # Re-queue with backoff
            delay = config.CRAWL_BROWSER_RETRY_BASE_SECONDS * (2 ** (job.launch_attempts - 1))
            job.not_before = time.monotonic() + delay
            job.state = JobState.QUEUED
            crawl_queue._pending.append(job)
            crawl_queue._pending.sort(key=lambda j: j.sequence)
            await telegram_notifier.on_retry(job, job.launch_attempts, delay, e.kind.value)
            crawl_queue.pump()

    except Exception as e:
        logger.error(f"Crawl failed for {job.url}: {e}", exc_info=True)
        await telegram_notifier.on_failed(job, str(e)[:100])
        crawl_queue.complete(job)

    finally:
        if progress_task:
            progress_task.cancel()
            try:
                await progress_task
            except asyncio.CancelledError:
                pass
        # Final cleanup (best effort)
        await asyncio.to_thread(shutil.rmtree, output_dir, ignore_errors=True)
        if os.path.exists(zip_path):
            await asyncio.to_thread(os.remove, zip_path)


# ── Background tasks ───────────────────────────────────────────────

async def _resource_probe():
    """Probe resources and update queue capacity."""
    while True:
        try:
            reading = read_resources()
            capacity, workers = derive_capacity(
                reading,
                config.CRAWL_MAX_CONCURRENT,
                config.CRAWL_MEMORY_BUDGET_MB,
                config.CRAWL_MEMORY_RESERVE_MB,
            )
            if crawl_queue:
                crawl_queue.set_capacity(capacity, workers)
        except Exception as e:
            logger.error(f"Resource probe error: {e}")
        await asyncio.sleep(config.CRAWL_RESOURCE_PROBE_INTERVAL)


async def _position_refresh():
    """Refresh queue position messages."""
    while True:
        try:
            if crawl_queue and telegram_notifier:
                updates = crawl_queue.position_updates()
                for job, position, wait_seconds in updates:
                    # Throttle: top N get fast cadence, rest get slow
                    is_top = position <= config.CRAWL_POSITION_REFRESH_TOP_N
                    interval = (config.CRAWL_POSITION_REFRESH_SECONDS if is_top
                                else config.CRAWL_POSITION_REFRESH_SECONDS_LONG)
                    # Only refresh if enough time passed
                    if position != job.last_advertised_position:
                        await telegram_notifier.on_queued(job, position, wait_seconds)
                        job.last_advertised_position = position
                        job.last_advertised_wait = wait_seconds
        except Exception as e:
            logger.error(f"Position refresh error: {e}")
        await asyncio.sleep(config.CRAWL_POSITION_REFRESH_SECONDS)


async def _maintenance():
    """Periodic maintenance: expire stale jobs, cleanup DB, sweep orphans."""
    # Startup: sweep orphaned artifacts and reap Chromium
    await _startup_sweep()

    while True:
        try:
            if crawl_queue:
                # Expire stale queue entries
                expired = crawl_queue.expire_stale(telegram_notifier)
                if expired:
                    logger.info("Expired %d stale queue entries", expired)

            # Database cleanup
            db.cleanup_old_data()

            # Sweep orphaned artifacts
            await _sweep_orphans()

        except Exception as e:
            logger.error(f"Maintenance error: {e}")

        await asyncio.sleep(config.CLEANUP_INTERVAL_SECONDS)


async def _startup_sweep():
    """Sweep orphaned artifacts and reap Chromium on startup."""
    import glob
    from pathlib import Path

    # Sweep clone_* directories and ZIPs older than threshold
    max_age = config.CRAWL_ORPHAN_MAX_AGE_MINUTES * 60
    now = time.time()

    for pattern in ("clone_*", "clone_*.zip"):
        for path_str in glob.glob(pattern):
            path = Path(path_str)
            try:
                if path.is_dir():
                    mtime = path.stat().st_mtime
                else:
                    mtime = path.stat().st_mtime
                if now - mtime > max_age:
                    if path.is_dir():
                        await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)
                    else:
                        await asyncio.to_thread(os.remove, path)
                    logger.info("Swept orphan: %s", path)
            except Exception as e:
                logger.debug("Failed to sweep %s: %s", path, e)

    # Reap orphaned Chromium processes
    await _reap_chromium()


async def _reap_chromium():
    """Kill orphaned Chromium processes from previous runs."""
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'exe', 'cmdline']):
            try:
                exe = proc.info.get('exe') or ''
                cmdline = proc.info.get('cmdline') or []
                if 'chromium' in exe.lower() and '--remote-debugging-pipe' in ' '.join(cmdline):
                    proc.kill()
                    logger.info("Reaped orphaned Chromium PID %s", proc.info['pid'])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception as e:
        logger.debug("Chromium reap failed: %s", e)


async def _sweep_orphans():
    """Periodic orphan sweep (startup sweep is primary; this is safety net)."""
    # Note: periodic sweep can delete live crawls if they run longer than
    # CRAWL_ORPHAN_MAX_AGE_MINUTES. The startup sweep is the safe one.
    # This is kept minimal - only sweeps very old artifacts.
    pass


def start_background_tasks() -> list[asyncio.Task]:
    """Start all background tasks and return them for shutdown."""
    tasks = [
        asyncio.create_task(_resource_probe()),
        asyncio.create_task(_position_refresh()),
        asyncio.create_task(_maintenance()),
    ]
    # Supervise each task
    for task in tasks:
        task.add_done_callback(_task_done_callback)
    return tasks


def _task_done_callback(task: asyncio.Task):
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error("Background task crashed: %s", e, exc_info=True)


async def cancel_all_crawl_tasks() -> None:
    """Cancel all running crawl tasks (for shutdown)."""
    if crawl_queue:
        await crawl_queue.shutdown()


async def _notify_restarted_users():
    """Notify users who were waiting when bot restarted."""
    if crawl_queue:
        for job in crawl_queue._by_user.values():
            if job.state == JobState.QUEUED and job.message_id:
                try:
                    await bot.edit_message_text(
                        job.chat_id,
                        job.message_id,
                        constants.RESTART_NOTICE
                    )
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

    queue_info = ""
    if crawl_queue:
        qstats = crawl_queue.stats()
        queue_info = f"""
**Queue**
Waiting: `{qstats['waiting']}`
Running: `{qstats['running']}`
Capacity: `{qstats['capacity']}` (workers: `{qstats['workers_per_crawl']}`)
Launch fails (1h): `{qstats['launch_failures_last_hour']}` (total: `{qstats['launch_failures_total']}`)
Suspended: `{qstats['suspended']}`"""

    admin_text = f"""
**Admin Panel**

Users: `{stats['total_users']}`
Requests: `{stats['total_requests']}`
Errors: `{stats['total_errors']}`
Avg/User: `{(stats['total_requests'] / stats['total_users']) if stats['total_users'] > 0 else 0:.2f}`
Uptime: `{uptime_str}`
Cache hit: `{perf_stats['cache_hit_rate']:.1f}%`{queue_info}

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


# ── Broadcast system (unchanged) ───────────────────────────────────

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