"""Caching system for scraped content"""
import os
import json
import hashlib
from datetime import datetime, timedelta
import logging
from performance import record_cache_hit, record_cache_miss

logger = logging.getLogger(__name__)

CACHE_DIR = "cache"
CACHE_EXPIRY_HOURS = 24


def init_cache():
    """Initialize cache directory"""
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)
        logger.info(f"Created cache directory: {CACHE_DIR}")


def get_cache_key(url):
    """Generate cache key from URL"""
    return hashlib.md5(url.encode()).hexdigest()


def is_cache_valid(cache_file):
    """Check if cache file is still valid"""
    if not os.path.exists(cache_file):
        return False
    
    # Check file age
    file_time = datetime.fromtimestamp(os.path.getmtime(cache_file))
    expiry_time = datetime.now() - timedelta(hours=CACHE_EXPIRY_HOURS)
    
    return file_time > expiry_time


def get_cached_content(url):
    """Retrieve cached content if available"""
    cache_key = get_cache_key(url)
    cache_file = os.path.join(CACHE_DIR, f"{cache_key}.html")
    meta_file = os.path.join(CACHE_DIR, f"{cache_key}.meta")
    
    if is_cache_valid(cache_file) and os.path.exists(meta_file):
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            with open(meta_file, 'r') as f:
                metadata = json.load(f)
            
            logger.info(f"Cache hit for URL: {url}")
            record_cache_hit()
            return content, metadata
        except Exception as e:
            logger.error(f"Error reading cache: {e}")
            record_cache_miss()
            return None, None
    
    logger.info(f"Cache miss for URL: {url}")
    record_cache_miss()
    return None, None


def save_to_cache(url, content, metadata):
    """Save content to cache"""
    try:
        init_cache()
        cache_key = get_cache_key(url)
        cache_file = os.path.join(CACHE_DIR, f"{cache_key}.html")
        meta_file = os.path.join(CACHE_DIR, f"{cache_key}.meta")
        
        with open(cache_file, 'w', encoding='utf-8') as f:
            f.write(content)
        
        metadata['cached_at'] = datetime.now().isoformat()
        with open(meta_file, 'w') as f:
            json.dump(metadata, f)
        
        logger.info(f"Saved to cache: {url}")
        return True
    except Exception as e:
        logger.error(f"Error saving to cache: {e}")
        return False


def clear_expired_cache():
    """Remove expired cache files"""
    if not os.path.exists(CACHE_DIR):
        return
    
    removed = 0
    for filename in os.listdir(CACHE_DIR):
        filepath = os.path.join(CACHE_DIR, filename)
        if not is_cache_valid(filepath):
            try:
                os.remove(filepath)
                removed += 1
            except Exception as e:
                logger.error(f"Error removing cache file {filename}: {e}")
    
    if removed > 0:
        logger.info(f"Cleared {removed} expired cache files")
