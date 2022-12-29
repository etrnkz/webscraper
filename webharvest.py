#!/usr/bin/env python3
"""
WebHarvest Bot - Advanced Web Scraping Telegram Bot
Main entry point for the application
"""

import sys
import os

# Add bot directory to path
sys.path.insert(0, os.path.dirname(__file__))

from bot.core.scraper import bot, logger, constants

if __name__ == "__main__":
    logger.info(f"Starting {constants.BOT_NAME} v{constants.BOT_VERSION}")
    logger.info("=" * 50)
    
    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
