# Crawl Admission Control & Queue for @wbclnbot

* **Author:** etrnkz
* **Status:** Draft (v3 — rewritten after Goldfish review round 2)
* **Date:** 2026-11-12
* **Supersedes:** `design_draft.md` v2 (1,664 lines)
* **Review input:** `review_findings_v2.md` (6 reviewers, ~120 findings, all resolved below)

---

## Overview

`@wbclnbot` clones websites on demand. Every clone launches a brand-new headless
Chromium, and nothing currently limits how many run at once. On the deployment
target — 2 CPU cores of cgroup quota and about 5.3 GB of RAM shared with twenty
other applications — a popular moment exhausts memory and leaves every user
staring at a failure message.

This design puts one **admission gate** in front of every clone. The gate knows
how much work the machine can currently take, admits that many crawls, and parks
the rest in a visible, cancellable queue that shows the user their position and
an honest estimate. If Chromium cannot be launched, the job goes back into the
queue with backoff instead of failing the user.

Two things this design deliberately does **not** do: it does not rewrite the
crawler to reuse a browser, and it does not add any runtime dependency.

### The headline fix is not the queue

The single highest-leverage change in this document is one line of
configuration: `CRAWL_CONCURRENCY` currently defaults to **15**, which means
each browser opens 15 page contexts on a two-core quota. Ten concurrent
Chromiums create 150 page contexts on 2 cores, every crawl runs into its
300-second cap, and nothing ever finishes. Lowering that value to the CPU
quota is what actually stops the thrash. The queue then makes the remaining
overshoot graceful instead of fatal.

The queue matters because, even with correct worker counts, 2,000 users can
still ask for more crawls than 2 cores can run, and something has to decide who
waits.

---

## The Problem

`bot/core/crawler.py:240-241` launches a fresh browser per crawl:

```python
async with async_playwright() as pw:
    browser = await pw.chromium.launch(...)
```

One crawl costs roughly **300–650 MB** of resident memory (Chromium spawns
5–8 child processes plus a Playwright Node driver) and about one core.

The only registry of in-flight work is `active_jobs` (`bot/core/scraper.py:57`),
a `dict` keyed by `user_id`. It enforces "one crawl per user" and nothing
else. The semaphores at `bot/core/crawler.py:227-228` bound the worker tasks
*within one crawl*, not the number of crawls.

The request handler does this, for every URL-shaped message, with no limit of
any kind:

```python
bot/core/scraper.py:561   asyncio.create_task(_run_crawl(...))
```

Three things follow when requests arrive faster than crawls finish:

1. **The OOM killer reaps the bot.** The process dies mid-crawl. Any orphaned
   Chromium survives, permanently consuming 300–650 MB.
2. **Chromium fails to launch** under memory pressure. The user sees "Clone
   failed" even though their URL was fine.
3. **CPU thrash.** `CRAWL_CONCURRENCY` defaults to 15 (`bot/config.py:75`),
   so ten concurrent Chromium instances create 150 page contexts on 2 cores.
   Every crawl then runs into its 300-second cap
   (`CRAWL_TIMEOUT_SECONDS`, `bot/config.py:79`). Nothing finishes.

In all three cases the bot looks broken, and there is no way to tell a user
"the machine is busy, you are 14th in line."

### Three further defects this change must not inherit

These are confirmed against the source and are not optional to fix:

* **Quota is charged twice per successful clone.** `db.increment_usage`
  (`bot/database.py:188`) and `db.increment_requests`
  (`bot/database.py:84`, which also writes `usage_count`) both run, at
  `bot/core/scraper.py:509` and `:651`. The effective free allowance today is
  **two** crawls, not the three the code implies.
* **A failed crawl leaks its progress task.** `progress_task.cancel()` at
  `bot/core/scraper.py:591` is reached only on the success path. On failure the
  task keeps running and overwrites the failure text with "Cloning…" every five
  seconds (`bot/core/scraper.py:669-681`).
* **`cleanup_old_data()` deletes nothing from `request_stats`.** The column is
  `TEXT` holding an ISO timestamp (`bot/database.py:49,83,87`); the cutoff is an
  epoch float (`bot/database.py:174-175`). The comparison never matches, so the
  table grows forever.

### The owner's requirement

> "okay add a queu sysyem if our resources exceded like if we cant open chromium or
> we exceded 100 users actively add a queue and such thing and tell me to test when
> its done"

Two triggers were named: **Chromium cannot be opened**, and **more than 100
users active**.

### How the two triggers are reconciled

Read literally, "allow 100 concurrent crawls" contradicts "queue when Chromium
cannot be opened" — 100 concurrent Chromium instances cannot open on a 2-core,
5.3 GB container, so the second trigger would fire constantly and the first
would never bind.

This design resolves it as follows:

* **The number 100 bounds how many users may wait**, not how many crawls may
  run.
* **Running crawls are bounded by a resource-derived limit** — currently 2 on
  the target container.
* **A request that arrives when both are full is rejected** with a clear
  message, and the rejected user is **not** charged usage quota.

This interpretation is called out explicitly because the owner's wording
supports more than one reading.

---

## Goals

* **G1** — No crawl starts unless an admission gate has approved it.
* **G2** — The concurrent-crawl limit is derived at runtime from the process's
  own cgroup quota and available memory. The same build behaves correctly on a
  different or larger machine, and on a **nested** cgroup, where the root's
  quota is not the process's quota.
* **G3** — A Chromium launch failure is treated as a resource signal: the job
  is re-queued with backoff and the user is told the machine is busy. A failure
  is only shown to the user after the retry budget is exhausted — or
  immediately, when the cause is a configuration fault that retrying cannot fix.
* **G4** — At most 100 users wait. Beyond that, requests are rejected with a
  clear message, and the rejected user is **not** charged usage quota.
* **G5** — A queued user sees their **position** and an **estimated wait**
  derived from measured crawl durations, and can request cancellation at any
  time; cancellation is honoured up to the point the archive is built. The total
  queue depth and the machine's capacity are deliberately **not** shown.
* **G6** — Cancellation works in every state it can meaningfully work in. The
  existing `/cancel` command and the existing inline "Cancel" button both work
  against the new structure, and the user is told the truth when a cancel
  arrives too late to honour.
* **G7** — Usage quota is charged **exactly once**, and only for work that
  actually ran. A published truth table defines every terminal outcome.
* **G8** — Disk stays bounded: per-crawl artifacts are removed on every exit
  path, and orphans left by an OOM kill are swept at startup.
* **G9** — `/admin` reports queue depth, running count, current capacity, the
  CPU and memory readings behind it, the probe's health, and launch failures
  broken down by cause.
* **G10** — No new runtime dependency. The queue is built from the Python
  standard library and what is already installed.
* **G11** — The bot's event loop is never blocked by a crawl. Archiving,
  deletion, video download, HTML rewrite, and database maintenance all run off
  the loop.

---

## Non-Goals

* **N1** — Rewriting the crawler to reuse a single Chromium across crawls.
  This is the largest available performance win but it restructures
  `WebCloner.run()`, and it is a separate change. It is listed as the first
  Future Improvement and as the trigger for the next capacity step.
* **N2** — Redis, a message broker, or any external queue service.
* **N3** — Making the queue survive a bot restart. The queue is in memory; a
  restart drops waiting users. Their status messages are left as they were;
  this is the accepted trade-off for a single-process bot.
* **N4** — Implementing `RATE_LIMIT` and `DAILY_LIMIT`. They are defined at
  `bot/config.py:27-28` and referenced nowhere. The per-user pending cap and
  the usage limit provide equivalent protection for this change.
* **N5** — Cleaning up `bot/core/__init__.py` import side effect (this is done
  as part of the change).

---

## Open Questions

Two questions remain genuinely open. Both have a default that ships, so
neither blocks implementation.

* **Q1 — Should the free allowance be three crawls or two?** Today it is two,
  because of the double-count bug. This design restores it to three, matching
  `FREE_USAGE_LIMIT = 3` and the owner's stated policy. Changing it to two
  would be a one-line config change, but it would be a **revenue-affecting
  change made inside a performance ticket**, so it is called out rather than
  decided silently. *Default: three.*
* **Q2 — Should a queued job survive a bot restart?** Persisting it needs
  either a schema migration or Redis, both of which this design excludes.
  The alternative — leaving waiting users' messages as-is — is the default.
  *Default: drop waiting jobs on restart.*

---

## Critical User Journeys (CUJs)

### CUJ 1 — Normal clone (machine has capacity)

* **Given** — The bot is running, the user is not banned, the URL is valid, the
  domain is allowed, and the machine has capacity for at least one more crawl.
* **When** — The user sends a URL.
* **Then** — The gate admits the job. The status message reads "Starting clone"
  with the URL, scope, cookies, and a Cancel button. The archive is delivered.
  The user's usage counter has moved by exactly **one**.

### CUJ 2 — Clone arrives while the machine is busy

* **Given** — The machine is already running as many crawls as its resources
  allow, and the queue is not full.
* **When** — The user sends a URL.
* **Then** — The gate queues the job. The status message says the server is
  busy, gives the user's **position** and an **estimated wait**, and carries a
  Cancel button. The position is refreshed periodically while it changes. When
  capacity frees up, the job starts and the same message becomes "Starting
  clone", then the live progress display. The usage counter moves only when the
  browser actually launches.

### CUJ 3 — Clone arrives when the queue is full

* **Given** — 100 users are already waiting, and no capacity slot is free.
* **When** — Another user sends a URL.
* **Then** — The request is rejected immediately. The message says the server is
  at capacity and to try again later, and confirms the usage was **not**
  charged. It does **not** disclose the queue depth, the capacity, or any other
  internal figure. No task is created and no browser is launched.

### CUJ 4 — Chromium cannot be launched (resource pressure)

* **Given** — A job is admitted and starts, but `chromium.launch()` fails
  because the machine is momentarily out of memory or file descriptors.
* **When** — The launch raises.
* **Then** — The failure is classified as resource pressure. The job is returned
  to its **original position** in the queue, becomes ineligible for a backoff
  period, and the user is told the server is busy and still trying. The usage
  counter does **not** move, because no browser ever launched. If the retry
  budget is exhausted, the user receives a final failure message and is still
  not charged.

### CUJ 5 — Chromium cannot be launched (configuration fault)

* **Given** — The Playwright browser binary is missing or its dependencies are
  not installed.
