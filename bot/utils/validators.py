"""Input validation and URL normalization"""
from urllib.parse import urlparse, urljoin
import ipaddress
import re


def normalize_url(url: str) -> str:
    """Auto-prepend https:// if no scheme present"""
    url = url.strip()
    if not url:
        return url
    if not re.match(r'^https?://', url, re.IGNORECASE):
        url = 'https://' + url
    return url


def is_valid_url(url: str) -> bool:
    """Validate URL format, scheme, and block private IPs"""
    try:
        url = normalize_url(url)
        result = urlparse(url)

        if result.scheme.lower() not in ('http', 'https'):
            return False
        if not result.netloc:
            return False

        host = result.netloc.split(':')[0].lower()

        blocked = ['localhost', '127.0.0.1', '0.0.0.0', '::1']
        if host in blocked:
            return False

        try:
            addr = ipaddress.ip_address(host)
            if addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_link_local:
                return False
        except ValueError:
            pass

        return True
    except Exception:
        return False


def is_safe_domain(domain: str) -> bool:
    """Block known malicious TLDs"""
    dangerous_tlds = ['.tk', '.ml', '.ga', '.cf', '.gq']
    return not any(domain.lower().endswith(tld) for tld in dangerous_tlds)
