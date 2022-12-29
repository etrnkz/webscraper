import requests 
from bs4 import BeautifulSoup 
from pyrogram import Client, filters
import os
import logging
import re
from urllib.parse import urlparse
from collections import defaultdict
from datetime import datetime, timedelta
import bot.config as config
import chardet
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
from bot.modules.robots_handler import robots_checker
from bot.modules.metadata_parser import extract_metadata, format_metadata
from bot.admin.panel import admin_panel
from bot.admin.activity_tracker import activity_logger
from bot.plugins.force_subscribe import ForceSubscribe

# Validate configuration on startup
config.validate_config()

# Initialize cache
cache.init_cache()

# Initialize force subscribe
force_subscribe = ForceSubscribe(bot, config.FORCE_SUBSCRIBE_CHANNELS)

# Configure logging
logging.basicConfig(
    level=config.LOG_LEVEL,
    format=config.LOG_FORMAT
)
logger = logging.getLogger(__name__)

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

# Rate limiting: track user requests
user_requests = defaultdict(list)
daily_requests = defaultdict(list)  # Track daily requests
request_stats = defaultdict(int)  # Track total requests per user
error_stats = defaultdict(int)  # Track errors per user
bot_start_time = datetime.now()  # Track bot uptime


def check_rate_limit(user_id):
    """Check if user has exceeded rate limit"""
    now = datetime.now()
    minute_ago = now - timedelta(minutes=1)
    
    # Clean old requests
    user_requests[user_id] = [req_time for req_time in user_requests[user_id] if req_time > minute_ago]
    
    if len(user_requests[user_id]) >= config.RATE_LIMIT:
        return False
    
    user_requests[user_id].append(now)
    return True


def check_daily_limit(user_id):
    """Check if user has exceeded daily limit"""
    now = datetime.now()
    day_ago = now - timedelta(days=1)
    
    # Clean old requests
    daily_requests[user_id] = [req_time for req_time in daily_requests[user_id] if req_time > day_ago]
    
    if len(daily_requests[user_id]) >= config.DAILY_LIMIT:
        return False
    
    daily_requests[user_id].append(now)
    return True

@bot.on_message(filters.private & filters.command('start'))
def start(bot, msg):
    user_id = msg.from_user.id
    username = msg.from_user.username
    first_name = msg.from_user.first_name
    
    # Register user in admin panel
    admin_panel.register_user(user_id, username, first_name)
    
    msg.reply(constants.WELCOME_MESSAGE.format(name=first_name))

@bot.on_message(filters.private & filters.command('help'))
def help_command(bot, msg):
    msg.reply(constants.HELP_MESSAGE, disable_web_page_preview=True)

@bot.on_message(filters.private & filters.command('stats'))
def stats_command(bot, msg):
    user_id = msg.from_user.id
    total = request_stats.get(user_id, 0)
    errors = error_stats.get(user_id, 0)
    success_rate = ((total - errors) / total * 100) if total > 0 else 0
    msg.reply(f"📊 **Your Statistics:**\n\n✅ Total requests: {total}\n❌ Errors: {errors}\n📈 Success rate: {success_rate:.1f}%")

@bot.on_message(filters.private & filters.command('version'))
def version_command(bot, msg):
    msg.reply(f"🤖 **{constants.BOT_NAME}**\nVersion: `{constants.BOT_VERSION}`")

