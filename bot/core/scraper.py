import requests
from bs4 import BeautifulSoup
from pyrogram import Client, filters
import os
import logging
import re
from urllib.parse import urlparse
from datetime import datetime
import bot.config as config
import time
from bot.utils.helpers import sanitize_filename, format_file_size, extract_domain
from bot import constants
from bot.utils.validators import is_valid_url, is_safe_domain
from bot.utils.user_agents import get_random_headers
import bot.modules.cache_manager as cache
from bot.modules import media_extractor
import shutil
from bot.modules.web_archiver import DownloadManager
import zipfile
from concurrent.futures import ThreadPoolExecutor
from bot.modules.metadata_parser import extract_metadata, format_metadata
from bot import database as db
from bot.plugins.force_subscribe import ForceSubscribe
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Validate configuration on startup
config.validate_config()

# Initialize cache
cache.init_cache()

# Configure logging
logging.basicConfig(
    level=config.LOG_LEVEL,
    format=config.LOG_FORMAT
)
logger = logging.getLogger(__name__)

# Thread pool for concurrent downloads
download_executor = ThreadPoolExecutor(max_workers=20)


# Load credentials from environment variables
API_ID = config.API_ID
API_HASH = config.API_HASH
BOT_TOKEN = config.BOT_TOKEN

bot = Client(
    'my_bot',
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# Initialize force subscribe after bot is defined
force_subscribe = ForceSubscribe(bot, config.FORCE_SUBSCRIBE_CHANNELS)
if config.FORCE_JOIN_CHANNEL and config.FORCE_JOIN_CHANNEL not in force_subscribe.channel_ids:
    force_subscribe.add_channel(config.FORCE_JOIN_CHANNEL)

bot_start_time = datetime.now()  # Track bot uptime


def check_force_join(user_id, msg):
    """Force user to join channel after free usage limit"""
    usage = db.get_usage_count(user_id)
    if usage >= config.FREE_USAGE_LIMIT:
        is_subscribed, not_subscribed = force_subscribe.check_all_subscriptions(user_id)
        if not is_subscribed:
            buttons = force_subscribe.create_join_buttons(not_subscribed)
            msg.reply(
                force_subscribe.get_force_subscribe_message(not_subscribed),
                reply_markup=buttons
            )
            return False
    return True


@bot.on_callback_query()
def handle_callback(bot, callback_query):
    user_id = callback_query.from_user.id
    data = callback_query.data
    
    if data == "check_subscription":
        is_subscribed, not_subscribed = force_subscribe.check_all_subscriptions(user_id)
        if is_subscribed:
            bot.answer_callback_query(callback_query.id, "✅ Subscribed! Enjoy the bot.", show_alert=True)
            callback_query.message.delete()
        else:
            bot.answer_callback_query(callback_query.id, "❌ You haven't joined yet. Please join the channel first.", show_alert=True)
    
    elif data == "help":
        bot.answer_callback_query(callback_query.id)
        callback_query.message.reply(constants.HELP_MESSAGE, disable_web_page_preview=True)
    
    elif data == "stats":
        bot.answer_callback_query(callback_query.id)
        user = db.get_user(user_id)
        total = user['total_requests'] if user else 0
        errors = user['total_errors'] if user else 0
        usage = db.get_usage_count(user_id)
        free_left = max(0, config.FREE_USAGE_LIMIT - usage)
        success_rate = ((total - errors) / total * 100) if total > 0 else 0
        callback_query.message.reply(
            f"╔════════════════════════╗\n"
            f"║   📊 **Your Stats**     ║\n"
            f"╚════════════════════════╝\n\n"
            f"✅ **Requests:** `{total}`\n"
            f"❌ **Errors:** `{errors}`\n"
            f"📈 **Success:** `{success_rate:.1f}%`\n"
            f"🎫 **Free uses left:** `{free_left}`"
        )


@bot.on_message(filters.private & filters.command('start'))
def start(bot, msg):
    user_id = msg.from_user.id
    username = msg.from_user.username
    first_name = msg.from_user.first_name
    
    db.register_user(user_id, username, first_name)
    usage = db.get_usage_count(user_id)
    free_left = max(0, config.FREE_USAGE_LIMIT - usage)
    
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Help", callback_data="help"),
         InlineKeyboardButton("📊 Stats", callback_data="stats")],
        [InlineKeyboardButton("👨‍💻 Developer", url="https://t.me/e_phador")],
    ])
    
    msg.reply(
        constants.WELCOME_MESSAGE.format(name=first_name, free_uses=free_left),
        reply_markup=buttons
    )