* **When** — The launch raises.
* **Then** — The failure is classified as a configuration fault, which retrying
  cannot fix. The job fails immediately, the user is told the server has a
  problem, and the cause is logged at ERROR for the operator. The usage counter
  does not move.

### CUJ 6 — User cancels while queued

* **Given** — The user's job is waiting in the queue.
* **When** — The user sends `/cancel` or presses the Cancel button.
* **Then** — The job is removed from the queue, the user is told it was
  cancelled, no browser is ever launched for it, and the usage counter does not
  move. Every other queued user moves up one position.

### CUJ 7 — User cancels while running

* **Given** — The user's job is running and the browser has launched.
* **When** — The user sends `/cancel` or presses the Cancel button.
* **Then** — The crawler's cancel event is set, the crawl stops, the temporary
  directory and archive are removed, and the user is told the clone was
  cancelled. The usage counter **does** move, because a browser was launched
  and consumed the machine's resources.

### CUJ 8 — User cancels too late

* **Given** — The archive has already been built and is being uploaded.
* **When** — The user sends `/cancel`.
* **Then** — The user is told the archive is already on its way and cannot be
  stopped. The delivery completes. Deleting the archive mid-upload would race
  the transfer and could produce a truncated file.

### CUJ 9 — User is over the free allowance

* **Given** — The user has used 3 crawls and has not joined the required
  channel.
* **When** — The user sends a URL.
* **Then** — The existing force-join flow runs and shows the join prompt. No
  job is created and the usage counter does not move. A user who *has* joined
  is not blocked by the allowance, which is today's behaviour and is unchanged.

### CUJ 10 — User waits beyond the queue-age limit

* **Given** — A user's job has been queued for longer than
  `CRAWL_MAX_QUEUE_AGE_MINUTES` (45).
* **When** — The expiry check runs.
* **Then** — The job is removed from the queue, the user is told the wait limit
  was reached, and it is not charged. No browser is ever launched.

### CUJ 11 — The bot restarts while users are waiting

* **Given** — Users are queued when the process is restarted.
* **When** — The new process starts.
* **Then** — Waiting users' status messages are left as they were. The user
  must send the URL again. This is the accepted trade-off recorded in N3 and Q2.

---

## Technical Plan (Architecture)

### 1. Shape of the system

```
        user sends URL
              |
              v
   handle_url()  ── validate URL, domain, ban, force-join, cookies
              |
              v
   queue.submit(job)              <-- SYNCHRONOUS. no awaits inside.
      |            |              <-- every decision happens here, atomically
      |            +--> REJECTED_DUPLICATE / QUEUE_FULL / REJECTED_USAGE
      |                  -> reply, release anything held, return
      |
      +--> ADMITTED  (slot claimed)
      |        |
      |        v
      |   handler sends "Starting clone" status message
      |   handler sets job.status_ready
      |   queue.launch(job)  -> creates the task
      |        |
      |        v
      |   runner: may_start()? --no--> defer (back to queue, no retry spent)
      |        | yes
      |        v
      |   chromium.launch() --fails--> classify
      |        |                        RESOURCE_PRESSURE -> re-queue, backoff
      |        |                        CONFIGURATION     -> fail now, log ERROR
      |        |                        TRANSIENT         -> re-queue, backoff
      |        | ok
      |        v
      |   charge usage (exactly once, here)
      |   crawl -> zip -> release slot -> upload -> done
      |
      +--> QUEUED (position N)
               |
               v
        position refresh task updates the message
               |
               v
        a slot frees -> pump() -> admitted -> runner
```

Two structural rules make the rest of this document work:

* **The queue is synchronous.** `submit()`, `pump()`, `cancel()`, `defer()`,
  `release_slot()`, `may_start()`, `mark_browser_launched()`, `abandon()`,
  `set_capacity()`, `record_duration()`, `expire_stale()`, `launch()`,
  `shutdown()` contain no `await`. Under asyncio's single-threaded model, a
  synchronous function cannot be interleaved, so every decision it makes is
  atomic with respect to every other decision. This is what closes the
  duplicate-submission race, the over-admission race, and the cancel-versus-start
  race in one stroke.
* **The queue never touches Telegram.** It holds integers (`chat_id`,
  `message_id`), not message objects, and it calls no client. The Telegram
  side of the system lives entirely in the runner and in the notifier.

### 2. The resource model

`bot/queue/resources.py` exposes one probe and one derivation.

**Probe.** `read_resources() -> ResourceReading` returns:

```python
@dataclass(frozen=True)
class ResourceReading:
    cpu_quota_cores: float | None        # None means "no quota found"
    cpu_source: str                      # cgroup-v2 | cgroup-v1 | affinity | cpu_count
    cpu_affinity_cores: int              # len(os.sched_getaffinity(0))
    memory_available_mb: int             # host MemAvailable (post-minimum with cgroup if limit exists)
    memory_source: str                   # cgroup-v2 | cgroup-v1 | meminfo
    memory_limit_mb: int | None          # cgroup memory.max / memory.limit_in_bytes (None if unlimited)
    memory_current_mb: int | None        # cgroup memory.current / memory.usage_in_bytes
    measured_at: float                   # time.monotonic()
```

**Resolving the cgroup correctly.** A process may live in a *nested* cgroup — a
systemd slice, for example — in which case the root's `cpu.max` describes the
host, not the process. The probe therefore:

1. Reads `/proc/self/cgroup` to learn this process's own cgroup path.
2. Reads `/proc/self/mountinfo` to find the mount point for the `cgroup2` or
   `cgroup` filesystem that carries the `cpu` and `memory` controllers.
3. Joins the two to build the file path.

On cgroup v2 the files are `cpu.max`, `memory.max`, and `memory.current`.
On cgroup v1 they are `cpu.cfs_quota_us` with `cpu.cfs_period_us`, plus
`memory.limit_in_bytes` and `memory.usage_in_bytes`. The v1 layout commonly
mounts the CPU controller at `/sys/fs/cgroup/cpu,cpuacct/`, so the mount point
is taken from `mountinfo` rather than assumed.

**Sentinels are handled explicitly.**

* v2 `cpu.max == "max 100000"` means unlimited → `cpu_quota_cores = None`.
* v1 `cpu.cfs_quota_us <= 0` means unlimited → `cpu_quota_cores = None`.
* v2 `memory.max == "max"` means unlimited → `memory_limit_mb = None`.
* v1 `memory.limit_in_bytes` at or above 2⁶⁰ means unlimited → `None`.

**Fallbacks, in order:** `len(os.sched_getaffinity(0))`, then
`os.cpu_count()`, then 1. The probe logs the source it used on every
derivation, and logs a **WARNING** when it had to fall back, so a silent
mis-detection is visible in the pm2 log rather than invisible.

