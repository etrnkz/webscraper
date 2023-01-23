"""Tests for utility helper functions"""

from bot.utils.helpers import sanitize_filename, format_file_size, extract_domain


def test_sanitize_filename():
    assert sanitize_filename("normal.txt") == "normal.txt"
    assert sanitize_filename('file<>:"/\\|?*name') == "file_________name"
    assert sanitize_filename("") == ""


def test_format_file_size():
    assert format_file_size(0) == "0.00 B"
    assert format_file_size(1024) == "1.00 KB"
    assert format_file_size(1048576) == "1.00 MB"
    assert format_file_size(1073741824) == "1.00 GB"


def test_extract_domain():
    assert extract_domain("https://www.example.com/page") == "example.com"
    assert extract_domain("http://example.com") == "example.com"
    assert extract_domain("https://sub.domain.org") == "sub.domain.org"
