import requests 
from bs4 import BeautifulSoup 
from pyrogram import Client, filters
import os
import logging
import re
from urllib.parse import urlparse

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load credentials from environment variables
API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')

bot = Client(
    'my_bot',
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

def is_valid_url(url):
    """Validate URL format and scheme"""
    try:
        result = urlparse(url)
        return all([result.scheme in ['http', 'https'], result.netloc])
    except Exception:
        return False

@bot.on_message(filters.private & filters.command('start'))
def start(bot, msg):
    msg.reply(f'Hello {msg.from_user.first_name}! I am a webpage source code downloader bot. Just send me a link.')


	
@bot.on_message(filters.private & filters.regex("http"))
def scrap(bot, msg):
    url = msg.text.strip()
    
    # Validate URL
    if not is_valid_url(url):
        msg.reply("❌ Invalid URL format. Please provide a valid http:// or https:// URL.")
        logger.warning(f"Invalid URL from user {msg.from_user.id}: {url}")
        return
    
    logger.info(f"Processing URL request from user {msg.from_user.id}: {url}")
    
    try:
        request = requests.get(url, timeout=30)
        request.raise_for_status()
        
        soup = BeautifulSoup(request.content, 'html.parser')
        
        with open('source-code.txt', 'w', encoding="utf-8") as parse:
            parse.write(soup.prettify())
        
        msg.reply_document("source-code.txt")
        logger.info(f"Successfully sent source code for {url}")
        
        # Clean up temporary file
        try:
            os.remove('source-code.txt')
        except Exception as e:
            logger.warning(f"Failed to remove temp file: {e}")
            
    except requests.exceptions.Timeout:
        msg.reply("⏱️ Request timed out. The website took too long to respond.")
        logger.error(f"Timeout error for URL: {url}")
    except requests.exceptions.RequestException as e:
        msg.reply(f"❌ Failed to fetch the webpage. Error: {str(e)}")
        logger.error(f"Request error for {url}: {e}")
    except Exception as e:
        msg.reply("❌ An unexpected error occurred while processing your request.")
        logger.error(f"Unexpected error processing {url}: {e}")

       
@bot.on_message(filters.private & filters.text)
def show(bot, msg):
    msg.reply(text="**Your link must start from http like:\nhttps://www.google.com\n\nFor more feel free to contact the** [Developer](https://t.me/e_phador)", disable_web_page_preview=True, quote=True)
	    
    
bot.run()