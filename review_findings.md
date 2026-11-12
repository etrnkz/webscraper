# EGM Review Findings — Crawl Admission Control & Queue

* **Target:** `design_draft.md` (v1, 873 lines)
* **Reviewers:** egm-zoom-out (Stage 5), egm-comprehension (Stage 6), egm-design (Stage 7),
  egm-codebase-details (reality check)
* **Date:** 2026-11-12
* **Status:** All findings below are verified against source and must be resolved in design v2.

Counts: 4 reviewers produced ~90 distinct findings. This file is the deduplicated,
prioritized, actionable set. Every item was independently verified by the orchestrator
against the real code where the reviewers disagreed.

---

## A. FACTS CONFIRMED BY THE ORCHESTRATOR (read these before rewriting)

| # | Fact | Evidence |
|---|---|---|
| A1 | `usage_count` is incremented **twice** per successful crawl today | `bot/database.py:84` (`increment_requests` → `usage_count = usage_count + 1`) and `bot/database.py:188` (`increment_usage`); called at `bot/core/scraper.py:509` and `bot/core/scraper.py:651` |
| A2 | `bot/core/__init__.py` imports the whole bot → any `bot.core.*` import runs `config.validate_config()` which `sys.exit(1)` without env vars | `bot/core/__init__.py:3` `from bot.core.scraper import bot`; `bot/config.py:19-24` |
| A3 | `request_stats.timestamp` is `TEXT` holding `isoformat()`; `cleanup_old_data()` compares it to an epoch **float** → the DELETE matches **zero rows** | `bot/database.py:49,83,87` vs `bot/database.py:174-175` |
| A4 | The cache directory is **never written** by any code path. `save_to_cache()` / `get_cached_content()` have no callers | `bot/modules/cache_manager.py:39-85`; clone path writes to `clone_{domain}_{user_id}` (`bot/core/crawler.py:394-399`) |
| A5 | `CRAWL_CONCURRENCY` also sizes the asset semaphore (`concurrency*2`) and the httpx pool | `bot/core/crawler.py:228`, `bot/core/crawler.py:512` |
| A6 | The call site that must change is `bot/core/scraper.py:577` `concurrency=config.CRAWL_CONCURRENCY` | grep confirmed |
| A7 | `_update_progress` task is cancelled **only on the success path** → leaks on failure and overwrites the failure text every 5 s | `bot/core/scraper.py:586-591`, `669-681` |
| A8 | `tests/` does **not** exist (comprehension reviewer was wrong; codebase-details was right) | `glob tests/**` → no files |
| A9 | `bot/monitoring/performance.py` `measure_time` is **synchronous** → measures coroutine *creation*, not execution. Unusable on `_run_crawl` | `bot/monitoring/performance.py:15-27` |
| A10 | `pending_cookie_requests` is written and never read (unbounded write-only leak); `active_progress_msgs` and `download_executor` are fully dead | `bot/core/scraper.py:36,58,60,531` |
| A11 | `CRAWL_TIMEOUT_SECONDS` bounds only the **page-crawl phase**; video download (`yt-dlp`) and HTML rewrite run after it, unbounded | `bot/core/crawler.py:278` vs `300-307,469-505,564` |
| A12 | Hydrogram 0.2.0 has **no `on_start`**; `Client.run(coro)` does `run(coroutine)` only; `idle()`/`start()`/`stop()` are public | `hydrogram/methods/utilities/run.py:73,76-77`; `methods/decorators/__init__.py` has 13 decorators, no `OnStart` |
| A13 | `output_dir` / `zip_path` are **not unique per job** — two concurrent crawls for one user collide | `bot/core/scraper.py:569-570` `clone_{domain}_{user_id}` |
| A14 | `bot/constants.py` is the established home for every user-facing string | `bot/constants.py:37-50`, consumed at `bot/core/scraper.py:409,445,516,696` |
| A15 | Every `active_jobs` line reference in the design is correct (57,125,264,511,584,595,606,622,663,715) | codebase-details verified all 10 |

---

## B. BLOCKERS — must be resolved in the design contract

