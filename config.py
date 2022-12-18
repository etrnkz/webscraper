"""Configuration settings for the bot"""
import os

# Telegram API credentials
API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')

# Rate limiting
RATE_LIMIT = 5  # requests per minute
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

# Request settings
REQUEST_TIMEOUT = 30  # seconds
REQUEST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# Logging
LOG_LEVEL = 'INFO'
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