**Memory headroom** is `memory_available_mb` — the smaller of the host's
`MemAvailable` and the cgroup's own remaining allowance (`memory_limit_mb -
memory_current_mb`), when a cgroup limit exists.

**Derivation.**

```python
def derive_capacity(reading: ResourceReading) -> tuple[int, int]:
    cpu_slots = max(1, math.floor(reading.cpu_quota_cores or reading.cpu_affinity_cores or 1))
    headroom_mb = max(0, reading.memory_available_mb - CRAWL_MEMORY_RESERVE_MB)
    memory_slots = headroom_mb // CRAWL_MEMORY_BUDGET_MB
    capacity = min(cpu_slots, memory_slots, CRAWL_MAX_CONCURRENT)
    workers_per_crawl = max(1, cpu_slots // max(1, capacity))
    return capacity, workers_per_crawl
```

On the target container:

```
cpu_slots      = 2
headroom_mb    = 5330 - 1024 = 4306
memory_slots   = 4306 // 1000 = 4
capacity       = min(2, 4, 4) = 2
workers_per_crawl = 2 // 2 = 1
```

Three properties of this formula are deliberate:

* **`capacity` may be zero.** When memory headroom falls below one crawl's
  budget, the gate queues rather than admits. A floor of 1 would guarantee a
  browser launch attempt at exactly the moment the machine cannot afford one,
  which is the condition the gate exists to prevent. The floor of 1 applies to
  the CPU term only, because there is always at least one core.
* **`CRAWL_MEMORY_RESERVE_MB` exists** because the 5.3 GB is shared with about
  twenty sibling applications. A sibling can grow by hundreds of megabytes
  between two probes; the reserve absorbs that growth without the gate being
  consulted.
* **`capacity × workers_per_crawl ≤ cpu_slots`** is a derived invariant. Two
  crawls each running two page workers would create four page contexts on two
  cores — the same thrash the design is fixing, at a smaller scale. Dividing
  the cores between the concurrent crawls fixes it properly. The product is
  displayed in `/admin` so the operator can see it.

**`CRAWL_CONCURRENCY` is resolved in exactly one place.** Its default changes
from `15` to `0`, where `0` means "derive from the CPU quota". The resolved
value is computed **immediately before `WebCloner` is constructed** as:

```python
concurrency = max(1, workers_per_crawl)
```

and `WebCloner.__init__` asserts `concurrency >= 1`. The resolved worker count
is logged at construction, so the pm2 log proves the new value is in effect.
The call site that must change is `bot/core/scraper.py:577`
`concurrency=config.CRAWL_CONCURRENCY`.

`CRAWL_CONCURRENCY` also sizes the asset semaphore (`concurrency * 2`) and the
`httpx` connection pool (`bot/core/crawler.py:228,512`). That coupling is
undesirable: a page-worker count of 1 would leave a single HTTP connection for
every asset on every page. The crawler therefore gains a separate
`asset_concurrency` parameter, defaulting to `CRAWL_ASSET_CONCURRENCY` (4),
used for the asset semaphore and the `httpx` pool.

### 3. The queue contract

`bot/queue/crawl_queue.py` defines the outcome type, the job, and the queue.

```python
class Outcome(str, Enum):
    ADMITTED = "admitted"
    QUEUED = "queued"
    REJECTED_DUPLICATE = "rejected_duplicate"
    REJECTED_QUEUE_FULL = "rejected_queue_full"
    REJECTED_USAGE = "rejected_usage"

@dataclass(frozen=True)
class Admission:
    outcome: Outcome
    reason: str = ""                    # e.g. "duplicate", "queue_full", "usage_exhausted"
    position: int = 0                   # 1-based; meaningful only when QUEUED
    queue_length: int = 0
    capacity: int = 0
    estimated_wait_seconds: int = 0
```

```python
class JobState(str, Enum):
    QUEUED = "queued"
    ADMITTED = "admitted"
    LAUNCHING = "launching"
    CRAWLING = "crawling"
    PACKING = "packing"
    DELIVERING = "delivering"
    DONE = "done"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

@dataclass(eq=False)
class CrawlJob:
    job_id: str
    user_id: int
    url: str
    domain: str
    scope: str
    cookies_file: str | None
    chat_id: int
    message_id: int | None = None
    state: JobState = JobState.QUEUED
    sequence: int = 0                   # submission order; preserved across retries
    enqueued_at: float = 0.0
    not_before: float = 0.0             # monotonic deadline; 0 means eligible now
    launch_attempts: int = 0
    charged: bool = False
    cancel_requested: bool = False
    slot_held: bool = False
    last_advertised_position: int = 0
    last_advertised_wait: int = 0
    last_advertised_at: float = 0.0
    status_ready: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_running: Callable | None = None
```

`eq=False` matters: two jobs with identical field values must never compare
equal, or removal-by-identity can remove the wrong one.

**The queue's internal state.**

| Structure | Purpose |
|---|---|
| `_by_user: dict[int, CrawlJob]` | The authoritative registry of every live job, keyed by user. Presence here is what "this user has a clone" means. |
| `_pending: list[CrawlJob]` | Jobs waiting for a slot, kept sorted by `sequence`. |
| `_running_count: int` | How many jobs hold a capacity slot. |
| `_capacity: int` | The current derived capacity. Owned by the queue. |
| `_workers_per_crawl: int` | The current workers_per_crawl. Owned by the queue. |
| `_candidate_capacity: int \| None`, `_agreement: int` | The hysteresis state for rising capacity. |
| `_delivering: list[CrawlJob]` | Jobs whose archive is built, slot released, but delivery not yet complete. |
| `_launch_failures: deque[tuple[float, str]]` | Timestamped launch failures, for `/admin`. |
| `_consecutive_failures: int`, `_suspended_until: float` | The launch circuit breaker. |
| `_durations: deque[float]` | Rolling sample of successful crawl durations (held window). Maxlen 1000. |
| `_last_reading: ResourceReading \| None` | Last successful resource probe reading. |
| `_last_probe_error: str \| None` | Last probe error, if any. |
| `_clock: Callable[[], float]` | Injected monotonic clock (default `time.monotonic`). |

**Published invariants.** These are asserted in the tests, not merely intended.

* **INV-1** — A user id appears in `_by_user` at most once.
* **INV-2** — `len(_pending) <= CRAWL_MAX_QUEUE_SIZE`.
* **INV-3** — `_running_count <= _capacity` whenever no job is inside its
  pre-launch window (states `ADMITTED` and `LAUNCHING`).
* **INV-4** — `len(_by_user) == _running_count + len(_pending) + len(_delivering)`.
* **INV-5** — `capacity × workers_per_crawl <= cpu_slots`.

**`submit(job) -> Admission` — synchronous, no awaits.** The order matters and
is deliberate:

1. **Duplicate.** `user_id in _by_user` → `REJECTED_DUPLICATE`, `reason="duplicate"`. Checked first, because a user with a live job should hear about their existing job, not about capacity.
2. **Usage.** The effective allowance is exhausted → `REJECTED_USAGE`, `reason="usage_exhausted"`. See §4.
3. **Capacity available.** `_running_count < _capacity` → claim the slot (`_running_count += 1`), register the job, set `state = ADMITTED`, `job.slot_held = True`, return `ADMITTED`. The slot is claimed *here*, before the caller's next `await`, so no second submission can over-admit.
4. **Queue full.** `len(_pending) >= CRAWL_MAX_QUEUE_SIZE` → `REJECTED_QUEUE_FULL`, `reason="queue_full"`. Checked *after* capacity, so a request is never rejected while a slot is demonstrably idle.
5. **Otherwise** → insert into `_pending` in sequence order (using `bisect.insort(..., key=lambda j: j.sequence)`), register the job, `state = QUEUED`, return `QUEUED` with the position.

**`pump() -> None` — synchronous, no awaits.** While `_running_count <
_capacity`, it selects the first job in `_pending` whose `not_before` has
passed, removes it, increments `_running_count`, sets `state = ADMITTED`,
`job.slot_held = True`, and creates the task via `launch(job)`. Jobs whose
backoff has not elapsed are skipped, not blocking.

**`launch(job) -> None` — synchronous.** Creates the crawl task, assigns
`job.cancel_running` (the crawler's cancel method), and sets `job.state =
LAUNCHING`. The task's `finally` block calls `release_slot(job)` then
`pump()`.

**Re-queueing preserves position.** When a launch fails, the job is put back
where it was: `_pending` is kept sorted by `sequence`, and the failed job keeps
its original sequence number, so it returns to exactly the slot it held
relative to the other waiting users. It becomes ineligible until `not_before`
passes. This resolves the contradiction between "returned to the front of
`_pending`" and "keeps its place relative to other waiting users" — the answer
is that it keeps its place and waits out the backoff.

**`may_start(job) -> bool`** is the provisional-admission check the runner
calls immediately before `chromium.launch()`:

```python
def may_start(self, job) -> bool:
    if job.cancel_requested: return False
    if self._suspended_until > self._clock(): return False
    return self._running_count <= self._capacity
```

If it returns False the runner calls `defer(job)`: the slot is released,
`_running_count -= 1`, the job returns to its sequence position with
`not_before = now + 1`, and the user is told the server is busy. **A deferral
does not consume the launch-retry budget**, because the machine was busy rather
than broken.

This is what makes the claim true: the queue never starts more crawls than
the current capacity, *even when capacity falls between the admission decision
and the launch*.

**The launch circuit breaker.** Three consecutive resource-pressure launch
failures suspend all launches for `CRAWL_LAUNCH_SUSPEND_SECONDS` (30) and force
capacity to zero. The probe clears the suspension once memory headroom
recovers. Without this, a machine that cannot start a browser turns every
queued job into a launch attempt, each one consuming transient memory and
failing — a spawn storm on an already-starved container.

**`mark_browser_launched(job) -> None` — synchronous.** Called by the runner
immediately after `chromium.launch()` returns successfully. Sets
`job.charged = True` and invokes the injected `charge(user_id)` callable.

**`abandon(job) -> None` — synchronous.** Called by the handler if the initial
status message send fails. Releases the slot if held, removes job from
`_by_user` and `_pending`, sets `job.status_ready.set()` so the runner exits.

**`release_slot(job) -> None` — synchronous.** Decrements `_running_count`,
sets `job.slot_held = False`. If job is in `_delivering`, removes it from there
on delivery completion.

**`set_capacity(capacity: int, workers_per_crawl: int) -> None` — synchronous.**
Updates `_capacity` and `_workers_per_crawl` with hysteresis: a **lower**
candidate takes effect immediately; a **higher** candidate takes effect only
after **two consecutive probes agree on the same higher value**. A different
higher value resets the agreement counter. Equal candidates leave it unchanged.
Also updates `_last_reading`.

**`record_duration(job, seconds: float) -> None` — synchronous.** Called by the
runner in its `finally` block, **only when the job reached `DONE` and its slot
was held for the measured window**. Failed and cancelled jobs are not sampled.
Appends to `_durations` (maxlen 1000).

**`expire_stale(now: float) -> list[CrawlJob]` — synchronous.** Returns jobs
where `now - job.enqueued_at > CRAWL_MAX_QUEUE_AGE_MINUTES * 60`. For each:
removes from `_pending` and `_by_user`, sets `state = EXPIRED`. Caller must
notify via notifier.

**`cancel(user_id) -> str` — synchronous.** Returns one of `"none"`,
`"queued"`, `"not_launched"`, `"running"`, or `"too_late"`:

* `"none"` — no job for this user.
* `"queued"` — job was in `QUEUED`; removed from `_pending` and `_by_user`.
* `"not_launched"` — job was in `ADMITTED` or `LAUNCHING`, browser never
  launched; slot released, job removed.
* `"running"` — job was in `CRAWLING`; `cancel_requested = True`, crawler will
  stop at next checkpoint.
* `"too_late"` — job was in `PACKING` or `DELIVERING`; cannot cancel.

**`shutdown() -> list[asyncio.Task]` — synchronous.** Returns all crawl tasks
for the caller to cancel and await. Sets a flag to stop `pump()` from creating
new tasks.

**`stats() -> dict` — synchronous.** Returns:
```python
{
    "waiting": len(_pending),
    "running": _running_count,
    "delivering": len(_delivering),
    "capacity": _capacity,
    "workers_per_crawl": _workers_per_crawl,
    "cpu_slots": cpu_slots,          # from last reading
    "memory_available_mb": memory_available_mb,
    "memory_reserve_mb": CRAWL_MEMORY_RESERVE_MB,
    "memory_budget_mb": CRAWL_MEMORY_BUDGET_MB,
    "last_reading": _last_reading,
    "last_probe_error": _last_probe_error,
    "launch_failures_last_hour": count of _launch_failures within 3600s,
    "launch_failures_total": len(_launch_failures),
    "launch_failures_by_kind": Counter of kinds,
}
```

**`position_updates() -> list[tuple[CrawlJob, int, int]]` — synchronous.**
Returns jobs whose advertised position has changed since `last_advertised_at`.
For top `CRAWL_POSITION_REFRESH_TOP_N` (20) positions, cadence is
`CRAWL_POSITION_REFRESH_SECONDS` (120); for others, `CRAWL_POSITION_REFRESH_SECONDS_LONG` (600). Caller updates `last_advertised_position`, `last_advertised_wait`, `last_advertised_at`.

---

### 4. Usage quota: one writer, one moment, one truth table

**The double-count is removed.** `db.increment_requests` (`bot/database.py:80-88`)
stops writing `usage_count`; it continues to maintain `total_requests` and
`total_errors`. `db.increment_usage` (`bot/database.py:186`) becomes the sole
writer of `usage_count`. The effective free allowance becomes the three crawls
that `FREE_USAGE_LIMIT = 3` promises, instead of today's accidental two.

**The charge happens at browser-launch success.** The runner calls
`queue.mark_browser_launched(job)` immediately after `chromium.launch()`
returns successfully. The queue calls `charge(user_id)` — an injected callable
that wraps `db.increment_usage` — and sets `job.charged = True`. The queue
never charges for a job whose browser never launched, and it never charges the
same job twice.

**The rule, stated once:**

> A user is charged one unit of usage for a clone **if and only if** a
> Chromium browser was successfully launched for it. Cancellation never refunds
> a charge for a browser that did launch.

| Terminal outcome | Charged? |
|---|---|
| Rejected — duplicate | No |
| Rejected — queue full | No |
| Rejected — usage exhausted | No |
| Cancelled while queued, browser never launched | No |
| Cancelled during the pre-launch window, browser never launched | No |
| Launch failed, retry budget exhausted, browser never launched | No |
| Configuration fault, browser never launched | No |
| Cancelled while running, browser had launched | **Yes** |
| Terminal failure after the browser launched | **Yes** |
| Success | **Yes** |
| `/media` command (any outcome) | **No** — does not count against clone allowance |
| Expired in queue, browser never launched | No |

**Why there is no reservation ledger.** v1 proposed reserving a usage unit at
submit time and settling it later. That was solving a problem that a simpler
rule dissolves. The danger v1 identified was that moving the charge later
would let a user queue many crawls while the counter still read zero. The
actual defence is the **per-user live-job cap**: a user may hold exactly one
clone — queued or running — at a time, the same guarantee today's `active_jobs`
gives for running clones, extended to queued ones. With that cap, a burst of
100 URLs from one account yields one admitted job and 99 duplicate rejections,
no matter how long the queue wait is. The force-join gate reads the committed
counter at submit time exactly as it does today.

This is the single largest simplification: no reservation table, no
settlement step, no refund path, and the force-join gate keeps working.

**The force-join gate is unchanged.** `check_force_join`
(`bot/core/scraper.py:78-89`) still reads `db.get_usage_count(user_id)` and
still only engages a subscriber check once the count reaches
`FREE_USAGE_LIMIT`. Because the charge now happens at launch rather than at
submit, a user whose clone is still queued has not been charged, which is
correct — and their live-job cap prevents them from queueing a second one.

---

### 5. The job state machine and cancellation

```
                    admit                runner starts
   QUEUED ─────────────────────► ADMITTED ─────────────────► LAUNCHING
      │                              │                           │
      │ cancel                       │ cancel                    │ browser up
      ▼                              ▼                           ▼
   CANCELLED                     CANCELLED                   CRAWLING
   (no charge)                 (no charge)                    │
                                                           │ crawl done
                                                           ▼
                                                        PACKING
                                                           │
                                                     zip built
                                                           ▼
                                                     DELIVERING ──► DONE
                                                     (slot released
                                                      before upload)
      ▲                              ▲                           ▲
      │                              │                           │
      └────── cancel (no charge) ────┴─────────── cancel ────────┘
       (cancel_requested = True     (crawler stops,             (archive on its
        checked before launch)      artifacts removed,          way, delivery
                                    slot released,              completes)
                                    charged = True)
```

* **Cancel is honoured** in `QUEUED`, `ADMITTED`, `LAUNCHING`, and `CRAWLING`.
* **Cancel is recorded but not honoured** in `PACKING` and `DELIVERING`; the
  user is told the archive is already on its way. This is deliberate: deleting
  the archive while Telegram is reading it produces a truncated file.
* **`_rewrite_all_html` and `create_zip` do not poll the cancel event.** They
  are bounded by the archive size limit and are not interruptible. The design
  states this rather than pretending otherwise.

`queue.cancel(user_id)` returns one of `"none"`, `"queued"`, `"not_launched"`,
`"running"`, or `"too_late"`, and the handler replies accordingly. A running
job's `cancel_running` callback is the crawler's existing cancel method; the
runner assigns it when it constructs the `WebCloner`, and `cancel()` handles
the case where it has not been assigned yet by setting `job.cancel_requested` —
which the runner checks before it launches anything.

The existing `/cancel` command (`bot/core/scraper.py:261-271`) and the existing
inline Cancel button (`bot/core/scraper.py:93-125`) both route through
`queue.cancel()`. Neither is deleted.

---

### 6. The notifier protocol and the status message

The queue calls no Telegram API. All user-facing communication goes through a
protocol that the queue's runner uses:

```python
class JobNotifier(Protocol):
    async def on_queued(self, job: CrawlJob, position: int, wait_seconds: int) -> None: ...
    async def on_started(self, job: CrawlJob) -> None: ...
    async def on_retry(self, job: CrawlJob, attempt: int, delay_seconds: int, kind: str) -> None: ...
    async def on_deferred(self, job: CrawlJob) -> None: ...
    async def on_failed(self, job: CrawlJob, reason: str) -> None: ...
    async def on_cancelled(self, job: CrawlJob, flavour: str) -> None: ...  # flavour: "queued" | "not_launched" | "running" | "too_late"
    async def on_expired(self, job: CrawlJob) -> None: ...
```

The implementation, `TelegramNotifier`, lives in `bot/core/scraper.py`. It
**edits** the job's existing status message — identified by the two integers
`chat_id` and `message_id` — rather than sending a second message. A job
therefore produces exactly one message that changes over its lifetime:

```
"Starting clone"  ->  "Cloning… 43 pages"  ->  "Clone ready"  ->  (document attached)
```

and, for a queued job:

```
"Queued — 3rd in line, about 6 min"  ->  "Starting clone"  ->  "Cloning…"  ->  ...
```

**Only integers are retained.** The queue holds `chat_id` and `message_id`,
never a Telegram `Message` object. A queued job therefore retains no personal
data beyond the user id and the URL the user already sent, and the queue does
not keep a live object graph alive for the length of a queue wait.

**The status message is created by the handler, once.** The handler calls
`queue.submit(job)` first — synchronously, so the outcome is known before any
`await` — and then sends the first message: "Starting clone" for an admitted
job, "Queued …" for a queued one, or the rejection message. It then records
`message_id` on the job and sets `job.status_ready`.

`job.status_ready` is an `asyncio.Event`, and it is what removes the last
ordering race. The runner's first action is `await job.status_ready.wait()`.
If a queued job is admitted by `pump()` in the middle of the handler's
`await msg.reply(...)`, the runner waits for the message to exist instead of
racing it. If the handler's reply fails outright, it calls
`queue.abandon(job)`, which releases the slot and sets the event so the runner
exits immediately instead of waiting out a timeout.

**The queued message does not show internal figures.** It shows the user's
position and an estimated wait. It does not show the total queue depth, the
capacity, the memory reading, or the CPU count. The security requirement —
not disclosing internal capacity — and the feature requirement — showing the
user their position — are both satisfiable at once, and the message is where
they meet.

**The estimate is honest.**

```python
per_job   = clamp(mean(successful crawl durations), 5, 600)
wait      = ceil(position * per_job / max(1, capacity))
```

* Only **successful** completions are sampled. Failed and cancelled crawls
  would contaminate the average with short, unrepresentative values.
* The duration measured is the **held window** — from admission to the moment
  the archive is built — because that is the window during which the job
  occupies a capacity slot. The upload happens after the slot is released, so
  it does not distort the estimate.
* The fallback when no sample exists is `CRAWL_ESTIMATE_MINUTES_PER_CRAWL`
  (2 minutes) for `per_job`; the formula then applies position and capacity.
* The estimate is clamped to 5–600 seconds. A wait longer than ten minutes is
  reported as "more than 10 minutes" rather than as a precise figure, because
  the sample cannot support minute-level accuracy at that range.

**Position refresh is owned.** A background task runs every
`CRAWL_POSITION_REFRESH_SECONDS` (120) and asks the queue for jobs whose
advertised position has changed. To bound Telegram's edit volume, the top
`CRAWL_POSITION_REFRESH_TOP_N` (20) positions refresh every 120 seconds and
everyone else every `CRAWL_POSITION_REFRESH_SECONDS_LONG` (600). That caps
edits at roughly 0.3 per second across the whole bot — about 26,000 a day at
full queue depth, which is well inside Telegram's limits and far below the
~72,000 a day a uniform 120-second cadence would cost.

The refresh task is the only component that rewrites a queued message, and it
is the only reason `CrawlJob` carries `last_advertised_position`,
`last_advertised_wait`, and `last_advertised_at`.

---

### 7. Background tasks and their supervision

`start_background_tasks()` returns three tasks, each of which has a supervisor.

| Task | Cadence | Responsibility |
|---|---|---|
| Resource probe | every 2 s (CPU re-read every 15 s) | read resources, update capacity, call `pump()` |
| Queue reporter | every 120 s / 600 s | refresh stale queue positions |
| Maintenance | every 900 s, plus once at startup | sweep orphaned artifacts, prune the database |

**Every loop body is wrapped in `try/except Exception`** and logs a structured
error on failure. Without this, one `OSError` kills the probe forever, its
exception is never retrieved, and capacity silently freezes at whatever it was
— which, if that was the ceiling, is worse than the behaviour being fixed.

Each task also gets a `done_callback` that logs unexpected termination at
ERROR level. `/admin` shows the probe's last successful reading time and its
last error, so a dead probe is visible rather than inferred.

**Capacity ownership and hysteresis.** The queue owns `_capacity` and
`_workers_per_crawl`. The probe computes a candidate and calls
`queue.set_capacity(capacity, workers_per_crawl)`. The queue applies the
hysteresis internally: a **lower** candidate takes effect immediately, because
over-admitting is the dangerous direction; a **higher** candidate takes effect
only after **two consecutive probes agree on the same higher value**, so a
fluctuating memory reading cannot make the queue flap. The request path never
reads the filesystem — it reads the cached integers.

**`pump()` is called by the probe**, not by the request path. That means a
capacity increase starts waiting jobs within one probe interval, and a
capacity decrease is honoured within one probe interval. The memory probe
cadence is 2 seconds because memory is the only input that moves quickly; the
cgroup CPU quota is effectively static and is re-read every 15 seconds.

---

### 8. Disk and database hygiene

**Per-crawl artifacts.** The existing `finally` block
(`bot/core/scraper.py:662-666`) removes the working directory and the archive.
This design makes that block unconditional by moving the archive creation and
the deletion off the event loop (§11) and by making the output paths unique
per job (§9).

**The orphan scavenger.** That `finally` runs only on a clean Python exit. The
design's own first failure mode is the OOM kill, which leaves a working tree
of up to 2 GB and an archive of up to 2 GB behind. On startup, before any
traffic is accepted, the bot removes any `clone_*` directory and any
`clone_*.zip` in the project root whose modification time is older than
`CRAWL_ORPHAN_MAX_AGE_MINUTES` (60) **and** that do not belong to a job in the
current process's registry. A live crawl's artifacts are protected because the
scavenger consults the live registry rather than relying on age alone.

**The cache sweep is honest about being inert.** `clear_expired_cache()`
(`bot/modules/cache_manager.py:88`) exists and works, but nothing in the
repository ever writes to the cache directory — `save_to_cache()` and
`get_cached_content()` have no callers; clone output goes straight to the
crawl directory (`bot/core/crawler.py:394-399`). The sweep is therefore
retained and scheduled, but the disk-bounding argument rests on the per-job
cleanup and the orphan scavenger, not on the cache. The `/admin` cache-hit
figure is permanently `0.0%`, and this design does not pretend otherwise.

**`cleanup_old_data()` is fixed before it is scheduled.** The function is
currently a no-op for `request_stats`, because the column holds an ISO string
and the cutoff is an epoch float. The comparison is changed to use ISO strings
on both sides, written with `timespec="microseconds"` so the lexical order
matches the chronological order exactly. The unbounded full-table deletes are
replaced with batched deletes of 5,000 rows per statement, committing between
batches, so a maintenance pass cannot hold a long write transaction that
blocks every other writer under WAL.

**Database robustness.** `get_conn()` gains `PRAGMA busy_timeout = 5000`, and
`bot/database.py` gains a `close_conn()` that closes the thread-local
connection. `asyncio.to_thread` creates a thread-local connection per thread,
so the maintenance task runs on a **dedicated single-thread executor** with
exactly one connection, closed at shutdown.

**Every database call that can be interrupted is guarded.** A locked database
must not raise out of `handle_url` and leave the user's message
unacknowledged. The counter helpers used on the request path catch
`sqlite3.OperationalError` and retry briefly before giving up.

---

### 9. Startup, shutdown, and the lifecycle of a crawl

**Startup, in order:**

1. Load configuration and validate credentials.
2. Sweep orphaned `clone_*` artifacts older than the threshold, consulting the
   live registry.
3. Reap orphaned Chromium processes left by a previous kill. The filter is
   exact: the executable path must be under the Playwright browsers directory
   **and** the command line must carry Playwright's `--remote-debugging-pipe`
   argument. Nothing else on the machine is touched.
4. **No restart notice for waiting users** — the in-memory queue is lost (N3, Q2).
5. Start the background tasks.
6. Begin accepting traffic.

**Shutdown.** Hydrogram 0.2.0 has no `on_start` decorator — verified against
the installed package, whose decorator set is `on_message`, `on_edited_message`,
`on_deleted_messages`, `on_callback_query`, `on_raw_update`, `on_disconnect`,
`on_user_status`, `on_inline_query`, `on_poll`, `on_chosen_inline_result`,
`on_chat_member_updated`, `on_chat_join_request`, and `on_error`. There is no
startup hook to attach a background task to.

The supported path is to own the event loop explicitly. `Client.run()` accepts
a coroutine, and `Client.start()`, `Client.stop()`, and `hydrogram.idle()` are
all public. `main.py` becomes:

```python
async def _main():
    await bot.start()
    tasks = start_background_tasks()
    crawl_tasks = set()
    try:
        await idle()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await queue.shutdown()           # returns crawl tasks
        await asyncio.gather(*crawl_tasks, return_exceptions=True)
        await bot.stop()

if __name__ == "__main__":
    try:
        bot.run(_main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
```

The existing `try/except` handling and the `__main__` guard are retained, and
the tasks are **awaited** with `asyncio.gather(..., return_exceptions=True)`
rather than merely cancelled, because cancelling without awaiting leaves
"Task was destroyed but it is pending" warnings and does not guarantee the
cleanup ran.

**Crawl tasks are cancelled and awaited too.** The queue keeps a strong
reference to every crawl task it creates. On shutdown those tasks are
cancelled and awaited before `bot.stop()`. Without this, pm2's SIGINT followed
by SIGKILL after 1.6 seconds leaves orphaned Chromium processes holding
300–650 MB each; four or five restarts would permanently consume the
container's memory, and the bot could never launch a browser again — the exact
failure this design exists to prevent.

`WebCloner.run()` also gains a `try/finally` so `browser.close()` executes
even when the task is cancelled. Today the close at `bot/core/crawler.py:296`
is reachable only on the normal path.

**Unique output paths.** `output_dir` becomes
`clone_{domain}_{user_id}_{job_id}` and the archive sits beside it. The
current naming (`bot/core/scraper.py:569-570`) is not unique per job, so two
concurrent crawls for one user would write to the same directory and delete
each other's archive.

---

### 10. Observability

`/admin` (`bot/core/scraper.py:692`) gains a queue block, built from
`queue.stats()` and the last resource reading:

```
Queue          12 waiting / 2 running / 1 delivering / capacity 2
Capacity       2 CPU cores of quota, 4306 MB headroom after reserve
Workers        1 page worker per crawl (capacity x workers = 2 of 2 cores)
Launch fails   1 in the last hour (resource pressure), 0 total
Probe          last reading 3 s ago, last error: none
```

The queue exposes `stats()` as a plain dictionary so the admin command does
not reach into queue internals. The launch-failure count is backed by a
bounded timestamped deque, so "in the last hour" is a real window rather than
a cumulative number wearing a window's label.

Every state transition is logged at INFO with the user id and the resulting
state — admitted, queued, deferred, retried, launched, charged, cancelled,
failed, expired — so a busy period is reconstructable from the pm2 log alone.

---

### 11. Keeping the event loop free

Four operations in the current runner block the loop for minutes:

* `create_zip(output_dir, zip_path)` (`bot/core/scraper.py:616`) walks and
  compresses a tree of up to 2 GB.
* `shutil.rmtree(output_dir, ignore_errors=True)`
  (`bot/core/scraper.py:664-666`) recursively deletes it.
* `_download_videos()` calls blocking `ydl.extract_info(url, download=True)`
  in a loop over every discovered video.
* `_rewrite_all_html()` is synchronous and does ~500,000 full-string
  replacements over multi-MB strings.

While any of these runs, the probe does not run, the queue does not pump,
`/cancel` is not processed, and the Telegram heartbeat is not serviced — the
bot appears frozen, which is the same symptom this design exists to remove.
All four move to `asyncio.to_thread`.

The capacity slot is released **after the archive is built and before the
upload**, because uploading is I/O-bound and does not compete for the CPU or
the RAM that the capacity limit protects. This raises throughput at the same
concurrency by the share of the slot's lifetime previously spent uploading,
which is not yet measured. It also keeps the duration estimate consistent with
the slot's real lifetime.

The progress task is cancelled in a `finally` rather than on the success path
only, which fixes the leak that currently overwrites a failure message with
"Cloning…" every five seconds.

---

### 12. Dead code removed in passing

Three module-level structures in `bot/core/scraper.py` are dead or leaking and
are removed as part of this change, because leaving them would contradict the
claim that the queue is the single registry of in-flight work:

* `pending_cookie_requests` (`:60`) — written at `:531`, never read. The cookie
  flow asks the user for `cookies.json` and then never resumes. It is an
  unbounded write-only leak. **The user-facing behaviour is unchanged**: the
  bot still asks for the file, because that is what it does today.
* `active_progress_msgs` (`:58`) — never read or written.
* `download_executor` (`:36`) — a 20-thread `ThreadPoolExecutor` that is never
  used.

`bot/core/crawler.py:_PriorityQueue` is **not** reused: it offers no removal
by identity, which the queue needs. This is recorded so an implementer does
not reach for it.

---

### 13. Testability

`bot/core/__init__.py` currently contains `from bot.core.scraper import bot`,
which means importing *anything* under `bot.core` executes the whole bot
bootstrap — including `config.validate_config()`, which calls `sys.exit(1)`
when the Telegram credentials are absent. No module under `bot.core` can be
imported in a test today.

The new modules therefore live in a **new package** `bot/queue/`, whose
`__init__.py` is inert, as is `bot/__init__.py`. `bot.errors` is equally
safe. `import bot.queue.crawl_queue` needs no credentials, creates no
database, and creates no directories.

Removing the side effect from `bot/core/__init__.py` as well is a one-line
change that makes the whole package importable in tests. Only `main.py`
imports from `bot.core`, and it imports `bot.core.scraper` directly
(`main.py:18`), so nothing breaks.

---

## Alternatives Considered

### Alternative A — A single fixed semaphore, no queue

* **Pros** — Roughly fifteen lines. Every request creates a task that waits on
  `asyncio.Semaphore(2)`. No queue object, no ordering, no position display.
* **Cons** — Users waiting on the semaphore have no idea why nothing is
  happening; the status message would sit on "Launching Chromium…" forever.
  There is no place to hang a wait estimate, no natural place to put the
  100-user cap, and no way to distinguish "queued behind two crawls" from
  "hung". Cancelling a queued job means cancelling a task, which works but
  leaves the task object alive until it is scheduled.
* **Reason for Rejection** — It does not deliver the "queue and such thing"
  the owner asked for. A queue with no visible position produces the same user
  experience as the current failure mode, only slower.

### Alternative B — Redis-backed queue

* **Pros** — Survives a bot restart. Survives a second bot process. A natural
  fit if the bot is ever scaled to more than one worker.
* **Cons** — Redis is not a dependency of this bot; adding it means a new
  runtime dependency (which G10 forbids), a new failure mode (Redis down means
  the bot cannot accept work), and an operational step on the container. The
  bot is a single pm2 process with a single event loop.
* **Reason for Rejection** — Premature infrastructure for a single-process bot,
  and it contradicts G10. Revisit if the bot ever runs more than one worker
  process.

### Alternative C — Persist the queue in the existing SQLite database

* **Pros** — Durable across restarts, using a dependency the bot already has.
* **Cons** — Requires a schema migration, a write path for every state change
  under contention, and careful recovery logic on startup. The bot's SQLite
  usage is currently counters and logs; adding a queue table makes the database
  a coordination point between the event loop and a restart.
* **Reason for Rejection** — The durability benefit is deferred to Q2, and the
  in-memory queue plus an explicit restart message is a satisfactory answer for
  a bot that restarts rarely.

### Alternative D — Reject requests instead of queueing them

* **Pros** — Simplest possible implementation. No queue, no position, no
  estimate, no ordering.
* **Cons** — Directly contradicts the owner's request. Under a traffic spike,
  every user beyond the first two gets an error, which is the behaviour being
  fixed.
* **Reason for Rejection** — It does not meet the stated requirement.

### Alternative E — Keep `active_jobs` and add a counter alongside it

* **Pros** — Smallest diff. The existing dictionary keeps tracking running
  jobs and a new integer tracks waiting ones.
* **Cons** — Two structures describe the same system, and the invariant "one
  clone per user" has to be enforced across both. The running set and the
  waiting set need different lifecycles and different cancellation semantics.
* **Reason for Rejection** — The queue supersedes `active_jobs` entirely: one
  registry, one invariant, one place to look.

### Alternative F — Run several bot worker processes

* **Pros** — Each process has its own event loop and could admit crawls
  independently. More CPU utilisation without changing the code.
* **Cons** — The admission gate is per-process, so N workers would admit N
  times the intended number of crawls, which is the failure this design
  prevents. It would also need a shared queue (Alternative B or C) to be
  correct, and Telegram session conflicts arise from two clients using one bot
  token.
* **Reason for Rejection** — The single-process model is a constraint of the
  deployment, and the answer to more load is a bigger box or a faster crawler,
  not more processes fighting over one token.

### Alternative G — A static, hand-configured concurrency limit

* **Pros** — Trivially simple. One environment variable, no cgroup parsing, no
  probe task, no hysteresis.
* **Cons** — The limit has to be correct on every machine the bot runs on, and
  the bot's quota differs from the host's: `os.cpu_count()` reports 2 inside a
  12-core host, so a statically configured value is wrong on at least one of
  the two readings. It also cannot respond to a sibling process taking the
  memory.
* **Reason for Rejection** — Rejected as the *only* mechanism. The static
  ceiling `CRAWL_MAX_CONCURRENT` survives as a hard upper bound, because a
  probe that fails must never be able to start an unbounded number of browsers
  — but it is a ceiling, not the limit.

---

## Risks and Mitigations

| # | Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|---|
| R1 | The queue overflows on launch day and rejects a large share of requests | **High** | Medium | Staged rollout (200 → 500 → 1,000 → 2,000, seven-day observation per stage); the rejection message is honest; per-user live-job cap stops one account filling the queue | Owner |
| R2 | The cgroup probe mis-detects the quota and over-admits | Medium | **High** | Resolve the cgroup path from `/proc/self/cgroup` and `mountinfo`; WARNING on every fallback; hard ceiling `CRAWL_MAX_CONCURRENT = 4`; the resolved source is logged and shown in `/admin` | Owner |
| R3 | Launch failures become a Chromium spawn storm | Medium | **High** | Exponential backoff with a real deadline; retry budget of 2 attempts; circuit breaker suspends all launches for 30 s after 3 consecutive resource failures | Owner |
| R4 | Orphaned Chromium exhausts the container's RAM across restarts | Medium | **High** | Crawl tasks cancelled *and awaited* at shutdown; `browser.close()` in a `finally`; startup reaper with an exact process filter; startup orphan-artifact sweep | Owner |
| R5 | The quota change breaks the force-join gate | Medium | **High** | One writer (`increment_usage`); charge at browser-launch success; published truth table; counter assertions in the test suite and in the E2E plan | Owner |
| R6 | The 300-second cap does not bound total crawl time | **High** | Medium | Documented as a page-phase bound only; the duration sample covers the full held window, including post-processing, because that is the window the slot is occupied; a total-phase bound is a future improvement | Owner |
| R7 | The pm2 log grows without bound and fills the disk | Medium | Medium | `pm2 install pm2-logrotate`; `debug_catch_all` downgraded to DEBUG or removed; the gate's own transitions are the intended log source | Owner |
| R8 | Database maintenance blocks the request path under WAL | Medium | Medium | `busy_timeout`; batched deletes committing between batches; a dedicated single-thread executor; `sqlite3.OperationalError` guarded on the request path | Owner |
| R9 | The async tests silently do not run and the suite passes green | Medium | **High** | `pytest-asyncio` with `asyncio_mode = "auto"`; a test that asserts the collected async-test count; no `async def` test may be skipped | Owner |
| R10 | The estimate is wrong and users are promised a wait that does not arrive | Medium | Medium | The estimate is derived from measured successes only, clamped to 5–600 s, and refreshed as real data arrives; the fallback is explicitly approximate | Owner |
| R11 | A configuration fault (missing Chromium binary) looks like a capacity problem and the bot quietly retries forever | Low | **High** | `BrowserLaunchError` classification; configuration faults fail immediately at ERROR level and are never retried | Owner |
| R12 | The admission gate itself has a bug and stops the bot working | Low | **High** | The gate is synchronous and therefore atomic; every decision path has a unit test; the whole change is a single git revert | Owner |

---

## Cost

No new infrastructure, no new runtime dependency, no new service to operate.

| Item | Cost |
|---|---|
| **Compute** | Unchanged. Python, one pm2 process, two Chromium slots. The design *reduces* CPU per crawl by moving from 15 page workers to 1. |
| **Memory** | Queue state is at most 100 jobs of roughly 1 KB each — under 100 KB. The queue holds integers, not message objects, so it retains no meaningful personal data. |
| **Disk, steady state** | Unchanged in the happy path: each crawl's working tree and archive are removed on exit. Peak transient usage is 2 concurrent crawls × up to 4 GB (tree plus archive) ≈ 8 GB against 78 GB free. |
| **Disk, orphaned** | Bounded by the startup sweep. Worst case before the first sweep after an OOM kill: one dead crawl's artifacts, ≤ 4 GB. |
| **Database** | One `request_stats` row per crawl. At 1,440 crawls/day that is ~1 MB/week before pruning; `activity_log` is capped at 10,000 rows. The file does not shrink without a `VACUUM`, which this design does not schedule — so the metric is "rows pruned", not "file size", and the verification plan says so. |
| **Telegram API** | 1 result message plus 1 document per crawl. Queue-position edits are throttled to ~0.3/s (≈26,000/day at full depth), down from ~72,000/day at a uniform 120-second cadence. |
| **Headcount** | Three working days for the queue feature, plus the owner's staged observation windows. Cleanup items are separate tickets. |

---

## Timeline

| Phase | Work | Dependency | Estimate |
|---|---|---|---|
| 1 | `bot/errors.py`, `bot/queue/resources.py`, `bot/queue/crawl_queue.py`, `bot/queue/notifier.py`; the quota and cleanup fixes in `bot/database.py`; the new settings in `bot/config.py`; the new strings in `bot/constants.py` | — | 1 day |
| 2 | `bot/core/scraper.py` refactor: the queue wiring, the runner, the notifier, the cancel paths, the progress-task fix, the off-loop archive/video/HTML work; `bot/core/crawler.py`: the launch-error wrapper, the `try/finally`, the `asset_concurrency` split; `main.py`: the explicit lifecycle | Phase 1 | 1 day |
| 3 | Startup scavenger, Chromium reaper, dead-code removal, `bot/core/__init__.py` cleanup | Phase 2 | 0.5 day |
| 4 | The test suite and the dev tooling it needs | Phases 1–2 | 1 day |
| 5 | Deployment to Hoody #2 and the staged E2E run | Phases 1–4 | 0.5 day |

Total: **four working days** for the queue feature, with the owner testing at
the end of Phase 5 and observing for seven days before the next rollout stage.
Cleanup items (DB batching, log rotation, `pyproject.toml` cleanup) are
separate tickets.

---

## Metrics and Kill Criterion

**Metrics**, collected from the queue's own log lines and shown in `/admin`:

| Metric | Threshold | Why |
|---|---|---|
| Queue overflow rate (rejections ÷ submissions) | < 2% | Above this, the staged rollout stops advancing |
| Median wait for a queued clone | < 15 min | Above this, users feel the machine is broken |
| p95 crawl duration (held window) | < 240 s | Above this, throughput collapses |
| Launch-failure rate | < 1% | Above this, the browser or the memory budget is wrong |
| OOM kills (pm2 unexpected restarts) | 0 | Any occurrence means the gate is not working |
| Orphaned Chromium after a restart | 0 | Any occurrence means shutdown is not working |
| `request_stats` rows older than 7 days | 0 | Confirms the maintenance task works |

**Observation window:** seven consecutive days at each rollout stage.

**Kill criterion.** If, after the stage-3 rollout to 2,000 users, **any** of
the following holds for seven consecutive days, the crawler must be scaled or
rewritten before any further promotion:

* queue overflow rate above 5%;
* median wait above 60 minutes;
* any OOM kill.

The remedy, in order, is the one in the Scale section: a larger box first,
then a crawler that reuses one Chromium.

**A note on what the kill criterion is not.** It does not say "if users
complain". It says what the machine can actually sustain, measured. A queue
that is 20 deep is not a failure; it is a queue. A queue that is overflowing is
a failure.

---

## Non-Functional Requirements

### Performance and latency

* The admission decision is a dictionary lookup, an integer comparison, and one
  read of the user's committed usage count. It touches no file and performs no
  write.
* A queued job consumes no CPU, no file descriptor, and no meaningful memory
  while it waits.
* The archive creation, artifact deletion, video download, and HTML rewrite all
  run off the event loop (§11), so the loop is never blocked for minutes by a
  large site.
* The gate does not make any crawl faster. It makes the overshoot orderly.

### Reliability

* A crash in one crawl cannot remove another queued or running job. Each job
  runs in its own task and the queue's bookkeeping happens in `finally` blocks.
* A failed launch cannot consume another job's retry budget.
* The queue never starts more crawls than the current capacity, even when the
  capacity falls between the decision and the launch (§3).
* The queue survives its own bugs: a failure inside one decision path cannot
  interleave with another submission; every decision path is synchronous and
  contains no `await`, so the registry is never observed in a half-updated
  state by another coroutine.

### Security and privacy

* The queue stores no new personal data. A job holds a user id, the URL the
  user already sent, and two integers identifying the status message.
* Queue state is in memory only; nothing is written to disk.
* The rejection and queued messages disclose no internal capacity figures.
* The queue-full message does not reveal whether the machine is busy because of
  memory, CPU, or a suspended circuit breaker.

### Observability

* Every state transition is logged with the user id, so the pm2 log alone
  explains what happened to any request.
* `/admin` reports the derived capacity and the readings behind it, so a wrong
  capacity can be diagnosed without attaching a debugger.
* A dead background task is visible in `/admin` rather than inferred from
  stale numbers.

### Rollout and rollback

* The change is additive: new modules, plus edits to the crawler, the bot
  module, the config, the database, the constants, and `main.py`. No schema
  migration and no data change.
* Rollback is a git revert and a pm2 restart. Because the change is additive,
  the revert is clean.
* There is **no** "disable the queue" switch, because a switch that claimed to
  restore the old behaviour would be a lie: the old behaviour was unbounded
  concurrent crawls, and restoring it is exactly what must not happen. The
  operational controls are `CRAWL_MAX_CONCURRENT` (a ceiling that can only
  lower the limit) and `CRAWL_MAX_QUEUE_SIZE` (which bounds the waiters, not
  the runners).

---

## Detailed Implementation Plan

### Target files to create

#### 1. `bot/errors.py`

* **Scope** — Python module, shared exception types.
* **Proposed changes** — Define `BrowserLaunchError`, carrying the original
  exception as `__cause__` and a `kind` attribute drawn from an inner
  `BrowserLaunchError.Kind` enum with the members `RESOURCE_PRESSURE`,
  `CONFIGURATION`, and `TRANSIENT`. The module lives at the `bot` package
  root, whose `__init__.py` is inert, so it can be imported without
  credentials and without a circular import.

#### 2. `bot/queue/__init__.py`

* **Scope** — Python package marker.
* **Proposed changes** — Empty apart from a docstring. It must **not** import
  anything from `bot.core`, or the whole testability problem returns.

#### 3. `bot/queue/resources.py`

* **Scope** — Python module, runtime resource probing.
* **Proposed changes** — Provide `read_resources() -> ResourceReading` and
  `derive_capacity(reading) -> tuple[int, int]` returning `(capacity,
  workers_per_crawl)`. Implement the cgroup resolution described in §2:
  `/proc/self/cgroup` plus `/proc/self/mountinfo`, cgroup v2 and v1, the
  `"max"` and `-1` sentinels, the `memory.max == "max"` sentinel, and the
  `memory.limit_in_bytes` unlimited sentinel. Fall back to
  `len(os.sched_getaffinity(0))`, then `os.cpu_count()`, then 1. Every file
  read tolerates a missing, unreadable, or malformed file and returns a safe
  value; the probe must never raise. Log the source used on every derivation
  and a WARNING whenever a fallback was taken.

#### 4. `bot/queue/crawl_queue.py`

* **Scope** — Python module, the admission gate.
* **Proposed changes** — Define `Outcome`, `Admission`, `JobState`,
  `CrawlJob`, and `CrawlQueue`. The queue is constructed with injected
  callables so it imports neither the bot module nor the database:
  `runner` (async crawl callable, `async def runner(job: CrawlJob) -> None`),
  `charge` (usage writer, `def charge(user_id: int) -> None`),
  `usage_of` (usage reader, `def usage_of(user_id: int) -> int`),
  `clock` (monotonic clock, `Callable[[], float]`, default `time.monotonic`),
  plus the limits: `max_concurrent`, `max_queue_size`, `max_queue_age_seconds`,
  `browser_retry_limit`, `browser_retry_base_seconds`, `launch_failure_threshold`,
  `launch_suspend_seconds`, `estimate_minutes_per_crawl`, `position_refresh_seconds`,
  `position_refresh_seconds_long`, `position_refresh_top_n`. Implement
  `submit`, `pump`, `cancel`, `defer`, `release_slot`, `may_start`,
  `mark_browser_launched`, `abandon`, `launch`, `set_capacity`,
  `record_duration`, `expire_stale`, `position_updates`, `stats`, `shutdown`.
  All of them except the ones that are explicitly asynchronous are
  **synchronous and free of `await`**. `_pending` is a list kept sorted by
  `sequence`, with insertion via `bisect.insort(..., key=lambda j: j.sequence)`.
  The queue creates and owns the crawl tasks, keeps strong references to them,
  and cancels and awaits them in `shutdown()`.

#### 5. `bot/queue/notifier.py`

* **Scope** — Python module, the user-facing boundary.
* **Proposed changes** — Define the `JobNotifier` `Protocol` with the seven
  methods listed in §6. This file exists so the contract is documented and
  importable, and so the queue module has no Telegram import.

#### 6. `requirements-dev.txt`

* **Scope** — Development dependency manifest.
* **Proposed changes** — `pytest`, `pytest-asyncio`, `ruff`, and `mypy`,
  matching the versions already declared in `pyproject.toml`'s dev extra. The
  runtime manifest is untouched.

#### 7. `tests/conftest.py`

* **Scope** — Pytest fixtures.
* **Proposed changes** — Provide a fixture that constructs a `CrawlQueue` with
  a fake runner, a fake charge function, a fake usage reader, a controllable
  clock, and a small queue limit, so the queue's behaviour can be tested
  without Telegram, a database, or a browser. Provide a fake `JobNotifier`
  implementation.

#### 8. `tests/test_resources.py`

* **Scope** — Unit tests.
* **Proposed changes** — Test the parsers against fixed inputs rather than the
  live machine: `"200000 100000"` yields 2.0 cores; `"max 100000"` yields
  `None`; a v1 quota/period pair yields the right ratio; a v1 quota of `-1`
  yields `None`; a v1 quota of `0` yields `None`; a zero period yields
  `None`; `memory.max == "max"` yields no limit; a v1 unlimited memory limit
  yields no limit; a `/proc/meminfo` sample yields the expected megabytes; a
  cgroup limit smaller than `MemAvailable` wins. Test that `derive_capacity`
  returns 0 when the memory headroom is below the budget, respects the ceiling,
  and produces `capacity × workers ≤ cpu_slots`. Assert `derive_capacity`
  returns 2 (not 1) for a reading with `cpu_source="affinity"`.

#### 9. `tests/test_crawl_queue.py`

* **Scope** — Unit tests.
* **Proposed changes** — Cover: admission on an idle queue; queuing when
  capacity is full, with positions 1, 2, 3; FIFO order by `sequence`; the
  queue-full rejection at the limit; the duplicate rejection; the usage
  rejection; the submit order (capacity is checked before queue-full, so a
  request is never rejected while a slot is idle); cancellation of a queued
  job; cancellation of a running job; cancellation arriving before the
  `cancel_running` callback is assigned; the `may_start` refusal when capacity
  has fallen; a deferral not consuming the retry budget; a launch failure
  being re-queued with a backoff that is actually observed using the injected
  clock; the retry budget being enforced; a configuration failure failing
  immediately; the circuit breaker suspending launches; the charge happening
  exactly once and only after a successful launch; a re-queued job keeping its
  sequence position; the published invariants INV-1 to INV-5 holding after
  every operation; `position_updates` reporting only jobs whose advertised
  position changed; `expire_stale` returning the correct jobs; `record_duration`
  only sampling successful `DONE` jobs; `stats()` returning the published keys.

#### 10. `tests/test_database.py`

* **Scope** — Unit tests.
* **Proposed changes** — Against a temporary database: `increment_requests`
  does not change `usage_count`; `increment_usage` changes it by exactly one;
  `cleanup_old_data` removes a `request_stats` row older than seven days and
  keeps a recent one; `activity_log` is capped; a `busy_timeout` is set.

### Target files to modify

#### 11. `bot/core/__init__.py`

* **Scope** — Package marker.
* **Proposed changes** — Remove `from bot.core.scraper import bot`. Nothing
  imports `bot.core` as a package (`main.py:18` imports `bot.core.scraper`
  directly), so this is safe, and it makes every module under `bot.core`
  importable in tests without credentials.

#### 12. `bot/core/crawler.py`

* **Scope** — Python module, the crawler.
* **Proposed changes** — Wrap the **acquisition of the Playwright driver and
  the browser launch only**, not the whole crawl, in a `try/except` that
  converts exceptions into `BrowserLaunchError` with a classified `kind`. The
  classifier maps: missing binary / blocked sandbox / missing libs →
  `CONFIGURATION`; `ENOMEM`/`EAGAIN` on spawn or "Target page, context or
  browser has been closed" under memory pressure → `RESOURCE_PRESSURE`;
  transient Playwright/driver IPC error → `TRANSIENT`. Add a `try/finally` so
  `browser.close()` runs even when the task is cancelled. Add an
  `asset_concurrency` parameter, defaulting to the configured value, used for
  the asset semaphore and the `httpx` pool instead of `concurrency`. Assert
  `concurrency >= 1` in `__init__`.

#### 13. `bot/core/scraper.py`

* **Scope** — Python module, the bot.
* **Proposed changes** —
  * Remove `active_jobs` and all ten references to it (`:57`, `:125`, `:264`,
    `:511`, `:584`, `:595`, `:606`, `:622`, `:663`, `:715`); the queue becomes
    the single registry of in-flight work.
  * Remove `pending_cookie_requests`, `active_progress_msgs`, and
    `download_executor`.
  * **Reorder `handle_url` statement sequence** (exact order):
    1. `db.register_user(...)`
    2. `db.log_activity(...)`
    3. `if db.is_banned(user_id): ... return`
    4. `if not await check_force_join(user_id, msg): return`
    5. `if not is_valid_url(url): ... return`
    6. `domain = extract_domain(url)`
    7. `if not is_safe_domain(domain): ... return`
    8. Settings/scope/cookies computation
    9. Cookie branch (`if wants_cookies and not use_cookies: ... return`)
    10. `queue.submit(job)` — **no `db.increment_usage` call**
    11. Handle outcome: send first message ("Starting clone" / "Queued …" / rejection), record `message_id`, `job.status_ready.set()`
    12. If admitted: `queue.launch(job)`
  * Replace the `_run_crawl` body with a queue runner that: waits for
    `status_ready`; checks `cancel_requested`; calls `queue.may_start(job)` and
    defers if it refuses; calls `queue.mark_browser_launched(job)` after a
    successful launch; assigns `job.cancel_running`; cancels the progress task
    in a `finally`; moves `create_zip`, `_download_videos`, `_rewrite_all_html`,
    and the artifact deletion into `asyncio.to_thread`; calls
    `queue.release_slot(job)` before the upload; calls
    `queue.record_duration(job, held_seconds)` in the `finally`; and re-raises
    `BrowserLaunchError` so the queue can apply the retry policy.
  * Route the `/cancel` command and the Cancel callback through
    `queue.cancel()` and reply according to the returned state.
  * Add `TelegramNotifier`, implementing the `JobNotifier` protocol by
    **editing** the job's status message via `bot.edit_message_text(chat_id,
    message_id, text)`.
  * Add `start_background_tasks()` returning the probe, the reporter, and the
    maintenance tasks, each supervised.
  * Add the startup scavenger, the Chromium reaper.
  * Extend the `/admin` handler with the queue block from §10.
  * Make the output paths unique per job.

#### 14. `bot/config.py`

* **Scope** — Python configuration module.
* **Proposed changes** — Change `CRAWL_CONCURRENCY` from `15` to `0`, meaning
  "derive from the CPU quota". Add the following settings, each read from the
  environment with a default:

| Name | Default | Meaning |
|---|---|---|
| `CRAWL_MAX_CONCURRENT` | `4` | Hard ceiling on concurrent crawls. |
| `CRAWL_MEMORY_BUDGET_MB` | `1000` | RAM assumed per crawl. Provisional — tune from the measured p90. |
| `CRAWL_MEMORY_RESERVE_MB` | `1024` | RAM held back for the sibling applications. |
| `CRAWL_MAX_QUEUE_SIZE` | `100` | Maximum waiting users. |
| `CRAWL_MAX_QUEUE_AGE_MINUTES` | `45` | A job waiting longer is expired and not charged. |
| `CRAWL_BROWSER_RETRY_LIMIT` | `2` | Total launch attempts before giving up (2 attempts = 1 retry). |
| `CRAWL_BROWSER_RETRY_BASE_SECONDS` | `5` | Backoff base; attempt N waits `base × 2^(N-1)`. |
| `CRAWL_LAUNCH_FAILURE_THRESHOLD` | `3` | Consecutive resource failures that trip the breaker. |
| `CRAWL_LAUNCH_SUSPEND_SECONDS` | `30` | How long launches stay suspended. |
| `CRAWL_RESOURCE_PROBE_INTERVAL` | `2` | Memory probe interval, seconds. |
| `CRAWL_CPU_PROBE_INTERVAL` | `15` | CPU probe interval, seconds. |
| `CRAWL_POSITION_REFRESH_SECONDS` | `120` | Refresh cadence for the top positions. |
| `CRAWL_POSITION_REFRESH_SECONDS_LONG` | `600` | Refresh cadence for the rest. |
| `CRAWL_POSITION_REFRESH_TOP_N` | `20` | How many positions get the fast cadence. |
| `CRAWL_ESTIMATE_MINUTES_PER_CRAWL` | `2` | Fallback per-clone duration estimate. |
| `CRAWL_ORPHAN_MAX_AGE_MINUTES` | `60` | Age at which a leftover artifact is an orphan. |
| `CRAWL_ASSET_CONCURRENCY` | `4` | Concurrent asset fetches, decoupled from the page-worker count. |
| `CLEANUP_INTERVAL_SECONDS` | `900` | Maintenance interval. |
| `CRAWL_FORCE_LAUNCH_FAILURE` | `0` | Fault injection: the first N launch attempts fail. |
| `CRAWL_FORCE_LAUNCH_FAILURE_KIND` | `resource_pressure` | Which classification the injected launch failure carries: `resource_pressure`, `configuration`, or `transient`. |

#### 15. `bot/constants.py`

* **Scope** — Python module, user-facing strings.
* **Proposed changes** — Add the queued message, the queue-full message, the
  server-busy retry message, the deferred message, the cancelled message, the
  not-launched cancellation message, the too-late-to-cancel message, the
  expired message, and the restart-notice message (unused, kept for
  completeness), each with a `{placeholder}` for any number that comes from
  configuration or state. No message hardcodes "100" or "15 minutes". No
  message discloses the capacity or the queue depth.

#### 16. `bot/database.py`

* **Scope** — Python module, persistence.
* **Proposed changes** — Remove `usage_count = usage_count + 1` from
  `increment_requests`, making `increment_usage` the sole writer. Fix
  `cleanup_old_data` to compare ISO timestamps written with
  `timespec="microseconds"` on both sides, and replace the unbounded deletes
  with batched deletes that commit between batches. Add `PRAGMA busy_timeout
  = 5000` to `get_conn()`. Add `close_conn()`. Guard the request-path
  counters against `sqlite3.OperationalError`.

#### 17. `main.py`

* **Scope** — Python entrypoint.
* **Proposed changes** — Replace the bare `bot.run()` with the explicit
  lifecycle shown in §9: `await bot.start()`, start the background tasks,
  `await idle()`, and in a `finally` cancel **and await** the background tasks
  and the crawl tasks, then `await bot.stop()`. Retain the existing
  `try/except KeyboardInterrupt` and `except Exception` handling and the
  `__main__` guard.

#### 18. `pyproject.toml`

* **Scope** — Python project metadata.
* **Proposed changes** — Add `asyncio_mode = "auto"` under
  `[tool.pytest.ini_options]`, and add `pytest-asyncio` to the dev extra.
  While editing the file, drop the stale `lxml`, `chardet`, `Flask`, and
  `gunicorn` runtime dependencies, which `requirements.txt` no longer lists.
  This is a small correction of an existing inconsistency, not a refactor.

#### 19. `README.md`

* **Scope** — Project documentation.
* **Proposed changes** — Update the Configuration table with the 19 new
  settings (name, default, meaning) so operators can discover them.

### Target files deliberately not changed

* **`requirements.txt`** — no new runtime dependency. This is the check that
  G10 is honoured: if an implementation needs a new package, the design is
  wrong.
* **`Dockerfile`** — the image installs `requirements.txt` and runs
  `python3 main.py`. Neither changes.

---

## Verification Plan

### Where the checks run

The unit and static checks run in a development environment with
`requirements-dev.txt` installed. The Dockerfile installs only
`requirements.txt` and is not a test environment; the end-to-end checks run
on the Hoody container #2, where the project virtualenv can have the dev
extras installed alongside the runtime dependencies.

### Automated checks

* **Compilation** — `python -m compileall -q bot main.py` must pass.
* **Unit tests** — `pytest tests/` must pass, with **no skipped async
  tests**. The suite includes an assertion on the number of collected async
  tests, so the "pytest silently skips async tests" failure mode cannot recur.
* **Static analysis** — `ruff check bot main.py` and `mypy bot/queue` using
  the configuration already in `pyproject.toml`.
* **Coverage of the decision paths** — every branch of `submit`, `cancel`,
  `pump`, `may_start`, `defer`, the launch-failure handler, and the resource
  parsers must be exercised.

### Manual and end-to-end checks, on Hoody container #2

1. **The normal path still works.** Send one URL. It is admitted, cloned, and
   the archive is delivered. The usage counter moves by exactly **one**.
2. **The resolved worker count is right.** The pm2 log shows one page worker
   per crawl, not fifteen, and `capacity × workers = 2 of 2 cores`.
3. **The queue works.** Send five URLs in quick succession from five accounts.
   At most `CRAWL_MAX_CONCURRENT` run at once; the rest show a position and an
   estimate; every one eventually receives its archive. The log shows the
   admission and start decisions.
4. **The queue-full rejection is correct.** Set `CRAWL_MAX_CONCURRENT=1` and
   `CRAWL_MAX_QUEUE_SIZE=2`, send four URLs, and confirm the fourth is
   rejected with the busy message, that its usage counter did not move, and
   that the message discloses no internal figures.
5. **Cancelling a queued job works.** Send `/cancel` while a job is queued.
   Confirm it never starts, that the next user advances one position, and that
   the cancelled user's usage counter did not move.
6. **Cancelling a running job works.** Confirm the crawl stops, the artifacts
   are removed, and the usage counter **does** move.
7. **A launch failure retries with backoff.** Set `CRAWL_FORCE_LAUNCH_FAILURE=2`
   and restart. The next clone's first two launch attempts fail. Confirm the
   user is told the server is busy and is still trying, that the third attempt
   succeeds, that the backoff was observed, and that the usage counter moved
   exactly once.
8. **A configuration fault fails fast.** Set `CRAWL_FORCE_LAUNCH_FAILURE=1`
   with `CRAWL_FORCE_LAUNCH_FAILURE_KIND=configuration`. Confirm the job fails
   immediately with no retry and an ERROR-level log line.
9. **The circuit breaker engages.** Set `CRAWL_FORCE_LAUNCH_FAILURE=5`.
   Confirm that after three consecutive resource failures no further launch
   is attempted until the suspension expires.
10. **The usage truth table holds.** Across the runs above, assert: success
    → +1; rejection → +0; cancel while queued → +0; pre-launch failure → +0;
    cancel while running → +1; `/media` → +0.
11. **`/admin` matches reality.** The queue block agrees with the pm2 log, the
    probe's last reading time is recent, and the launch-failure count has a
    real one-hour window.
12. **Restart with users waiting.** Restart the bot while three jobs are
    queued. Confirm waiting users' messages are left as-is (no restart notice)
    and this is the accepted trade-off.
13. **The orphan sweep works.** Create a `clone_test_999999/` directory and a
    `clone_test_999999.zip`, both with an old modification time, plus a fresh
    one. Restart. Confirm the old pair is gone and the fresh one survives.
14. **No orphaned Chromium survives a restart.** Start a crawl, `pm2 restart`
    the bot mid-crawl, and confirm with `pgrep -f "chromium.*--remote-debugging-pipe"`
    that no Playwright Chromium process remains.
15. **Database pruning works.** Insert a `request_stats` row older than seven
    days, trigger maintenance, and confirm the row is gone and the table
    count drops.
16. **The loop is not blocked.** Clone a large site and confirm that
    `/cancel` and the queue-position refresh still respond while the archive
    is being built.
17. **Regression checks.** `/start` still shows the premium emoji header and
    the premium button icons; the force-join flow still gates usage past
    three crawls; `/cancel` still stops a running clone; the archive is still
    delivered and the temporary directory is still removed.

---

## Future Improvements

* Reuse a single Chromium instance across all crawls instead of launching one
  per crawl. This removes the largest per-crawl cost, but it restructures
  `WebCloner.run()`. It is the first thing to try when the crawler, rather than
  the box, becomes the constraint.
* Persist the queue to survive restarts, in SQLite or Redis. Depends on Q2 and
  on whether the bot ever runs more than one worker.
* Bound the whole crawl, not just the page phase. Today `CRAWL_TIMEOUT_SECONDS`
  covers only the page crawl; video download and the HTML rewrite run
  afterwards, unbounded.
* Decouple the crawl from the delivery so the capacity slot can be released
  before the archive is built, not only before the upload. §11 already releases
  the slot before the upload; releasing earlier would also remove the ZIP-build
  time from the slot's occupancy.
* Implement the `RATE_LIMIT` and `DAILY_LIMIT` values that are already defined
  but unused, for protection against a single abusive account from a large
  address pool.
* Measure the real per-crawl memory distribution and replace the provisional
  `CRAWL_MEMORY_BUDGET_MB` with a measured p90, including the Playwright Node
  driver and Chromium's child processes.
* Give long-running crawls a reserved share of the capacity rather than a
  first-come-first-served slot.

---

*Drafted with EGM*