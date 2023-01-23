"""Tests for URL validation functions"""

from bot.utils.validators import is_valid_url, is_safe_domain


def test_valid_urls():
    assert is_valid_url("https://example.com")
    assert is_valid_url("http://example.com")
    assert is_valid_url("https://www.example.com/path?q=1")
    assert is_valid_url("https://sub.example.co.uk")


def test_invalid_urls():
    assert not is_valid_url("")
    assert not is_valid_url("not-a-url")
    assert not is_valid_url("ftp://example.com")
    assert not is_valid_url("http://localhost")
    assert not is_valid_url("http://127.0.0.1")
    assert not is_valid_url("http://192.168.1.1")
    assert not is_valid_url("http://10.0.0.1")


def test_safe_domains():
    assert is_safe_domain("example.com")
    assert is_safe_domain("google.com")
    assert is_safe_domain("github.io")


def test_unsafe_domains():
    assert not is_safe_domain("evil.tk")
    assert not is_safe_domain("spam.ml")
    assert not is_safe_domain("phish.ga")
