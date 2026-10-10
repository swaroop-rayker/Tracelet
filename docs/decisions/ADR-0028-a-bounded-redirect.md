# ADR-0028 — A bounded redirect: answered from memory when the box is overloaded

**Status:** Accepted (2026-10-10). The owner approved the direction (a destination cache, a
deadline, shedding on latency) and asked for this ADR before the code.
**Deciders:** repository owner
**Amends:** ADR-0010 (shedding, M7 amendment), ADR-0009 (the in-process scheduler, under load)
**Relates to:** CLAUDE.md invariant 1; SPEC F11.AC3, F15.AC6, F15.AC7, NFR1, NFR2, NFR3.AC2,
§11 row 35; ADR-0004 (server-authoritative capture), ADR-0012, ADR-0027; RISKS R6; ERRORS E79

---

## Context

M9's load test on the e2-micro (ADR-0027, 2026-10-10): 10 visitors back to back for 15
minutes, then 30 for 3. **500 visitors were never sent on**: Caddy waited its 10 s for the
api and answered 504. Capture's server time was p50 758 ms, p99 17 s, worst 54 s; four
gunicorn workers were killed for missing their 30 s heartbeat; sixteen background jobs
timed out; host memory averaged 771 MB.

Invariant 1 says the redirect must never fail, and the design already degrades for every
*failing* dependency (F15.AC6, F15.AC7): a database error falls back to a cached link.
What it never covered is a dependency that is merely **slow**, and the slowest one turned
out to be the box itself:

1. **The redirect needs the database.** The destination comes from a query; the link cache
   is filled only by queries that succeeded and is read only after one raised.
2. **Nothing bounds the request below Caddy's 10 s.** A capture waits as long as the pool,
   the database and the event loop take.
3. **The event loop is shared.** ADR-0009 runs the scheduler (inference, classification,
   rollups, the outbox) inside the same worker processes that serve redirects. On a
   throttled shared vCPU (ARCHITECTURE §6.5: about 0.8 of 2 vCPUs accounted under load)
   that work and the redirects starve each other; a worker whose loop did not run for 30 s
   was killed by gunicorn, dropping every request it held.
4. **Shedding watches memory only** (ADR-0010 amendment). It switched on 16 times, but it
   cannot see a CPU queue, and even shed it still writes a row per visit.

## Decision

**When a worker is overloaded, a capture is answered from memory in microseconds, with no
database, and the worker stops doing background work until it recovers. When it is not, a
capture that overruns a deadline is answered the same way and finishes recording behind.**

1. **A warm link cache.** Each worker loads every live link at start and refreshes the
   whole set every 30 s (one small query; links are few). Edits keep invalidating it as
   today, so a change is seen at once by the worker that made it and within 30 s by the
   other. Only the overload and deadline paths read it; a normal capture still reads the
   database, so the normal path cannot serve a stale destination.
2. **Overload detection, per worker, three signals:** event-loop lag (a 100 ms ticker
   measuring how late it wakes, smoothed), captures in flight in that worker, and the
   existing memory pressure. Overloaded when lag exceeds `TRACELET_OVERLOAD_LAG_MS` (250),
   or in-flight captures exceed `TRACELET_OVERLOAD_INFLIGHT` (8), or memory pressure sheds.
   Hysteresis like the memory signal, so it does not flap. Visible in System Health and
   the degradation banner (NFR3.AC3).
3. **Overloaded: redirect from memory, record in a buffer.** The visitor gets the
   rate-limited response (an immediate redirect to the cached destination, F11.AC3). No
   query runs. The visit is appended to a bounded in-memory buffer as the minimal
   `rate_limited` row it would have written (link, time, network prefix, HMAC; never a raw
   IP, invariant 4), and the buffer is written in batches once the worker recovers. Beyond
   `TRACELET_OVERLOAD_BUFFER` rows (5 000 per worker) visits are only counted, and the count
   is logged and shown. A slug the cache does not know is a 404 as now (F1.AC4). *(Settled
   while building:)* a cache that has never loaded (a worker that has just started) cannot
   answer from memory, so that capture takes the deadline path of item 4 instead of
   answering 503: asking the database, bounded, is better than stranding the visitor. The
   integration test of memory-pressure shedding found the gap.
