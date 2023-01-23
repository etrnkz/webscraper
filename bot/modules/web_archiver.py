"""Download manager for recursive and batch downloads"""
import os
import logging
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import requests
from bot.utils.user_agents import get_random_headers
import time

logger = logging.getLogger(__name__)


class DownloadManager:
    def __init__(self, max_depth=2, max_files=50, delay=1):
        self.max_depth = max_depth
        self.max_files = max_files
        self.delay = delay
        self.visited = set()
        self.downloaded = []
    
    def is_same_domain(self, url1, url2):
        """Check if two URLs are from the same domain"""
        domain1 = urlparse(url1).netloc
        domain2 = urlparse(url2).netloc
        return domain1 == domain2
    
    def extract_links(self, soup, base_url):
        """Extract all links from HTML"""
        links = []
        for a in soup.find_all('a', href=True):
            href = a['href']
            full_url = urljoin(base_url, href)
            
            # Only include same-domain links
            if self.is_same_domain(base_url, full_url):
                links.append(full_url)
        
        return list(set(links))  # Remove duplicates
    
    def download_page(self, url, output_dir, timeout=30):
        """Download a single page"""
        try:
            if url in self.visited:
                return None
            
            self.visited.add(url)
            
            headers = get_random_headers()
            response = requests.get(url, timeout=timeout, headers=headers)
            response.raise_for_status()
            
            # Generate filename
            parsed = urlparse(url)
            path = parsed.path.strip('/').replace('/', '_') or 'index'
            filename = f"{path}.html"
            filepath = os.path.join(output_dir, filename)
            
            # Save file
            with open(filepath, 'wb') as f:
                f.write(response.content)
            
            logger.info(f"Downloaded: {url}")
            self.downloaded.append(filepath)
            
            return response.content
        except Exception as e:
            logger.error(f"Failed to download {url}: {e}")
            return None
    
    def recursive_download(self, start_url, output_dir, depth=0):
        """Recursively download pages"""
        if depth > self.max_depth or len(self.downloaded) >= self.max_files:
            return
        
        content = self.download_page(start_url, output_dir)
        if not content:
            return
        
        # Parse and find links
        soup = BeautifulSoup(content, 'html.parser')
        links = self.extract_links(soup, start_url)
        
        # Download linked pages
        for link in links:
            if len(self.downloaded) >= self.max_files:
                break
            
            if link not in self.visited:
                time.sleep(self.delay)  # Rate limiting
                self.recursive_download(link, output_dir, depth + 1)
    
    def get_stats(self):
        """Get download statistics"""
        return {
            'total_downloaded': len(self.downloaded),
            'total_visited': len(self.visited),
            'files': self.downloaded
        }