@bot.on_message(filters.private & filters.command('info'))
def info_command(bot, msg):
    user_id = msg.from_user.id
    
    # Extract URL from command
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        msg.reply("❌ Usage: /info <url>\n\nExample: /info https://example.com")
        return
    
    url = parts[1].strip()
    
    # Validate URL
    if not is_valid_url(url):
        msg.reply(constants.ERROR_INVALID_URL)
        return
    
    processing_msg = msg.reply("⏳ Fetching page information...")
    
    try:
        headers = get_random_headers()
        proxies = config.get_proxies()
        response = requests.get(url, timeout=config.REQUEST_TIMEOUT, headers=headers, proxies=proxies)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        metadata = extract_metadata(soup)
        
        # Format response
        info_text = f"ℹ️ **Page Information**\n\n🌐 **URL:** `{url}`\n\n{format_metadata(metadata)}"
        
        msg.reply(info_text, disable_web_page_preview=True)
        processing_msg.delete()
        
    except Exception as e:
        msg.reply(f"❌ Failed to fetch page info: {str(e)}")
        logger.error(f"Info command error for {url}: {e}")

@bot.on_message(filters.private & filters.command('media'))
def media_command(bot, msg):
    user_id = msg.from_user.id
    
    # Extract URL from command
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        msg.reply("❌ Usage: /media <url>\n\nExample: /media https://example.com")
        return
    
    url = parts[1].strip()
    
    # Check rate limits
    if not check_rate_limit(user_id):
        msg.reply(constants.ERROR_RATE_LIMIT)
        return
    
    if not check_daily_limit(user_id):
        msg.reply("📅 Daily limit reached (15 requests/day).")
        return
    
    # Validate URL
    if not is_valid_url(url):
        msg.reply(constants.ERROR_INVALID_URL)
        return
    
    processing_msg = msg.reply("⏳ Fetching media from webpage...")
    
    try:
        headers = get_random_headers()
        proxies = config.get_proxies()
        response = requests.get(url, timeout=config.REQUEST_TIMEOUT, headers=headers, proxies=proxies)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        domain = extract_domain(url)
        output_dir = f"media_{domain}_{user_id}"
        
        processing_msg.edit("📥 Downloading media files...")
        
        # Download images only for now
        downloaded = media_scraper.scrape_media(url, soup, output_dir, ['images'])
        
        if downloaded:
            processing_msg.edit(f"📤 Sending {len(downloaded)} files...")
            
            for filepath in downloaded[:5]:  # Send max 5 files
                try:
                    msg.reply_document(filepath)
                except Exception as e:
                    logger.error(f"Failed to send {filepath}: {e}")
            
            msg.reply(f"✅ Downloaded {len(downloaded)} media files from {domain}")
        else:
            msg.reply("❌ No media files found on this page.")
        
        # Cleanup
        try:
            shutil.rmtree(output_dir)
        except Exception:
            pass
        
        processing_msg.delete()
        request_stats[user_id] += 1
        
    except Exception as e:
        msg.reply(f"❌ Failed to scrape media: {str(e)}")
        logger.error(f"Media scraping error for {url}: {e}")

@bot.on_message(filters.private & filters.command('archive'))
def archive_command(bot, msg):
    user_id = msg.from_user.id
    
    # Extract URL from command
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        msg.reply("❌ Usage: /archive <url>\n\nExample: /archive https://example.com\n\nThis will recursively download up to 50 pages from the website.")
        return
    
    url = parts[1].strip()
    
    # Check rate limits
    if not check_rate_limit(user_id):
        msg.reply(constants.ERROR_RATE_LIMIT)
        return
    
    if not check_daily_limit(user_id):
        msg.reply("📅 Daily limit reached (15 requests/day).")
        return
    
    # Validate URL
    if not is_valid_url(url):
        msg.reply(constants.ERROR_INVALID_URL)
        return
    
    processing_msg = msg.reply("⏳ Starting recursive download...\n\nThis may take a few minutes.")
    
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
                caption=f"✅ **Website Archive**\n\n🌐 Domain: `{domain}`\n📄 Pages: {stats['total_downloaded']}\n📦 Size: {format_file_size(os.path.getsize(zip_filename))}"
            )
            
            # Cleanup
            try:
                os.remove(zip_filename)
                shutil.rmtree(output_dir)
            except Exception:
                pass
            
            processing_msg.delete()
        else:
            msg.reply("❌ Failed to download any pages from the website.")
        
        request_stats[user_id] += 1
        
    except Exception as e:
        msg.reply(f"❌ Archive failed: {str(e)}")
        logger.error(f"Archive error for {url}: {e}")

