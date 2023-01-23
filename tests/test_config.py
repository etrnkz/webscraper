"""Tests for configuration"""

from bot import config


def test_config_defaults():
    assert config.RATE_LIMIT == 5
    assert config.DAILY_LIMIT == 15
    assert config.MAX_FILE_SIZE == 50 * 1024 * 1024
    assert config.REQUEST_TIMEOUT == 30
    assert config.MAX_RETRIES == 3
