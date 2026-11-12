# EGM Review Findings v2 — Crawl Admission Control & Queue

* **Target:** `design_draft.md` v2 (1,664 lines)
* **Reviewers:** egm-zoom-out (Stage 8), egm-implementation-readiness (Stage 9),
  egm-codebase-details, egm-comprehension, egm-structural, egm-style
* **Date:** 2026-11-12
* **Status:** All findings below are verified against source and must be resolved in design v3.

Counts: 6 reviewers produced ~120 distinct findings. This file is the deduplicated,
prioritized, actionable set. Every item was independently verified by the orchestrator
against the real code where the reviewers disagreed.

---

## A. FACTS CONFIRMED BY THE ORCHESTRATOR (read these before rewriting)

| # | Fact | Evidence |
|---|---|---|
| A1 | `usage_count` is incremented **twice** per successful crawl today | `bot/database.py:84` (`increment_requests` → `usage_count = usage_count + 1`) and `bot/database.py:188` (`increment_usage`); call sites `bot/core/scraper.py:509` and `:651` |
| A2 | `bot/core/__init__.py` imports the whole bot → any `bot.core.*` import runs `config.validate_config()` which `sys.exit(1)` without env vars | `bot/core/__init__.py:3` `from bot.core.scraper import bot`; `bot/config.py:19-24` |
| A3 | `request_stats.timestamp` is `TEXT` holding `isoformat()`; `cleanup_old_data()` compares it to an epoch **float** → the DELETE matches **zero rows** | `bot/database.py:49,83,87` vs `bot/database.py:174-175` |
| A4 | The cache directory is **never written** by any code path. `save_to_cache()` / `get_cached_content()` have no callers | `bot/modules/cache_manager.py:39-85`; clone path writes to `clone_{domain}_{user_id}` (`bot/core/crawler.py:394-399`) |
| A5 | `CRAWL_CONCURRENCY` also sizes the asset semaphore (`concurrency*2`) and the httpx pool | `bot/core/crawler.py:228`, `bot/core/crawler.py:512` |
| A6 | The call site that must change is `bot/core/scraper.py:577` `concurrency=config.CRAWL_CONCURRENCY` | grep confirmed |
| A7 | `_update_progress` task is cancelled **only on the success path** → leaks on failure and overwrites the failure text every 5 s | `bot/core/scraper.py:586-591`, `669-681` |
| A8 | `tests/` does **not** exist | `glob tests/**` → no files |
| A9 | `bot/monitoring/performance.py` `measure_time` is **synchronous** → measures coroutine *creation*, not execution. Unusable on `_run_crawl` | `bot/monitoring/performance.py:15-27` |
| A10 | `pending_cookie_requests` is written and never read (unbounded write-only leak); `active_progress_msgs` and `download_executor` are fully dead | `bot/core/scraper.py:36,58,60,531` |
| A11 | `CRAWL_TIMEOUT_SECONDS` bounds only the **page-crawl phase**; video download (`yt-dlp`) and HTML rewrite run after it, unbounded | `bot/core/crawler.py:278` vs `300-307,469-505,564` |
| A12 | Hydrogram 0.2.0 has **no `on_start`**; `Client.run(coro)` does `run(coroutine)` only; `idle()`/`start()`/`stop()` are public | `hydrogram/methods/utilities/run.py:73,76-77`; `methods/decorators/__init__.py` has 13 decorators, no `OnStart` |
| A13 | `output_dir` / `zip_path` are **not unique per job** — two concurrent crawls for one user collide | `bot/core/scraper.py:569-570` `clone_{domain}_{user_id}` |
| A14 | `bot/constants.py` is the established home for every user-facing string | `bot/constants.py:37-50`, consumed at `bot/core/scraper.py:409,445,516,696` |
| A15 | Every `active_jobs` line reference in the design is correct (57,125,264,511,584,595,606,622,663,715) | codebase-details verified all 10 |
| A16 | The `/media` command calls `increment_requests(success=True)` at `scraper.py:476` — a third call site unaccounted for in the quota model | `bot/core/scraper.py:476` |
| A17 | `increment_requests(success=False)` at `scraper.py:660` is the failure-path counter, also unaccounted | `bot/core/scraper.py:660` |
| A18 | `bot/core/crawler.py:_PriorityQueue` exists but has no `get_nowait`; `_drain_queue` calls it and the exception is swallowed | `bot/core/crawler.py:136-185, 309-314` |
| A19 | `_download_videos` calls `ydl.extract_info(url, download=True)` — a **blocking** yt-dlp download inside an async function | `bot/core/crawler.py:496` |
| A20 | `_rewrite_all_html` is synchronous and loops over all pages/assets doing full-string replaces | `bot/core/crawler.py:564-595` |
| A21 | `FORCE_SUBSCRIBE_ENABLED` is a no-op flag; force-join gate is unconditionally active | `bot/config.py:59`; `bot/core/scraper.py:50-52, 78-89`; `bot/plugins/force_subscribe.py` |
| A22 | `os.cpu_count()` returns 2 on the target (the cgroup quota), `nproc` reports 12 (host cores) | concepts_brief line 210 |
| A23 | `CRAWL_CONCURRENCY` default is 15, also used for asset semaphore (`*2`) and httpx pool | `bot/config.py:75`; `crawler.py:228,512` |
| A24 | The queue is in-memory only; a restart drops all waiting jobs | N3, Security NFR, Rollout guarantee |
| A25 | `bot/edit_message_text(chat_id, message_id, text)` exists and is used at `scraper.py:143, 816` | codebase confirmed |

---

## B. BLOCKERS — must be resolved in the design contract

### B1. Quota model is wrong: double-count + `/media` + failure path unaccounted
Design §4 / truth table assert "charged once at browser launch". Reality: three `increment_requests` call sites (`scraper.py:476, 651, 660`), two of which write `usage_count`. The effective free allowance today is **two** crawls, not three. The `/media` command consumes quota but is never mentioned. The failure-path counter (`success=False`) at `:660` is not accounted for in the truth table. The truth table must cover `/media` and the failure path, and `increment_requests` must stop writing `usage_count`.

### B2. Charging at job-start creates an unbounded force-join bypass
`check_force_join` (`scraper.py:78-89`) reads `usage_count` at **submit** time. If the charge moves to job-start, the check and the charge are separated by the queue wait. One user can submit 100 URLs, all pass the gate at usage 0, all eventually execute and charge. **Fix:** reservation/settlement or per-user live-job cap. The design already has the per-user cap (`_by_user` index) but §4's truth table doesn't reflect it.

