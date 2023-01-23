"""Tests for metadata extraction"""

from bs4 import BeautifulSoup
from bot.modules.metadata_parser import extract_metadata, format_metadata


def test_extract_metadata():
    html = """
    <html lang="en">
    <head>
        <title>Test Page</title>
        <meta name="description" content="A test page">
        <meta name="author" content="Test Author">
        <meta property="og:title" content="OG Test">
    </head>
    <body></body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    meta = extract_metadata(soup)

    assert meta["title"] == "Test Page"
    assert meta["description"] == "A test page"
    assert meta["author"] == "Test Author"
    assert meta["og_title"] == "OG Test"
    assert meta["language"] == "en"


def test_extract_metadata_empty():
    soup = BeautifulSoup("<html></html>", "html.parser")
    meta = extract_metadata(soup)
    assert meta["title"] is None


def test_format_metadata():
    metadata = {
        "title": "Test",
        "description": "Desc",
        "author": None,
        "keywords": None,
        "og_title": None,
        "og_description": None,
        "og_image": None,
        "twitter_card": None,
        "canonical": None,
        "language": "en",
        "robots": None,
    }
    result = format_metadata(metadata)
    assert "Test" in result
    assert "Desc" in result
    assert "en" in result
