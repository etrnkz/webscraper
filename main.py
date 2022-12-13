#!/usr/bin/env python3

import sys
import os
import asyncio
from dotenv import load_dotenv

# Load .env file before any imports that read env vars
load_dotenv()

# Workaround for Python 3.12+ event loop behavior
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

# Fix console encoding for Unicode emoji output
if sys.stdout.encoding and sys.stdout.encoding.lower() in ('cp874', 'cp1252', 'latin-1'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

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