@bot.on_message(filters.private & filters.command('admin'))
def admin_command(bot, msg):
    user_id = msg.from_user.id
    
    if user_id not in config.ADMIN_IDS:
        msg.reply(constants.ERROR_PERMISSION_DENIED)
        logger.warning(f"Unauthorized admin access attempt by user {user_id}")
        return
    
    total_users = len(request_stats)
    total_requests = sum(request_stats.values())
    total_errors = sum(error_stats.values())
    avg_requests = total_requests / total_users if total_users > 0 else 0
    uptime = datetime.now() - bot_start_time
    uptime_str = str(uptime).split('.')[0]  # Remove microseconds
    
    # Get performance stats
    from performance import get_performance_stats
    perf_stats = get_performance_stats()
    
    admin_text = f"""
🔧 **Admin Statistics:**

👥 Total users: {total_users}
📊 Total requests: {total_requests}
❌ Total errors: {total_errors}
📈 Avg requests/user: {avg_requests:.2f}
⏰ Uptime: {uptime_str}
💾 Cache hit rate: {perf_stats['cache_hit_rate']:.1f}%
⚡ Rate limit: {config.RATE_LIMIT}/min
📅 Daily limit: {config.DAILY_LIMIT}/day
💾 Max file size: {config.MAX_FILE_SIZE // (1024*1024)}MB
🔄 Max retries: {config.MAX_RETRIES}

**Commands:**
/broadcast <message> - Send message to all users
/clearcache - Clear all cached content
/users - List all users
/topusers - Show top 10 users by activity
/userinfo <user_id> - Get detailed user information
/ban <user_id> - Ban a user
/unban <user_id> - Unban a user
/logs [user_id] - View activity logs
"""
    msg.reply(admin_text)

@bot.on_message(filters.private & filters.command('broadcast'))
def broadcast_command(bot, msg):
    user_id = msg.from_user.id
    
    if user_id not in config.ADMIN_IDS:
        msg.reply(constants.ERROR_PERMISSION_DENIED)
        logger.warning(f"Unauthorized broadcast attempt by user {user_id}")
        return
    
    # Extract message after command
    broadcast_text = msg.text.split(maxsplit=1)
    if len(broadcast_text) < 2:
        msg.reply("❌ Usage: /broadcast <message>\n\nOptions:\n/broadcast all <message> - Send to all users\n/broadcast active <message> - Send to active users (24h)")
        return
    
    full_text = broadcast_text[1]
    parts = full_text.split(maxsplit=1)
    
    # Check for targeting
    target = "all"
    message = full_text
    
    if len(parts) >= 2 and parts[0].lower() in ['all', 'active']:
        target = parts[0].lower()
        message = parts[1]
    
    # Get target users
    if target == 'active':
        target_users = [uid for uid, _ in admin_panel.get_active_users(24)]
        target_desc = "active users (24h)"
    else:
        target_users = list(admin_panel.get_all_users().keys())
        target_desc = "all users"
    
    if not target_users:
        msg.reply("❌ No users to broadcast to.")
        return
    
    # Confirm broadcast
    confirm_msg = msg.reply(f"📢 Broadcasting to {len(target_users)} {target_desc}...")
    
    success = 0
    failed = 0
    
    for uid in target_users:
        try:
            bot.send_message(uid, f"📢 **Broadcast Message:**\n\n{message}")
            success += 1
            time.sleep(0.05)  # Small delay to avoid flooding
        except Exception as e:
            failed += 1
            logger.error(f"Failed to send broadcast to {uid}: {e}")
    
    confirm_msg.edit(f"✅ Broadcast complete!\n\n✓ Sent: {success}\n✗ Failed: {failed}\n📊 Target: {target_desc}")

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
    
    stats = admin_panel.get_statistics()
    active_users = admin_panel.get_active_users(24)
    
    user_list = admin_panel.format_user_list(active_users, limit=20)
    
    users_text = f"""
👥 **User Management**

📊 **Statistics:**
Total users: {stats['total_users']}
Active (24h): {stats['active_24h']}
Total requests: {stats['total_requests']}
Total errors: {stats['total_errors']}
Blocked requests: {stats['total_blocks']}

**Active Users (Last 24h):**
{user_list}
"""
    msg.reply(users_text)