### B3. The backoff has no mechanism
`CrawlJob` has no `retry_at`/`not_before` field (or it does but `pump()` has no skip condition); the pump runs after every completion and submit. A re-queued job is immediately eligible → hot loop, a Chromium spawn storm on an already-starved container. **Fix:** `not_before: float` (monotonic) on the job; `pump()` skips jobs with `not_before > now`; injected clock for testability.

### B4. "Front of `_pending`" contradicts "keeps its place" and the FIFO promise
Two opposite policies in adjacent sentences. Front-insertion + no backoff = 3-job infinite loop starving 97 waiting users. **Fix:** re-queue at the **back**, preserving relative order; the job becomes ineligible until `not_before`. Restate the fairness invariant precisely.

### B5. Capacity is specified three incompatible ways
§1 "re-read on every decision" / §3 "refreshed by the probe task" / NFR "file reads happen on the probe task, not on the request path" / NFR Reliability "re-read at the moment of the decision". The injected `capacity_provider` has no defined semantics. **Fix:** one model. The queue owns a cached `_capacity`; the probe is the **only** component that touches the filesystem; the request path reads the cached integer only.

### B6. `pump()` reentrancy and cancel-vs-start race are unspecified
Nothing states when a job enters `_running` relative to `asyncio.create_task`, nor whether `pump()` may await. Over-admission: `_running` populated inside the task body → two admits for capacity 1. Cancel race: `pump()` pops then awaits the "Starting clone" edit → the job is in neither collection, `cancel()` returns "none", and the crawl runs anyway. **Fix:** `pump()` is **synchronous and contains no awaits**; the slot is claimed and the job inserted into `_running` **before** `create_task`; the task's `finally` removes it then pumps. State testable invariants.

### B7. Capacity can drop between decision and launch
The NFR claims the queue never starts more crawls than capacity "even if capacity drops between the decision and the launch" — the mitigation only protects work *already in flight*, not an admitted-but-not-yet-launched job. **Fix:** admission is **provisional**. The runner re-checks `queue.may_start(job)` immediately before `chromium.launch()`; a capacity refusal re-queues **without** consuming the launch retry budget. Three distinct start outcomes: admitted-and-launched / admitted-but-deferred / admitted-but-launch-failed.

### B8. Position refresh and estimate have no owner
G5 and §6 promise a live-updating position and a measured estimate. `CrawlQueue`'s public surface has no refresh method; `start_background_tasks()` returns only probe + cleanup; no `last_advertised_position` field; **nothing calls `record_duration()`**. **Fix:** name the owner (a third background task or a per-job timer), name the fields, name the call site of `record_duration`, and name the message text producer.

### B9. `AdmissionResult` cannot distinguish DUPLICATE from QUEUE_FULL
Three enum members; two distinct rejections with two distinct required messages. `submit()` returning `tuple[AdmissionResult, int]` cannot carry the reason. **Fix:** a result object carrying `outcome` + `reason` + `position`. Also: submit order must check **capacity before queue-full**, otherwise a request is rejected while a slot is demonstrably idle.

### B10. `cleanup_old_data()`'s `request_stats` DELETE is a no-op (see A3)
G8's "tables are swept" is false for `request_stats`. Verification step 7 ("DB stops growing") fails. **Fix:** fix the cutoff comparison (ISO string or epoch int) and add a unit test that inserts an old row and asserts it is deleted. Restate the verification step honestly (`request_stats` pruned, `activity_log` capped at 10k, **file size** not guaranteed to shrink without `VACUUM`).

### B11. No scavenger for orphaned clone directories and ZIPs
The `finally` cleanup (`scraper.py:662-666`) runs only on a clean exit. The design's own failure mode #1 is the OOM kill. Up to 2 GB ZIP + working tree per orphan, forever. **Fix:** startup scavenger removing `clone_*` dirs and `clone_*.zip` older than a threshold.

### B12. Shutdown does not cancel or await crawl tasks → orphaned Chromium
`asyncio.create_task(_run_crawl(...))` keeps no reference. pm2 SIGINT + 1.6 s SIGKILL cannot close Chromium. Each restart orphans 300–650 MB permanently; after ~5 restarts the container can never launch a browser again. **Fix:** keep strong task references; cancel **and await** them in the shutdown `finally`; wrap `WebCloner.run()`'s body in `try/finally` so `browser.close()` always runs; reap pre-existing orphan Chromium at startup.

### B13. `create_zip` and `shutil.rmtree` block the event loop
`create_zip` over up to 2 GB (`scraper.py:616`) and the recursive delete (`664-666`) run synchronously on the loop thread — the probe, the pump, `/cancel`, and the Telegram heartbeat all stall for minutes. Same visible symptom the design exists to fix. **Fix:** move both through `asyncio.to_thread`.

### B14. Background tasks have no exception guard and no supervisor
One `OSError` or `int("max")` `ValueError` kills the probe task forever; the exception is never retrieved; capacity freezes at its last value and `/admin` still shows a normal panel. **Fix:** `try/except Exception` around each loop body with structured logging; a `done_callback` that logs unexpected death at ERROR; `/admin` shows the probe's last-success timestamp and derived capacity.

### B15. The per-user duplicate invariant is not closed
The existing check (`scraper.py:511`) is followed by `await msg.reply(...)` (550) and `asyncio.create_task(...)` (561); `active_jobs[user_id] = cloner` happens **inside** the task at 584. Two rapid messages from one user both pass the check and then write to the **same** `clone_{domain}_{user_id}` paths (A13) → corrupt/deleted ZIPs. **Fix:** `_by_user: dict[int, CrawlJob]` index covering pending **and** running; insert synchronously at the top of `submit()` before any await; per-job unique output paths.

### B16. `CrawlJob` dataclass as written raises `TypeError` at import
Field `status_ready: asyncio.Event` has no default but follows fields with defaults (`message_id: int | None = None`, etc.). `@dataclass` requires non-default fields first. **Fix:** `@dataclass(eq=False)`; `status_ready: asyncio.Event = field(default_factory=asyncio.Event)` with `from dataclasses import field`; reorder fields so all non-defaults come first.

### B17. `JobState` is mandated but never defined
The plan instructs the implementer to define `JobState`, and the dataclass references `JobState.QUEUED`. The document names eight states in the diagram — `QUEUED`, `ADMITTED`, `LAUNCHING`, `CRAWLING`, `PACKING`, `DELIVERING`, `DONE`, `CANCELLED` — but never states the member names, values, base class (`str, Enum` like `Outcome`?), whether `DONE` and `CANCELLED` are terminal states on the same enum, or whether the runner is expected to set `LAUNCHING`, `CRAWLING`, `PACKING`, `DELIVERING`. **Fix:** give the full enum, the base class, and the complete list of state assignments and which component performs each.

