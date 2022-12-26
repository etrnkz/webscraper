import requests 
from bs4 import BeautifulSoup 
from pyrogram import Client, filters
import os
import logging
import re
from urllib.parse import urlparse
from collections import defaultdict
from datetime import datetime, timedelta
import config
import chardet
import time
from utils import sanitize_filename, format_file_size, extract_domain
import constants
from validators import is_valid_url, is_safe_domain
from user_agents import get_random_headers
import cache
import media_scraper
import shutil
from download_manager import DownloadManager
import zipfile
from robots_checker import robots_checker

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
    msg.reply(constants.WELCOME_MESSAGE.format(name=msg.from_user.first_name))

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
    
    admin_text = f"""
🔧 **Admin Statistics:**

👥 Total users: {total_users}
📊 Total requests: {total_requests}
❌ Total errors: {total_errors}
📈 Avg requests/user: {avg_requests:.2f}
⏰ Uptime: {uptime_str}
⚡ Rate limit: {config.RATE_LIMIT}/min
💾 Max file size: {config.MAX_FILE_SIZE // (1024*1024)}MB
🔄 Max retries: {config.MAX_RETRIES}

**Commands:**
/broadcast <message> - Send message to all users
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
        msg.reply("❌ Usage: /broadcast <message>")
        return
    
    broadcast_text = broadcast_text[1]
    success = 0
    failed = 0
    
    for uid in request_stats.keys():
        try:
            bot.send_message(uid, f"📢 **Broadcast Message:**\n\n{broadcast_text}")
            success += 1
        except Exception as e:
            failed += 1
            logger.error(f"Failed to send broadcast to {uid}: {e}")
    
    msg.reply(f"✅ Broadcast sent!\n\n✓ Success: {success}\n✗ Failed: {failed}")


	
@bot.on_message(filters.private & filters.regex("http"))
def scrap(bot, msg):
    url = msg.text.strip()
    user_id = msg.from_user.id
    
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
        logger.info(f"Successfully sent source code for {url}")
        
        # Clean up temporary file
        try:
            os.remove(filename)
        except Exception as e:
            logger.warning(f"Failed to remove temp file: {e}")
            
    except requests.exceptions.Timeout:
        msg.reply(constants.ERROR_TIMEOUT)
        error_stats[user_id] += 1
        logger.error(f"Timeout error for URL: {url}")
    except requests.exceptions.ConnectionError:
        msg.reply(constants.ERROR_CONNECTION)
        error_stats[user_id] += 1
        logger.error(f"Connection error for URL: {url}")
    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code
        msg.reply(f"❌ HTTP Error {status_code}. The server returned an error response.")
        error_stats[user_id] += 1
        logger.error(f"HTTP {status_code} error for {url}")
    except requests.exceptions.RequestException as e:
        msg.reply(f"❌ Failed to fetch the webpage. Please try again later.")
        error_stats[user_id] += 1
        logger.error(f"Request error for {url}: {e}")
    except Exception as e:
        msg.reply(constants.ERROR_UNEXPECTED)
        error_stats[user_id] += 1
        logger.error(f"Unexpected error processing {url}: {e}")

       
@bot.on_message(filters.private & filters.text)
def show(bot, msg):
    msg.reply(text="**Your link must start from http like:\nhttps://www.google.com\n\nFor more feel free to contact the** [Developer](https://t.me/e_phador)", disable_web_page_preview=True, quote=True)
	    
    
logger.info(f"Starting {constants.BOT_NAME} v{constants.BOT_VERSION}")
bot.run()