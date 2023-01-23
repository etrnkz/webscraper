"""Utility functions for the bot"""
import re
from urllib.parse import urlparse
from typing import Union


def sanitize_filename(text: str) -> str:
    """Remove invalid characters from filename"""
    return re.sub(r'[<>:"/\\|?*]', '_', text)


def format_file_size(size_bytes: Union[int, float]) -> str:
    """Format bytes to human readable size"""
    size = float(size_bytes)
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TB"


def extract_domain(url: str) -> str:
    """Extract clean domain from URL"""
    parsed = urlparse(url)
    domain = parsed.netloc.replace('www.', '')
    return domain
