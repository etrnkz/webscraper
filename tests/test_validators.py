"""Unit tests for URL validators."""
import pytest
from bot.utils.validators import is_valid_url, normalize_url, is_safe_domain


def test_normalize_url():
    assert normalize_url("example.com") == "https://example.com"
    assert normalize_url("http://example.com") == "http://example.com"
    assert normalize_url("https://example.com") == "https://example.com"
    assert normalize_url("") == ""


def test_is_valid_url_valid_domains():
    assert is_valid_url("example.com") is True
    assert is_valid_url("https://example.com") is True
    assert is_valid_url("http://sub.domain.org/path?q=1") is True
    assert is_valid_url("my-site.co.uk") is True
    assert is_valid_url("1.1.1.1") is True


def test_is_valid_url_invalid_text():
    assert is_valid_url("hello") is False
    assert is_valid_url("hi there") is False
    assert is_valid_url("normal text message") is False
    assert is_valid_url("just a word") is False
    assert is_valid_url("test.") is False
    assert is_valid_url(".test") is False
    assert is_valid_url("something.123") is False


def test_is_valid_url_blocked_hosts():
    assert is_valid_url("localhost") is False
    assert is_valid_url("127.0.0.1") is False
    assert is_valid_url("0.0.0.0") is False
    assert is_valid_url("192.168.1.1") is False
    assert is_valid_url("10.0.0.1") is False


def test_is_safe_domain():
    assert is_safe_domain("example.com") is True
    assert is_safe_domain("phishing.tk") is False
    assert is_safe_domain("bad.ml") is False
