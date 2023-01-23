"""Message handlers for the bot"""
from pyrogram import filters
import logging
from collections import defaultdict
from bot import constants
from bot.utils.validators import is_valid_url, is_safe_domain
from bot.utils.helpers import extract_domain

logger = logging.getLogger(__name__)

# Track user requests
user_requests = defaultdict(list)
request_stats = defaultdict(int)


def register_handlers(bot):
    """Register all bot handlers"""
    
    @bot.on_message(filters.private & filters.command('start'))
    def start(bot, msg):
        msg.reply(constants.WELCOME_MESSAGE.format(name=msg.from_user.first_name))
    
    @bot.on_message(filters.private & filters.command('help'))
    def help_command(bot, msg):
        msg.reply(constants.HELP_MESSAGE, disable_web_page_preview=True)
    
    @bot.on_message(filters.private & filters.command('version'))
    def version_command(bot, msg):
        msg.reply(f"🤖 **{constants.BOT_NAME}**\nVersion: `{constants.BOT_VERSION}`")
    
    @bot.on_message(filters.private & filters.command('stats'))
    def stats_command(bot, msg):
        user_id = msg.from_user.id
        total = request_stats.get(user_id, 0)
        msg.reply(f"📊 **Your Statistics:**\n\nTotal requests: {total}")
