"""Configuration settings for the bot"""
import os
import sys

# Telegram API credentials — read from env, never hardcoded
API_ID = int(os.getenv('API_ID', '0'))
API_HASH = os.getenv('API_HASH', '')
BOT_TOKEN = os.getenv('BOT_TOKEN', '')

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

# Rate limiting (legacy, unused)
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

# Force join settings
FORCE_JOIN_CHANNEL = 3876315930
FREE_USAGE_LIMIT = 3

# Crawl settings
CRAWL_MAX_PAGES = int(os.getenv('CRAWL_MAX_PAGES', '200'))
CRAWL_MAX_DEPTH = int(os.getenv('CRAWL_MAX_DEPTH', '10'))
CRAWL_CONCURRENCY = int(os.getenv('CRAWL_CONCURRENCY', '0'))  # 0 = derive from CPU quota
CRAWL_PAGE_TIMEOUT = int(os.getenv('CRAWL_PAGE_TIMEOUT', '30000'))  # ms
CRAWL_ASSET_TIMEOUT = int(os.getenv('CRAWL_ASSET_TIMEOUT', '15000'))  # ms
CRAWL_MAX_ZIP_SIZE = 2 * 1024 * 1024 * 1024  # 2GB ZIP limit
CRAWL_TIMEOUT_SECONDS = int(os.getenv('CRAWL_TIMEOUT_SECONDS', '300'))  # 5 min hard cap

# Queue / admission control settings
CRAWL_MAX_CONCURRENT = int(os.getenv('CRAWL_MAX_CONCURRENT', '4'))  # hard ceiling on concurrent crawls
CRAWL_MEMORY_BUDGET_MB = int(os.getenv('CRAWL_MEMORY_BUDGET_MB', '1000'))  # RAM assumed per crawl
CRAWL_MEMORY_RESERVE_MB = int(os.getenv('CRAWL_MEMORY_RESERVE_MB', '1024'))  # RAM held back for sibling apps
CRAWL_MAX_QUEUE_SIZE = int(os.getenv('CRAWL_MAX_QUEUE_SIZE', '100'))  # max waiting users
CRAWL_MAX_QUEUE_AGE_MINUTES = int(os.getenv('CRAWL_MAX_QUEUE_AGE_MINUTES', '45'))  # max queue wait time
CRAWL_BROWSER_RETRY_LIMIT = int(os.getenv('CRAWL_BROWSER_RETRY_LIMIT', '2'))  # total launch attempts
CRAWL_BROWSER_RETRY_BASE_SECONDS = int(os.getenv('CRAWL_BROWSER_RETRY_BASE_SECONDS', '5'))  # backoff base
CRAWL_LAUNCH_FAILURE_THRESHOLD = int(os.getenv('CRAWL_LAUNCH_FAILURE_THRESHOLD', '3'))  # consecutive failures to trip breaker
CRAWL_LAUNCH_SUSPEND_SECONDS = int(os.getenv('CRAWL_LAUNCH_SUSPEND_SECONDS', '30'))  # suspension duration
CRAWL_RESOURCE_PROBE_INTERVAL = int(os.getenv('CRAWL_RESOURCE_PROBE_INTERVAL', '2'))  # memory probe interval (seconds)
CRAWL_CPU_PROBE_INTERVAL = int(os.getenv('CRAWL_CPU_PROBE_INTERVAL', '15'))  # CPU probe interval (seconds)
CRAWL_POSITION_REFRESH_SECONDS = int(os.getenv('CRAWL_POSITION_REFRESH_SECONDS', '120'))  # fast refresh cadence
CRAWL_POSITION_REFRESH_SECONDS_LONG = int(os.getenv('CRAWL_POSITION_REFRESH_SECONDS_LONG', '600'))  # slow refresh cadence
CRAWL_POSITION_REFRESH_TOP_N = int(os.getenv('CRAWL_POSITION_REFRESH_TOP_N', '20'))  # how many get fast cadence
CRAWL_ESTIMATE_MINUTES_PER_CRAWL = int(os.getenv('CRAWL_ESTIMATE_MINUTES_PER_CRAWL', '2'))  # fallback per-crawl duration
CRAWL_ORPHAN_MAX_AGE_MINUTES = int(os.getenv('CRAWL_ORPHAN_MAX_AGE_MINUTES', '60'))  # age for orphan sweep
CRAWL_ASSET_CONCURRENCY = int(os.getenv('CRAWL_ASSET_CONCURRENCY', '4'))  # concurrent asset fetches
CLEANUP_INTERVAL_SECONDS = int(os.getenv('CLEANUP_INTERVAL_SECONDS', '900'))  # maintenance interval
CRAWL_FORCE_LAUNCH_FAILURE = int(os.getenv('CRAWL_FORCE_LAUNCH_FAILURE', '0'))  # fault injection: first N launches fail
CRAWL_FORCE_LAUNCH_FAILURE_KIND = os.getenv('CRAWL_FORCE_LAUNCH_FAILURE_KIND', 'resource_pressure')  # kind of injected failure
CRAWL_CPU_QUOTA_OVERRIDE = int(os.getenv('CRAWL_CPU_QUOTA_OVERRIDE', '0'))  # override CPU quota detection (0=auto)

# Logging
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'