@bot.on_message(filters.private & filters.command('help'))
def help_command(bot, msg):
    msg.reply(constants.HELP_MESSAGE, disable_web_page_preview=True)

@bot.on_message(filters.private & filters.command('stats'))
def stats_command(bot, msg):
    user_id = msg.from_user.id
    user = db.get_user(user_id)
    total = user['total_requests'] if user else 0
    errors = user['total_errors'] if user else 0
    usage = db.get_usage_count(user_id)
    free_left = max(0, config.FREE_USAGE_LIMIT - usage)
    success_rate = ((total - errors) / total * 100) if total > 0 else 0
    msg.reply(
        f"╔════════════════════════╗\n"
        f"║   📊 **Your Stats**     ║\n"
        f"╚════════════════════════╝\n\n"
        f"✅ **Requests:** `{total}`\n"
        f"❌ **Errors:** `{errors}`\n"
        f"📈 **Success:** `{success_rate:.1f}%`\n"
        f"🎫 **Free uses left:** `{free_left}`"
    )

@bot.on_message(filters.private & filters.command('version'))
def version_command(bot, msg):
    msg.reply(
        f"╔════════════════════════╗\n"
        f"║   🤖 **Bot Info**       ║\n"
        f"╚════════════════════════╝\n\n"
        f"**Name:** `{constants.BOT_NAME}`\n"
        f"**Version:** `{constants.BOT_VERSION}`\n"
        f"**Python:** `3.10+`"
    )

@bot.on_message(filters.private & filters.command('info'))
def info_command(bot, msg):
    user_id = msg.from_user.id
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        msg.reply("❌ **Usage:** `/info <url>`\nExample: `/info https://example.com`")
        return
    
    url = parts[1].strip()
    if not is_valid_url(url):
        msg.reply(constants.ERROR_INVALID_URL)
        return
    
    processing_msg = msg.reply("🔍 **Fetching page info...**")
    
    try:
        headers = get_random_headers()
        proxies = config.get_proxies()
        response = requests.get(url, timeout=config.REQUEST_TIMEOUT, headers=headers, proxies=proxies)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        metadata = extract_metadata(soup)
        meta_text = format_metadata(metadata)
        
        msg.reply(
            f"╔════════════════════════╗\n"
            f"║   ℹ️ **Page Info**      ║\n"
            f"╚════════════════════════╝\n\n"
            f"🔗 **URL:** `{url}`\n\n{meta_text}",
            disable_web_page_preview=True
        )
        processing_msg.delete()
        
    except Exception as e:
        msg.reply(f"❌ **Failed:** `{str(e)[:80]}`")
        logger.error(f"Info error for {url}: {e}")

