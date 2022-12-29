"""Utility functions for the bot"""
import re
from urllib.parse import urlparse


def sanitize_filename(text):
    """Remove invalid characters from filename"""
    return re.sub(r'[<>:"/\\|?*]', '_', text)


def format_file_size(size_bytes):
    """Format bytes to human readable size"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"


def extract_domain(url):
    """Extract clean domain from URL"""
    parsed = urlparse(url)
    domain = parsed.netloc.replace('www.', '')
    return domain