### B18. `CrawlQueue` constructor and injected-callable contracts are undefined
Unspecified: parameter names and ordering; "the limits" — which limits, under what names? (config defines 8+ queue-owned limits); `runner`'s signature (async def taking job? returns? raises `BrowserLaunchError`?); `charge`'s sync-vs-async status (if sync, `mark_browser_launched` must be sync; if async, violates the "no awaits" rule); `usage_of`'s signature and the allowance threshold (`FREE_USAGE_LIMIT` = 3 not in injected list); `clock`'s signature (zero-arg callable returning float? `time.monotonic()` or `time.time()`?). **Fix:** publish the full signature with defaults, the unit of every limit, and the clock's semantics.

### B19. `queue.launch(job)` is called by the plan but does not exist in the queue's method list
`launch` is not in the list. The list contains `pump`, but `pump()` admits from `_pending`; an **admitted** job at submit time is not in `_pending` — it was claimed directly by `submit()` step 3. So something else must create the task for the admitted job. Same for `shutdown()` and `record_duration()`. **Fix:** publish a single, complete, authoritative list of `CrawlQueue`'s methods with signatures, and reconcile `launch`/`pump`/`shutdown`/`record_duration` into it.

### B20. `BrowserLaunchError` classification rules are undefined
The plan says to "Raise `BrowserLaunchError` with a classified `kind`" and defines three kinds (`RESOURCE_PRESSURE`, `CONFIGURATION`, `TRANSIENT`). It never states **how** a Playwright exception is mapped to a kind. No mapping for: missing binary → `CONFIGURATION`; blocked sandbox / missing libs → `CONFIGURATION`; `ENOMEM`/`EAGAIN` or "Target page, context or browser has been closed" under memory pressure → `RESOURCE_PRESSURE`; transient Playwright/driver IPC error → `TRANSIENT`. Also: base class, constructor signature, whether it carries the original exception as attribute or only `__cause__`, whether classifier is a separate function (needs unit test). **Fix:** explicit decision table: exception type/message pattern → kind.

