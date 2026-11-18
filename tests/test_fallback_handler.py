"""Handler-level regression tests for fallback handler when normal text is sent."""
import asyncio

try:
    asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

import pytest
from bot.core import scraper


class StubUser:
    def __init__(self, user_id=12345):
        self.id = user_id
        self.username = "testuser"
        self.first_name = "Test"


class StubMessage:
    def __init__(self, text, user_id=12345):
        self.text = text
        self.from_user = StubUser(user_id)
        self.replies = []
        self._stopped = False

    async def reply(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return self

    def stop_propagation(self):
        self._stopped = True
        from hydrogram import StopPropagation
        raise StopPropagation()


@pytest.mark.asyncio
async def test_fallback_handler_replies_to_normal_text():
    msg = StubMessage("hello")
    await scraper.fallback_handler(scraper.bot, msg)

    assert len(msg.replies) == 1, "Fallback handler should reply to normal text 'hello'"
    reply_text, _ = msg.replies[0]
    assert "Send me a URL to clone!" in reply_text


@pytest.mark.asyncio
async def test_fallback_handler_ignores_valid_urls():
    msg = StubMessage("https://example.com")
    await scraper.fallback_handler(scraper.bot, msg)
    assert len(msg.replies) == 0, "Fallback handler should not reply to valid URL"


@pytest.mark.asyncio
async def test_fallback_handler_ignores_valid_urls_with_flags():
    msg = StubMessage("example.com subdomains")
    await scraper.fallback_handler(scraper.bot, msg)
    assert len(msg.replies) == 0, "Fallback handler should not reply to valid URL with flags"


@pytest.mark.asyncio
async def test_fallback_handler_ignores_commands():
    msg = StubMessage("/help")
    await scraper.fallback_handler(scraper.bot, msg)
    assert len(msg.replies) == 0, "Fallback handler should not reply to commands"
