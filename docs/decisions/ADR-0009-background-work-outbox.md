# ADR-0009 — Background work: transactional outbox with an in-process asyncio worker

**Status:** Accepted (Gate 2, 2026-09-25)
**Deciders:** repository owner
**Relates to:** F7.AC5, F7.AC6, F10.AC13, NFR5.AC2, NFR6, ADR-0001, ADR-0002

---

## Context

Tracelet has two kinds of deferred work.

**Event-driven, delivery-critical:**
- Telegram visit alerts (F7)
- Telegram password-reset links (F8.AC7)
- Telegram health alerts

**Scheduled, periodic:**
- The 90-second unenriched-visit sweeper (F2.AC7) — **on the correctness path**, not
  merely housekeeping
- Nightly rollup refresh (F9.AC19)
- Retention purges, IP TTL purges (F12)
- Nightly backup, monthly restore-verification (ADR-0014)
- Geo-database freshness checks and updates (F10.AC3)
- ASN profile recomputation after a database update (ADR-0005)
- Rate-limit bucket and session cleanup
- CI-independent accuracy recomputation

Total volume is roughly 600 jobs per day. The binding requirement is not throughput — it
is **NFR5.AC2**: a visit and its notification must commit atomically. A visit must never
exist without its queued alert, and a rolled-back visit must never emit one.

Two further constraints: there are **two Uvicorn workers** (ADR-0001), so anything
scheduled must not run twice; and the memory budget is 1 GB (NFR6).

## Decision

**A transactional outbox table plus an in-process asyncio worker and scheduler. No broker,
no separate worker container.**

### The outbox

```sql
outbox(id, kind, dedup_key UNIQUE, payload jsonb, status, attempts,
       max_attempts, next_attempt_at, locked_by, locked_at, last_error,
       created_at, completed_at)
```

**Enqueue happens in the same transaction as visit finalisation.** That single fact is
what satisfies NFR5.AC2 — there is no window in which one exists without the other.

**`dedup_key` is `UNIQUE`**, formatted `visit_alert:{link_id}:{visitor_id}:{utc_date}`.
This is what actually implements the 24-hour notification deduplication rule (F7.AC2).
Doing it as an application-level "have we sent one already?" check would race under
concurrent visits from the same visitor — two requests would both read "no" and both send.
A unique constraint cannot race.

**No foreign key to `visits`.** A retention purge must never block a pending notification,
and the payload is self-contained.

### The worker

An asyncio task inside the API process. Claims with
`SELECT … FOR UPDATE SKIP LOCKED LIMIT n` — correct with two workers, which is one of the
PostgreSQL features ADR-0002 was chosen for. Exponential backoff with jitter, an
idempotency key per job, dead-lettering after `max_attempts`, and every dead job visible
in System Health with a manual retry (F10.AC13).

### The scheduler

A single asyncio scheduler that takes a **PostgreSQL advisory lock** before running
periodic work, so exactly one of the two workers executes it. Without this, nightly
purges and backups would run twice.

## Alternatives considered

| Option | Why rejected |
|---|---|
| **Celery** | The default answer, and a poor fit. Requires a broker (Redis or RabbitMQ): roughly 40–80 MB plus a third stateful container to back up and monitor, for 600 jobs a day. More importantly, Celery enqueues **outside** the database transaction — the standard `task.delay()` after a commit has a window where the commit succeeds and the enqueue fails, losing the notification. Achieving NFR5.AC2 with Celery means implementing an outbox anyway, and then adding Celery on top of it. |
| **ARQ (Redis-based, async-native)** | Lighter and cleaner than Celery, same fundamental objection: a broker, and enqueue outside the transaction. |
| **`FastAPI BackgroundTasks`** | Zero infrastructure. **Loses everything on a process restart or crash** — no retries, no persistence, no visibility. Unacceptable for a delivery-critical alert. |
| **PostgreSQL `LISTEN`/`NOTIFY`** | Elegant for wakeups and worth using as an *optimisation* on top of polling. Not a queue: notifications are not persisted, so a worker that is down misses them entirely. |
| **A separate worker container sharing the code** | Cleaner separation, and removes any risk of background work starving the request loop. Costs roughly 110 MB — a third of remaining headroom — for 600 jobs a day. Rejected on memory, with an explicit extraction trigger below. |
| **`pg_cron`** | Would cover the scheduled half neatly, but not the event-driven half, and it adds a database extension to manage. Not worth it for one of the two problems. |

## Consequences

**Positive**

- **NFR5.AC2 is satisfied by construction**, not by careful ordering. This is stronger
  than what a broker-based design gives you by default.
- Notification deduplication is enforced by a unique constraint and cannot race.
- No broker: roughly 40–80 MB saved and one fewer stateful service to operate, back up,
  and have fail at 3 a.m.
- Retries, backoff, dead-lettering and operator visibility come from ordinary SQL against
  a table you can inspect with a query.
- `SKIP LOCKED` makes concurrent claiming correct without application-level locking.

**Negative, and accepted**

- **Background work shares the request event loop.** A slow job — a Telegram call, a
  `pg_dump` — can add latency to concurrent requests if not carefully bounded. Mitigations:
  every outbound call has a hard timeout; heavy jobs (backup, geo-database update) run as
  memory-capped **subprocesses** rather than inline; job concurrency is capped.
- **The queue lives in the primary database.** Queue write traffic and application traffic
  contend for the same resource. At 600 jobs a day this is unmeasurable, and the risk is
  recorded rather than dismissed.
- **The scheduler needs the advisory lock to be correct.** If that lock is ever dropped or
  mis-scoped, nightly purges and backups run twice. It needs a dedicated test.
- **The sweeper is on the correctness path.** If the worker stops, visits never finalise
  and no notification fires. This needs its own health check and a stuck-visit alert — the
  partial index on `finalized_at IS NULL` in `DATA_MODEL.md` exists for exactly this.
- **No task result backend and no chaining.** No workflow currently needs one; if a
  multi-step pipeline appears, this decision should be revisited rather than worked around.

## Extraction trigger — the condition for revisiting

Extract a separate worker container, and reconsider a broker, when **any** of:

1. Outbox job latency p95 exceeds **5 seconds**.
2. Request latency measurably degrades while background work is running — the signature of
   event-loop starvation.
3. Scheduled jobs begin overlapping their own next run.
4. The deployment moves to a host with 4 GB or more, at which point the memory argument
   for keeping it in-process disappears.

The extraction is deliberately cheap: the worker is already a separate module with its own
entry point, so it becomes a second Compose service running the same image with a different
command. **No broker is required even then** — the outbox continues to work across
processes, because `SKIP LOCKED` does not care which process holds the connection.