4. **Not overloaded: a deadline.** A normal capture that has not produced its response
   after `TRACELET_CAPTURE_DEADLINE_MS` (1 000) is answered from the cache, as in item 3,
   and its recording task is **not cancelled**: it finishes in the background (shielded,
   at most the in-flight cap of them), so the visit is still recorded, only without the
   capture page and so without enrichment; the sweeper finalises it `server_only`.
5. **Background work yields to redirects.** While a worker is overloaded, its scheduler
   skips every job except the outbox and the sweeper's claim step, and resumes when it
   recovers. Inference and rollups catch up afterwards; they were never on the visitor's
   path (NFR2.AC6).
6. **Caddy keeps its 10 s.** With items 1-5 the app answers long before it; it remains the
   last resort for a wedged process.

## Alternatives considered

| Option | Why not (now) |
|---|---|
| **Move the scheduler into its own process or container** | The structural fix for item 3 of the context, and the next step if this is not enough. A process needs a supervisor in the api container; a container costs ~100 MB of the 1 GB (ADR-0012). Pausing jobs under overload recovers most of the benefit at no memory cost. |
| **Drop to one gunicorn worker** | ADR-0012's first memory lever (~110 MB). It halves capacity on the CPU that is already short; kept as the lever for memory, not for this. |
| **A deadline alone (`asyncio.wait_for`)** | A deadline is a timer on the same starved loop: it fires late exactly when it is needed. It also cancels the database work mid-transaction, losing the visit. |
| **Write a row per shed visit, as now** | Each still costs a transaction when the database is the contended resource. The buffer keeps the record and moves the cost to when it is cheap. |
| **Count shed visits only, never record them** | Simpler, but analytics would undercount exactly the busiest moments. The buffer records them; counting is only the overflow. |
| **A larger host** | ADR-0012's revisit condition, and the honest answer if bursts at NFR1 cannot be met after this. Outside the free tier. |

## Consequences

- **The redirect no longer depends on the database or on the event loop's spare time,**
  only on the worker being alive and its cache loaded.
- **Under overload, telemetry degrades in stages:** a full capture, then a capture without
  enrichment (deadline), then a buffered minimal row (overload), then a count (buffer full).
  Each stage is visible. The visitor's journey is the same in all of them.
- **A destination edit reaches the overload path of the other worker within 30 s.** During
  that window a visitor arriving while the box is overloaded can be sent to the old
  destination. The normal path is never stale.
- **Buffered rows are lost if the worker dies before it recovers** (a crash, an OOM). They
  are minimal rows of visits that were already shed; the loss is counted in the log line
  that the worker writes on shutdown when it can, and otherwise accepted.
- **Memory:** the cache is a few hundred bytes per link; the buffer at its cap is about
  1-2 MB per worker; the lag ticker is one task. Within the api's budget.
- The load test is repeated (ADR-0027) with the pass condition **no visitor left
  un-redirected at any load**, and SPEC §11 row 35's burst test for NFR1.

## Amended 2026-10-10 (M9, after the re-test): the memory the redirect path needs

The re-test on the box with items 1-5 in place: the **burst** test (SPEC §11 row 35) sent on
910 of 910 visitors with time to first byte p95 860 ms, no worker lost. The **stress** flood
ran four times the earlier throughput (13.7/s) and answered 12 409 visits from memory, yet
**718 visitors (2.3 %) still waited past Caddy's 10 s**, and gunicorn killed 9 workers. The
app logged requests stalled up to 24 s inside the process with swap near 200 MB: a worker
whose own pages are swapped out runs no code at all, not even the answer from memory. The
host averaged 741-792 MB in use against NFR6.AC1's 700.

Offered with those figures, the owner approved three changes, all configuration:

1. **No swap for the redirect path.** `memswap_limit` equals `mem_limit` for `api` (and so
   `cli`, through the shared anchor) and `caddy`: the kernel can no longer swap them out, so
   memory pressure lands on page cache, the database and Docker's daemons instead.
2. **One gunicorn worker** (`TRACELET_WEB_CONCURRENCY=1`), ADR-0012's documented first memory
   lever, about 110 MB. The box is granted about 0.8 of a vCPU under load (ARCHITECTURE §6.5),
   so the second worker cost memory without adding throughput.
3. **The GCP OS-config agent stopped and disabled** (ADR-0026 host baseline), 19-63 MB.
   Patching stays with `unattended-upgrades`; the guest agent stays, for SSH keys.