### B1. Quota is charged twice, and the design never mentions `increment_requests`
Design G7 / §5 / plan §5 assert "charged once". `increment_requests(success=True)` bumps
`usage_count` independently. Effective free allowance today is **two** crawls, not three.
The Verification Plan asserts "gates usage past three crawls" — false today and after.
**Fix:** `bot/database.py` joins the Target Files list; `increment_requests` stops touching
`usage_count`; `increment_usage` becomes the sole quota writer.

### B2. Charging at job-start creates an unbounded force-join bypass
`check_force_join` (`bot/core/scraper.py:78-89`) reads `usage_count` at **submit** time.
If the charge moves to job-start, the check and the charge are separated by the queue wait.
One user can submit 100 URLs, all pass the gate at usage 0, all eventually execute and charge.
**Fix:** reservation/settlement. Reserve one unit at submit (atomic, against
`usage_count + pending_reservations`), convert to a charge at job start, release on
rejection / queue cancellation / terminal failure.

### B3. The backoff has no mechanism
`CrawlJob` has no `retry_at`/`not_before` field; `pump()` has no skip condition; the pump
runs after every completion and submit. A re-queued job is immediately eligible → hot loop,
a Chromium spawn storm on an already-starved container.
**Fix:** `retry_at: float` (monotonic) on the job; `pump()` skips jobs with `retry_at > now`;
injected clock for testability.

### B4. "Front of `_pending`" contradicts "keeps its place" and the FIFO promise
Two opposite policies in adjacent sentences. Front-insertion + no backoff = 3-job infinite
loop starving 97 waiting users.
**Fix:** re-queue at the **back**, preserving relative order; the job becomes ineligible
until `retry_at`. Restate the fairness invariant precisely.

### B5. Capacity is specified three incompatible ways
§1 "re-read on every decision" / §3 "refreshed by the probe task" / NFR "file reads happen on
the probe task, not on the request path" / NFR Reliability "re-read at the moment of the
decision". The injected `capacity_provider` has no defined semantics.
**Fix:** one model. The queue owns a cached `_capacity`; the probe is the **only** component
that touches the filesystem; the request path reads the cached integer only.

### B6. `pump()` reentrancy and cancel-vs-start race are unspecified
Nothing states when a job enters `_running` relative to `asyncio.create_task`, nor whether
`pump()` may await. Over-admission: `_running` populated inside the task body → two admits
for capacity 1. Cancel race: `pump()` pops then awaits the "Starting clone" edit → the job is
in neither collection, `cancel()` returns "none", and the crawl runs anyway.
**Fix:** `pump()` is **synchronous and contains no awaits**; the slot is claimed and the job
inserted into `_running` **before** `create_task`; the task's `finally` removes it then pumps.
State testable invariants.

### B7. Capacity can drop between decision and launch
The NFR claims the queue never starts more crawls than capacity "even if capacity drops
between the decision and the launch" — the mitigation only protects work *already in flight*,
not an admitted-but-not-yet-launched job.
**Fix:** admission is **provisional**. The runner re-checks `queue.may_start(job)` immediately
before `chromium.launch()`; a capacity refusal re-queues **without** consuming the launch
retry budget. Three distinct start outcomes: admitted-and-launched / admitted-but-deferred /
admitted-but-launch-failed.

### B8. Position refresh and estimate have no owner
G5 and Q2 promise a live-updating position and a measured estimate. `CrawlQueue`'s public
surface has no refresh method; `start_background_tasks()` returns only probe + cleanup; no
`last_advertised_position` field; **nothing calls `record_duration()`**.
**Fix:** name the owner (a third background task or a per-job timer), name the fields, name
the call site of `record_duration`, and name the message text producer.

### B9. `AdmissionResult` cannot distinguish DUPLICATE from QUEUE_FULL
Three enum members; two distinct rejections with two distinct required messages.
`submit()` returning `tuple[AdmissionResult, int]` cannot carry the reason.
**Fix:** a result object carrying `outcome` + `reason` + `position`.
Also: submit order must check **capacity before queue-full**, otherwise a request is
rejected while a slot is demonstrably idle.

