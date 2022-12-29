"""Input validation functions"""
from urllib.parse import urlparse
import re


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


def is_safe_domain(domain):
    """Check if domain is safe to access"""
    # Block common malicious TLDs
    dangerous_tlds = ['.tk', '.ml', '.ga', '.cf', '.gq']
    return not any(domain.endswith(tld) for tld in dangerous_tlds)