@bot.on_message(filters.private & filters.command('media'))
def media_command(bot, msg):
    user_id = msg.from_user.id
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        msg.reply("❌ **Usage:** `/media <url>`\nExample: `/media https://example.com`")
        return
    
    url = parts[1].strip()
    if not is_valid_url(url):
        msg.reply(constants.ERROR_INVALID_URL)
        return
    
    processing_msg = msg.reply("🖼️ **Scanning page for media...**")
    
    try:
        headers = get_random_headers()
        proxies = config.get_proxies()
        response = requests.get(url, timeout=config.REQUEST_TIMEOUT, headers=headers, proxies=proxies)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        domain = extract_domain(url)
        output_dir = f"media_{domain}_{user_id}"
        
        processing_msg.edit("📥 **Downloading media...**")
        downloaded = media_extractor.scrape_media(url, soup, output_dir, ['images'])
        
        if downloaded:
            processing_msg.edit(f"📤 **Sending {len(downloaded)} files...**")
            for filepath in downloaded[:5]:
                try:
                    msg.reply_document(filepath)
                except Exception as e:
                    logger.error(f"Failed to send {filepath}: {e}")
            msg.reply(f"✅ **Done!** Downloaded `{len(downloaded)}` media files from `{domain}`")
        else:
            msg.reply(f"❌ **No media found** on `{domain}`")
        
        shutil.rmtree(output_dir, ignore_errors=True)
        processing_msg.delete()
        db.increment_requests(user_id, success=True)
        
    except Exception as e:
        shutil.rmtree(output_dir, ignore_errors=True)
        msg.reply(f"❌ **Failed:** `{str(e)[:80]}`")
        logger.error(f"Media error for {url}: {e}")

@bot.on_message(filters.private & filters.command('archive'))
def archive_command(bot, msg):
    user_id = msg.from_user.id
    
    # Extract URL from command
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        msg.reply("❌ Usage: /archive <url>\n\nExample: /archive https://example.com\n\nThis will recursively download up to 50 pages from the website.")
        return
    
    url = parts[1].strip()
    if not is_valid_url(url):
        msg.reply(constants.ERROR_INVALID_URL)
        return
    
    processing_msg = msg.reply("📦 **Starting recursive download...**\n\n⏳ This may take a few minutes.")
    
    try:
        domain = extract_domain(url)
        output_dir = f"archive_{domain}_{user_id}"
        os.makedirs(output_dir, exist_ok=True)
        
        # Start recursive download
        dm = DownloadManager(max_depth=2, max_files=50, delay=1)
        dm.recursive_download(url, output_dir)
        
        stats = dm.get_stats()
        
        if stats['total_downloaded'] > 0:
            processing_msg.edit(f"📦 Creating archive... Downloaded {stats['total_downloaded']} pages")
            
            # Create ZIP archive
            zip_filename = f"archive_{domain}.zip"
            with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file in stats['files']:
                    zipf.write(file, os.path.basename(file))
            
            # Send archive
            msg.reply_document(
                zip_filename,
                caption=f"╔════════════════════════╗\n║   📦 **Archive Ready**  ║\n╚════════════════════════╝\n\n🌐 **Domain:** `{domain}`\n📄 **Pages:** `{stats['total_downloaded']}`\n📦 **Size:** `{format_file_size(os.path.getsize(zip_filename))}`"
            )
            
            os.remove(zip_filename)
            shutil.rmtree(output_dir, ignore_errors=True)
            processing_msg.delete()
        else:
            msg.reply(f"❌ **No pages downloaded** from `{domain}`")
        
        db.increment_requests(user_id, success=True)
        
    except Exception as e:
        msg.reply(f"❌ **Archive failed:** `{str(e)[:80]}`")
        logger.error(f"Archive error for {url}: {e}")

@bot.on_message(filters.private & filters.command('admin'))
def admin_command(bot, msg):
    user_id = msg.from_user.id
    
    if user_id not in config.ADMIN_IDS:
        msg.reply(constants.ERROR_PERMISSION_DENIED)
        logger.warning(f"Unauthorized admin access attempt by user {user_id}")
        return
    
    stats = db.get_statistics()
    uptime = datetime.now() - bot_start_time
    uptime_str = str(uptime).split('.')[0]
    
    from bot.monitoring.performance import get_performance_stats
    perf_stats = get_performance_stats()
    
    admin_text = f"""
╔══════════════════════════════╗
║      🔧 **Admin Panel**       ║
╚══════════════════════════════╝

**📊 Statistics:**
👥 **Users:** `{stats['total_users']}`
📊 **Requests:** `{stats['total_requests']}`
❌ **Errors:** `{stats['total_errors']}`
📈 **Avg/User:** `{(stats['total_requests'] / stats['total_users']) if stats['total_users'] > 0 else 0:.2f}`
⏰ **Uptime:** `{uptime_str}`
💾 **Cache hit:** `{perf_stats['cache_hit_rate']:.1f}%`
📦 **Max file:** `{config.MAX_FILE_SIZE // (1024*1024)}MB`

**🛠 Commands:**
/broadcast — Broadcast to all users
/clearcache — Clear cached content
/users — List all users
/topusers — Top 10 users
/userinfo — User details
/ban — Ban a user
/unban — Unban a user
/logs — View activity logs
"""
    msg.reply(admin_text)

