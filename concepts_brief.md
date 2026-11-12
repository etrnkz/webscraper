# Concepts Brief — Crawl Admission Control & Queue for @wbclnbot

* **Author:** user (project owner)
* **Status:** Aligned
* **Date:** 2026-11-11

---

## The Problem

`@wbclnbot` ("Web Clone") runs as a single pm2-managed Python process on Hoody
container #2. That container is constrained: a cgroup CPU quota of **2 cores**
(host reports 12, `os.cpu_count()` returns 2) and about **5,330 MB of available
RAM**, shared with the music bot and roughly twenty other pm2 applications.
Disk has 78 GB free.

Every clone request starts a **fresh Chromium** through Playwright
(`bot/core/crawler.py:240-241`) plus a Node driver process. One crawl costs
roughly **300–650 MB of resident memory** and about one core of work.

There is **no global limit on concurrent crawls**. `active_jobs`
(`bot/core/scraper.py:57`) is a `dict` keyed by `user_id`: it stops one user
from running two crawls at once, and does nothing to bound the total across
users. The semaphores in `bot/core/crawler.py:227-228` limit the number of
worker tasks *inside one crawl*, not the number of crawls.

At the scale the owner intends to promote the bot to — roughly **2,000 active
users** — dozens of clone requests can arrive at once. Each spawns another
Chromium. Three failure modes follow:

1. **Out of memory.** The Linux OOM killer reaps the process, taking the bot
   offline mid-crawl.
2. **Chromium launch failure.** The launch itself fails under memory pressure.
   The user sees "Clone failed" even though nothing is wrong with their URL.
3. **CPU thrash.** Ten Chromiums each spawning 15 page workers (`CRAWL_CONCURRENCY`
   default is 15, `bot/config.py:75`) on 2 cores means 150 page contexts
   competing for 2 cores. Every crawl crawls to its 300-second hard cap
   (`CRAWL_TIMEOUT_SECONDS`, `bot/config.py:79`). Nothing finishes, and every
   user is left watching a status message that never resolves.

In all three cases the bot appears broken, and the owner has no way to tell a
user "the machine is busy, you are 14th in line" — the only outcome is a
failure message.

---

## What the Owner Asked For

> "okay add a queu sysyem if our resources exceded like if we cant open chromium or
> we exceded 100 users actively add a queue and such thing and tell me to test when
> its done"

Two trigger conditions were named:

1. **Resources exceeded** — operationally defined by the owner as "if we can't
   open Chromium".
2. **More than 100 users active.**

And an explicit deliverable: a working system the owner can test.

---

## The Tension Between the Two Triggers

Condition 2 read literally as "permit 100 concurrent crawls" directly
contradicts condition 1. On a 2-core, 5.3 GB container, 100 concurrent
Chromiums *cannot* open — so condition 1 would fire continuously and condition 2
would never be the binding constraint. The two conditions cannot both govern
the same number.

The resolution used in this design: **the number 100 governs how many users may
wait, not how many crawls may run.** Running crawls are bounded by a
resource-derived limit (currently 2). Waiting crawls are bounded at 100. A
request that arrives when both are full is rejected with a clear message rather
than silently failing later.

This interpretation is stated explicitly in the design because the owner's
wording supports both readings.

---

## Decisions Already Made in Earlier Sessions (context, not open)

These are settled and are carried forward as constraints. They are not reopened
by this work.

* **Hydrogram, not Pyrogram.** The old Pyrogram 2.0.106 build had a zero-update
  bug; Hydrogram 0.2.0 fixed it.
* **Premium emoji rendering.** Message text uses `bot/utils/premium_text.py`
  (`PremiumText`) which builds `MessageEntity(type=CUSTOM_EMOJI)` with UTF-16
  offsets. HTML `<tg-emoji>` does not work over MTProto. Button icons are
  applied post-send through the Bot API `editMessageReplyMarkup` call in
  `bot/services/botapi.py`.
* **Python is retained.** Playwright drives a Node driver in every language, so
  a rewrite does not reduce browser cost; `yt-dlp` is Python-only; and the
  cgroup quota is the quota, so a rewrite buys no additional CPU.
* **Reusing one Chromium across crawls** was identified as the largest
  available performance win and deliberately deferred — it is a rewrite of the
  crawler engine, not part of this change.
* **Dependency cleanup.** `requirements.txt` was rewritten (commit `91e02fd`) to
  drop `lxml`, `chardet`, `Flask`, and `gunicorn`. Note that `pyproject.toml`
  still lists the stale set and is **not** part of that cleanup.
* **Existing policy.** Force-join channel `3876315930`; `FREE_USAGE_LIMIT = 3`;
  SQLite in WAL mode with `synchronous=NORMAL`.

---

## Code Realities That Constrain the Design

Verified by reading the source, not assumed.