@bot.on_message(filters.private & filters.command('topusers'))
def topusers_command(bot, msg):
    user_id = msg.from_user.id
    
    if user_id not in config.ADMIN_IDS:
        msg.reply(constants.ERROR_PERMISSION_DENIED)
        return
    
    top_users = admin_panel.get_top_users(10)
    user_list = admin_panel.format_user_list(top_users, limit=10)
    
    msg.reply(f"🏆 **Top 10 Users by Activity:**\n\n{user_list}")

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
        target_user_id = int(parts[1])
        user_info = admin_panel.get_user_info(target_user_id)
        
        if not user_info or not user_info['first_seen']:
            msg.reply(f"❌ User {target_user_id} not found.")
            return
        
        name = user_info['first_name'] or user_info['username'] or f"User {target_user_id}"
        status = "🚫 Banned" if not user_info['is_active'] else "✅ Active"
        
        info_text = f"""
👤 **User Information**

**ID:** `{target_user_id}`
**Name:** {name}
**Username:** @{user_info['username'] or 'N/A'}
**Status:** {status}

📊 **Activity:**
Total requests: {user_info['total_requests']}
Total errors: {user_info['total_errors']}
Blocked requests: {user_info['blocked_count']}
Success rate: {((user_info['total_requests'] - user_info['total_errors']) / user_info['total_requests'] * 100) if user_info['total_requests'] > 0 else 0:.1f}%

⏰ **Timeline:**
First seen: {user_info['first_seen'].strftime('%Y-%m-%d %H:%M')}
Last seen: {user_info['last_seen'].strftime('%Y-%m-%d %H:%M')}
"""
        msg.reply(info_text)
    except ValueError:
        msg.reply("❌ Invalid user ID. Must be a number.")
    except Exception as e:
        msg.reply(f"❌ Error: {str(e)}")
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
        target_user_id = int(parts[1])
        
        if target_user_id in config.ADMIN_IDS:
            msg.reply("❌ Cannot ban an admin user.")
            return
        
        admin_panel.ban_user(target_user_id)
        msg.reply(f"✅ User {target_user_id} has been banned.")
        
        # Notify the banned user
        try:
            bot.send_message(target_user_id, "🚫 You have been banned from using this bot.")
        except Exception:
            pass
    except ValueError:
        msg.reply("❌ Invalid user ID. Must be a number.")
    except Exception as e:
        msg.reply(f"❌ Error: {str(e)}")

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
        target_user_id = int(parts[1])
        admin_panel.unban_user(target_user_id)
        msg.reply(f"✅ User {target_user_id} has been unbanned.")
        
        # Notify the user
        try:
            bot.send_message(target_user_id, "✅ You have been unbanned. You can now use the bot again.")
        except Exception:
            pass
    except ValueError:
        msg.reply("❌ Invalid user ID. Must be a number.")
    except Exception as e:
        msg.reply(f"❌ Error: {str(e)}")


	
