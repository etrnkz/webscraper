#!/usr/bin/env python3

import sys
import os
import asyncio
from dotenv import load_dotenv

load_dotenv()

# Must set up event loop BEFORE importing Pyrogram (it calls get_event_loop at import time)
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

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
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
