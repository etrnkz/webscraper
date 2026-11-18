"""Handler-level regression tests for /start command and premium custom emoji entities."""
import asyncio

# Ensure event loop exists on Python 3.14 before importing scraper (which initializes Client/Dispatcher)
try:
    asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

import pytest
from hydrogram.enums import MessageEntityType

from bot.core import scraper


class StubMessage:
    def __init__(self):
        self.replies = []

    async def reply(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return self


@pytest.mark.asyncio
async def test_start_handler_emits_valid_entities():
    msg = StubMessage()
    await scraper.start(scraper.bot, msg)

    assert len(msg.replies) == 1, "Handler must emit exactly one reply"
    text, kwargs = msg.replies[0]

    # No literal HTML markup in text
    for tag in ("<b>", "</b>", "<i>", "</i>", "<tg-emoji>"):
        assert tag not in text, f"literal markup '{tag}' in message text: {text!r}"

    # No inline buttons
    assert kwargs.get("reply_markup") is None, "Expected no reply_markup on /start reply"

    entities = kwargs.get("entities")
    assert entities, "Expected non-empty entities list"

    # UTF-16 code units check
    utf16_bytes = text.encode("utf-16-le")
    total_utf16_units = len(utf16_bytes) // 2

    for e in entities:
        assert e.offset >= 0, f"Negative offset {e.offset}"
        assert e.offset + e.length <= total_utf16_units, (
            f"Entity {e} extends beyond text length {total_utf16_units} (offset={e.offset}, length={e.length})"
        )

    # Custom emoji entities
    custom_emojis = [e for e in entities if e.type == MessageEntityType.CUSTOM_EMOJI]
    assert len(custom_emojis) == 6, f"Expected 6 CUSTOM_EMOJI entities, got {len(custom_emojis)}"

    spider_emojis = [e for e in custom_emojis if e.custom_emoji_id == 5206456392407341290]
    star_emojis = [e for e in custom_emojis if e.custom_emoji_id == 5319156402973849743]

    assert len(spider_emojis) == 1, f"Expected 1 spider custom emoji, got {len(spider_emojis)}"
    assert len(star_emojis) == 5, f"Expected 5 star custom emojis, got {len(star_emojis)}"

    for e in custom_emojis:
        assert isinstance(e.custom_emoji_id, int), f"custom_emoji_id must be int, got {type(e.custom_emoji_id)}"

    # Check that spider wraps exactly 🕸 (2 UTF-16 units)
    sp = spider_emojis[0]
    assert sp.length == 2, f"Spider custom emoji length must be 2 UTF-16 units, got {sp.length}"
    sliced_spider = utf16_bytes[sp.offset * 2 : (sp.offset + sp.length) * 2].decode("utf-16-le")
    assert sliced_spider == "🕸", f"Spider entity at {sp.offset} wraps {sliced_spider!r}, expected '🕸'"

    # Check that stars wrap exactly ⭐️ (2 UTF-16 units)
    for st in star_emojis:
        assert st.length == 2, f"Star custom emoji length must be 2 UTF-16 units, got {st.length}"
        sliced_star = utf16_bytes[st.offset * 2 : (st.offset + st.length) * 2].decode("utf-16-le")
        assert sliced_star == "⭐️", f"Star entity at {st.offset} wraps {sliced_star!r}, expected '⭐️'"


@pytest.mark.asyncio
async def test_start_entities_survive_raw_serialization():
    msg = StubMessage()
    await scraper.start(scraper.bot, msg)

    assert len(msg.replies) == 1
    _, kwargs = msg.replies[0]
    entities = kwargs.get("entities", [])
    assert entities, "Expected entities to serialize"

    for e in entities:
        # Client reference is needed for write()
        e._client = scraper.bot
        raw_obj = await e.write()
        raw_bytes = raw_obj.write()
        assert isinstance(raw_bytes, (bytes, bytearray))
        assert len(raw_bytes) > 0
