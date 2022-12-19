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
request_stats = defaultdict(int)  # Track total requests per user

def is_valid_url(url):
    """Validate URL format and scheme"""
    try:
        # Remove whitespace and common issues
        url = url.strip()
        
        result = urlparse(url)
        
        # Check scheme and netloc
        if not all([result.scheme in ['http', 'https'], result.netloc]):
            return False
        
        # Block localhost and private IPs
        blocked_hosts = ['localhost', '127.0.0.1', '0.0.0.0', '::1']
        if result.netloc.split(':')[0] in blocked_hosts:
            return False
        
        # Block private IP ranges
        if result.netloc.startswith(('10.', '172.', '192.168.')):
            return False
            
        return True
    except Exception:
        return False

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

@bot.on_message(filters.private & filters.command('start'))
def start(bot, msg):
    msg.reply(f'Hello {msg.from_user.first_name}! I am a webpage source code downloader bot. Just send me a link.')

@bot.on_message(filters.private & filters.command('help'))
def help_command(bot, msg):
    help_text = """
**📖 How to use this bot:**

1️⃣ Send me any webpage URL (starting with http:// or https://)
2️⃣ I'll fetch and send you the HTML source code
3️⃣ Rate limit: 5 requests per minute

**Example:**
`https://www.example.com`

**Commands:**
/start - Start the bot
/help - Show this help message
/stats - Show your usage statistics

**Need help?** Contact: [Developer](https://t.me/e_phador)
"""
    msg.reply(help_text, disable_web_page_preview=True)

@bot.on_message(filters.private & filters.command('stats'))
def stats_command(bot, msg):
    user_id = msg.from_user.id
    total = request_stats.get(user_id, 0)
    msg.reply(f"📊 **Your Statistics:**\n\nTotal requests: {total}")

@bot.on_message(filters.private & filters.command('admin'))
def admin_command(bot, msg):
    user_id = msg.from_user.id
    
    if user_id not in config.ADMIN_IDS:
        msg.reply("❌ You don't have permission to use this command.")
        return
    
    total_users = len(request_stats)
    total_requests = sum(request_stats.values())
    
    admin_text = f"""
🔧 **Admin Statistics:**

👥 Total users: {total_users}
📊 Total requests: {total_requests}
⚡ Rate limit: {config.RATE_LIMIT}/min
💾 Max file size: {config.MAX_FILE_SIZE // (1024*1024)}MB

**Commands:**
/broadcast <message> - Send message to all users
"""
    msg.reply(admin_text)

@bot.on_message(filters.private & filters.command('broadcast'))
def broadcast_command(bot, msg):
    user_id = msg.from_user.id
    
    if user_id not in config.ADMIN_IDS:
        msg.reply("❌ You don't have permission to use this command.")
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
        msg.reply("⏳ Rate limit exceeded. Please wait a minute before making more requests.")
        logger.warning(f"Rate limit exceeded for user {user_id}")
        return
    
    # Validate URL
    if not is_valid_url(url):
        msg.reply("❌ Invalid URL format. Please provide a valid http:// or https:// URL.")
        logger.warning(f"Invalid URL from user {user_id}: {url}")
        return
    
    logger.info(f"Processing URL request from user {user_id}: {url}")
    
    # Send processing message
    processing_msg = msg.reply("⏳ Fetching webpage...")
    
    try:
        request = requests.get(url, timeout=config.REQUEST_TIMEOUT, headers=config.REQUEST_HEADERS)
        request.raise_for_status()
        
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
        from urllib.parse import quote
        domain = urlparse(url).netloc.replace('www.', '')
        filename = f"source_{domain}.txt"
        
        # Write prettified HTML
        with open(filename, 'w', encoding="utf-8") as parse:
            parse.write(soup.prettify())
        
        processing_msg.edit("📤 Sending file...")
        
        # Get file size
        file_size = os.path.getsize(filename)
        file_size_kb = file_size / 1024
        
        msg.reply_document(
            filename,
            caption=f"✅ **Source code extracted**\n\n🌐 Domain: `{domain}`\n📦 Size: {file_size_kb:.2f} KB\n🔤 Encoding: {encoding}"
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
        msg.reply("⏱️ Request timed out. The website took too long to respond. Please try again later.")
        logger.error(f"Timeout error for URL: {url}")
    except requests.exceptions.ConnectionError:
        msg.reply("🔌 Connection error. Unable to reach the website. Please check the URL and try again.")
        logger.error(f"Connection error for URL: {url}")
    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code
        msg.reply(f"❌ HTTP Error {status_code}. The server returned an error response.")
        logger.error(f"HTTP {status_code} error for {url}")
    except requests.exceptions.RequestException as e:
        msg.reply(f"❌ Failed to fetch the webpage. Please try again later.")
        logger.error(f"Request error for {url}: {e}")
    except Exception as e:
        msg.reply("❌ An unexpected error occurred while processing your request.")
        logger.error(f"Unexpected error processing {url}: {e}")

       
@bot.on_message(filters.private & filters.text)
def show(bot, msg):
    msg.reply(text="**Your link must start from http like:\nhttps://www.google.com\n\nFor more feel free to contact the** [Developer](https://t.me/e_phador)", disable_web_page_preview=True, quote=True)
	    
    
bot.run()