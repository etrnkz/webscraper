"""Hydrogram bot core: handlers, queue integration, crawl runner."""
import asyncio
import logging
import os
import re
import shutil
import time
import uuid
from datetime import datetime
from typing import Optional

from hydrogram import Client, filters
from hydrogram.enums import ParseMode
from hydrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import config, constants, database as db
from bot.errors import BrowserLaunchError
from bot.queue.crawl_queue import CrawlJob, CrawlQueue, Outcome, JobState
from bot.queue.resources import read_resources, derive_capacity, ResourceReading
from bot.utils.validators import normalize_url, is_valid_url, is_safe_domain
from bot.utils.helpers import extract_domain, format_file_size as format_size
from bot.modules.zip_packager import create_zip
from bot.utils.premium_text import PremiumText

# In-memory user settings (cookies_file, subdomains preference)
user_settings: dict[int, dict] = {}

logger = logging.getLogger(__name__)

# Pyrogram/Hydrogram client
bot = Client(
    "webscraper",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN,
    workers=16,
)

# Force subscribe
from bot.plugins.force_subscribe import ForceSubscribe
force_subscribe = ForceSubscribe(bot, config.FORCE_SUBSCRIBE_CHANNELS)


def safe_handler(handler):
    """Decorator to wrap handlers and hide raw errors from users."""
    async def wrapper(bot, msg):
        try:
            return await handler(bot, msg)
        except Exception as e:
            logger.error(f"Handler error in {handler.__name__}: {e}", exc_info=True)
            # Try to send a generic error message to user
            try:
                if hasattr(msg, 'reply'):
                    await msg.reply("Something went wrong. Please try again later.")
                elif hasattr(msg, 'message') and hasattr(msg.message, 'reply'):
                    await msg.message.reply("Something went wrong. Please try again later.")
            except Exception:
                pass
    return wrapper


# ── Notifier ──────────────────────────────────────────────────────────

class TelegramNotifier:
    """Sends user-facing status updates via message edits."""

    def __init__(self, client: Client):
        self._client = client

    async def _edit(self, chat_id: int, message_id: Optional[int], text: str, reply_markup=None) -> None:
        if message_id is None:
            return
        try:
            await self._client.edit_message_text(chat_id, message_id, text, reply_markup=reply_markup)
        except Exception:
            pass

    async def on_queued(self, job: CrawlJob, position: int, wait_seconds: int) -> None:
        wait_min = max(1, (wait_seconds + 59) // 60)
        text = constants.QUEUE_WAITING.format(position=position, wait_min=wait_min)
        await self._edit(job.chat_id, job.message_id, text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Cancel", callback_data="cancel_crawl")]
        ]))
        job.last_advertised_position = position
        job.last_advertised_wait = wait_seconds

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


# Queue and notifier (initialized at module level)

def _make_runner():
    """Create runner function that awaits _run_crawl (pump() wraps in create_task)."""
    async def runner(job: CrawlJob):
        await _run_crawl(job)
    return runner

def _get_usage(user_id: int) -> int:
    return db.get_usage_count(user_id)

crawl_queue = CrawlQueue(
    runner=_make_runner(),
    charge=db.increment_usage,
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
)

telegram_notifier = TelegramNotifier(bot)
crawl_queue.set_notifier(telegram_notifier)

# Background tasks
_background_tasks: list[asyncio.Task] = []


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
@safe_handler
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

@bot.on_message(filters.private & filters.command('start'), group=-2)
@safe_handler
async def start(bot, msg):
    features = [
        "Clone any website — HTML, assets, videos",
        "Subdomain or same-domain scope",
        "Cookie-based authenticated crawls",
        "Resource-aware queue (auto-scales)",
        "Cancel anytime, real-time progress",
    ]

    pt = PremiumText()
    pt.emoji("WEB").add(" ").bold("Web Clone")
    pt.add("\n\n")
    for feature in features:
        pt.emoji("STAR").add(f" {feature}")
        pt.add("\n")
    pt.add("\n").italic("Send a URL to start.")
    text, entities = pt.build()

    await msg.reply(text, entities=entities)


@bot.on_message(filters.private & filters.command('help'), group=-1)
@safe_handler
async def help_command(bot, msg):
    await msg.reply(constants.HELP_MESSAGE, disable_web_page_preview=True)


@bot.on_message(filters.private & filters.command('stats'), group=-1)
@safe_handler
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


@bot.on_message(filters.private & filters.command('version'), group=-1)
@safe_handler
async def version_command(bot, msg):
    await msg.reply(f"{constants.BOT_NAME} v{constants.BOT_VERSION}")


@bot.on_message(filters.private & filters.command('cancel'), group=-1)
@safe_handler
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


