"""Force channel subscription functionality"""
import logging
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant, ChatAdminRequired, ChannelPrivate

logger = logging.getLogger(__name__)


class ForceSubscribe:
    def __init__(self, bot, channel_ids=None):
        self.bot = bot
        self.channel_ids = channel_ids or []
        self.channel_info = {}
    
    def set_channels(self, channel_ids):
        """Set required channels"""
        self.channel_ids = channel_ids
        logger.info(f"Force subscribe channels set: {channel_ids}")
    
    def add_channel(self, channel_id):
        """Add a channel to force subscribe list"""
        if channel_id not in self.channel_ids:
            self.channel_ids.append(channel_id)
            logger.info(f"Added force subscribe channel: {channel_id}")
    
    def remove_channel(self, channel_id):
        """Remove a channel from force subscribe list"""
        if channel_id in self.channel_ids:
            self.channel_ids.remove(channel_id)
            logger.info(f"Removed force subscribe channel: {channel_id}")
    
    def get_channel_info(self, channel_id):
        """Get channel information"""
        if channel_id in self.channel_info:
            return self.channel_info[channel_id]
        
        try:
            chat = self.bot.get_chat(channel_id)
            info = {
                'title': chat.title,
                'username': chat.username,
                'invite_link': None
            }
            
            # Try to get invite link
            try:
                invite_link = self.bot.export_chat_invite_link(channel_id)
                info['invite_link'] = invite_link
            except Exception:
                pass
            
            self.channel_info[channel_id] = info
            return info
        except Exception as e:
            logger.error(f"Failed to get channel info for {channel_id}: {e}")
            return None
    
    def is_user_subscribed(self, user_id, channel_id):
        """Check if user is subscribed to a channel"""
        try:
            member = self.bot.get_chat_member(channel_id, user_id)
            return member.status in ['member', 'administrator', 'creator']
        except UserNotParticipant:
            return False
        except Exception as e:
            logger.error(f"Error checking subscription for user {user_id} in channel {channel_id}: {e}")
            return True  # Allow on error to avoid blocking users
    
    def check_all_subscriptions(self, user_id):
        """Check if user is subscribed to all required channels"""
        if not self.channel_ids:
            return True, []
        
        not_subscribed = []
        
        for channel_id in self.channel_ids:
            if not self.is_user_subscribed(user_id, channel_id):
                not_subscribed.append(channel_id)
        
        return len(not_subscribed) == 0, not_subscribed
    
    def create_join_buttons(self, not_subscribed_channels):
        """Create inline keyboard with join buttons"""
        buttons = []
        
        for channel_id in not_subscribed_channels:
            info = self.get_channel_info(channel_id)
            if info:
                channel_name = info['title'] or f"Channel {channel_id}"
                
                # Create button with channel link
                if info['username']:
                    url = f"https://t.me/{info['username']}"
                elif info['invite_link']:
                    url = info['invite_link']
                else:
                    url = f"https://t.me/c/{str(channel_id).replace('-100', '')}"
                
                buttons.append([InlineKeyboardButton(f"📢 Join {channel_name}", url=url)])
        
        # Add check button
        buttons.append([InlineKeyboardButton("✅ I've Joined", callback_data="check_subscription")])
        
        return InlineKeyboardMarkup(buttons)
    
    def get_force_subscribe_message(self, not_subscribed_channels):
        """Get force subscribe message"""
        channel_names = []
        
        for channel_id in not_subscribed_channels:
            info = self.get_channel_info(channel_id)
            if info:
                channel_names.append(info['title'] or f"Channel {channel_id}")
        
        channels_text = '\n'.join([f"• {name}" for name in channel_names])
        
        message = f"""
🔒 **Subscription Required**

To use this bot, you must join our channel(s):

{channels_text}

Click the button(s) below to join, then click "I've Joined" to continue.
"""
        return message


# Global force subscribe instance (will be initialized with bot)
force_subscribe = None
