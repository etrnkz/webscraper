#!/usr/bin/env python3

import sys
import os
import asyncio
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))

# Import after path setup
from bot.core.scraper import bot, logger, constants, crawl_queue, telegram_notifier, start_background_tasks, cancel_all_crawl_tasks, _notify_restarted_users


async def _main():
    """Main async entry point with explicit lifecycle."""
    logger.info(f"Starting {constants.BOT_NAME} v{constants.BOT_VERSION}")
    logger.info("=" * 50)

    # Start the bot client
    await bot.start()

    # Start background tasks
    global _background_tasks
    _background_tasks = start_background_tasks()

    # Notify users who were waiting on restart
    await _notify_restarted_users()

    try:
        # Run until idle
        from hydrogram import idle
        await idle()
    finally:
        logger.info("Shutting down...")

        # Cancel and await background tasks
        for task in _background_tasks:
            task.cancel()
        if _background_tasks:
            await asyncio.gather(*_background_tasks, return_exceptions=True)

        # Cancel and await all crawl tasks
        await cancel_all_crawl_tasks()

        # Stop the bot
        await bot.stop()

        logger.info("Shutdown complete")


if __name__ == "__main__":
    try:
        bot.run(_main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)