### B21. Fault injection has no specified mechanism; `CRAWL_FORCE_LAUNCH_FAILURE_KIND` missing
Config table defines only `CRAWL_FORCE_LAUNCH_FAILURE` but verification step 8 uses `CRAWL_FORCE_LAUNCH_FAILURE_KIND=configuration`. No mechanism: where injected? (crawler.py at launch? queue's task wrapper? runner?). Counter scope? (process-global, per-job, per-user?). Where does counter live? Does it reset? Is injected failure's kind configurable? Does it count toward circuit breaker? **Fix:** add `CRAWL_FORCE_LAUNCH_FAILURE_KIND` to config table; state exactly where the check lives and what the counter counts.

### B22. `CRAWL_BROWSER_RETRY_LIMIT` contradicts verification step 7
Config: "Total launch attempts before giving up = 2". Verification step 7: "first two launch attempts fail... third attempt succeeds". "Total attempts = 2" means the job makes **two** attempts and then fails. **Fix:** pick one reading ("2 total attempts" vs "2 retries = 3 attempts total"), state it in the config table, align R3 and verification step 7. Also state whether `TRANSIENT` failures consume the same budget.

### B23. `REJECTED_USAGE` has no defined trigger, and the natural reading contradicts CUJ 9
`submit()` step 2: "The effective allowance is exhausted → `REJECTED_USAGE`". §4: force-join gate is unchanged, only engages subscriber check at `FREE_USAGE_LIMIT`. CUJ 9: "A user who has joined is not blocked by the allowance". So for a **joined** user at usage 10, `check_force_join` returns `True` → user proceeds to `submit()` → `REJECTED_USAGE` fires → **breaks CUJ 9**. For a **non-joined** user at usage ≥ 3, `check_force_join` returns `False` and handler returns before `submit()`. So `REJECTED_USAGE` has **no reachable trigger**. **Fix:** state the exact predicate, threshold source, and how it coexists with `check_force_join`. If intended to never fire, remove the enum member, truth-table row, and test case.

### B24. CUJ 10 and verification step 12 require state the design explicitly forbids persisting
CUJ 10 promises: "every waiting user's status message is edited with 'the server restarted...'". But the queue is **in memory only** (N3, Security NFR, Rollout guarantee). On restart, the new process has **no list of previous queued jobs**, no `(chat_id, message_id)` pairs, no user ids. **Fix:** either (1) persist a minimal waiting-job list (file or SQLite table) and relax N3/Rollout, or (2) drop the per-user restart notice, state waiting users' messages are left as-is, remove verification step 12 and the restart-notice constant.

### B25. `CRAWL_CONCURRENCY` resolution value and implementation anchor are unspecified
§2: "resolved in exactly one place... computed immediately before `WebCloner` is constructed and clamped to at least 1". But never says **what number it resolves to**: `cpu_slots` (2), `workers_per_crawl` (1), or `capacity` (2)? The wrong choice reproduces the CPU-thrash bug. No implementation instruction in §12/§13. Call site `scraper.py:577` never named in plan. Source of `workers_per_crawl` ambiguous (queue owns `_capacity` with hysteresis; `derive_capacity` returns raw candidate). **Fix:** state: resolved value = `workers_per_crawl` from `derive_capacity`; where computed; exact replacement for `scraper.py:577`; how it reaches `WebCloner`.

### B26. `ResourceReading` lacks the field `derive_capacity`'s own formula requires
`derive_capacity` uses `affinity_cores` in `cpu_slots = max(1, floor(cpu_quota_cores or affinity_cores or 1))`. But `affinity_cores` **is not a field of `ResourceReading`**. When source is `affinity`, `cpu_quota_cores` is `None` → `cpu_slots = 1` → pins bot to one crawl forever on any host without cgroup quota. Also: "Memory headroom is the smaller of host's `MemAvailable` and cgroup's own remaining allowance" but no `memory_current_mb` field; single `memory_source: str` cannot express the two-term minimum. **Fix:** add `cpu_affinity_cores: int` and `memory_current_mb: int | None` to dataclass, or change formula to consume only fields that exist.

### B27. "Wrap the whole `async with async_playwright()` region" is either wrong or requires an unspecified protocol
The `async with` spans lines 240–299 — the entire crawl. Wrapping it means **any exception during the crawl** (site error, page exception, worker failure, `TimeoutError`) is converted to `BrowserLaunchError`, re-queued and retried, and truth-table rule "Launch failed, retry exhausted, browser never launched → No charge" applies to jobs that *did* launch — under-charging and contradicting "Cancelled while running, browser had launched → Yes". **Fix:** state exact try/except shape — catch only the acquisition of Playwright driver and browser, not the crawl. This also determines where `try/finally` for `browser.close()` sits.

### B28. `expire_stale()` has no signature, no call site, no user-facing message
Config: job waiting > 45 min "is expired and not charged". `expire_stale` appears once in method list. No signature, no args, no return, no stated cadence, **no message**. Constants list has 7 strings — no expiry string. Notifier protocol has no `on_expired`. A job that expires leaves user staring at stale "Queued" message indefinitely. **Fix:** specify `expire_stale(now) -> list[CrawlJob]`, the task that calls it, the notifier event and message text, ordinal placement in truth table, whether `message_id` is cleared.

### B29. `position_updates()` has no signature; two-tier refresh needs `last_advertised_at`
§6 specifies two-tier cadence: top 20 every 120s, rest every 600s. `CrawlJob` has `last_advertised_position` and `last_advertised_wait` — **no `last_advertised_at` timestamp**. Without it, cannot distinguish "advertised 10s ago" from "500s ago". `position_updates()` also has no signature/return. **Fix:** add timestamp field, give `position_updates` signature/return, state who writes `last_advertised_*`, define "top N" boundary.

### B30. Duration sample has no recorder, no call site, no defined measurement window
§6: estimate derived from "held window — from admission to the moment the archive is built", only successful completions sampled. Never says: which method records (`record_duration()` absent from plan); where timer starts (admission = `submit()` returned `ADMITTED`? slot claimed? `pump()` created task?); where it stops (before/after `create_zip`? before/after `release_slot`?); sample max size (`deque` with no `maxlen`); who decides "successful". **Fix:** add recorder to method list with signature, name the two timestamps precisely, state `deque` bound.

### B31. `set_capacity()` takes one integer, but `derive_capacity()` returns two
§7: "probe computes a candidate and calls `queue.set_capacity(candidate)`" — single int. But `derive_capacity(reading) -> tuple[int, int]` returns `(capacity, workers_per_crawl)`, and `/admin` must display "Workers 1 page worker per crawl". Queue needs worker count; `set_capacity` as described cannot carry it. Also: hysteresis agreement-counter reset rule unspecified; whether `workers_per_crawl` follows hysteresis or tracks raw candidate. **Fix:** give `set_capacity` signature — `set_capacity(capacity: int, workers_per_crawl: int)` — and state hysteresis reset rule precisely.

### B32. `/admin` needs probe state that no component is designated to hold
`/admin` must show: probe last reading timestamp; probe last error; last `ResourceReading`; `workers_per_crawl`. Queue's internal-state table contains none; `stats()` described as "plain dictionary" with no key list; §10 says block built from "`queue.stats()` **and the last resource reading**" — two sources, second's owner unnamed. Probe runs as background task in `bot/core/scraper.py`; no module-level holder, callback, or setter specified. **Fix:** queue stores `_last_reading` and `_last_probe_error`, updated by `set_capacity` (or companion `record_reading`); `stats()` returns them; publish `stats()` key names.

### B33. `on_queued` and `on_started` have no call site
Protocol declares six methods including `on_queued(job, position, wait_seconds)` and `on_started(job)`. Runner step list (lines 1464–1471) includes neither. §6 says *handler* sends first message directly ("handler calls `queue.submit(job)` first... and then sends the first message: 'Starting clone' for admitted, 'Queued…' for queued"), leaving `on_queued` with no caller. CUJ 2 requires queued→started transition ("same message becomes 'Starting clone'") — `on_started`'s job, no call site. **Fix:** state which component calls each of the six notifier methods and at which point.

### B34. Queue's "no awaits" rule and async notifier protocol are not reconciled
Rule 1: `submit()`, `pump()`, `cancel()`, `defer()`, `release_slot()`, `may_start()` contain no `await`. Rule 2: "The queue never touches Telegram... The Telegram side lives entirely in the runner and in the notifier." But six notifier methods are all `async def`. If queue must call `on_retry` or `on_deferred`, and queue's methods may not `await`, then either: (a) queue schedules notification on task (fire-and-forget, no ordering guarantee, "Task was destroyed" risk); (b) notifier callbacks invoked by **runner** (async) after queue records state; (c) queue's `defer()` returns a decision and caller performs notification. **Fix:** specify for each of the six notifier events whether queue raises it, runner raises it, or handler raises it — and how that coexists with atomicity rule.

### B35. Runner has no specified way to obtain or edit the status message
Design: queue holds integers (`chat_id`, `message_id`), not message objects; notifier edits "job's existing status message — identified by the two integers". But plan never says **how** runner and progress updater obtain something editable. Today `_update_progress(cloner, status_msg, bot)` calls `status_msg.edit(text)` on live `Message` object. Review finding C16's fix was "reconstruct via `bot.get_messages`" — v2 does not mention `get_messages` and does not name `bot.edit_message_text(chat_id, message_id, text)`. Hydrogram's `edit_message_text` and `get_messages` have different signatures and failure modes. **Fix:** specify exact API used to edit by id, and new signature of `_update_progress`.

### B36. Concurrent edits to the status message are not ordered
Three components will write to the same message: `on_started` (editing to "Starting clone"), progress task (editing to "Cloning… 43 pages" every 5s), terminal handlers (packing/failed/cancelled). Plan's runner steps say to create progress task and cancel in `finally`, but do not say **when progress task starts relative to `on_started`**, nor that the two are serialised. Two concurrent edits can land out of order. **Fix:** state ordering — e.g. `on_started` awaited before progress task created, progress task's first edit only after sleep.

### B37. `handle_url` statement order not given; `db.increment_usage` at `:509` never explicitly removed
Plan §13: "Move URL and domain validation **above** the quota gate". Current order: `:509 db.increment_usage(user_id)` → `:511 if user_id in active_jobs` → `:515 is_valid_url` → `:519 extract_domain` → `:520 is_safe_domain` → `:530 cookie branch` → `:550 processing_msg = await msg.reply(...)` → `:561 create_task`. The "quota gate" is undefined; `increment_usage` removal not explicit; cookie branch position relative to `submit()` unstated; §12 note "bot still asks for the file" implies cookie branch remains before `submit()` but never states ordering. **Fix:** give the exact reordered statement sequence for `handle_url`, including the cookie branch position and the removal of `increment_usage`.

### B38. `_download_videos` and `_rewrite_all_html` block the event loop (G11 violated)
G11: "The bot's event loop is never blocked by a crawl." §11 moves only `create_zip` and `shutil.rmtree` to `asyncio.to_thread`. Two others remain: `_download_videos` calls blocking `ydl.extract_info()` in a loop; `_rewrite_all_html` is synchronous and does ~500,000 full-string replacements over multi-MB strings. Both hold the capacity slot and freeze the loop. **Fix:** move both to `asyncio.to_thread` (same mechanism), or downgrade G11 to "the two blocking disk operations move off the loop" and record remaining two as known limitations.

### B39. The feature has absorbed the database, logging and dead-code backlog
Detailed plan's 18 target files: ~half are not admission control (`bot/database.py` cutoff fix, batching, pragmas, `close_conn`, guarded counters; `bot/core/__init__.py`; `debug_catch_all` removal; pm2 log rotation; `pending_cookie_requests`/`active_progress_msgs`/`download_executor` deletion; startup Chromium reaper; `asset_concurrency` split; `pyproject.toml` dependency cleanup). Timeline prices whole bundle at "four working days". Round-1 findings already track these as **independent** items (A1, A3, A7, A10, E1–E3). **Fix:** natural split: (1) `CRAWL_CONCURRENCY` fix; (2) admission gate, queue, notifier, launch-retry — the feature; (3) cleanup items as their own tickets.

### B40. A `/proc`-scanning process reaper is proposed where a pm2 setting would remove the root cause
Design identifies: "pm2's SIGINT followed by SIGKILL after 1.6 seconds leaves orphaned Chromium". Then answers with startup reaper scanning process table, matching executable path and `--remote-debugging-pipe`. pm2's kill timeout is configurable; raising it past crawl's cancellation-and-await time gives shutdown path a chance to run, makes reaper unnecessary in ordinary case. No pm2 config file in repo. **Fix:** order of preference: fix the timer, then fix shutdown path (already specified), then add reaper only if first two prove insufficient.

### B41. Three settings and two-tier scheduler for a problem that does not exist; edit-volume accounting omits two channels
`CRAWL_POSITION_REFRESH_SECONDS` (120), `CRAWL_POSITION_REFRESH_SECONDS_LONG` (600), `CRAWL_POSITION_REFRESH_TOP_N` (20) for two-tier policy. Arithmetic: single cadence for all 100 jobs every 120s = 100 × 30 = 3,000 edits/hour = **0.83 edits/second** — order of magnitude below Telegram's ~30 msg/s ceiling. "Unthrottled" 120,000/day is itself only 3× single-cadence ~72,000/day. Tiering buys 0.5 edits/s for 3 settings, 2 cadences, priority partition, 2 extra job fields. Cost table covers only one channel; `on_retry` and `on_deferred` have no throttling rule and no volume term. **Fix:** single-cadence version delivers same user-visible info at rate inside every limit, with 1 setting instead of 3 and 1 rule instead of 2. Cost table should account for retry/deferred edit channels or state they are rate-limited by same mechanism.

### B42. Alternatives section lost the two comparisons that would justify the design's complexity
Alternative G dismissed on false premise: "bot currently runs on two very different machines" — unsubstantiated (A22: one machine observed two ways). Missing comparisons: "why not just set concurrency to 2 and use a semaphore?" and "what does the dynamic probe cost relative to a static limit plus a five-line memory check?". Alternative A dismissed because "users waiting on semaphore have no idea why nothing is happening; status message would sit on 'Launching Chromium…' forever" — that's a *presentation* defect fixed by one message edit, not architectural. **Fix:** price the incremental machinery against the simpler queue; answer the strong comparison honestly.

### B43. Second duration store introduced without acknowledging `bot/monitoring/performance.py`
Queue keeps own rolling duration sample (`_durations: deque[float]`) for wait estimate. Repo already has `bot/monitoring/performance.py` owning duration stats (`request_times`, `get_average_time`) feeding existing `/admin` via `get_performance_stats()` (`scraper.py:703-704`). Round-1 E4 flagged this: "extend it with an async-aware recorder". v2 mentions it nowhere. Existing `measure_time` is synchronous and unusable on coroutine. **Fix:** either extend existing module with async-aware `record_duration(name, seconds)` and read from queue, or state why existing module cannot hold it.

### B44. "The binding constraint" is CPU, not memory, by the document's own calculation
§2: `capacity = min(cpu_slots=2, memory_slots=4, ceiling=4) = 2`. CPU term binds; memory has 2 slots headroom. But Scale section: "Reuse one Chromium... removes 300–650 MB per-crawl cost, **which is the binding constraint on a memory-limited box**". §11: releasing slot before upload "roughly doubles throughput" — CPU-time argument. **Fix:** correct the claim to match the design's own arithmetic.

### B45. Lowering concurrency to one worker per crawl changes the product; design never says so
Derivation yields `capacity = 2`, `workers = 2 // 2 = 1` on target. One page worker per crawl. Per-page cost: `page.goto` (30s timeout) + `wait_for_timeout(1500)` + `_auto_scroll` (up to 5s) + `wait_for_timeout(500)` ≈ 3.5–8s per page. At 1 worker, 200 pages = 700–1,600s. 300s cap bites: **a crawl of a real site will not complete; it will hit the cap and deliver a partial clone**. With 15 workers, single crawl *does* complete. Design never states this trade. Cost table: "reduces CPU per crawl by moving from 15 page workers to 1" — true, silent on consequence. Scale table assumes "Crawl wall time 120–300s (unmeasured)" implying completion. Stage-3 gate: "p95 crawl duration < 240s" — at 1 worker will be dominated by cap, may be permanently unachievable, blocking rollout at stage 3. **Fix:** state the product decision: what the user receives, whether 300s cap is now the binding constraint on crawl *completeness*, whether `CRAWL_MAX_PAGES` or `CRAWL_TIMEOUT_SECONDS` should move.

### B46. The hard ceiling of 4 does not protect the 2-core quota; "unknown quota" should mean 1, not 4
Fallback chain: affinity → `os.cpu_count()` → 1. Derivation: `max(1, floor(cpu_quota_cores or affinity_cores or 1))`. On host where affinity mask not restricted by cgroup quota (normal for cgroup-v2, concepts brief: host reports 12 cores), `sched_getaffinity` could return 12 → `capacity = min(12, 4, 4) = 4` on 2-core box: four Chromiums, two cores, the exact thrash the design fixes. Ceiling of 4 protects memory but not CPU. **Fix:** ceiling's job is to bound damage when probe mis-detects. If CPU quota cannot be determined, safe value is 1 (one crawl, one worker) rather than "whatever affinity says" — one-line change to fallback.

### B47. "lifts capacity to 4–6" contradicts the configured ceiling of 4
Scale section: "4 cores / 12 GB lifts capacity to 4–6". With `CRAWL_MAX_CONCURRENT = 4` as default ceiling, derived capacity on 4-core box is exactly 4 (`min(4, 11, 4)`). Six is unreachable without raising ceiling. **Fix:** if recommendation includes raising `CRAWL_MAX_CONCURRENT`, say so.

### B48. Same threshold means two different things
"Sustained queue depth above 20 for three consecutive days means the crawler... is the product constraint" and "A queue that is 20 deep is not a failure; it is a queue." Reader will think document contradicts itself. **Fix:** use two numbers, or one number with two clearly distinct labels.

### B49. CUJ 3's precondition omits "no capacity available"
CUJ 3: "Given — 100 users are already waiting... request is rejected immediately". But `submit()` checks capacity *before* queue-full, so with 100 waiting and a free slot, request is **admitted**. Contract is right; CUJ's precondition is incomplete. **Fix:** add "and no capacity slot is free" to CUJ 3 Given.

### B50. The orphan scavenger's own argument depends on the unbounded phase it does not acknowledge
Scavenger justification: "A live crawl's artifacts are younger than that, so they are never touched". But §5 and R6 say post-page phase is unbounded (R6: "300-second cap does not bound total crawl time"; §5: `_rewrite_all_html` and `create_zip` "not interruptible"; yt-dlp video downloads unbounded). A crawl can exceed 60 min; periodic sweep would delete live crawl's artifacts. Startup sweep is safe; periodic is not. **Fix:** either make scavenger consult live registry, or drop periodic sweep (orphans only created by process death, followed by startup).

### B51. Two mitigations in risk register have no implementation-plan item; README config table not updated
R7: "`pm2 install pm2-logrotate`" and "`debug_catch_all` downgraded to DEBUG or removed". Neither in Detailed plan: `scraper.py` entry removes `active_jobs`, `pending_cookie_requests`, `active_progress_msgs`, `download_executor`, but not `debug_catch_all` at `scraper.py:64-75` (logs every message at INFO with usernames/URLs — against Security NFR). README's Configuration table is only operator-facing env var list; not in Target-files list; 19 new settings would ship undocumented. **Fix:** add pm2-logrotate and debug_catch_all removal to plan; add README to Target-files.

### B52. Two policy changes made without being called out
(a) Removing `usage_count` from `increment_requests` also removes allowance charge from `/media` command (`scraper.py:476` calls `increment_requests(user_id, success=True)`) — media extraction no longer counts against clone allowance. Probably correct, but silent in a section meticulous about truth tables. (b) Concepts brief R5 requires queued user to learn "their position, **the queue length**, and an estimated wait"; G5 deliberately drops queue length (security rationale sound), but narrowing of an *aligned* requirement not acknowledged. **Fix:** one sentence each in truth table and goals.

### B53. "Synchronous therefore survives its own bugs" over-claims
"The queue survives its own bugs: every decision path is synchronous, so a failure in one path cannot leave the registry half-updated." Synchronous code can still raise partway through a multi-step mutation (claim slot → register job → create task). Atomicity here comes from asyncio's single-threaded scheduling (prevents *interleaving*, not *partial failure*). **Fix:** say what is true: no interleaving, and each decision path mutates registry in order chosen so first mutation is last one that can fail.

### B54. Fallback estimate's wording does not match the formula
"The fallback when no sample exists is `CRAWL_ESTIMATE_MINUTES_PER_CRAWL` (2 minutes) per position, divided by capacity." Formula substitutes per-job duration for `per_job`; "per position" double-counts position multiplier. "2 minutes per position, divided by capacity" disagrees with 2-minute fallback value (shorter than table's 120–300s crawl estimate). **Fix:** "with no sample, `per_job` defaults to `CRAWL_ESTIMATE_MINUTES_PER_CRAWL`."

### B55. Existing `/admin` already reads a second, conflicting performance source
`/admin` calls `get_performance_stats()` from `bot/monitoring/performance.py` displaying `cache_hit_rate` and `total_requests`. New queue block added alongside, with own duration/capacity numbers. Design does not say which panel owns which figure. `cache_hit_rate` permanently 0.0% (design notes at line 907). **Fix:** remove dead line while touching panel.

### B56. Document-history references ("v1", "v2") sit inside normative prose
L638–640, L657–659, L702–704, L712–714, L812–817, header L4–7. References to "v1" and "v2" are changelog content, not requirements; make reader hunt for document they don't have. Header "all resolved below" asserts completeness not demonstrated. **Fix:** replace with self-contained statements; narrow header claim.

### B57. `bot/core/scrawler.py` does not exist (typo in Timeline)
Timeline Phase 2: "`bot/core/scrawler.py` refactor". Every other reference (30+ occurrences) is `bot/core/scraper.py`. **Fix:** `scraper.py`.

### B58. `CRAWL_FORCE_LAUNCH_FAILURE_KIND` used but never defined
Verification step 8: "Set `CRAWL_FORCE_LAUNCH_FAILURE=1` with `CRAWL_FORCE_LAUNCH_FAILURE_KIND=configuration`". Absent from config table; implementer won't add it to `bot/config.py`; operator will set env var bot doesn't read. **Fix:** add to config table with default and accepted values.

### B59. Duration sample has no named producer (review finding B8 unresolved)
§3 internal state: `_durations: deque[float]`. §6: "Only **successful** completions are sampled". No method writes `_durations`; no call site records duration. §4 method list omits recorder. **Fix:** add `record_duration(job, seconds)` to method list; state call site: "called by runner in its `finally` block, but only when job reached `DONE` and its slot was held for the measured window. Failed and cancelled jobs are not sampled."

### B60. Restart-resolution promise contradicts in-memory queue (N3, CUJ 10, verification step 12)
N3: "queue is in memory; a restart drops waiting users. Their status messages are resolved at startup with explicit edit..." CUJ 10: "every waiting user's status message is edited with 'the server restarted...'". Verification step 12: "Restart bot while three jobs are queued. Confirm each waiting user's message is edited..." But queue is in-memory only (Security NFR, Rollout: "No schema migration and no data change"). New process has no list of previous queued jobs, no `(chat_id, message_id)` pairs. **Fix:** either (1) persist minimal waiting-job list (file/SQLite) and relax N3/Rollout, or (2) drop per-user restart notice, state waiting users' messages left as-is, remove verification step 12 and restart-notice constant.

### B61. G5 promises cancellation "at any time"; §5 and CUJ 8 refuse it
G5: "can cancel at any time". §5: "Cancel is recorded but not honoured in `PACKING` and `DELIVERING`; user is told archive is already on its way." Two contradictory promises about same user-visible behaviour. **Fix:** G5 → "can request cancellation at any time; cancellation is honoured up to the point the archive is built (§5)."

### B62. Estimate clamped to ten minutes, then described as "still reported honestly"
"The estimate is clamped to 5–600 seconds. A longer wait than ten minutes is still reported honestly..." Clamp at 600s *is* the falsely precise number for a 4-hour wait: user told "about 10 min" when wait is hours. Design elsewhere acknowledges real drain time is hours (Scale: 100–250 hours for 6,000 crawls) and introduces `CRAWL_MAX_QUEUE_AGE_MINUTES = 45` making waits >10 min routine. **Fix:** "A wait longer than ten minutes is reported as 'more than 10 minutes' rather than as a precise figure, because the sample cannot support minute-level accuracy at that range."

### B63. R6 and §6 disagree about what the duration sample contains
R6: "duration estimate excludes the post-processing phase". §6: "duration measured is the **held window** — from admission to the moment the archive is built — because that is the window during which the job occupies a capacity slot." "From admission to archive built" includes HTML rewrite and ZIP build — the post-processing phase R6 says is excluded. **Fix:** adopt §6 reading and correct R6: "duration sample covers the full held window, including post-processing, because that is the window the slot is occupied".

### B64. NFR says admission path touches no database; design reads usage on that path
NFR: "The admission decision is a dictionary lookup and an integer comparison. It touches no file, no socket, and no database." `submit()` step 2 calls injected `usage_of` reader, reading user's committed usage count from DB — §8 confirms it by guarding request-path counters against `sqlite3.OperationalError`. **Fix:** "The admission decision is a dictionary lookup, an integer comparison, and one read of the user's committed usage count. It touches no file and performs no write."

### B65. `delivering_count` is undefined and removal point of `_by_user` never stated
INV-4: `len(_by_user) == _running_count + len(_pending) + delivering_count`. `delivering_count` appears in no dataclass, no state table row, no method. No section states when job leaves `_by_user`: §11 releases *slot* before upload but job must remain registered until delivery completes (otherwise second submission from same user admitted while first archive uploading). **Fix:** INV-4 → `len(_by_user) == _running_count + len(_pending) + len(_delivering)`, where `_delivering` is list of jobs whose archive is built, slot released, but delivery not completed. Add `_delivering: list[CrawlJob]` to internal-state table.

### B66. Queue-age expiry configured but absent from state machine, truth table, CUJs
`CRAWL_MAX_QUEUE_AGE_MINUTES = 45` and `expire_stale` appear only in implementation-plan appendix. Normative sections — state machine, charging truth table, CUJ list — contain no expired state, no expired row, no CUJ for expiry. Config says long-waiting job "is expired and not charged"; truth table claims to define "every terminal outcome" (G7) but has no such row. 45-min cap exceeds 10-min estimate clamp (B-3), so expiry and estimate interact and are not reconciled. **Fix:** add `EXPIRED` to state machine, row "| Expired in the queue, browser never launched | No |" to truth table, CUJ 11 ("User waits beyond the queue-age limit"), §3 subsection stating expiry rule.

### B67. `cancel()` has no return value for pre-launch cancellation truth table requires
Truth table distinguishes "cancelled while queued" from "cancelled during the pre-launch window, browser never launched" — two different user-visible outcomes with different charge results. `cancel()` returns `"none"`, `"queued"`, `"running"`, or `"too_late"` — cannot express the second one: job in `ADMITTED` or `LAUNCHING` is not `"queued"`, returning `"running"` is false (runner checks `cancel_requested` and never launches browser). **Fix:** add `"not_launched"` to return vocabulary.

### B68. Scale section arithmetic contradicts its own numbers
Launch-day: 200 arrivals/hour, capacity 24–60/hour, backlog 140–176/hour, queue holds 100, full at 35–45 min. Rejections *within hour* = arrivals after queue fills (≈50–83 in last 15–25 min) = **25–42%** of 200. Un*serviced* at end of hour = 200 − (24…60 served) = 140–176 = **70–88%**. Document states **60–75%** — matches neither. Throughput model measures wrong phase: table's "Crawl wall time 120–300s" and "24–60 crawls/hour" use only page phase. But §6 defines measured duration as **held window** (admission to archive built); §11 confirms slot held through HTML rewrite and ZIP build. R6 states 300s cap "does not bound total crawl time" and video/HTML rewrite run afterwards, unbounded. So slot (unit capacity counts) occupied for strictly longer than duration used to compute throughput. Every downstream figure inherits the optimism. "Result messages 0.07 msg/s at full daily throughput": 1,440 crawls/day = 0.017 msg/s; 0.07 msg/s = ~6,000 crawls/day (full free allowance of 2,000 users), not machine's daily throughput. Metrics table has no defined source for median wait / overflow rate: §10 only guarantees "Every state transition is logged at INFO with user id and resulting state" — neither wait duration nor queue position is logged. **Fix:** correct rejection percentage; state held-window duration as throughput unit (or label table "crawl-phase only"); name log field producing each metric.

### B69. Scale section placed before the design it analyzes (structural defect)
Scale and Impact inserted between Open Questions and CUJs, written as analysis of completed design: quotes derived capacity of 2, 24–60/hour throughput, staged-rollout gates, 10× remedy — none of which reader has been given (capacity derivation in §2, rollout gates defined in Scale then again in Metrics). Breaks blueprint sequence (CUJs immediately after Open Questions). **Fix:** move Scale and Impact down to sit with Cost and Risks, immediately after Technical Plan.

### B70. Non-Goal N5 states as out-of-scope a change the implementation plan performs
N5: "Cleaning up `pyproject.toml`'s stale dependency list... corrected in the same edit". Target file 18 (`pyproject.toml`) then instructs exactly that removal. N5 is an in-scope goal wearing a non-goal label. Also silently reverses concepts brief where N5 was "Do not clean up `pyproject.toml`". **Fix:** remove N5 from Non-Goals (renumber N6→N5 or restore brief's original N5); record cleanup as explicit in-scope item.

### B71. Verification Plan and Future Improvements don't match blueprint heading levels/positions
Blueprint: `### Verification Plan` nested inside `## Detailed Implementation Plan`; `### Future Improvements` before Detailed Implementation Plan, as child of NFR. v2 promotes Verification Plan to H2 and relocates Future Improvements to tail after Verification Plan. `Additional Resources` (blueprint) absent in v2 though external resources were used (front matter cites `review_findings.md`; `concepts_brief.md` never cited). **Fix:** align with blueprint; add `### Additional Resources` listing both review artifacts and key source files.

### B72. EGM provenance footer absent
Blueprint closes with separator and `*Drafted with EGM*`. v2 ends at line 1664 mid-list with no footer. **Fix:** add required footer.

### B73. Three target-file lists structurally asymmetric
"Target files to create" (1–10) and "Target files to modify" (11–18) follow blueprint's per-file entry shape (`####` heading + `Type/Scope` + `Proposed Changes`). "Target files deliberately not changed" is flat two-item bullet list with no `####` entries, none of required fields. **Fix:** give the two excluded files the same entry shape, or render as table with `File | Reason | Compliance check` columns.

### B74. Front-matter `Supersedes` metadata is self-referential and points at artifact that no longer exists
`**Supersedes:** design_draft.md v1 (873 lines)` names revision of *same file path reader is currently holding*. Directory scan returns only `review_findings.md`, `README.md`, `design_draft.md`, `concepts_brief.md` — no v1 artifact. Overwrite happened in place. **Fix:** either preserve v1 as versioned file (e.g. `design_draft.v1.md`) and point at it, or remove the line and declare v2 as sole revision.

### B75. Body contains eight unresolvable references to "v1"
"This resolves the contradiction in v1 between…" (638), "This is what makes the v1 claim true" (657), "v1 proposed reserving a usage unit…" (702), "The danger v1 identified…" (705), "the single largest simplification in v2" (714), "The security requirement in v1... and the feature requirement in v1..." (815–816). Cross-references to document revision reader does not have and cannot obtain. **Fix:** either restore v1 beside v2 or rewrite each reference as self-contained statement ("an earlier draft proposed X; this design instead does Y, because Z").

---

## C. MAJORS — resource model, user-facing correctness, operability (see B1–B75 above for the full actionable set; the Blockers and Majors are merged in this round because the implementation-readiness review found 13 Blockers that are design-level)

The findings above (B1–B75) constitute the complete actionable set. They span:
- Resource model (cgroup probe, capacity derivation, memory budget, CPU budget, hysteresis, circuit breaker, queue expiry)
- User-facing correctness (state machine, cancellation, messages, estimate clamp, restart notice)
- Operability (background task supervision, DB hygiene, log rotation, testability, verification plan executability)
- Structural defects (front matter, section placement, heading levels, provenance footer, target-file asymmetry)
- Document hygiene (v1/v2 references, editorial asides, unverified claims, unverifiable figures)

---

## D. WHAT SURVIVED THE ATTACK (keep in v3)

1. **The headline diagnosis** — `CRAWL_CONCURRENCY = 15` on 2-core quota is the real defect; putting it at the top is the single best editorial decision.
2. **The two-trigger reconciliation** — reading "100" as bound on waiters not runners, with owner's wording quoted verbatim.
3. **The synchronous-queue structural rule** — `submit()`, `pump()`, `cancel()`, `defer()` contain no `await`; every decision atomic under asyncio; closes duplicate-submission, over-admission, cancel-vs-start races.
4. **The quota simplification** — replacing v1's reservation/settlement/refund ledger with single rule + per-user live-job cap is a genuine large simplification; argument that cap dissolves force-join bypass is sound.
5. **The charge-at-launch rule and its truth table** — one writer, one moment, one published table. Exactly the executable contract round 1 asked for.
6. **`status_ready`** — justified; closes real race: `pump()` can admit queued job during handler's `await msg.reply()`, Event is minimum mechanism preventing runner editing non-existent message.
7. **Integer-only job state** — holding `chat_id`/`message_id` instead of Telegram `Message` objects satisfies privacy NFR and reuses existing `bot.edit_message_text` path.
8. **`capacity × workers ≤ cpu_slots` invariant and "capacity may be zero" property** — both right; refusing to floor capacity at 1 is correct.
9. **`eq=False` on `CrawlJob`** and explicit refusal to reuse `bot/core/crawler.py:_PriorityQueue` — correct; note saves implementer from mistake.
10. **Codebase-factual accuracy** — every source claim verified holds (subject to B1–B75).
11. **`bot/queue/` as inert package** — placing new modules outside `bot.core` avoids `bot/core/__init__.py:3` side effect without refactor.
12. **The queue shape itself** — synchronous `submit()`/`pump()`, one `_by_user` registry, one slot-claim point, one charge moment, one truth table — is right and should not be re-litigated.

---

## E. DESIGN v3 MUST ANSWER (the human-approval checklist)

1. Quota: exact predicate for `REJECTED_USAGE`, `/media` handling, failure-path counter, truth table covering all three call sites, `increment_requests` stops writing `usage_count`.
2. Capacity: one source of truth, provisional admission, circuit breaker reconciled, reserve, probe cadence, hard ceiling protects CPU, `ResourceReading` has all fields `derive_capacity` needs.
3. Backoff: `not_before` field, back-of-queue re-queue, fairness invariant in one sentence, injected clock.
4. Queue contract: result object with reason, correct submit ordering, `_by_user` index, synchronous `pump()`, published invariants with defined symbols, complete `CrawlQueue` method list with signatures.
5. Telegram isolation: `JobNotifier` protocol (or simpler) with declared owner, six call sites named, integer IDs only, real testability via fake notifier in `tests/conftest.py`.
6. Durability: startup scavenger (consults live registry), shutdown cancels+awaits crawl tasks, orphaned-Chromium reaping (pm2 timer fix first), no periodic sweep that deletes live artifacts.
7. Event loop: ZIP/rmtree/video-download/HTML-rewrite all off the loop; or G11 downgraded honestly.
8. Truthful user messages: every number derived from state; published cancel state machine with `not_launched` state; queue-age expiry with message and truth-table row; restart notice either persisted or dropped honestly.
9. Scale, cost, risk, timeline, metrics, kill criterion — arithmetic corrected, holder for each metric named, 10× trigger consistent with ceiling.
10. A verification path the owner can actually run on Hoody #2, including deterministic launch-failure test (fault-injection mechanism fully specified) and stated dev-tooling environment.
11. Structural compliance: blueprint heading levels, provenance footer, front matter, `Additional Resources` section, target-file list symmetry, no "v1"/"v2" in normative prose.
12. Split the cleanup backlog into separate tickets; the queue feature itself should be ~3 target files, not 18.