"""Extract metadata from webpages"""
from bs4 import BeautifulSoup
import logging

logger = logging.getLogger(__name__)


def extract_metadata(soup):
    """Extract comprehensive metadata from HTML"""
    metadata = {
        'title': None,
        'description': None,
        'keywords': None,
        'author': None,
        'og_title': None,
        'og_description': None,
        'og_image': None,
        'twitter_card': None,
        'canonical': None,
        'language': None,
        'robots': None
    }
    
    try:
        # Title
        title_tag = soup.find('title')
        if title_tag:
            metadata['title'] = title_tag.get_text().strip()
        
        # Meta tags
        meta_tags = soup.find_all('meta')
        for tag in meta_tags:
            name = tag.get('name', '').lower()
            property_attr = tag.get('property', '').lower()
            content = tag.get('content', '')
            
            # Standard meta tags
            if name == 'description':
                metadata['description'] = content
            elif name == 'keywords':
                metadata['keywords'] = content
            elif name == 'author':
                metadata['author'] = content
            elif name == 'robots':
                metadata['robots'] = content
            
            # Open Graph
            elif property_attr == 'og:title':
                metadata['og_title'] = content
            elif property_attr == 'og:description':
                metadata['og_description'] = content
            elif property_attr == 'og:image':
                metadata['og_image'] = content
            
            # Twitter Card
            elif name == 'twitter:card':
                metadata['twitter_card'] = content
        
        # Canonical URL
        canonical = soup.find('link', rel='canonical')
        if canonical:
            metadata['canonical'] = canonical.get('href')
        
        # Language
        html_tag = soup.find('html')
        if html_tag:
            metadata['language'] = html_tag.get('lang')
        
    except Exception as e:
        logger.error(f"Error extracting metadata: {e}")
    
    return metadata


def format_metadata(metadata):
    """Format metadata for display"""
    lines = []
    
    if metadata.get('title'):
        lines.append(f"📄 **Title:** {metadata['title']}")
    
    if metadata.get('description'):
        desc = metadata['description'][:200] + '...' if len(metadata['description']) > 200 else metadata['description']
        lines.append(f"📝 **Description:** {desc}")
    
    if metadata.get('author'):
        lines.append(f"✍️ **Author:** {metadata['author']}")
    
    if metadata.get('language'):
        lines.append(f"🌐 **Language:** {metadata['language']}")
    
    if metadata.get('keywords'):
        keywords = metadata['keywords'][:100] + '...' if len(metadata['keywords']) > 100 else metadata['keywords']
        lines.append(f"🔑 **Keywords:** {keywords}")
    
    return '\n'.join(lines) if lines else "No metadata found"
