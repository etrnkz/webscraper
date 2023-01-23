"""Input validation functions"""
from urllib.parse import urlparse
from typing import Optional


def is_valid_url(url: str) -> bool:
    """Validate URL format and scheme"""
    try:
        url = url.strip()
        result = urlparse(url)

        if not all([result.scheme in ['http', 'https'], result.netloc]):
            return False

        blocked_hosts = ['localhost', '127.0.0.1', '0.0.0.0', '::1']
        if result.netloc.split(':')[0] in blocked_hosts:
            return False

        if result.netloc.startswith(('10.', '172.', '192.168.')):
            return False

        return True
    except Exception:
        return False


def is_safe_domain(domain: str) -> bool:
    """Check if domain is safe to access"""
    dangerous_tlds = ['.tk', '.ml', '.ga', '.cf', '.gq']
    return not any(domain.endswith(tld) for tld in dangerous_tlds)