### B10. `cleanup_old_data()`'s `request_stats` DELETE is a no-op (see A3)
G8's "tables are swept" is false for `request_stats`. Verification step 7 ("DB stops
growing") fails.
**Fix:** fix the cutoff comparison (ISO string or epoch int) and add a unit test that
inserts an old row and asserts it is deleted. Restate the verification step honestly
(`request_stats` pruned, `activity_log` capped at 10k, **file size** not guaranteed to shrink
without `VACUUM`).

### B11. No scavenger for orphaned clone directories and ZIPs
The `finally` cleanup (`bot/core/scraper.py:662-666`) runs only on a clean exit. The design's
own failure mode #1 is the OOM kill. Up to 2 GB ZIP + working tree per orphan, forever.
**Fix:** startup scavenger removing `clone_*` dirs and `clone_*.zip` older than a threshold.

### B12. Shutdown does not cancel or await crawl tasks → orphaned Chromium
`asyncio.create_task(_run_crawl(...))` keeps no reference. pm2 SIGINT + 1.6 s SIGKILL cannot
close Chromium. Each restart orphans 300–650 MB permanently; after ~5 restarts the container
can never launch a browser again.
**Fix:** keep strong task references; cancel **and await** them in the shutdown `finally`;
wrap `WebCloner.run()`'s body in `try/finally` so `browser.close()` always runs; reap
pre-existing orphan Chromium at startup.

### B13. `create_zip` and `shutil.rmtree` block the event loop
`create_zip` over up to 2 GB (`bot/core/scraper.py:616`) and the recursive delete
(`664-666`) run synchronously on the loop thread — the probe, the pump, `/cancel`, and the
Telegram heartbeat all stall for minutes. Same visible symptom the design exists to fix.
**Fix:** move both through `asyncio.to_thread`.

### B14. Background tasks have no exception guard and no supervisor
One `OSError` or `int("max")` `ValueError` kills the probe task forever; the exception is never
retrieved; capacity freezes at its last value and `/admin` still shows a normal panel.
**Fix:** `try/except Exception` around each loop body with structured logging; a `done_callback`
that logs unexpected death at ERROR; `/admin` shows the probe's last-success timestamp and
derived capacity.

### B15. The per-user duplicate invariant is not closed
The existing check (`bot/core/scraper.py:511`) is followed by `await msg.reply(...)` (550) and
`asyncio.create_task(...)` (561); `active_jobs[user_id] = cloner` happens **inside** the task
at 584. Two rapid messages from one user both pass the check and then write to the **same**
`clone_{domain}_{user_id}` paths (A13) → corrupt/deleted ZIPs.
**Fix:** `_by_user: dict[int, CrawlJob]` index covering pending **and** running; insert
synchronously at the top of `submit()` before any await; per-job unique output paths.

---

## C. MAJORS — resource model

- **C1** cgroup probe never reads `/proc/self/cgroup` → on a nested cgroup (e.g. a systemd
  slice) it reads the root's quota and falls back to `os.cpu_count()` = host cores → capacity 4
  on a 2-core box. Resolve the cgroup path from `/proc/self/cgroup` + `/proc/self/mountinfo`;
  log the resolved path and WARN on fallback.
- **C2** cgroup v1 fallback incomplete: the combined mount is usually `/sys/fs/cgroup/cpu,cpuacct/`;
  `cpu.cfs_quota_us = -1` means unlimited and must map to `None`, not a negative truthy float
  (`max(1, min(-0.02, ...))` = 1, pinning the bot to one crawl forever).
- **C3** `memory.max == "max"` (v2) is unhandled → `int("max")` `ValueError`. CPU path handles
  the sentinel; memory path must too.
- **C4** `max(1, …)` floor defeats the memory guard exactly when memory is exhausted.
  **Fix:** `capacity = max(0, min(cpu_slots, memory_slots, ceiling))` with `cpu_slots >= 1`;
  capacity 0 means queue, not admit.
- **C5** No reserve margin. The 5.3 GB is shared with ~20 apps. **Fix:**
  `CRAWL_MEMORY_RESERVE_MB` (default 1024) subtracted from headroom.
