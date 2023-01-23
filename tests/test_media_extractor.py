"""Tests for media extraction"""

from bs4 import BeautifulSoup
from bot.modules.media_extractor import extract_media_urls


def test_extract_media_urls():
    html = """
    <html>
    <body>
        <img src="/image.jpg">
        <img src="https://cdn.example.com/photo.png" data-src="/lazy.jpg">
        <video src="/video.mp4"></video>
        <link rel="stylesheet" href="/style.css">
        <script src="/app.js"></script>
    </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    media = extract_media_urls(soup, "https://example.com")

    assert any("image.jpg" in url for url in media["images"])
    assert any("photo.png" in url for url in media["images"])
    assert any("video.mp4" in url for url in media["videos"])
    assert any("style.css" in url for url in media["css"])
    assert any("app.js" in url for url in media["js"])


def test_extract_media_urls_empty():
    soup = BeautifulSoup("<html></html>", "html.parser")
    media = extract_media_urls(soup, "https://example.com")
    assert all(len(urls) == 0 for urls in media.values())
