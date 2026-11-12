"""Crawl admission control queue.

Single source of truth for all in-flight crawl jobs: queued, admitted, running,
and completing. All decision methods are synchronous and contain no awaits,
making them atomic under asyncio's single-threaded execution.
"""

from __future__ import annotations

import asyncio
import bisect
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional, Protocol

from bot.errors import BrowserLaunchError


class Outcome(str, Enum):
    """Result of submitting a crawl job."""
    ADMITTED = "admitted"
    QUEUED = "queued"
    REJECTED_DUPLICATE = "rejected_duplicate"
    REJECTED_QUEUE_FULL = "rejected_queue_full"
    REJECTED_USAGE = "rejected_usage"


@dataclass(frozen=True)
class Admission:
    """Result of a submission attempt."""
    outcome: Outcome
    position: int = 0               # 1-based; meaningful only when QUEUED
    queue_length: int = 0
    capacity: int = 0
    estimated_wait_seconds: int = 0


class JobState(str, Enum):
    """Lifecycle state of a crawl job."""
    QUEUED = "queued"
    ADMITTED = "admitted"
    LAUNCHING = "launching"
    CRAWLING = "crawling"
    PACKING = "packing"
    DELIVERING = "delivering"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass(eq=False)
class CrawlJob:
    """A single crawl job, tracked from submission to completion.

    eq=False prevents accidental equality; removal is by identity via _by_user.
    """
    job_id: str
    user_id: int
    url: str
    domain: str
    scope: str
    cookies_file: Optional[str]
    chat_id: int
    message_id: Optional[int] = None
    state: JobState = JobState.QUEUED
    sequence: int = 0               # submission order; preserved across retries
    enqueued_at: float = 0.0
    not_before: float = 0.0         # monotonic deadline; 0 = eligible now
    launch_attempts: int = 0
    charged: bool = False
    cancel_requested: bool = False
    slot_held: bool = False
    last_advertised_position: int = 0
    last_advertised_wait: int = 0
    status_ready: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_running: Optional[Callable[[], None]] = None


class JobNotifier(Protocol):
    """Protocol for user-facing job notifications.

    Implementations must be async and edit the job's status message
    identified by (chat_id, message_id).
    """
    async def on_queued(self, job: CrawlJob, position: int, wait_seconds: int) -> None: ...
    async def on_started(self, job: CrawlJob) -> None: ...
    async def on_retry(self, job: CrawlJob, attempt: int, delay_seconds: int, kind: str) -> None: ...
    async def on_deferred(self, job: CrawlJob) -> None: ...
    async def on_failed(self, job: CrawlJob, reason: str) -> None: ...
    async def on_cancelled(self, job: CrawlJob, charged: bool) -> None: ...
    async def on_expired(self, job: CrawlJob) -> None: ...


