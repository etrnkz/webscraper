"""Robots.txt compliance checker"""
import requests
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
import logging

logger = logging.getLogger(__name__)


class RobotsChecker:
    def __init__(self):
        self.parsers = {}  # Cache robots.txt parsers
    
    def get_robots_url(self, url):
        """Get robots.txt URL for a given URL"""
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        return robots_url
    
    def get_parser(self, url):
        """Get or create robots.txt parser for domain"""
        robots_url = self.get_robots_url(url)
        
        if robots_url in self.parsers:
            return self.parsers[robots_url]
        
        parser = RobotFileParser()
        parser.set_url(robots_url)
        
        try:
            parser.read()
            self.parsers[robots_url] = parser
            logger.info(f"Loaded robots.txt from {robots_url}")
        except Exception as e:
            logger.warning(f"Could not load robots.txt from {robots_url}: {e}")
            # If robots.txt doesn't exist or fails, allow all
            self.parsers[robots_url] = None
        
        return self.parsers[robots_url]
    
    def can_fetch(self, url, user_agent="*"):
        """Check if URL can be fetched according to robots.txt"""
        parser = self.get_parser(url)
        
        if parser is None:
            # No robots.txt or failed to load, allow
            return True
        
        try:
            return parser.can_fetch(user_agent, url)
        except Exception as e:
            logger.error(f"Error checking robots.txt for {url}: {e}")
            return True  # Allow on error
    
    def get_crawl_delay(self, url, user_agent="*"):
        """Get crawl delay from robots.txt"""
        parser = self.get_parser(url)
        
        if parser is None:
            return None
        
        try:
            return parser.crawl_delay(user_agent)
        except Exception:
            return None


# Global instance
robots_checker = RobotsChecker()