@bot.on_message(filters.private & filters.command('cookies'), group=-1)
@safe_handler
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
        user_settings[user_id] = user_settings.get(user_id, {})
        user_settings[user_id]['cookies_file'] = None
        await msg.reply("Cookies disabled.")
        return

    # Handle file reply
    if msg.reply_to_message and msg.reply_to_message.document:
        doc = msg.reply_to_message.document
        if doc.file_name.endswith('.json'):
            file_path = await msg.reply_to_message.download(file_name=f"cookies_{user_id}.json")
            user_settings[user_id] = user_settings.get(user_id, {})
            user_settings[user_id]['cookies_file'] = file_path
            await msg.reply("Cookies file saved.")
        else:
            await msg.reply("Please reply to a `.json` file.")
    else:
        await msg.reply("Reply to a `.json` cookie file with `/cookies`.")


@bot.on_message(filters.private & filters.command('scope'), group=-1)
@safe_handler
async def scope_command(bot, msg):
    user_id = msg.from_user.id
    args = msg.text.split(maxsplit=1)

    if len(args) < 2:
        current = user_settings.get(user_id, {}).get('subdomains', False)
        scope_label = 'Subdomains' if current else 'Same domain'
        await msg.reply(
            f"**Current scope**: `{scope_label}`\n\n"
            "Send `/scope subdomains` or `/scope same-domain` to change."
        )
        return

    scope_arg = args[1].strip().lower()
    if scope_arg in ('subdomains', 'subdomain'):
        user_settings[user_id] = user_settings.get(user_id, {})
        user_settings[user_id]['subdomains'] = True
        await msg.reply("Scope set to **Subdomains**.")
    elif scope_arg in ('same-domain', 'same', 'domain'):
        user_settings[user_id] = user_settings.get(user_id, {})
        user_settings[user_id]['subdomains'] = False
        await msg.reply("Scope set to **Same domain**.")
    else:
        await msg.reply("Invalid scope. Use `subdomains` or `same-domain`.")


@bot.on_message(filters.private & filters.command('settings'), group=-1)
@safe_handler
async def settings_command(bot, msg):
    user_id = msg.from_user.id
    settings = user_settings.get(user_id, {})
    scope = 'Subdomains' if settings.get('subdomains') else 'Same domain'
    cookies = 'Set' if settings.get('cookies_file') else 'Not set'
    await msg.reply(
        f"**Your Settings**\n\n"
        f"Scope: `{scope}`\n"
        f"Cookies: `{cookies}`\n\n"
        "Use `/scope` and `/cookies` to change."
    )


@bot.on_message(filters.private & filters.command('info'), group=-1)
@safe_handler
async def info_command(bot, msg):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        await msg.reply("Usage: `/info <URL>`", parse_mode=ParseMode.MARKDOWN)
        return

    url = normalize_url(parts[1])
    domain = extract_domain(url)
    safe = is_safe_domain(domain)
    await msg.reply(
        f"**Page Info**\n\n"
        f"URL: `{url}`\n"
        f"Domain: `{domain}`\n"
        f"Safe: `{safe}`",
        parse_mode=ParseMode.MARKDOWN
    )


@bot.on_message(filters.private & filters.command('media'), group=-1)
@safe_handler
async def media_command(bot, msg):
    await msg.reply(
        "**Media Extraction**\n\n"
        "The bot automatically downloads:\n"
        "• Images (jpg, png, webp, svg, gif)\n"
        "• Videos (mp4, webm) via yt-dlp\n"
        "• Audio (mp3, ogg)\n"
        "• Fonts (woff, woff2, ttf)\n"
        "• Stylesheets & scripts\n\n"
        "All assets are rewritten for local browsing."
    )


# ── Main URL handler → Playwright crawl ───────────────────────────

