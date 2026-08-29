"""Force channel subscription functionality"""
import logging
from hydrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from hydrogram.errors import UserNotParticipant

logger = logging.getLogger(__name__)

class ForceSubscribe:
    def __init__(self, bot, channel_ids=None):
        self.bot = bot
        self.channel_ids = channel_ids or []
        self.channel_info = {}
    
    def add_channel(self, channel_id):
        """Add a channel to force subscribe list"""
        if channel_id not in self.channel_ids:
            self.channel_ids.append(channel_id)
            logger.info(f"Added force subscribe channel: {channel_id}")
    
    def get_channel_info(self, channel_id):
        """Get channel information"""
        if channel_id in self.channel_info:
            return self.channel_info[channel_id]
        try:
            chat = self.bot.get_chat(channel_id)
            info = {'title': chat.title, 'username': chat.username, 'invite_link': None}
            try:
                invite_link = self.bot.export_chat_invite_link(channel_id)
                info['invite_link'] = invite_link
            except Exception:
                pass
            self.channel_info[channel_id] = info
            return info
        except Exception:
            return None
    
    async def check_all_subscriptions(self, user_id):
        """Check if user is subscribed to all required channels"""
        if not self.channel_ids:
            return True, []
        not_subscribed = []
        for channel_id in self.channel_ids:
            try:
                member = await self.bot.get_chat_member(channel_id, user_id)
                if member.status not in ['member', 'administrator', 'creator']:
                    not_subscribed.append(channel_id)
            except UserNotParticipant:
                not_subscribed.append(channel_id)
            except Exception:
                pass
        return len(not_subscribed) == 0, not_subscribed
    
    def get_force_subscribe_message(self, not_subscribed_channels):
        """Get force subscribe message"""
        channel_names = []
        for channel_id in not_subscribed_channels:
            info = self.get_channel_info(channel_id)
            if info:
                channel_names.append(info['title'])
        channels_text = '\n'.join([f"• {name}" for name in channel_names])
        return f"🔓 **Join to continue**\n\nPlease join:\n{channels_text}"
    
    def create_join_buttons(self, not_subscribed_channels):
        """Create join buttons"""
        buttons = []
        for channel_id in not_subscribed_channels:
            info = self.get_channel_info(channel_id)
            if info:
                url = f"https://t.me/{info['username']}" if info['username'] else f"https://t.me/c/{str(channel_id).replace('-100', '')}"
                buttons.append([InlineKeyboardButton(f"📢 Join", url=url)])
        buttons.append([InlineKeyboardButton("✅ Check", callback_data="check_subscription")])
        return InlineKeyboardMarkup(buttons)
