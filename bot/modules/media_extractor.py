"""Media scraping functionality"""
import os
import requests
import logging
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from bot.utils.user_agents import get_random_headers

logger = logging.getLogger(__name__)


def extract_media_urls(soup, base_url):
    """Extract all media URLs from HTML"""
    media = {
        'images': [],
        'videos': [],
        'css': [],
        'js': []
    }
    
    # Extract images
    for img in soup.find_all('img'):
        src = img.get('src') or img.get('data-src')
        if src:
            full_url = urljoin(base_url, src)
            media['images'].append(full_url)
    
    # Extract videos
    for video in soup.find_all('video'):
        src = video.get('src')
        if src:
            full_url = urljoin(base_url, src)
            media['videos'].append(full_url)
    
    for source in soup.find_all('source'):
        src = source.get('src')
        if src:
            full_url = urljoin(base_url, src)
            if source.get('type', '').startswith('video'):
                media['videos'].append(full_url)
    
    # Extract CSS
    for link in soup.find_all('link', rel='stylesheet'):
        href = link.get('href')
        if href:
            full_url = urljoin(base_url, href)
            media['css'].append(full_url)
    
    # Extract JavaScript
    for script in soup.find_all('script', src=True):
        src = script.get('src')
        if src:
            full_url = urljoin(base_url, src)
            media['js'].append(full_url)
    
    return media


def download_media(url, output_dir, timeout=30):
    """Download a single media file"""
    try:
        headers = get_random_headers()
        response = requests.get(url, timeout=timeout, headers=headers, stream=True)
        response.raise_for_status()
        
        # Get filename from URL
        parsed = urlparse(url)
        filename = os.path.basename(parsed.path) or 'index.html'
        filepath = os.path.join(output_dir, filename)
        
        # Ensure unique filename
        counter = 1
        base, ext = os.path.splitext(filepath)
        while os.path.exists(filepath):
            filepath = f"{base}_{counter}{ext}"
            counter += 1
        
        # Download file
        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        logger.info(f"Downloaded: {filename}")
        return filepath
    except Exception as e:
        logger.error(f"Failed to download {url}: {e}")
        return None


def scrape_media(url, soup, output_dir, media_types=['images']):
    """Scrape specified media types from webpage"""
    os.makedirs(output_dir, exist_ok=True)
    
    media_urls = extract_media_urls(soup, url)
    downloaded = []
    
    for media_type in media_types:
        if media_type in media_urls:
            type_dir = os.path.join(output_dir, media_type)
            os.makedirs(type_dir, exist_ok=True)
            
            for media_url in media_urls[media_type][:10]:  # Limit to 10 per type
                filepath = download_media(media_url, type_dir)
                if filepath:
                    downloaded.append(filepath)
    
    return downloaded