@bot.on_message(filters.private & filters.command('broadcast'))
def broadcast_command(bot, msg):
    user_id = msg.from_user.id
    
    if user_id not in config.ADMIN_IDS:
        msg.reply(constants.ERROR_PERMISSION_DENIED)
        logger.warning(f"Unauthorized broadcast attempt by user {user_id}")
        return
    
    broadcast_text = msg.text.split(maxsplit=1)
    if len(broadcast_text) < 2:
        msg.reply("❌ **Usage:** `/broadcast all/active <message>`\n\n**Examples:**\n`/broadcast all Hello everyone!`\n`/broadcast active Hey active users!`")
        return
    
    full_text = broadcast_text[1]
    parts = full_text.split(maxsplit=1)
    
    target = "all"
    message = full_text
    
    if len(parts) >= 2 and parts[0].lower() in ['all', 'active']:
        target = parts[0].lower()
        message = parts[1]
    
    if target == 'active':
        target_users = [u['user_id'] for u in db.get_active_users(24)]
        target_desc = "active users (24h)"
    else:
        target_users = [u['user_id'] for u in db.get_all_users()]
        target_desc = "all users"
    
    if not target_users:
        msg.reply("❌ No users to broadcast to.")
        return
    
    confirm_msg = msg.reply(f"📢 Broadcasting to {len(target_users)} {target_desc}...")
    
    success = 0
    failed = 0
    
    def send(uid):
        try:
            bot.send_message(uid, f"📢 **Broadcast Message:**\n\n{message}")
            return True
        except Exception:
            return False
    
    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(send, target_users))
    
    success = sum(1 for r in results if r)
    failed = len(results) - success
    confirm_msg.edit(f"╔══════════════════════════╗\n║  📢 **Broadcast Done**    ║\n╚══════════════════════════╝\n\n✅ **Sent:** `{success}`\n❌ **Failed:** `{failed}`\n👥 **Target:** `{target_desc}`")

@bot.on_message(filters.private & filters.command('clearcache'))
def clearcache_command(bot, msg):
    user_id = msg.from_user.id
    
    if user_id not in config.ADMIN_IDS:
        msg.reply(constants.ERROR_PERMISSION_DENIED)
        return
    
    try:
        cache.clear_expired_cache()
        msg.reply("✅ Cache cleared successfully!")
        logger.info(f"Cache cleared by admin {user_id}")
    except Exception as e:
        msg.reply(f"❌ Failed to clear cache: {str(e)}")
        logger.error(f"Cache clear error: {e}")

@bot.on_message(filters.private & filters.command('users'))
def users_command(bot, msg):
    user_id = msg.from_user.id
    if user_id not in config.ADMIN_IDS:
        msg.reply(constants.ERROR_PERMISSION_DENIED)
        return
    
    stats = db.get_statistics()
    all_users = db.get_all_users()
    lines = []
    for i, u in enumerate(all_users[:20], 1):
        name = u['first_name'] or u['username'] or f"User {u['user_id']}"
        status = "🚫" if not u['is_active'] else "✅"
        lines.append(f"{i}. {status} `{u['user_id']}` {name} - {u['total_requests']} req")
    
    msg.reply(f"""
╔══════════════════════════════╗
║     👥 **User Management**    ║
╚══════════════════════════════╝

📊 **Total:** `{stats['total_users']}`  |  **Active (24h):** `{stats['active_24h']}`
📈 **Requests:** `{stats['total_requests']}`  |  ❌ **Errors:** `{stats['total_errors']}`

**Recent Users:**
{chr(10).join(lines) if lines else "No users"}
""")

