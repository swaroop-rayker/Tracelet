# ADR-0010 — Rate limiting: four layers, GCRA in PostgreSQL, redirect never denied

**Status:** Accepted (Gate 2, 2026-09-25)
**Deciders:** repository owner
**Relates to:** F11, NFR1, NFR3, ADR-0001, ADR-0002, ADR-0012

---

## Context

The brief asked for rate limiting "for visits, admins both upstream and downstream",
control of visitor hits so the system is not overwhelmed, DDoS and spam prevention, and
control of admin login and dashboard use.

The capacity envelope is 10 concurrent capture requests, 500 per day, 2 concurrent admins
(NFR1). That is small enough that the *purpose* of rate limiting here is not capacity
management — it is survival of a 1 vCPU / 1 GB box against a burst, and protection of the
login endpoint against credential attack.

One product question had to be settled first: **what happens to a visitor who is rate
limited?** They are, in the overwhelming majority of cases, a human who clicked a link,
not an attacker. Denying them the redirect punishes the wrong party.

A second constraint: there are **two Uvicorn workers** (ADR-0001). Per-process rate-limit
state would grant each worker a full allowance, doubling every limit silently.

## Decision

### Four layers, each catching what the next cannot

| Layer | Where | Catches | Available on |
|---|---|---|---|
| **L0** | Cloudflare free | Volumetric DDoS, known-bad networks, before a packet reaches the VM | **Purchased-domain path only** |
| **L1** | Caddy | Concurrent connections, request body size, slow-loris, header limits | Always |
| **L2** | Application, GCRA in PostgreSQL | Per-prefix, per-route, per-identifier, per-session logical limits | Always |
| **L3** | asyncpg pool, 5–10 connections | Backpressure at the database instead of collapse | Always |

### Algorithm: GCRA

Generic Cell Rate Algorithm, state stored as a single `tat` (theoretical arrival time)
timestamp per key in `rate_limit_buckets`. Chosen over alternatives because it gives smooth
rate limiting with a configurable burst from **one column and one comparison**, with no
sliding-window bookkeeping and no background expiry sweep on the hot path.

### State in PostgreSQL, not in process memory

Required for correctness with two workers (F11.AC8). Not Redis — see ADR-0009 and the
alternatives below.

### The governing product rule

**A rate-limited visitor is still redirected.** Immediately, with no capture, recorded as
`stage='rate_limited'` so the shedding is visible in analytics rather than invisible.

This is the one place where the abuse-control mechanism deliberately does not protect the
data — because NFR3.AC2 and the CLAUDE.md invariant "the redirect must never fail" outrank
telemetry completeness. Losing a visit record is acceptable; breaking a human journey is
not.

### Amended 2026-10-07 (M7): shedding under memory pressure (F15.AC6, RISKS R6)

F15.AC6 asks that, when the host swaps, requests are shed at L2 before the kernel's OOM
killer has to choose. The mechanism, which no earlier milestone built:

- **Signal:** the kernel's pressure stall information, `/proc/pressure/memory` "some
  avg10" -- the share of the last ten seconds in which a task waited for memory. It
  measures thrashing directly, which free-memory or swap-used figures do not: a box can
  hold swap it is not using. On a kernel without PSI, the swap-in rate from `/proc/vmstat`
  stands in.
- **Threshold:** shed at `TRACELET_SHED_MEMORY_PRESSURE` (20 %), stop below half of it, so
  it does not flap; `0` turns it off. The swap-in fallback uses
  `TRACELET_SHED_SWAPIN_PAGES_PER_S` (256).
- **Where:** each worker reads the host's file at most every two seconds (no background
  task, no shared state). While shedding, a capture skips the limiter and is handled
  exactly as a rate-limited one -- redirected at once, recorded as `stage='rate_limited'`
  -- so it costs one small insert and the visitor never waits on memory.
- **Seen:** the degradation banner shows `shedding`, critical, with the count so far.

Rejected: a shared "shedding" flag in the database (a write under exactly the conditions
that make writes slow), dropping the record too (shedding would be invisible in the
funnel), and shedding the dashboard (the owner needs it most then).

### Both directions — the "upstream and downstream" requirement

**Inbound** (F11.AC2, F11.AC5, F11.AC6):

