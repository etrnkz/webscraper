"""Crawl admission control queue package.

This package is intentionally inert - it performs no imports at load time
so it can be imported in tests without credentials or side effects.
"""

__all__ = ["crawl_queue", "notifier", "resources"]