- **C6** `CRAWL_MEMORY_BUDGET_MB = 1000` is unmeasured and excludes the Playwright Node driver,
  Chromium's 5–8 child processes, and `_download_assets`' per-page `httpx.AsyncClient` that
  buffers every asset body concurrently (`bot/core/crawler.py:507-522`). Set from a measured
  p90/p95, and bound in-flight asset fetches.
- **C7** The CPU budget is spent twice: capacity 2 × 2 workers = 4 page contexts on 2 cores.
  **Fix:** `workers_per_crawl = max(1, floor(cores / capacity))`; invariant `capacity × workers ≤ cores`.
- **C8** `CRAWL_CONCURRENCY = 0` sentinel can reach `WebCloner` → `asyncio.Semaphore(0)` never
  releases and `min(concurrency, max_pages)` creates zero workers → crawl burns the full 300 s.
  **Fix:** resolve in exactly one place, clamp to ≥ 1, assert in `WebCloner.__init__`.
- **C9** The deployment note omits `CRAWL_CONCURRENCY`; an existing `.env` value silently keeps
  the 15-worker thrash while the queue makes it look fixed. Log the resolved worker count.
- **C10** 15 s probe interval is too coarse for a shared container; no circuit breaker on
  repeated launch failures. **Fix:** 1–2 s memory probe, 15 s CPU probe, and a launch circuit
  breaker (N consecutive failures → suspend all launches until a probe confirms headroom).
- **C11** `BrowserLaunchError` conflates a missing Chromium binary / blocked sandbox / bad
  driver with genuine resource pressure. **Fix:** classify as `ResourcePressure | Configuration
  | Transient`; only the first and third consume the retry budget; a configuration failure
  fails fast and loudly.
- **C12** A job whose browser never launched, or that is cancelled mid-flight, is charged under
  the current G7 wording. **Fix:** publish an explicit charging truth table.
- **C13** No `job_started` callback → a queued user never sees "Starting clone"; the message
  jumps from "Queued" straight to "Cloning…" (and the progress task sleeps 5 s first).
- **C14** `cancel()` reports "running" while `cancel_running` is still `None` → user is told
  "cancelled" and the crawl proceeds. **Fix:** `cancel_requested: bool` / `asyncio.Event` set
  regardless of cloner existence; the runner checks it before constructing the cloner.
- **C15** `CrawlJob` as a value-equality dataclass + `deque.remove()` can remove the wrong job.
  **Fix:** `@dataclass(eq=False)`; look up by `user_id` via `_by_user`; remove by identity.
- **C16** The queue retains full Telegram `Message` objects for up to 100 queued jobs —
  personal data held for the whole wait, contradicting the Security NFR.
  **Fix:** store `(chat_id, message_id)` integers only; reconstruct via `bot.get_messages`.
- **C17** `pending_cookie_requests` is a second unbounded write-only registry (A10), so the
  "single registry" claim is false. **Fix:** delete it or implement and bound it.
- **C18** The wait estimate is wrong three ways: the fallback omits `capacity` (disagreeing by
  exactly `capacity` with the measured branch); cancelled/zero-page crawls contaminate the
  sample; `average_duration == 0` divides by zero.
  **Fix:** one formula `wait = position × duration_estimate / capacity`; sample only successful
  completions; clamp the estimate to 5–600 s.
- **C19** The capacity slot is held through ZIP creation and a multi-gigabyte upload, but the
  duration model measures only the crawl. **Fix:** define the slot lifetime explicitly and
  measure across the whole runner.
- **C20** The queue-full message hardcodes "100" and "15 minutes" while both are configurable
  and the real drain time with capacity 2 and a 300 s cap is ~4.2 hours.
  **Fix:** derive every number from state; say "try later" instead of a false precise figure.
- **C21** The queue is bounded by count, not by time; the worst-case wait is ~250 minutes and
  is never communicated. **Fix:** a maximum queue age (30–45 min) with an honest expiry message.
- **C22** No per-user fairness: one account can occupy all 100 slots. **Fix:** per-user pending
  cap (e.g. 2 queued + 1 running); implement `RATE_LIMIT`/`DAILY_LIMIT` or state the limitation.