class CrawlQueue:
    """Admission gate and queue for crawl jobs.

    All public mutating methods are synchronous and contain no awaits.
    This ensures atomic decisions under asyncio's single-threaded model.
    """

    def __init__(
        self,
        runner: Callable[[CrawlJob], asyncio.Task],
        charge: Callable[[int], None],
        usage_of: Callable[[int], int],
        max_queue_size: int,
        max_concurrent: int,
        memory_budget_mb: int,
        memory_reserve_mb: int,
        retry_limit: int,
        retry_base_seconds: int,
        launch_failure_threshold: int,
        launch_suspend_seconds: int,
        max_queue_age_minutes: int,
        estimate_minutes_per_crawl: int,
        position_refresh_seconds: int,
        position_refresh_seconds_long: int,
        position_refresh_top_n: int,
        clock: Callable[[], float] = time.monotonic,
    ):
        # Injected dependencies
        self._runner = runner
        self._charge = charge
        self._usage_of = usage_of

        # Configuration
        self._max_queue_size = max_queue_size
        self._max_concurrent = max_concurrent
        self._memory_budget_mb = memory_budget_mb
        self._memory_reserve_mb = memory_reserve_mb
        self._retry_limit = retry_limit
        self._retry_base_seconds = retry_base_seconds
        self._launch_failure_threshold = launch_failure_threshold
        self._launch_suspend_seconds = launch_suspend_seconds
        self._max_queue_age_seconds = max_queue_age_minutes * 60
        self._estimate_minutes_per_crawl = estimate_minutes_per_crawl
        self._position_refresh_seconds = position_refresh_seconds
        self._position_refresh_seconds_long = position_refresh_seconds_long
        self._position_refresh_top_n = position_refresh_top_n
        self._clock = clock

        # Internal state
        self._by_user: dict[int, CrawlJob] = {}      # user_id -> CrawlJob (single source of truth)
        self._pending: list[CrawlJob] = []           # sorted by sequence
        self._running_count: int = 0
        self._capacity: int = 0
        self._workers_per_crawl: int = 1

        # Hysteresis for rising capacity
        self._candidate_capacity: Optional[int] = None
        self._agreement: int = 0

        # Launch circuit breaker
        self._consecutive_failures: int = 0
        self._suspended_until: float = 0.0

        # Duration tracking for wait estimates
        self._durations: deque[float] = deque(maxlen=100)

        # Timestamped launch failures for /admin
        self._launch_failures: deque[tuple[float, str]] = deque(maxlen=1000)

        # Notifier (set after construction)
        self._notifier: Optional[JobNotifier] = None

        # Active crawl tasks (for shutdown)
        self._crawl_tasks: set[asyncio.Task] = set()

    def set_notifier(self, notifier: JobNotifier) -> None:
        """Set the notifier (called after construction to avoid circular deps)."""
        self._notifier = notifier

    # ----------------------------------------------------------------------
    # Capacity management (called by resource probe)
    # ----------------------------------------------------------------------

    def set_capacity(self, capacity: int, workers_per_crawl: int) -> None:
        """Update capacity from the resource probe.

        Lower capacity takes effect immediately. Higher capacity requires
        two consecutive agreeing probes (hysteresis).
        """
        if capacity < self._capacity:
            self._capacity = capacity
            self._candidate_capacity = None
            self._agreement = 0
        elif capacity > self._capacity:
            if self._candidate_capacity == capacity:
                self._agreement += 1
                if self._agreement >= 2:
                    self._capacity = capacity
                    self._candidate_capacity = None
                    self._agreement = 0
            else:
                self._candidate_capacity = capacity
                self._agreement = 1
        # else: same capacity, nothing to do

        self._workers_per_crawl = workers_per_crawl
        # Pump immediately when capacity increases
        if capacity > 0:
            self.pump()

    def get_capacity(self) -> int:
        return self._capacity

    def get_workers_per_crawl(self) -> int:
        return self._workers_per_crawl

    # ----------------------------------------------------------------------
    # Admission decisions (synchronous, no awaits)
    # ----------------------------------------------------------------------

    def submit(self, job: CrawlJob) -> Admission:
        """Submit a job for admission. Synchronous, no awaits."""
        now = self._clock()

        # 1. Duplicate check - user can have at most one live job
        if job.user_id in self._by_user:
            return Admission(
                outcome=Outcome.REJECTED_DUPLICATE,
                capacity=self._capacity,
            )

        # 2. Usage check - only for non-subscribed users (force-join gate runs first)
        # This method assumes the caller has already checked force-join for free users.
        # If we reach here, the user either is subscribed or has allowance remaining.
        # The queue has no subscription knowledge; the handler gates this.

        # 3. Capacity available - claim slot immediately
        if self._running_count < self._capacity and not self._is_suspended():
            self._running_count += 1
            job.slot_held = True
            job.state = JobState.ADMITTED
            job.enqueued_at = now
            self._by_user[job.user_id] = job
            # Admitted jobs go straight to runner, not _pending
            return Admission(
                outcome=Outcome.ADMITTED,
                capacity=self._capacity,
            )

        # 4. Queue full
        queued_count = len([j for j in self._pending if j.state == JobState.QUEUED])
        if queued_count >= self._max_queue_size:
            return Admission(
                outcome=Outcome.REJECTED_QUEUE_FULL,
                queue_length=queued_count,
                capacity=self._capacity,
            )

        # 5. Queue the job
        job.state = JobState.QUEUED
        job.enqueued_at = now
        self._by_user[job.user_id] = job
        bisect.insort(self._pending, job, key=lambda j: j.sequence)
        position = self._position_of(job)

        # Estimate wait
        per_job = self._estimate_per_job()
        wait = int((position * per_job) / max(1, self._capacity)) if self._capacity > 0 else 0

        return Admission(
            outcome=Outcome.QUEUED,
            position=position,
            queue_length=queued_count + 1,  # include this job
            capacity=self._capacity,
            estimated_wait_seconds=wait,
        )

    def pump(self) -> None:
        """Admit waiting jobs up to capacity. Synchronous, no awaits."""
        while self._running_count < self._capacity and not self._is_suspended():
            # Find first eligible job
            eligible = None
            for job in self._pending:
                if job.state == JobState.QUEUED and job.not_before <= self._clock():
                    eligible = job
                    break
            if eligible is None:
                break

            # Admit it
            self._pending.remove(eligible)
            self._running_count += 1
            eligible.slot_held = True
            eligible.state = JobState.ADMITTED
            # Create the crawl task
            task = self._runner(eligible)
            self._crawl_tasks.add(task)
            task.add_done_callback(self._crawl_tasks.discard)

    def _is_suspended(self) -> bool:
        return self._suspended_until > self._clock()

    # ----------------------------------------------------------------------
    # Runner callbacks (called by the crawl task)
    # ----------------------------------------------------------------------

    def may_start(self, job: CrawlJob) -> bool:
        """Check if a job may start its browser launch.

        Called by the runner immediately before chromium.launch().
        Returns False if capacity has dropped or circuit breaker is active.
        """
        if job.cancel_requested:
            return False
        if self._is_suspended():
            return False
        if self._running_count >= self._capacity:
            return False
        return True

    def defer(self, job: CrawlJob) -> None:
        """Defer a job that was admitted but cannot start.

        Called by runner when may_start() returns False.
        Does not consume the retry budget.
        """
        self._running_count = max(0, self._running_count - 1)
        job.slot_held = False
        job.not_before = self._clock() + 1.0
        job.state = JobState.QUEUED
        bisect.insort(self._pending, job, key=lambda j: j.sequence)
        # Notify asynchronously (caller handles)
        self._notify_deferred(job)

    def mark_browser_launched(self, job: CrawlJob) -> None:
        """Called after successful chromium.launch().

        Charges the user's usage quota exactly once.
        """
        if not job.charged:
            self._charge(job.user_id)
            job.charged = True

    def record_duration(self, job: CrawlJob, duration_seconds: float) -> None:
        """Record a successful crawl duration for wait estimation."""
        if duration_seconds > 0:
            self._durations.append(duration_seconds)

    def record_launch_failure(self, job: CrawlJob, kind: BrowserLaunchError.Kind) -> None:
        """Record a launch failure and update circuit breaker."""
        now = self._clock()
        self._launch_failures.append((now, kind.value))

        if kind == BrowserLaunchError.Kind.RESOURCE_PRESSURE:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._launch_failure_threshold:
                self._suspended_until = now + self._launch_suspend_seconds
                # Force capacity to zero while suspended
                self._capacity = 0
        else:
            # Configuration or transient failures don't trip the breaker
            pass

    def release_slot(self, job: CrawlJob) -> None:
        """Release the capacity slot (called after archive built, before upload)."""
        if job.slot_held:
            self._running_count = max(0, self._running_count - 1)
            job.slot_held = False
        # Pump to admit next waiting job
        self.pump()

    def complete(self, job: CrawlJob) -> None:
        """Mark job as completed (success or failure after launch)."""
        self._by_user.pop(job.user_id, None)
        # Job already removed from _pending when admitted

    def abandon(self, job: CrawlJob) -> None:
        """Abandon a job that was admitted but never got a status message."""
        self._by_user.pop(job.user_id, None)
        if job.slot_held:
            self._running_count = max(0, self._running_count - 1)
            job.slot_held = False
        if job in self._pending:
            self._pending.remove(job)
        job.status_ready.set()  # Unblock any waiting runner

    # ----------------------------------------------------------------------
    # Cancellation
    # ----------------------------------------------------------------------

    def cancel(self, user_id: int) -> tuple[str, bool]:
        """Cancel a user's job.

        Returns (state_description, charged) where state_description is one of:
        "none", "queued", "admitted", "launching", "running", "not_launched", "too_late"
        and charged indicates whether usage was charged.
        """
        job = self._by_user.get(user_id)
        if job is None:
            return ("none", False)

        if job.state == JobState.QUEUED:
            self._pending.remove(job)
            self._by_user.pop(user_id, None)
            job.state = JobState.CANCELLED
            self._notify_cancelled(job, charged=False)
            return ("queued", False)

        if job.state in (JobState.ADMITTED, JobState.LAUNCHING):
            # Slot held but browser not yet launched
            if job.slot_held:
                self._running_count = max(0, self._running_count - 1)
                job.slot_held = False
            job.cancel_requested = True
            job.state = JobState.CANCELLED
            self._by_user.pop(user_id, None)
            self._notify_cancelled(job, charged=job.charged)
            return ("not_launched", job.charged)

        if job.state in (JobState.CRAWLING, JobState.PACKING):
            job.cancel_requested = True
            if job.cancel_running:
                job.cancel_running()
            self._by_user.pop(user_id, None)
            self._notify_cancelled(job, charged=job.charged)
            return ("running", job.charged)

        if job.state in (JobState.DELIVERING, JobState.DONE):
            return ("too_late", job.charged)

        if job.state in (JobState.FAILED, JobState.CANCELLED, JobState.EXPIRED):
            return (job.state.value, job.charged)

        return ("unknown", job.charged)

    # ----------------------------------------------------------------------
    # Expiry (called by maintenance task)
    # ----------------------------------------------------------------------

    def expire_stale(self, notifier: JobNotifier) -> int:
        """Expire jobs that have been queued too long.

        Returns the number of jobs expired.
        """
        now = self._clock()
        max_age = self._max_queue_age_seconds
        expired = []

        for job in self._pending:
            if job.state == JobState.QUEUED and (now - job.enqueued_at) > max_age:
                expired.append(job)

        for job in expired:
            self._pending.remove(job)
            self._by_user.pop(job.user_id, None)
            job.state = JobState.EXPIRED
            # Notify asynchronously - caller must await
            asyncio.create_task(notifier.on_expired(job))

        return len(expired)

    # ----------------------------------------------------------------------
    # Position updates (called by position refresh task)
    # ----------------------------------------------------------------------

    def position_updates(self) -> list[tuple[CrawlJob, int, int]]:
        """Get jobs whose advertised position has changed.

        Returns list of (job, new_position, new_wait_seconds) for jobs
        where position changed since last advertised.
        """
        updates = []
        for i, job in enumerate(self._pending):
            if job.state != JobState.QUEUED:
                continue
            position = i + 1
            if position != job.last_advertised_position:
                per_job = self._estimate_per_job()
                wait = int((position * per_job) / max(1, self._capacity)) if self._capacity > 0 else 0
                updates.append((job, position, wait))
        return updates

    def _position_of(self, job: CrawlJob) -> int:
        """Get 1-based position of a queued job."""
        try:
            return self._pending.index(job) + 1
        except ValueError:
            return 0

    def _estimate_per_job(self) -> float:
        """Get estimated per-job duration in seconds."""
        if self._durations:
            avg = sum(self._durations) / len(self._durations)
            return max(5.0, min(600.0, avg))
        # Fallback: configured minutes per crawl
        return self._estimate_minutes_per_crawl * 60.0

    # ----------------------------------------------------------------------
    # Notifier helpers (create async tasks)
    # ----------------------------------------------------------------------

    def _notify_queued(self, job: CrawlJob, position: int, wait_seconds: int) -> None:
        if self._notifier:
            asyncio.create_task(self._notifier.on_queued(job, position, wait_seconds))

    def _notify_started(self, job: CrawlJob) -> None:
        if self._notifier:
            asyncio.create_task(self._notifier.on_started(job))

    def _notify_retry(self, job: CrawlJob, attempt: int, delay_seconds: int, kind: str) -> None:
        if self._notifier:
            asyncio.create_task(self._notifier.on_retry(job, attempt, delay_seconds, kind))

    def _notify_deferred(self, job: CrawlJob) -> None:
        if self._notifier:
            asyncio.create_task(self._notifier.on_deferred(job))

    def _notify_failed(self, job: CrawlJob, reason: str) -> None:
        if self._notifier:
            asyncio.create_task(self._notifier.on_failed(job, reason))

    def _notify_cancelled(self, job: CrawlJob, charged: bool) -> None:
        if self._notifier:
            asyncio.create_task(self._notifier.on_cancelled(job, charged))

    # ----------------------------------------------------------------------
    # Stats and shutdown
    # ----------------------------------------------------------------------

    def stats(self) -> dict:
        """Get queue statistics for /admin."""
        now = self._clock()
        recent_failures = sum(1 for t, _ in self._launch_failures if now - t < 3600)
        return {
            "waiting": len([j for j in self._pending if j.state == JobState.QUEUED]),
            "running": self._running_count,
            "capacity": self._capacity,
            "workers_per_crawl": self._workers_per_crawl,
            "launch_failures_last_hour": recent_failures,
            "launch_failures_total": len(self._launch_failures),
            "suspended": self._is_suspended(),
            "suspended_until": self._suspended_until if self._is_suspended() else 0,
            "consecutive_failures": self._consecutive_failures,
        }

    async def shutdown(self) -> None:
        """Cancel and await all crawl tasks."""
        for task in self._crawl_tasks:
            task.cancel()
        if self._crawl_tasks:
            await asyncio.gather(*self._crawl_tasks, return_exceptions=True)
        self._crawl_tasks.clear()

    # ----------------------------------------------------------------------
    # Construction helper
    # ----------------------------------------------------------------------

    def launch(self, job: CrawlJob) -> None:
        """Launch an admitted job (called by handler after sending first message).

        Sets status_ready so the runner can begin.
        """
        job.state = JobState.LAUNCHING
        job.status_ready.set()
        # Runner will call may_start() and then mark_browser_launched()