@bot.on_message(filters.private & filters.regex("http"))
def scrap(bot, msg):
    url = msg.text.strip()
    user_id = msg.from_user.id
    
    # Register user activity
    admin_panel.register_user(user_id, msg.from_user.username, msg.from_user.first_name)
    activity_logger.log_activity(user_id, "scrape_request", url)
    
    # Check if user is banned
    if admin_panel.is_banned(user_id):
        msg.reply("🚫 You have been banned from using this bot. Contact admin for more information.")
        admin_panel.record_block(user_id)
        logger.warning(f"Banned user {user_id} attempted to use bot")
        return
    
    # Check rate limit
    if not check_rate_limit(user_id):
        msg.reply(constants.ERROR_RATE_LIMIT)
        logger.warning(f"Rate limit exceeded for user {user_id}")
        return
    
    # Check daily limit
    if not check_daily_limit(user_id):
        remaining_time = timedelta(days=1) - (datetime.now() - daily_requests[user_id][0])
        hours = int(remaining_time.total_seconds() // 3600)
        minutes = int((remaining_time.total_seconds() % 3600) // 60)
        msg.reply(f"📅 Daily limit reached (15 requests/day). Try again in {hours}h {minutes}m.")
        logger.warning(f"Daily limit exceeded for user {user_id}")
        return
    
    # Validate URL
    if not is_valid_url(url):
        msg.reply(constants.ERROR_INVALID_URL)
        logger.warning(f"Invalid URL from user {user_id}: {url}")
        return
    
    # Check domain safety
    domain = extract_domain(url)
    if not is_safe_domain(domain):
        msg.reply("⚠️ This domain is flagged as potentially unsafe. Request blocked.")
        logger.warning(f"Unsafe domain blocked: {domain}")
        return
    
    # Check robots.txt
    if not robots_checker.can_fetch(url):
        msg.reply("🤖 This URL is disallowed by robots.txt. Respecting website's crawling policy.")
        logger.info(f"URL blocked by robots.txt: {url}")
        return
    
    logger.info(f"Processing URL request from user {user_id}: {url}")
    
    # Check cache first
    cached_content, cached_meta = cache.get_cached_content(url)
    if cached_content and cached_meta:
        # Serve from cache
        domain = extract_domain(url)
        filename = sanitize_filename(f"source_{domain}.txt")
        
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(cached_content)
        
        msg.reply_document(
            filename,
            caption=f"✅ **Source code (cached)**\n\n🌐 Domain: `{domain}`\n📦 Size: {format_file_size(len(cached_content))}\n🔤 Encoding: {cached_meta.get('encoding', 'utf-8')}\n💾 Cached"
        )
        
        try:
            os.remove(filename)
        except Exception:
            pass
        
        request_stats[user_id] += 1
        logger.info(f"Served cached content for {url}")
        return
    
    # Send processing message
    processing_msg = msg.reply("⏳ Fetching webpage...")
    start_time = time.time()
    
    # Retry logic
    for attempt in range(config.MAX_RETRIES):
        try:
            # Use randomized headers and optional proxy
            headers = get_random_headers()
            proxies = config.get_proxies()
            request = requests.get(url, timeout=config.REQUEST_TIMEOUT, headers=headers, proxies=proxies)
            request.raise_for_status()
            break
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if attempt < config.MAX_RETRIES - 1:
                logger.warning(f"Attempt {attempt + 1} failed for {url}, retrying...")
                time.sleep(config.REQUEST_DELAY * (attempt + 1))  # Exponential backoff
                continue
            else:
                raise
    
    try:
        
        # Detect encoding
        detected = chardet.detect(request.content)
        encoding = detected['encoding'] or 'utf-8'
        
        # Check content size
        content_length = request.headers.get('content-length')
        if content_length and int(content_length) > config.MAX_FILE_SIZE:
            msg.reply(f"❌ File too large. Maximum size is {config.MAX_FILE_SIZE // (1024*1024)}MB.")
            logger.warning(f"File too large for {url}: {content_length} bytes")
            return
        
        soup = BeautifulSoup(request.content, 'html.parser', from_encoding=encoding)
        
        processing_msg.edit("📝 Generating source code file...")
        
        # Generate filename from URL
        domain = extract_domain(url)
        filename = sanitize_filename(f"source_{domain}.txt")
        
        # Write prettified HTML
        with open(filename, 'w', encoding="utf-8") as parse:
            prettified = soup.prettify()
            parse.write(prettified)
        
        # Save to cache
        cache.save_to_cache(url, prettified, {
            'domain': domain,
            'encoding': encoding,
            'size': len(prettified)
        })
        
        processing_msg.edit("📤 Sending file...")
        
        # Get file size
        file_size = os.path.getsize(filename)
        file_size_str = format_file_size(file_size)
        
        # Calculate processing time
        processing_time = time.time() - start_time
        
        msg.reply_document(
            filename,
            caption=constants.SUCCESS_EXTRACTED.format(
                domain=domain,
                size=file_size_str,
                encoding=encoding
            ) + f"\n⏱️ Time: {processing_time:.2f}s"
        )
        
        try:
            processing_msg.delete()
        except Exception:
            pass  # Message might already be deleted
            
        request_stats[user_id] += 1
        admin_panel.record_request(user_id, success=True)
        logger.info(f"Successfully sent source code for {url}")
        
        # Clean up temporary file
        try:
            os.remove(filename)
        except Exception as e:
            logger.warning(f"Failed to remove temp file: {e}")
            
    except requests.exceptions.Timeout:
        msg.reply(constants.ERROR_TIMEOUT)
        error_stats[user_id] += 1
        admin_panel.record_request(user_id, success=False)
        logger.error(f"Timeout error for URL: {url}")
    except requests.exceptions.ConnectionError:
        msg.reply(constants.ERROR_CONNECTION)
        error_stats[user_id] += 1
        admin_panel.record_request(user_id, success=False)
        logger.error(f"Connection error for URL: {url}")
    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code
        msg.reply(f"❌ HTTP Error {status_code}. The server returned an error response.")
        error_stats[user_id] += 1
        admin_panel.record_request(user_id, success=False)
        logger.error(f"HTTP {status_code} error for {url}")
    except requests.exceptions.RequestException as e:
        msg.reply(f"❌ Failed to fetch the webpage. Please try again later.")
        error_stats[user_id] += 1
        admin_panel.record_request(user_id, success=False)
        logger.error(f"Request error for {url}: {e}")
    except Exception as e:
        msg.reply(constants.ERROR_UNEXPECTED)
        error_stats[user_id] += 1
        admin_panel.record_request(user_id, success=False)
        logger.error(f"Unexpected error processing {url}: {e}")

       
@bot.on_message(filters.private & filters.text)
def show(bot, msg):
    msg.reply(text="**Your link must start from http like:\nhttps://www.google.com\n\nFor more feel free to contact the** [Developer](https://t.me/e_phador)", disable_web_page_preview=True, quote=True)
	    
    
logger.info(f"Starting {constants.BOT_NAME} v{constants.BOT_VERSION}")
bot.run()

@bot.on_message(filters.private & filters.command('logs'))
def logs_command(bot, msg):
    user_id = msg.from_user.id
    
    if user_id not in config.ADMIN_IDS:
        msg.reply(constants.ERROR_PERMISSION_DENIED)
        return
    
    parts = msg.text.split(maxsplit=1)
    
    if len(parts) >= 2:
        # Show logs for specific user
        try:
            target_user_id = int(parts[1])
            activities = activity_logger.get_user_activities(target_user_id, limit=20)
            formatted = activity_logger.format_activities(activities, limit=20)
            
            msg.reply(f"📋 **Activity Logs for User {target_user_id}:**\n\n```\n{formatted}\n```")
        except ValueError:
            msg.reply("❌ Invalid user ID. Must be a number.")
    else:
        # Show recent logs for all users
        activities = activity_logger.get_recent_activities(limit=30)
        formatted = activity_logger.format_activities(activities, limit=30)
        
        msg.reply(f"📋 **Recent Activity Logs:**\n\n```\n{formatted}\n```")