| Route class | Limit | Key |
|---|---|---|
| `GET /r/{slug}` | 30/min, 300/hr, burst 10 | IP prefix |
| `POST /api/v1/s/{nonce}` | Once per nonce, ever | nonce |
| `POST /auth/login`, `/auth/mfa` | 5 per 15 min; 20/hr | identifier; IP prefix |
| `POST /auth/reset/request` | 3/hr | identifier |
| `GET /visits/{id}/ip` | 10/hr | admin |
| All other `/api/v1` | 120/min | session |

**Outbound** (F11.AC7) — every external call has its own budget and circuit breaker:

| Target | Budget | Rationale |
|---|---|---|
| Nominatim | **1 request per second**, cached | Their usage policy. Exceeding it risks a block and is simply bad citizenship |
| ipwho.is, ip-api.com | Per-source budget + breaker | Undocumented limits (RISKS R2). Cached by /24 prefix so repeat visitors cost nothing |
| Telegram | Per-bot budget + backoff | Their limits are real and the outbox already handles backoff |

Outbound limiting is the half that gets forgotten. It is what stops a traffic spike turning
into a third-party ban, which would then degrade inference for every subsequent visitor.

All limits are editable without redeployment, and every change is audit-logged (F11.AC9).

## Alternatives considered

| Option | Why rejected |
|---|---|
| **Redis/Valkey for rate-limit state** | Atomic counters and native TTLs, the textbook choice. Costs roughly 40 MB and a third stateful container (ADR-0009). At 500 requests per day the PostgreSQL write volume for rate limiting is negligible — roughly one small upsert per request. Rejected on the same grounds as the broker: real operational cost, no benefit at this scale. |
| **In-process token bucket** | Zero infrastructure and fastest. **Incorrect with two workers** — each would grant a full allowance. Would silently double every limit, which is worse than having no limit, because it looks like it works. |
| **Sliding-window log** | Most precise. Requires storing a timestamp per request per key and pruning them. More writes, more storage, more code, for precision that does not change any outcome here. |
| **Fixed-window counter** | Simplest. Permits a 2x burst across a window boundary — exactly the pattern that hurts a 1 vCPU box. |
| **Cloudflare rate limiting alone** | Free-tier rules are coarse, and **absent entirely on the free-subdomain path**. Cannot express per-identifier login limits or outbound budgets. Good as L0, insufficient as the only layer. |
| **Denying the redirect when over limit** | The conventional behaviour for an API. Wrong for this product: the rate-limited party is almost always a human whose journey we would be breaking to protect a telemetry row. |

## Consequences

**Positive**

- Four layers mean the cheap defences absorb volume before the expensive ones are reached;
  L0 blocks at the edge, L1 before application code, L2 before the database, L3 before
  collapse.
- Correct across both workers, because the state is shared.
- GCRA gives burst tolerance from one column — well suited to real traffic, which is bursty.
- Outbound budgets protect third-party relationships, which protects inference quality.
- A rate-limited visit is recorded as such, so shedding appears in the funnel rather than
  as an unexplained gap.

**Negative, and accepted**

- **One database write per limited request.** Trivial at 500/day, and it would need
  revisiting two orders of magnitude higher.
- **L0 does not exist on the free-subdomain path.** That deployment mode has materially
  weaker DDoS protection, and on a 1 vCPU box a volumetric attack would be effective.
  Recorded in ADR-0012 and RISKS R8 as a consequence of the domain choice.
- **`rate_limit_buckets` needs periodic cleanup** or it grows unbounded with per-prefix
  keys. A scheduled job handles it (ADR-0009).
- **Rate-limited visits have no telemetry**, so a sustained attack produces a visible gap
  in data quality. That is the intended trade, and F9.AC20 stage-mix reporting makes it
  legible.
- **Tuning is guesswork until M9.** The defaults above are calibrated to NFR1 with roughly
  3x headroom, but the real values come from the load test.

## Revisit if

- Rate-limit write contention becomes measurable in `pg_stat_statements` — the first move
  is an in-process front cache with PostgreSQL as the authority, not adding Redis.
- The M9 load test shows the capture limits shed legitimate traffic, in which case raise
  the burst allowance rather than the sustained rate.
- Volumetric attack becomes a real rather than theoretical problem on the free-subdomain
  path, which would make the purchased domain effectively mandatory.
