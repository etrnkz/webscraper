"""Sitemap.xml parser for discovering URLs"""
import requests
import xml.etree.ElementTree as ET
from urllib.parse import urljoin
import logging

logger = logging.getLogger(__name__)


def get_sitemap_url(base_url):
    """Get sitemap URL for a domain"""
    return urljoin(base_url, '/sitemap.xml')


def parse_sitemap(sitemap_url, timeout=30):
    """Parse sitemap.xml and extract URLs"""
    urls = []
    
    try:
        response = requests.get(sitemap_url, timeout=timeout)
        response.raise_for_status()
        
        root = ET.fromstring(response.content)
        
        # Handle different sitemap namespaces
        namespaces = {
            'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9',
            'news': 'http://www.google.com/schemas/sitemap-news/0.9',
            'image': 'http://www.google.com/schemas/sitemap-image/1.1',
            'video': 'http://www.google.com/schemas/sitemap-video/1.1'
        }
        
        # Check if it's a sitemap index
        sitemaps = root.findall('.//sm:sitemap/sm:loc', namespaces)
        if sitemaps:
            # It's a sitemap index, parse each sitemap
            for sitemap in sitemaps[:5]:  # Limit to 5 sitemaps
                try:
                    sub_urls = parse_sitemap(sitemap.text, timeout)
                    urls.extend(sub_urls)
                except Exception as e:
                    logger.error(f"Failed to parse sub-sitemap {sitemap.text}: {e}")
        else:
            # It's a regular sitemap
            url_elements = root.findall('.//sm:url/sm:loc', namespaces)
            urls = [url.text for url in url_elements if url.text]
        
        logger.info(f"Found {len(urls)} URLs in sitemap")
        return urls
    
    except Exception as e:
        logger.error(f"Failed to parse sitemap {sitemap_url}: {e}")
        return []


def discover_urls(base_url, max_urls=100):
    """Discover URLs from sitemap"""
    sitemap_url = get_sitemap_url(base_url)
    urls = parse_sitemap(sitemap_url)
    
    # Limit number of URLs
    return urls[:max_urls]