- **C23** `/admin` shows "Launch failures (last hour)" but `stats()` returns a cumulative
  counter and no timestamped store exists. **Fix:** bounded timestamped deque; report both
  "last hour" and "total", broken down by classification.
- **C24** `BrowserLaunchError` must wrap the **whole** `async with async_playwright()` region —
  the Node driver spawns on context-manager entry, and an exception there escapes a
  `try` wrapped only around `pw.chromium.launch(...)`.
- **C25** `submit()` cannot distinguish duplicate from full queue (see B9).
- **C26** The `/admin` block shows CPU and memory readings that `derive_capacity()` (an `int`)
  cannot supply. **Fix:** the probe returns a structured reading object.
- **C27** The hysteresis rule has no home: nothing stores the previous value or counts
  agreements; `resources.py` exposes only three functions returning plain values.
  **Fix:** the queue owns `_capacity` and the agreement counter.
- **C28** The initial capacity is undefined and the first probe tick can be 15 s late.
  **Fix:** seed capacity at construction; the probe reads immediately before its first sleep.
- **C29** The `CrawlQueue` construction point and new imports are unspecified; placing it
  where `active_jobs` sits is an import-time `NameError` on `_run_crawl`.
  **Fix:** construct after `_run_crawl` is defined; list the imports.
- **C30** `main.py`'s `KeyboardInterrupt` / fatal-error handling and `__main__` guard must be
  retained; `task.cancel()` alone does not prevent "Task was destroyed but it is pending"
  (need `await asyncio.gather(*tasks, return_exceptions=True)`).
- **C31** `increment_requests(success=False)` is skipped if `BrowserLaunchError` propagates →
  `/admin`'s error count under-reports. Decide and state.
- **C32** The 300 s cap bounds only the page phase (A11) → the duration sample and the
  "hard cap" claim are both wrong. State it, and bound the post-processing phase or exclude
  it from the sample.
- **C33** `pytest` and `ruff` are dev extras not installed by the Dockerfile; the Verification
  Plan requires them "on the deployment host". **Fix:** state where verification runs.
- **C34** The design never mentions the ~2,000-user target → no Impact, Capacity, or Cost
  reasoning. At capacity 2 and a 300 s cap, throughput is ~430 crawls/day.
  **Fix:** add a Scale/Impact section with peak arrival rate, drain time, headroom at 10×, and
  the condition for the next capacity step. **Fix:** add a Cost section (queue memory, disk per
  crawl, Telegram API call volume, user-visible wait). **Fix:** add a Risk register table.
  **Fix:** add a Timeline with phases and dependencies. **Fix:** add success metrics with
  thresholds, an observation window, and a kill criterion.

---

## D. MAJORS — user-facing correctness

- **D1** "Replace the status message with the queue message" — edit in place or new reply? Is
  the Cancel button preserved? The queued block silently drops the URL/scope/cookies lines.
- **D2** A queued user is first told "Launching Chromium…" before any admission decision.
- **D3** CUJ 2 and §8 disagree about what a queued job shows when it starts.
- **D4** "The slot it would have taken is released" — cancelling a queued job releases no slot;
  it lets the next user move up one position.
- **D5** The §8 queued message does not show the Cancel button that CUJ 2 requires.
- **D6** Cancellation semantics across states are undefined: `_rewrite_all_html` ignores the
  cancel event, `create_zip` is uncancellable, and deleting the ZIP during an upload races the
  delivery. **Fix:** publish a state machine
  `QUEUED → ADMITTED → LAUNCHING → CRAWLING → PACKING → DELIVERING → DONE` with cancel
  honoured only up to `PACKING`.
- **D7** The queue-full message discloses the queue depth and capacity existence, contradicting
  the Security NFR's "must not reveal internal capacity numbers".
- **D8** New user-facing strings bypass `bot/constants.py` (A14).
- **D9** The `final_failure_handler` should **edit** the existing status message, not send a
  second near-identical message.
- **D10** The queue holds Telegram objects → the "testable without Telegram" claim is false.
  **Fix:** a `JobNotifier` protocol (`on_queued`, `on_started`, `on_retry`, `on_failed`).