@bot.on_message(filters.private & filters.command('topusers'))
def topusers_command(bot, msg):
    user_id = msg.from_user.id
    if user_id not in config.ADMIN_IDS:
        msg.reply(constants.ERROR_PERMISSION_DENIED)
        return
    
    top = db.get_top_users(10)
    lines = []
    for i, u in enumerate(top, 1):
        name = u['first_name'] or u['username'] or f"User {u['user_id']}"
        lines.append(f"{i}. `{u['user_id']}` {name} - {u['total_requests']} req ({u['total_errors']} err)")
    
    msg.reply(f"╔══════════════════════════╗\n║   🏆 **Top 10 Users**    ║\n╚══════════════════════════╝\n\n{chr(10).join(lines) if lines else 'No data'}")

@bot.on_message(filters.private & filters.command('userinfo'))
def userinfo_command(bot, msg):
    user_id = msg.from_user.id
    if user_id not in config.ADMIN_IDS:
        msg.reply(constants.ERROR_PERMISSION_DENIED)
        return
    
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        msg.reply("❌ Usage: /userinfo <user_id>")
        return
    
    try:
        target = int(parts[1])
        u = db.get_user(target)
        if not u or not u['first_seen']:
            msg.reply(f"❌ User {target} not found.")
            return
        
        name = u['first_name'] or u['username'] or f"User {target}"
        status = "🚫 Banned" if not u['is_active'] else "✅ Active"
        total, errs = u['total_requests'], u['total_errors']
        rate = ((total - errs) / total * 100) if total > 0 else 0.0
        
        msg.reply(f"""
╔══════════════════════════╗
║    👤 **User Info**       ║
╚══════════════════════════╝

**ID:** `{target}`
**Name:** {name}
**Username:** @{u['username'] or 'N/A'}
**Status:** {status}

📊 **Requests:** `{total}`  |  **Errors:** `{errs}`  |  **Blocks:** `{u['blocked_count']}`
📈 **Success:** `{rate:.1f}%`
⏰ **First seen:** `{u['first_seen']}`  |  **Last seen:** `{u['last_seen']}`
""")
    except ValueError:
        msg.reply("❌ Invalid ID.")
    except Exception as e:
        msg.reply(f"❌ Error: {e}")
        logger.error(f"Userinfo error: {e}")

@bot.on_message(filters.private & filters.command('ban'))
def ban_command(bot, msg):
    user_id = msg.from_user.id
    if user_id not in config.ADMIN_IDS:
        msg.reply(constants.ERROR_PERMISSION_DENIED)
        return
    
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        msg.reply("❌ Usage: /ban <user_id>")
        return
    
    try:
        target = int(parts[1])
        if target in config.ADMIN_IDS:
            msg.reply("❌ Cannot ban an admin.")
            return
        db.ban_user(target)
        msg.reply(f"✅ User {target} banned.")
        try:
            bot.send_message(target, "🚫 You have been banned.")
        except Exception:
            pass
    except ValueError:
        msg.reply("❌ Invalid ID.")

@bot.on_message(filters.private & filters.command('unban'))
def unban_command(bot, msg):
    user_id = msg.from_user.id
    if user_id not in config.ADMIN_IDS:
        msg.reply(constants.ERROR_PERMISSION_DENIED)
        return
    
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        msg.reply("❌ Usage: /unban <user_id>")
        return
    
    try:
        target = int(parts[1])
        db.unban_user(target)
        msg.reply(f"✅ User {target} unbanned.")
        try:
            bot.send_message(target, "✅ You have been unbanned.")
        except Exception:
            pass
    except ValueError:
        msg.reply("❌ Invalid ID.")


	
