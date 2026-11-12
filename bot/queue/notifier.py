"""Job notifier protocol for crawl queue.

Defines the contract between the queue and the user-facing notification layer.
The queue holds only integer message identifiers; all Telegram API calls
go through this protocol.
"""

from __future__ import annotations

from typing import Protocol

from bot.queue.crawl_queue import CrawlJob


class JobNotifier(Protocol):
    """Protocol for user-facing job notifications.

    All methods are async and must edit the job's existing status message
    identified by (chat_id, message_id). The queue holds only these two integers.
    """

    async def on_queued(self, job: CrawlJob, position: int, wait_seconds: int) -> None:
        """Called when a job is queued. Show position and estimated wait."""
        ...

    async def on_started(self, job: CrawlJob) -> None:
        """Called when an admitted job begins (browser launching)."""
        ...

    async def on_retry(self, job: CrawlJob, attempt: int, delay_seconds: int, kind: str) -> None:
        """Called when a launch fails and the job is re-queued with backoff."""
        ...

    async def on_deferred(self, job: CrawlJob) -> None:
        """Called when a job is admitted but capacity dropped before launch."""
        ...

    async def on_failed(self, job: CrawlJob, reason: str) -> None:
        """Called when a job fails permanently (retries exhausted or config fault)."""
        ...

    async def on_cancelled(self, job: CrawlJob, charged: bool) -> None:
        """Called when a job is cancelled by the user."""
        ...

    async def on_expired(self, job: CrawlJob) -> None:
        """Called when a queued job exceeds the maximum wait time."""
        ...