---

## E. MAJORS — operability

- **E1** `cleanup_old_data()`'s unbounded `DELETE ... NOT IN (SELECT ...)` is a full scan and
  a long write transaction; under WAL it blocks every other writer, and **no** database call in
  `bot/database.py` is wrapped against `sqlite3.OperationalError`. A locked DB raises out of
  `handle_url` and the user's message is never acknowledged.
  **Fix:** bounded batches with commits between; `PRAGMA busy_timeout`; a retry helper.
- **E2** `asyncio.to_thread` creates thread-local SQLite connections that are never closed
  (`get_conn()` uses `threading.local()`; no `close_conn()` exists).
  **Fix:** add `close_conn()`; give the cleanup task a dedicated single-thread executor.
- **E3** Unbounded pm2 log growth; `debug_catch_all` logs **every** message at INFO with
  usernames and URLs, in a file on the same 78 GB volume G8 protects.
  **Fix:** pm2 log rotation; gate or remove `debug_catch_all`.
- **E4** `bot/monitoring/performance.py` already owns duration stats and feeds `/admin`
  (A9) — a second private duration store will disagree. **Fix:** extend it with an async-aware
  recorder, and warn that `measure_time` is unusable on coroutines.
- **E5** No deterministic way to exercise the launch-failure path (the owner's first named
  trigger). **Fix:** a fault-injection hook (`CRAWL_FORCE_LAUNCH_FAILURE=1`).
- **E6** The `async def` test style requires `pytest-asyncio`, which is not in the dev extras
  and not in `pyproject.toml`; plain pytest silently **skips** async tests, so the suite can
  pass green with zero queue tests executed.
  **Fix:** add `pytest-asyncio` + `asyncio_mode = "auto"`, or require synchronous `def` tests
  driving `asyncio.run`.
- **E7** `bot/core/__init__.py` side effect (A2) breaks testability of anything placed in
  `bot/core/`. **Fix:** place the new modules outside `bot.core`, or remove the side effect.
- **E8** `pytest`/`ruff`/`mypy` availability (C33). `mypy` is configured but absent from the plan.
- **E9** `bot/core/crawler.py:_PriorityQueue` exists but must **not** be reused (no removal by
  identity). Recorded so an implementer does not reach for it.

---

## F. WHAT SURVIVED THE ATTACK (keep in v2)

- The queue itself is earned. Alternative A (a bare semaphore) is genuinely rejected: a task
  waiting on a semaphore gives no position, no estimate, and no cancellation story.
- Reading "100" as the bound on **waiters** rather than **runners** is the only self-consistent
  reading of the owner's wording, and the design says so explicitly.
- Folding `active_jobs` into one queue-owned registry is a real simplification.
- `bot/errors.py` as a separate module is correct and matches the codebase's lazy-import pattern.
- The change is additive and reversible: no schema migration, no new runtime dependency.
- **The `CRAWL_CONCURRENCY` default change (15 → quota-derived) is the single highest-leverage
  line in the whole design.** It should be the headline fix in v2, not buried in a section.

---

## G. DESIGN v2 MUST ANSWER (the human-approval checklist)

1. Quota: reservation vs settlement, one writer, truth table, refund policy.
2. Capacity: one source of truth, provisional admission, circuit breaker, reserve, probe cadence.
3. Backoff: `retry_at` field, back-of-queue re-queue, the fairness invariant in one sentence.
4. Queue contract: result object with a reason, correct submit ordering, `_by_user` index,
   synchronous `pump()`, published invariants.
5. Telegram isolation: `JobNotifier` protocol, integer IDs only, real testability.
6. Durability: startup scavenger, shutdown cancels+awaits crawl tasks, orphaned-Chromium reaping.
7. Event loop: ZIP/rmtree off the loop; background tasks supervised.
8. Truthful user messages: every number derived from state; a published cancel state machine.
9. Scale, cost, risk, timeline, metrics, kill criterion — the owner's actual decision inputs.
10. A verification path the owner can actually run on Hoody #2, including a deterministic
    launch-failure test and a stated dev-tooling environment.