| Location | Reality |
|---|---|
| `bot/core/scraper.py:486` | `handle_url` is the entry point for any URL-shaped message. |
| `bot/core/scraper.py:561` | It fires `asyncio.create_task(_run_crawl(...))` — one task per request, unbounded. |
| `bot/core/scraper.py:566` | `_run_crawl` builds `WebCloner` and awaits `cloner.run()`. |
| `bot/core/scraper.py:642` | The ZIP is delivered with `bot.send_document`. |
| `bot/core/scraper.py:662-666` | A `finally` block removes `output_dir` and the ZIP. Per-crawl disk does **not** leak. |
| `bot/core/crawler.py:240-241` | `async with async_playwright()` + `pw.chromium.launch(...)` — a new browser per crawl. |
| `bot/core/crawler.py:274-275` | Worker count is `min(concurrency, max_pages)` tasks sharing one browser. |
| `bot/core/crawler.py:278` | The crawl is bounded by `max_time` (300 s). |
| `bot/core/scraper.py:57` | `active_jobs` is per-user only. |
| `bot/core/scraper.py:509` | `db.increment_usage(user_id)` fires **before** URL validation and before any capacity decision. |
| `bot/core/scraper.py:80` | `FREE_USAGE_LIMIT` is enforced inside `check_force_join`, and only once usage is already at the limit. |
| `bot/config.py:27-28` | `RATE_LIMIT = 5` and `DAILY_LIMIT = 15` are defined but **never referenced anywhere**. Dead configuration. |
| `bot/core/scraper.py:715` | `/admin` reports `Active crawls: len(active_jobs)`. |
| `bot/modules/cache_manager.py:12,88` | `CACHE_EXPIRY_HOURS = 24`; `clear_expired_cache()` is called only from the `/clearcache` admin command (`bot/core/scraper.py:878`), never on a schedule. |
| `bot/database.py:171` | `cleanup_old_data()` is **never called from anywhere**. |
| `bot/database.py:19` | SQLite is WAL mode, `synchronous=NORMAL` — safe for concurrent counter writes. |
| `pyproject.toml:11-22` | Still lists `lxml`, `chardet`, `Flask`, `gunicorn` — stale relative to `requirements.txt`. |
| `pyproject.toml:32-35` | pytest is configured with `testpaths = ["tests"]`, but no `tests/` directory exists. |

---

## Requirements Carried Into the Design

**R1 — Admission control.** No crawl may start without passing an admission
gate that knows how much capacity the machine currently has.

**R2 — Resource-derived capacity.** The concurrent-crawl limit is computed from
the cgroup CPU quota and available memory at runtime, not hard-coded, so the
same build behaves correctly on a larger box.

**R3 — Chromium launch failure is a queue signal, not a user error.** If the
browser cannot be launched, the job is re-queued with backoff and a bounded
retry count, and the user is told the machine is busy. Only after retries are
exhausted does the user get a failure.

**R4 — Bounded waiting.** At most 100 users wait. Beyond that, requests are
rejected with a clear message and a retry hint, and the rejected user is **not**
charged usage quota.

**R5 — Honest queue feedback.** A queued user learns their position, the queue
length, and an estimated wait, and can cancel. The estimate must be derived
from measured throughput, not invented.

**R6 — Cancellation works in every state.** Cancelling a queued job removes it
from the queue; cancelling a running job stops the crawl. The existing
`/cancel` command and the existing "Cancel" inline button must both work
against the new structure.

**R7 — Quota accounting moves to admission.** Usage is charged when a job
actually starts running. Rejected jobs, invalid URLs, blocked domains, and
jobs abandoned in the queue do not consume quota.

**R8 — Disk stays bounded.** The cache directory and the request/activity log
tables are swept on a timer, not only by hand.

**R9 — Visibility.** `/admin` reports queue depth, running count, current
capacity, the derived CPU and memory readings, and recent browser-launch
failures.

**R10 — No new runtime dependency.** The queue must be built from the Python
standard library plus what is already installed.

---

## Non-Requirements

* **N1** — Do not rewrite the crawler to reuse a single Chromium. Deferred to a
  later change; it would restructure `WebCloner.run()`.
* **N2** — Do not add Redis, a message broker, or any external queue service.
* **N3** — Do not make the queue survive a bot restart. See Open Questions.
* **N4** — Do not implement `RATE_LIMIT` / `DAILY_LIMIT`. They are dead config
  today and are out of scope for this change.
* **N5** — Do not clean up `pyproject.toml`. It is stale, but unrelated.
* **N6** — Do not change the clone algorithm, the ZIP format, the premium
  emoji rendering, the force-join behaviour, or the media-extraction path.

---

## Open Questions

* **Q1 — Queue durability.** Should a job that is waiting in the queue survive
  a bot restart? The design as drafted keeps the queue in memory, so a restart
  silently drops waiting users. The bot is deployed under pm2 and restarts are
  rare but not impossible.
* **Q2 — Position refresh cadence.** Periodic position edits keep the user
  informed but consume Telegram message-edit quota across all waiting users.
  The design proposes a conservative default that can be turned off.
* **Q3 — Throughput estimate accuracy.** The estimate needs measured average
  crawl duration. Until real traffic exists, it will be based on a rolling
  average of observed crawls on this deployment, falling back to a configured
  default.

---

## Environment Facts (Hoody container #2)

* cgroup `cpu.max` = `200000 100000` → 2 cores of quota.
* `os.cpu_count()` = 2; `nproc` reports the host's 12 (misleading).
* Available RAM ≈ 5,330 MB; music bot ≈ 507 MB resident.
* Disk: 78 GB free on the project volume.
* Playwright Chromium installed at `/root/.cache/ms-playwright`.
* The bot runs as a single pm2 process; there is no second worker process, so
  an in-process queue is not competing with another copy of the bot.

---

## Definition of Done

1. The bot never runs more crawls concurrently than the machine can support.
2. A request arriving at capacity waits in a visible queue with a position and
   an estimate, and can be cancelled.
3. A request arriving when the queue is full is rejected with a clear message
   and is not charged quota.
4. A Chromium launch failure results in an automatic re-queue and a backoff,
   not a user-visible error, unless retries are exhausted.
5. The cache directory and log tables are swept automatically.
6. `/admin` shows queue state.
7. The owner can start the bot on Hoody #2 and test the behaviour by sending
   several URLs at once.