@bot.on_message(filters.private & filters.regex(r"(?i)(https?://|[\w-]+\.\w{2,})"), group=1)
@safe_handler
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
        return

    # Build job
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

    # Record message_id and launch (only for ADMITTED)
    job.message_id = processing_msg.id
    if admission.outcome == Outcome.ADMITTED:
        crawl_queue.launch(job)
        # NOTIFY STARTED ONLY HERE — runner will NOT call on_started again


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
        crawl_queue=crawl_queue,
        job=job,
    )

    # Set cancel callback
    job.cancel_running = cloner.cancel

    # Track progress task for cleanup
    progress_task: asyncio.Task | None = None

    try:
        # Progress updater with progress bar
        def _make_progress_bar(pages_done: int, pages_total: int) -> str:
            if pages_total == 0:
                return "▱▱▱▱▱▱▱▱▱▱ 0%"
            pct = min(100, int(pages_done / max(1, pages_total) * 100))
            filled = max(0, min(10, pct // 10))
            bar = "▰" * filled + "▱" * (10 - filled)
            return f"{bar} {pct}%"

        async def _progress_updater():
            while True:
                await asyncio.sleep(5)
                try:
                    if cloner.progress.cancelled:
                        break
                    text = (
                        f"**Cloning...**\n\n"
                        f"{cloner.progress.to_text()}\n\n"
                        f"{_make_progress_bar(cloner.progress.pages_downloaded, cloner.progress.pages_discovered)}\n\n"
                        f"_Updates every 5s — /cancel to stop_"
                    )
                    await bot.edit_message_text(job.chat_id, job.message_id, text)
                except Exception:
                    break

        progress_task = asyncio.create_task(_progress_updater())

        # Set state to CRAWLING
        job.state = JobState.CRAWLING

        # Run crawl (WebCloner.run() calls mark_browser_launched internally)
        await cloner.run()

        # Check for cancellation after crawl
        if cloner.progress.cancelled:
            job.state = JobState.CANCELLED
            await telegram_notifier.on_cancelled(job, charged=job.charged)
            crawl_queue.complete(job)
            await asyncio.to_thread(shutil.rmtree, output_dir, ignore_errors=True)
            if os.path.exists(zip_path):
                await asyncio.to_thread(os.remove, zip_path)
            return

        # Crawl succeeded - record duration (held window: admission to archive built)
        duration = time.monotonic() - job.admitted_at if job.admitted_at else 0
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

        # Set state to PACKING
        job.state = JobState.PACKING

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

        # Set state to DELIVERING
        job.state = JobState.DELIVERING
        crawl_queue._delivering.append(job)

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

            # Structured logging for diagnostics
            logger.info(
                "crawl_complete",
                extra={
                    "job_id": job.job_id,
                    "user_id": job.user_id,
                    "domain": job.domain,
                    "elapsed_seconds": round(duration, 1),
                    "pages": pages,
                    "assets": assets,
                    "videos": videos,
                    "zip_size_mb": round(zip_size / 1024 / 1024, 1),
                }
            )

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
            # Re-queue with backoff via queue API
            delay = config.CRAWL_BROWSER_RETRY_BASE_SECONDS * (2 ** (job.launch_attempts - 1))
            crawl_queue.requeue(job, delay)
            await telegram_notifier.on_retry(job, job.launch_attempts, delay, e.kind.value)

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
                # Store resource reading for stats
                crawl_queue._last_reading = reading.memory_available_mb
                crawl_queue._cpu_slots = reading.cpu_quota_cores or reading.cpu_affinity_cores or 0
                crawl_queue._memory_available_mb = reading.memory_available_mb
                crawl_queue._memory_reserve_mb = config.CRAWL_MEMORY_RESERVE_MB
                crawl_queue._memory_budget_mb = config.CRAWL_MEMORY_BUDGET_MB
                crawl_queue._last_probe_error = None
                crawl_queue.set_capacity(capacity, workers)
        except Exception as e:
            logger.error(f"Resource probe error: {e}")
            if crawl_queue:
                crawl_queue._last_probe_error = str(e)
        await asyncio.sleep(config.CRAWL_RESOURCE_PROBE_INTERVAL)


async def _position_refresh():
    """Refresh queue position messages (single 120s cadence)."""
    while True:
        try:
            if crawl_queue and telegram_notifier:
                updates = crawl_queue.position_updates()
                for job, position, wait_seconds in updates:
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
                # Expire stale queue entries (no notifier param)
                expired = crawl_queue.expire_stale()
                if expired:
                    for job in expired:
                        await telegram_notifier.on_expired(job)
                    logger.info("Expired %d stale queue entries", len(expired))

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

@bot.on_message(filters.private & filters.command('admin'), group=-1)
@safe_handler
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
Delivering: `{qstats['delivering']}`
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
        parse_mode = ParseMode.MARKDOWN
    elif fmt == "html":
        parse_mode = ParseMode.HTML

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


@bot.on_message(filters.private & filters.command('broadcast'), group=-1)
@safe_handler
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


@bot.on_message(filters.private & filters.command('clearcache'), group=-1)
@safe_handler
async def clearcache_command(bot, msg):
    user_id = msg.from_user.id
    if user_id not in config.ADMIN_IDS:
        await msg.reply(constants.ERROR_PERMISSION_DENIED)
        return
    try:
        from bot.utils import cache
        cache.clear_expired_cache()
        await msg.reply("Cache cleared.")
    except Exception as e:
        await msg.reply(f"Failed: {e}")


@bot.on_message(filters.private & filters.command('users'), group=-1)
@safe_handler
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


@bot.on_message(filters.private & filters.command('topusers'), group=-1)
@safe_handler
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


@bot.on_message(filters.private & filters.command('userinfo'), group=-1)
@safe_handler
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


@bot.on_message(filters.private & filters.command('ban'), group=-1)
@safe_handler
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


@bot.on_message(filters.private & filters.command('unban'), group=-1)
@safe_handler
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


@bot.on_message(filters.private & filters.command('logs'), group=-1)
@safe_handler
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

@bot.on_message(filters.private & filters.text, group=1)
@safe_handler
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


@bot.on_message(filters.private & filters.text, group=2)
@safe_handler
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


# Bot start time for uptime
bot_start_time = datetime.now()