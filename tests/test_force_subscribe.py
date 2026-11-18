"""Unit tests for ForceSubscribe plugin."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from hydrogram.errors import UserNotParticipant
from bot.plugins.force_subscribe import ForceSubscribe


class FakeChat:
    def __init__(self, title="My Channel", username="mychannel"):
        self.title = title
        self.username = username


class FakeMember:
    def __init__(self, status="member"):
        self.status = status


@pytest.fixture
def mock_bot():
    bot = MagicMock()
    bot.get_chat = AsyncMock(return_value=FakeChat("Channel 1", "ch1"))
    bot.export_chat_invite_link = AsyncMock(return_value="https://t.me/+invite1")
    bot.get_chat_member = AsyncMock(return_value=FakeMember("member"))
    return bot


@pytest.mark.asyncio
async def test_get_channel_info_success(mock_bot):
    fsub = ForceSubscribe(mock_bot, ["-1001234567890"])
    info = await fsub.get_channel_info("-1001234567890")

    assert info is not None
    assert info['title'] == "Channel 1"
    assert info['username'] == "ch1"
    assert info['invite_link'] == "https://t.me/+invite1"


@pytest.mark.asyncio
async def test_check_all_subscriptions(mock_bot):
    fsub = ForceSubscribe(mock_bot, ["-1001234567890", "-1009876543210"])

    # User is member of both
    ok, not_sub = await fsub.check_all_subscriptions(12345)
    assert ok is True
    assert len(not_sub) == 0

    # User is not member of second channel
    async def get_member(chat_id, user_id):
        if chat_id == "-1009876543210":
            raise UserNotParticipant()
        return FakeMember("member")

    mock_bot.get_chat_member.side_effect = get_member
    ok, not_sub = await fsub.check_all_subscriptions(12345)
    assert ok is False
    assert not_sub == ["-1009876543210"]


@pytest.mark.asyncio
async def test_get_force_subscribe_message_and_buttons(mock_bot):
    fsub = ForceSubscribe(mock_bot, ["-1001234567890"])
    text = await fsub.get_force_subscribe_message(["-1001234567890"])
    assert "Channel 1" in text

    buttons = await fsub.create_join_buttons(["-1001234567890"])
    assert buttons is not None
    # 1 join button + 1 check button
    assert len(buttons.inline_keyboard) == 2