@bot.on_message(filters.private & filters.regex("http"))
def scrap(bot, msg):
    url = msg.text.strip()
    user_id = msg.from_user.id
    
    db.register_user(user_id, msg.from_user.username, msg.from_user.first_name)
    db.log_activity(user_id, "scrape_request", url)
    
    if db.is_banned(user_id):
        msg.reply("╔══════════════════════════╗\n║  🚫 **Access Denied**    ║\n╚══════════════════════════╝\n\nYou have been banned from using this bot.")
        db.record_block(user_id)
        return
    
    if not check_force_join(user_id, msg):
        return
    db.increment_usage(user_id)
    
    if not is_valid_url(url):
        msg.reply(constants.ERROR_INVALID_URL)
        return
    
    domain = extract_domain(url)
    if not is_safe_domain(domain):
        msg.reply("⚠️ **Unsafe domain** — this URL has been blocked.")
        db.record_block(user_id)
        return
    
    logger.info(f"Processing URL from user {user_id}: {url}")
    
    processing_msg = msg.reply("🌐 **Downloading entire website...**\n\n⏳ This may take a few minutes.")
    
    try:
        output_dir = f"site_{domain}_{user_id}"
        os.makedirs(output_dir, exist_ok=True)
        
        dm = DownloadManager(max_depth=2, max_files=50, delay=1)
        dm.recursive_download(url, output_dir)
        stats = dm.get_stats()
        
        if stats['total_downloaded'] > 0:
            processing_msg.edit(f"📦 **Zipping {stats['total_downloaded']} pages...**")
            
            zip_filename = f"website_{domain}.zip"
            with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file in stats['files']:
                    zipf.write(file, os.path.basename(file))
            
            msg.reply_document(
                zip_filename,
                caption=f"╔══════════════════════════════╗\n║  🌐 **Website Downloaded**   ║\n╚══════════════════════════════╝\n\n🌐 **Domain:** `{domain}`\n📄 **Pages:** `{stats['total_downloaded']}`\n📦 **Size:** `{format_file_size(os.path.getsize(zip_filename))}`"
            )
            
            os.remove(zip_filename)
            shutil.rmtree(output_dir, ignore_errors=True)
            processing_msg.delete()
        else:
            msg.reply(f"❌ **No pages downloaded** from `{domain}`")
        
        db.increment_requests(user_id, success=True)
        
    except Exception as e:
        shutil.rmtree(output_dir, ignore_errors=True)
        msg.reply(f"❌ **Download failed:** `{str(e)[:80]}`")
        logger.error(f"Scrap error for {url}: {e}")

       
@bot.on_message(filters.private & filters.text)
def show(bot, msg):
    msg.reply(
        text="╔════════════════════════════════╗\n║  ❌ **Invalid Input**         ║\n╚════════════════════════════════╝\n\nYour message doesn't look like a valid URL.\n\n**Please send a link starting with:**\n`https://www.example.com`\n\nNeed help? Contact the [Developer](https://t.me/e_phador)",
        disable_web_page_preview=True,
        quote=True
    )

@bot.on_message(filters.private & filters.command('logs'))
def logs_command(bot, msg):
    user_id = msg.from_user.id
    if user_id not in config.ADMIN_IDS:
        msg.reply(constants.ERROR_PERMISSION_DENIED)
        return
    
    parts = msg.text.split(maxsplit=1)
    
    if len(parts) >= 2:
        try:
            target = int(parts[1])
            rows = db.get_user_activities(target, 20)
            lines = [f"{r['timestamp'][:19]} | {r['action']} | {r['details'] or ''}" for r in rows]
            msg.reply(f"📋 **Logs for User {target}:**\n\n```\n{chr(10).join(lines) or 'No logs'}\n```")
        except ValueError:
            msg.reply("❌ Invalid ID.")
    else:
        rows = db.get_recent_activities(30)
        lines = [f"{r['timestamp'][:19]} | U{r['user_id']} | {r['action']}" for r in rows]
        msg.reply(f"📋 **Recent Logs:**\n\n```\n{chr(10).join(lines) or 'No logs'}\n```")
    
logger.info(f"Starting {constants.BOT_NAME} v{constants.BOT_VERSION}")
bot.run()
