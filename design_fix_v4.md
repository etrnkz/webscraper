# Fix Specification: Hydrogram Bot Queue Defects & Missing Features

**Status**: Approved for Implementation
**Target**: `bot/queue/crawl_queue.py`, `bot/core/scraper.py`, `main.py`, `bot/core/crawler.py`, `tests/test_crawl_queue.py`
**Base Commit**: `155b5b9` (current HEAD on Hoody #2)
**Date**: 2026-11-15

---

## 1. Executive Summary

The resource-aware crawl admission-control queue (introduced in commit `5924bd7`, refined in subsequent commits) has **15 identified defects** that prevent any crawl from completing. Zero `crawl_complete` rows exist in the database since the rewrite; the last successful crawl was `2026-11-12T23:31:12` from the old code path. The bot menu is empty (`getMyCommands` → `[]`). No progress bar renders. This spec enumerates every defect with corrected contracts and prescribes minimal fixes.

---

## 2. Proven Defects (15 Total + 2 Discovered)

### 2.1 Core Queue Logic Defects

| # | Defect | Location | Evidence |
|---|--------|----------|----------|
| **D1** | **ADMITTED jobs never get a runner task** | `crawl_queue.py:233-244` `submit()` ADMITTED branch | `self._runner(job)` only called in `pump()`. Admitted jobs explicitly *not* added to `_pending`. Runner coroutine never scheduled → `job.status_ready.wait()` never runs → Chromium never launches → job hangs forever holding a capacity slot. |
| **D2** | **`launch()` called for QUEUED jobs, destroying their state** | `scraper.py:715` `handle_url` | `crawl_queue.launch(job)` invoked for both ADMITTED and QUEUED. `launch()` sets `job.state = JobState.LAUNCHING`, so queued jobs leave `_pending` with wrong state; `pump()` only selects `state == QUEUED` → never admitted → never run. |
| **D3** | **Runner callable returns coroutine, not `asyncio.Task`** | `scraper.py:135-139` `_make_runner()` | Returns `async def runner(job): return asyncio.create_task(_run_crawl(job))` → callable returns a **coroutine**. `pump()` does `task = self._runner(eligible); task.add_done_callback(...)` → `AttributeError: 'coroutine' object has no attribute 'add_done_callback'`. |
| **D4** | **`may_start()` comparison inverted** | `crawl_queue.py:313` | Uses `self._running_count >= self._capacity` but spec requires `self._running_count > self._capacity`. Job that just claimed a slot immediately fails its own provisional check and is deferred. |
| **D5** | **Usage never charged — `mark_browser_launched` never called** | `scraper.py:_run_crawl` | `handle_url` submits job; `CrawlQueue.mark_browser_launched` (queue line 331) exists but never called from `_run_crawl`. `_charge_usage` passed as `charge` callback but only invoked by `mark_browser_launched`. Usage charged zero times despite `usage_count=16` in DB. |
| **D6** | **Job state never advances to CRAWLING/PACKING/DELIVERING** | `scraper.py:_run_crawl` | `_run_crawl` never sets `job.state = JobState.CRAWLING`/`PACKING`/`DELIVERING`. `cancel()` branch for running jobs (queue line 416) is unreachable. |
| **D7** | **`complete()` leaks capacity slot + QUEUED jobs leak in `_pending`** | `crawl_queue.py:368-371` + `launch()` | `complete()` only pops `_by_user`; never releases held slot or pumps. Failure paths call `complete(job)` without `release_slot()` leak capacity. QUEUED jobs launched via D2 remain in `_pending` with `state=LAUNCHING` forever (not removed by `complete()`). |
| **D8** | **Double deferral notification** | `crawl_queue.py:329` + `scraper.py:739` | `defer()` calls `self._notify_deferred(job)` (fire-and-forget `asyncio.create_task`) AND `_run_crawl` awaits `telegram_notifier.on_deferred(job)` → duplicate "deferred" message. |
| **D9** | **`record_duration` mixes clocks** | `scraper.py:804` | Feeds `time.monotonic() - job.enqueued_at` but `job.enqueued_at` comes from queue's injected clock (`time.monotonic` default in production, fake clock in tests). Duration measurement is not the held-window duration spec requires. |
| **D10** | **Retry path bypasses queue bookkeeping** | `scraper.py:883-890` | Appends job directly to `crawl_queue._pending`, sets `job.state = JobState.QUEUED`, `job.not_before = time.monotonic() + delay` — bypasses queue's own bookkeeping and uses wall clock instead of queue clock. |
| **D11** | **`expire_stale()` requires running loop** | `crawl_queue.py:454` | Calls `asyncio.create_task(notifier.on_expired(job))` — requires a running event loop; invoked from `_maintenance` (async, fine) but queue API is otherwise synchronous. |
| **D12** | **`cancel()` for QUEUED job lacks membership check** | `crawl_queue.py:399` | `self._pending.remove(job)` without `if job in self._pending` → `ValueError` if job not in `_pending`. Also: `cancel()` for ADMITTED/LAUNCHING doesn't clean `_pending` (job may be there due to D2). |
| **D13** | **Missing `_delivering` tracking & `stats()` mismatch** | `crawl_queue.py` + spec | `CrawlJob` has no `_delivering` collection. Spec INV-4 (`len(_by_user) == _running_count + len(_pending) + len(_delivering)`) and `stats()` key `delivering` unimplemented. `stats()` returns different key set than specified. |
| **D14** | **`shutdown()` race with `pump()`/`set_capacity()`** | `crawl_queue.py:542-548` | `shutdown()` cancels `_crawl_tasks` but `set_capacity()`/`pump()` can still create new tasks after shutdown begins. Spec requires a flag stopping `pump()`. |
| **D15** | **Bot menu empty — `set_bot_commands` never called** | `main.py` startup | Hydrogram `set_bot_commands` exists (deployed venv confirmed). Bot API `getMyCommands` returns `{"ok":true,"result":[]}`. No call at startup. |

### 2.2 Additional Defects Discovered During Review

| # | Defect | Location | Evidence |
|---|--------|----------|----------|
| **D16** | **Double `on_started` notification for ADMITTED jobs** | `scraper.py:718-719, 769-770` | `handle_url` calls `await telegram_notifier.on_started(job)` (line 719) AND `_run_crawl` calls it again (line 770) for ADMITTED jobs. QUEUED jobs only get it once. |
| **D17** | **`cancel()` for ADMITTED/LAUNCHING doesn't clean `_pending`** | `crawl_queue.py:405-414` | Branch decrements `_running_count` and sets `cancel_requested`, but does not check/remove from `_pending`. A job in `_pending` with `state=LAUNCHING` (due to D2) would not be removed. |

---

## 3. Corrected Contracts

### 3.1 `CrawlQueue.submit(job) -> Admission` (Synchronous)

**Order (unchanged, per spec):**
1. Duplicate check → `REJECTED_DUPLICATE`
2. Usage check → `REJECTED_USAGE`
3. **Capacity available** (`_running_count < _capacity` and not suspended):
   - `_running_count += 1`
   - `job.slot_held = True`
   - `job.state = JobState.ADMITTED`
   - `job.enqueued_at = now`
   - `job.admitted_at = now`  *(NEW: for accurate held-window duration)*
   - Register in `_by_user`
   - **Call `self._runner(job)` directly** → wrap in `asyncio.create_task`, add to `_crawl_tasks`, add done callback
   - Return `ADMITTED`
4. Queue full → `REJECTED_QUEUE_FULL`
5. Otherwise → insert into `_pending` (bisect by sequence), `state = QUEUED`, register, return `QUEUED` with position.

**Fix for D1**: ADMITTED branch schedules the runner directly (Option A). `abandon()` must cancel the created task.

### 3.2 `CrawlQueue.pump() -> None` (Synchronous)

```python
def pump(self) -> None:
    if self._shutting_down:
        return
    while self._running_count < self._capacity and not self._is_suspended():
        eligible = first job in _pending with state==QUEUED and not_before <= self._clock()
        if not eligible:
            break
        self._pending.remove(eligible)
        self._running_count += 1
        eligible.slot_held = True
        eligible.state = JobState.ADMITTED
        eligible.admitted_at = self._clock()  # NEW
        task = asyncio.create_task(self._runner(eligible))  # WRAP HERE
        self._crawl_tasks.add(task)
        task.add_done_callback(self._crawl_tasks.discard)
```

**Fix for D3**: `_make_runner()` returns plain async function `async def runner(job): await _run_crawl(job)`; `pump()` wraps in `asyncio.create_task()`.

### 3.3 `CrawlQueue.launch(job) -> None` (Synchronous)

**Contract**: Called by handler **only for ADMITTED jobs** after sending first message and recording `message_id`. Sets `job.state = JobState.LAUNCHING` and `job.status_ready.set()`.

**Fix for D2**: `handle_url` must call `crawl_queue.launch(job)` **only when `admission.outcome == Outcome.ADMITTED`**.

### 3.4 `CrawlQueue.may_start(job) -> bool` (Synchronous)

```python
def may_start(self, job):
    if job.cancel_requested:
        return False
    if self._is_suspended():
        return False
    if self._running_count > self._capacity:  # FIXED: > not >=
        return False
    return True
```

**Fix for D4**: Change `>=` to `>`.

### 3.5 `CrawlQueue.mark_browser_launched(job) -> None` (Synchronous)

**Contract**: Called **immediately after `chromium.launch()` returns successfully** (inside `WebCloner.run()`). Sets `job.charged = True` and invokes injected `charge(user_id)`.

**Fix for D5**: Move call into `bot/core/crawler.py:WebCloner.run()` right after `browser = await pw.chromium.launch(...)`. Remove `_charge_usage` from `handle_url` / request path.

### 3.6 Job State Machine (Fix D6, D13, D17)

Required states in `_run_crawl`:
```python
job.state = JobState.LAUNCHING   # set by launch()
await job.status_ready.wait()
if job.cancel_requested:
    crawl_queue.complete(job)
    return

if not crawl_queue.may_start(job):
    crawl_queue.defer(job)
    await telegram_notifier.on_deferred(job)  # Single notification
    return

# Browser launch
browser = await pw.chromium.launch(...)
crawl_queue.mark_browser_launched(job)  # charges usage HERE
job.state = JobState.CRAWLING

# ... crawl pages ...
job.state = JobState.PACKING
# ... create zip ...
job.state = JobState.DELIVERING
# ... upload ...
job.state = JobState.DONE
```

Add `_delivering: list[CrawlJob]` to queue `__init__`. Manage in state transitions:
- When `job.state = JobState.DELIVERING`: `self._delivering.append(job)`
- When `job.state = JobState.DONE`: `self._delivering.remove(job)` (in `release_slot` before `pump()`)
- `cancel()` for running jobs: if job in `_delivering`, remove it.

### 3.7 `CrawlQueue.release_slot(job) -> None` (Synchronous)

**Contract**: Called **after archive built, before upload**. Decrements `_running_count`, sets `job.slot_held = False`. If job in `_delivering`, removes it. Then calls `pump()`.

**Fix for D7**: Wrap `_run_crawl` body in `try/finally`:
```python
try:
    # ... crawl logic ...
    crawl_queue.release_slot(job)  # before packing/upload
    # ... pack + upload ...
finally:
    if job.slot_held:  # covers early returns/exceptions
        crawl_queue.release_slot(job)
    crawl_queue.complete(job)
```

### 3.8 `CrawlQueue.defer(job) -> None` (Synchronous)

**Fix for D8, D11**: Remove `self._notify_deferred(job)` from `defer()`. Remove `notifier` parameter from `expire_stale`; use `self._notifier`. Let the caller (`_run_crawl` / `_maintenance`) handle notifications.

### 3.9 `CrawlQueue.record_duration(job, duration_seconds) -> None` (Synchronous)

**Fix for D9**: Runner passes held-window duration: `self._clock() - job.admitted_at` (set in ADMITTED branch and `pump()`). Queue records only successful DONE jobs.

### 3.10 `CrawlQueue.requeue(job, delay_seconds) -> None` (Synchronous) — NEW METHOD

**Fix for D10**: Encapsulates retry logic:
```python
def requeue(self, job: CrawlJob, delay_seconds: float) -> None:
    job.not_before = self._clock() + delay_seconds
    job.state = JobState.QUEUED
    job.launch_attempts += 1
    bisect.insort(self._pending, job, key=lambda j: j.sequence)
    self.pump()
```

Runner calls `crawl_queue.requeue(job, delay)` instead of manipulating `_pending` directly.

### 3.11 `expire_stale(now: float) -> list[CrawlJob]` (Synchronous)

**Fix for D11**: Return expired jobs list; caller (`_maintenance`) awaits notifications. Remove `notifier` parameter; use `self._notifier` internally.

### 3.12 `cancel(user_id) -> tuple[str, bool]` (Synchronous)

**Fix for D12, D17**: Add membership checks:
```python
if job.state == JobState.QUEUED:
    if job in self._pending:
        self._pending.remove(job)
    ...
if job.state in (JobState.ADMITTED, JobState.LAUNCHING):
    if job in self._pending:
        self._pending.remove(job)
    if job.slot_held:
        self._running_count = max(0, self._running_count - 1)
        job.slot_held = False
    ...
```

### 3.13 `stats() -> dict` (Synchronous)

**Fix for D13**: Add required keys per spec:
```python
delivering = len(self._delivering)
return {
    "waiting": len([j for j in self._pending if j.state == JobState.QUEUED]),
    "running": self._running_count,
    "delivering": delivering,
    "capacity": self._capacity,
    "workers_per_crawl": self._workers_per_crawl,
    "cpu_slots": self._cpu_slots,
    "memory_available_mb": self._memory_available_mb,
    "memory_reserve_mb": self._memory_reserve_mb,
    "memory_budget_mb": self._memory_budget_mb,
    "launch_failures_last_hour": recent_failures,
    "launch_failures_total": len(self._launch_failures),
    "launch_failures_by_kind": Counter(kind for _, kind in self._launch_failures),
    "suspended": self._is_suspended(),
    "suspended_until": self._suspended_until if self._is_suspended() else 0,
    "consecutive_failures": self._consecutive_failures,
    "last_reading": self._last_reading,
    "last_probe_error": self._last_probe_error,
}
```
Queue stores `_last_reading`, `_last_probe_error`, `_cpu_slots`, `_memory_available_mb`, `_memory_reserve_mb`, `_memory_budget_mb` from resource probe.

### 3.14 `shutdown() -> None` (Async)

**Fix for D14**: Add `_shutting_down` flag checked in `submit()`, `pump()`, `defer()`, `set_capacity()`:
```python
async def shutdown(self) -> None:
    self._shutting_down = True
    for task in self._crawl_tasks:
        task.cancel()
    if self._crawl_tasks:
        await asyncio.gather(*self._crawl_tasks, return_exceptions=True)
    self._crawl_tasks.clear()

# In submit(), pump(), defer(), set_capacity():
if self._shutting_down:
    return  # or reject for submit()
```

---

## 4. Missing Features

### 4.1 Bot Menu Commands (`set_bot_commands`) — Fix D15

**Location**: `main.py` in `_main()` after `bot.start()`.

**Commands** (only implemented handlers):
```python
from hydrogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChat

USER_COMMANDS = [
    BotCommand("start", "Start the bot"),
    BotCommand("help", "Show help"),
    BotCommand("cancel", "Cancel current crawl"),
    BotCommand("cookies", "Manage cookies"),
    BotCommand("scope", "Set crawl scope"),
    BotCommand("settings", "Show your settings"),
    BotCommand("info", "Page info for a URL"),
    BotCommand("media", "Media extraction help"),
    BotCommand("stats", "Your statistics"),
    BotCommand("version", "Bot version"),
]

ADMIN_COMMANDS = [
    BotCommand("admin", "Admin panel"),
    BotCommand("broadcast", "Broadcast message"),
    BotCommand("clearcache", "Clear cache"),
    BotCommand("users", "List users"),
    BotCommand("topusers", "Top users"),
    BotCommand("userinfo", "User details"),
    BotCommand("ban", "Ban user"),
    BotCommand("unban", "Unban user"),
]
# /logs and /promote omitted — not implemented
```

**Implementation**:
```python
# In _main(), after bot.start():
await bot.set_bot_commands(USER_COMMANDS, scope=BotCommandScopeDefault())
for admin_id in config.ADMIN_IDS:
    await bot.set_bot_commands(
        USER_COMMANDS + ADMIN_COMMANDS,
        scope=BotCommandScopeChat(chat_id=admin_id)
    )
```

### 4.2 Progress Bar in `_progress_updater`

**Location**: `scraper.py:~773` inside `_run_crawl`.

**Source Data**: `cloner.progress` (`CrawlProgress` in `crawler.py:100-127`) has:
- `pages_crawled`, `pages_discovered` (note: actual field names)
- `assets_found`, `videos_found`
- `total_bytes`, `video_bytes`
- `status`, `current_url`, `errors`, `cancelled`

**Progress Bar Format** (5s cadence):
```python
def _make_progress_bar(pages_done, pages_total):
    if pages_total == 0:
        return "▱▱▱▱▱▱▱▱▱▱ 0%"
    pct = min(100, int(pages_done / max(1, pages_total) * 100))
    filled = max(0, min(10, pct // 10))
    bar = "▰" * filled + "▱" * (10 - filled)
    return f"{bar} {pct}%"

text = (
    f"**Cloning...**\n\n"
    f"{cloner.progress.to_text()}\n\n"
    f"{_make_progress_bar(cloner.progress.pages_crawled, cloner.progress.pages_discovered)}\n\n"
    f"_Updates every 5s — /cancel to stop_"
)
```

### 4.3 Per-Crawl Timing Evidence

**Add to logs** (structured, parseable):
```python
logger.info(
    "crawl_complete",
    extra={
        "job_id": job.job_id,
        "user_id": job.user_id,
        "domain": job.domain,
        "elapsed_seconds": round(duration, 1),
        "pages": pages,
        "assets": assets,
        "videos": videos,
        "zip_size_mb": round(zip_size / 1024 / 1024, 1),
    }
)
```

---

## 5. File Change Summary

| File | Changes |
|------|---------|
| `bot/queue/crawl_queue.py` | D1, D3, D4, D7, D8, D9, D10, D11, D12, D13, D14, D17, new `requeue()`, `_delivering` tracking, `admitted_at` |
| `bot/core/scraper.py` | D2, D5, D6, D8, D9, D10, D12, D16, progress bar, timing logs, `handle_url` launch guard, `_run_crawl` try/finally |
| `main.py` | D15 (bot menu), import `BotCommand`, `BotCommandScopeChat`, `BotCommandScopeDefault` |
| `bot/core/crawler.py` | D5 (call `mark_browser_launched` after `chromium.launch()`) |
| `tests/test_crawl_queue.py` | Regression tests for D1–D17 (see §6) |
| `bot/config.py` | Remove `CRAWL_POSITION_REFRESH_SECONDS_LONG`, `CRAWL_POSITION_REFRESH_TOP_N` (YAGNI) |

---

## 6. Regression Tests (TDD — Write First)

**Additions to `tests/test_crawl_queue.py`** (uses existing fixtures: `crawl_queue`, `sample_job`, `fake_runner`, `fake_charge`, `fake_clock`, `fake_usage_of`):

```python
# D1: ADMITTED job gets runner task
def test_submit_admitted_creates_runner_task(crawl_queue, sample_job, fake_runner):
    crawl_queue.set_capacity(2, 1)
    crawl_queue.set_capacity(2, 1)
    runner, calls = fake_runner
    admission = crawl_queue.submit(sample_job)
    assert admission.outcome == Outcome.ADMITTED
    assert len(calls) == 1
    assert calls[0] == sample_job

# D2: launch() only for ADMITTED
def test_launch_only_admitted_jobs(crawl_queue):
    crawl_queue.set_capacity(2, 1)
    crawl_queue.set_capacity(2, 1)
    job = CrawlJob(job_id="j1", user_id=1, url="https://a.com", domain="a.com",
                   scope="same-domain", cookies_file=None, chat_id=1,
                   sequence=1, enqueued_at=0.0)
    crawl_queue.submit(job)  # ADMITTED
    crawl_queue.launch(job)
    assert job.state == JobState.LAUNCHING
    assert job.status_ready.is_set()

# D3: runner returns awaitable, pump wraps in create_task
def test_pump_wraps_runner_in_task(crawl_queue, fake_runner):
    crawl_queue.set_capacity(2, 1)
    crawl_queue.set_capacity(2, 1)
    job = CrawlJob(job_id="j1", user_id=1, url="https://a.com", domain="a.com",
                   scope="same-domain", cookies_file=None, chat_id=1,
                   sequence=1, enqueued_at=0.0)
    job.state = JobState.QUEUED
    crawl_queue._by_user[1] = job
    crawl_queue._pending.append(job)
    crawl_queue.pump()
    task = list(crawl_queue._crawl_tasks)[0]
    assert isinstance(task, asyncio.Task)

# D4: may_start allows when running == capacity
def test_may_start_allows_when_running_equals_capacity(crawl_queue):
    crawl_queue.set_capacity(2, 1)
    crawl_queue._running_count = 2
    crawl_queue._capacity = 2
    job = CrawlJob(job_id="j1", user_id=1, url="https://a.com", domain="a.com",
                   scope="same-domain", cookies_file=None, chat_id=1,
                   sequence=1, enqueued_at=0.0)
    job.slot_held = True
    assert crawl_queue.may_start(job) is True

# D5: mark_browser_launched charges once
def test_mark_browser_launched_charges_once(crawl_queue, fake_charge):
    charge, charged = fake_charge
    job = CrawlJob(job_id="j1", user_id=42, url="https://a.com", domain="a.com",
                   scope="same-domain", cookies_file=None, chat_id=42,
                   sequence=1, enqueued_at=0.0)
    job.charged = False
    crawl_queue.mark_browser_launched(job)
    assert job.charged is True
    assert charged == [42]
    crawl_queue.mark_browser_launched(job)
    assert charged == [42]

# D6: job state advances through CRAWLING/PACKING/DELIVERING/DONE
# (Integration test via mocked WebCloner)

# D7: release_slot decrements and pumps
def test_release_slot_decrements_and_pumps(crawl_queue):
    crawl_queue.set_capacity(2, 1)
    crawl_queue._running_count = 2
    job2 = CrawlJob(job_id="j2", user_id=2, url="https://b.com", domain="b.com",
                    scope="same-domain", cookies_file=None, chat_id=2,
                    sequence=2, enqueued_at=0.0)
    job2.state = JobState.QUEUED
    crawl_queue._by_user[2] = job2
    crawl_queue._pending.append(job2)
    job1 = CrawlJob(job_id="j1", user_id=1, url="https://a.com", domain="a.com",
                    scope="same-domain", cookies_file=None, chat_id=1,
                    sequence=1, enqueued_at=0.0)
    job1.slot_held = True
    crawl_queue.release_slot(job1)
    assert crawl_queue._running_count == 2
    assert job2.state == JobState.ADMITTED

# D8: defer doesn't double-notify
def test_defer_no_internal_notify(crawl_queue):
    crawl_queue.set_capacity(2, 1)
    crawl_queue._running_count = 2
    job = CrawlJob(job_id="j1", user_id=1, url="https://a.com", domain="a.com",
                   scope="same-domain", cookies_file=None, chat_id=1,
                   sequence=1, enqueued_at=0.0)
    job.state = JobState.ADMITTED
    job.slot_held = True
    crawl_queue._by_user[1] = job
    crawl_queue.defer(job)
    # No internal notification task created; caller handles

# D9: record_duration uses admitted_at
def test_record_duration_uses_admitted_at(crawl_queue, fake_clock):
    clock, _ = fake_clock
    job = CrawlJob(job_id="j1", user_id=1, url="https://a.com", domain="a.com",
                   scope="same-domain", cookies_file=None, chat_id=1,
                   sequence=1, enqueued_at=0.0)
    job.admitted_at = 100.0
    crawl_queue.record_duration(job, 50.0)
    assert len(crawl_queue._durations) == 1
    assert crawl_queue._durations[0] == 50.0

# D10: requeue method encapsulates retry logic
def test_requeue_adds_job_to_pending_with_backoff(crawl_queue, fake_clock):
    clock, _ = fake_clock
    job = CrawlJob(job_id="j1", user_id=1, url="https://a.com", domain="a.com",
                   scope="same-domain", cookies_file=None, chat_id=1,
                   sequence=1, enqueued_at=0.0)
    job.state = JobState.QUEUED
    crawl_queue.requeue(job, 10.0)
    assert job.state == JobState.QUEUED
    assert job.not_before == 10.0
    assert job.launch_attempts == 1
    assert job in crawl_queue._pending

# D11: expire_stale returns list, uses internal notifier
@pytest.mark.asyncio
async def test_expire_stale_returns_list_no_notifier_param(crawl_queue, fake_clock):
    clock, advance = fake_clock
    crawl_queue._max_queue_age_seconds = 60
    job1 = CrawlJob(job_id="j1", user_id=1, url="https://a.com", domain="a.com",
                    scope="same-domain", cookies_file=None, chat_id=1,
                    sequence=1, enqueued_at=0.0)
    job1.state = JobState.QUEUED
    crawl_queue._by_user[1] = job1
    crawl_queue._pending.append(job1)
    advance(120)
    expired = crawl_queue.expire_stale()  # no notifier param
    assert len(expired) == 1
    assert expired[0].state == JobState.EXPIRED

# D12: cancel queued safe without membership
def test_cancel_queued_no_valueerror(crawl_queue):
    job = CrawlJob(job_id="j1", user_id=1, url="https://a.com", domain="a.com",
                   scope="same-domain", cookies_file=None, chat_id=1,
                   sequence=1, enqueued_at=0.0)
    job.state = JobState.QUEUED
    crawl_queue._by_user[1] = job
    # job NOT in _pending
    state, charged = crawl_queue.cancel(1)
    assert state == "queued"

# D13: stats includes delivering
def test_stats_includes_delivering(crawl_queue):
    crawl_queue.set_capacity(2, 1)
    job = CrawlJob(job_id="j1", user_id=1, url="https://a.com", domain="a.com",
                   scope="same-domain", cookies_file=None, chat_id=1,
                   sequence=1, enqueued_at=0.0)
    job.state = JobState.DELIVERING
    crawl_queue._delivering.append(job)
    crawl_queue._by_user[1] = job
    stats = crawl_queue.stats()
    assert "delivering" in stats
    assert stats["delivering"] == 1

# D14: shutdown prevents new tasks
@pytest.mark.asyncio
async def test_shutdown_sets_flag_blocks_submit(crawl_queue):
    await crawl_queue.shutdown()
    crawl_queue.set_capacity(2, 1)
    job = CrawlJob(job_id="j1", user_id=1, url="https://a.com", domain="a.com",
                   scope="same-domain", cookies_file=None, chat_id=1,
                   sequence=1, enqueued_at=0.0)
    job.state = JobState.QUEUED
    crawl_queue._by_user[1] = job
    crawl_queue._pending.append(job)
    crawl_queue.pump()
    assert crawl_queue._running_count == 0

# D15: bot menu commands registered
# (Integration test via Bot API getMyCommands)

# D16: no double on_started
# (Integration test via mocked telegram_notifier)

# D17: cancel admitted/launching cleans _pending
def test_cancel_admitted_cleans_pending(crawl_queue):
    crawl_queue.set_capacity(2, 1)
    job = CrawlJob(job_id="j1", user_id=1, url="https://a.com", domain="a.com",
                   scope="same-domain", cookies_file=None, chat_id=1,
                   sequence=1, enqueued_at=0.0)
    job.state = JobState.ADMITTED
    job.slot_held = True
    crawl_queue._by_user[1] = job
    crawl_queue._pending.append(job)  # due to D2
    crawl_queue._running_count = 1
    state, charged = crawl_queue.cancel(1)
    assert state == "not_launched"
    assert job not in crawl_queue._pending
    assert crawl_queue._running_count == 0
```

---

## 7. Verification Plan (End-to-End)

After implementation, commit, push, and redeploy via `./deploy_hoody2.sh`:

| Step | Action | Expected |
|------|--------|----------|
| 1 | `pm2 restart webscraper --update-env` | Starts clean, logs show `capacity=2, workers=1` |
| 2 | Bot API `getMyCommands` | Returns 10 user commands + 8 admin commands for admin |
| 3 | Send `/start` to `@wbclnbot` | Premium welcome renders (🕸 header, ⭐️ list, premium button icons) |
| 4 | Send `https://example.com` | "Starting clone" → progress bar updates every 5s → ZIP delivered |
| 5 | Send second URL while first runs | "Queued (position 1, ~X min)" with cancel button |
| 6 | Tap "Cancel" on queued job | "Cancelled (queued)" |
| 7 | Send third URL → queue full (if capacity=2) | "Queue full" rejection |
| 8 | `/admin` as admin | Shows queue stats: waiting, running, delivering, capacity |
| 9 | Check pm2 logs | Structured `crawl_complete` lines with elapsed_seconds, pages, assets |
| 10 | `python -m pytest` | All 54 existing + new tests pass |
| 11 | `python -m compileall -q bot main.py` | No syntax errors |

---

## 8. Rollback Plan

```bash
git revert HEAD  # or specific commit
./deploy_hoody2.sh
pm2 restart webscraper --update-env
```
No "disable queue" switch exists — rollback = git revert (per v3 design decision).

---

## 9. References

- `design_draft.md` — Referenced v3 spec (lines 556-665 queue API, 1525-1566 scraper changes, 1619-1628 main.py)
- `review_findings_v2.md` — Consolidated round-2 findings (75 items, all resolved)
- Hydrogram `set_bot_commands` + `BotCommand` + `BotCommandScopeChat` signatures (deployed venv confirmed)
- `diag4_hoody2.sh` / `diag5_hoody2.sh` output — API verification
- SQLite `bot_data.db` — `activity_log` (16 `scrape_request`, 0 `crawl_complete` since rewrite), `usage_count=16`
- Commit history: `5924bd7` (queue), `4976722` (CPU override), `81c8f32` (time import), `11f5dda` (module-level queue), `155b5b9` (TelegramNotifier order)

---

## 10. Assumptions & Constraints

- **Hydrogram 0.2.0** (MTProto layer 181) — API signatures verified on deployed venv
- **Python 3.13** on Hoody #2 (venv at `/root/Dev/WebScraper/venv`)
- **Container has no cgroup CPU quota** — probe falls back to `sched_getaffinity` (12 cores); hard ceiling `CRAWL_MAX_CONCURRENT=2` in `.env`
- **Playwright Chromium** installed at `/root/.cache/ms-playwright`
- **No user Telegram session** for end-to-end testing — verification via bot sessions only
- **Music bot `@esongybot`** runs alongside (pm2 id 0) — no resource conflict expected
- **Rollback via git revert** — no feature flag to disable queue