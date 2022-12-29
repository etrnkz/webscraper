"""Configuration settings for the bot"""
import os
import sys

# Telegram API credentials
API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')

# Validate required environment variables
def validate_config():
    """Validate that all required config is present"""
    required = {
        'API_ID': API_ID,
        'API_HASH': API_HASH,
        'BOT_TOKEN': BOT_TOKEN
    }
    
    missing = [key for key, value in required.items() if not value]
    
    if missing:
        print(f"❌ Missing required environment variables: {', '.join(missing)}")
        print("Please set them in your .env file or environment.")
        sys.exit(1)

# Rate limiting
RATE_LIMIT = 5  # requests per minute
DAILY_LIMIT = 15  # requests per day
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

# Request settings
REQUEST_TIMEOUT = 30  # seconds
MAX_RETRIES = 3
REQUEST_DELAY = float(os.getenv('REQUEST_DELAY', '0.5'))  # Delay between requests
REQUEST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# Proxy settings (optional)
PROXY_ENABLED = os.getenv('PROXY_ENABLED', 'false').lower() == 'true'
PROXY_HTTP = os.getenv('PROXY_HTTP', '')
PROXY_HTTPS = os.getenv('PROXY_HTTPS', '')

def get_proxies():
    """Get proxy configuration if enabled"""
    if PROXY_ENABLED and (PROXY_HTTP or PROXY_HTTPS):
        proxies = {}
        if PROXY_HTTP:
            proxies['http'] = PROXY_HTTP
        if PROXY_HTTPS:
            proxies['https'] = PROXY_HTTPS
        return proxies
    return None

# Admin settings
ADMIN_IDS = [int(x) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip()]

# Force subscribe settings
FORCE_SUBSCRIBE_ENABLED = os.getenv('FORCE_SUBSCRIBE_ENABLED', 'false').lower() == 'true'
FORCE_SUBSCRIBE_CHANNELS = [x.strip() for x in os.getenv('FORCE_SUBSCRIBE_CHANNELS', '').split(',') if x.strip()]

# Convert channel IDs to integers
try:
    FORCE_SUBSCRIBE_CHANNELS = [int(ch) if ch.lstrip('-').isdigit() else ch for ch in FORCE_SUBSCRIBE_CHANNELS]
except ValueError:
    FORCE_SUBSCRIBE_CHANNELS = []

# Logging
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
