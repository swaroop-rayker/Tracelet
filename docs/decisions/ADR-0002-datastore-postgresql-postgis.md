# ADR-0002 — Datastore: PostgreSQL 16 + PostGIS

**Status:** Accepted (Gate 2, 2026-09-25)
**Deciders:** repository owner
**Relates to:** C3, C4, F6, NFR5, NFR6, SC4

---

## Context

This was the most consequential decision at Gate 2, for two reasons: roughly 210 MB of a
1024 MB budget rides on it (NFR6.AC1), and it is the hardest decision in the set to
reverse once schema and queries depend on engine-specific features.

Requirements that bear on it:

- **Geofencing is a headline feature** (F6). Admins draw polygons and circles, and every
  visit must be tested against them. Correctness matters: a false "outside" means a
  missed alert.
- **Notifications must be transactionally consistent** with visit finalisation
  (NFR5.AC2) — a visit must never exist without its queued alert, and a rolled-back visit
  must never emit one.
- **Every inference carries a full evidence trail** (F4.AC6, F4.AC11), stored as
  semi-structured data, and **per-source accuracy must be computable** (F4.AC17).
- **Two Uvicorn workers** share rate-limit state and an outbox queue (ADR-0001).
- **Volume is small:** roughly 90 k visit rows and 720 k candidate rows at 180-day
  retention. Nothing here is a scale problem.
- **Success criterion SC4** requires every architectural decision to be defensible.

The honest tension: at 500 visits per day, a far lighter datastore genuinely suffices.
The choice is therefore not about capacity — it is about which categories of bug we are
willing to own.

## Decision

**PostgreSQL 16 with the PostGIS 3.4 extension**, tuned for the envelope:
`shared_buffers=96MB`, `max_connections=24`, `work_mem=4MB`,
`effective_cache_size=256MB`, `maintenance_work_mem=32MB`. Application pool 5–10
connections **per worker**, so 20 at peak with 2 workers, leaving 4 reserved for
`pg_dump`, restore-verify and an interactive `psql` — see ARCHITECTURE §6.2.

Specific engine features we are deliberately committing to:

| Feature | Used for |
|---|---|
| `geography(Polygon,4326)` + `ST_Covers` + GiST | Geodesically correct point-in-polygon geofencing (F6.AC2) |
| `SELECT … FOR UPDATE SKIP LOCKED` | Outbox claim, correct with two workers (ADR-0009) |
| `JSONB` + GIN | Classification signals, evidence, abstain reasons, versioned settings |
| Partial unique indexes | "Exactly one default link", "one active settings version" — enforced by the engine, not by application code |
| `CONSTRAINT TRIGGER … DEFERRABLE` | "At least one owner always exists" (F8.AC13) |
| Table-level `GRANT` separation | Append-only `audit_log` (NFR5.AC5) |
| `citext`, `inet`, `timestamptz`, array types | Correct native representations rather than text encodings |

## Alternatives considered

### SQLite (WAL) + Shapely — the serious contender

At roughly 30 MB instead of 210 MB, single-file backups, no network layer, and entirely
sufficient throughput at this volume, this was a legitimate choice and was presented as
such. Rejected on four specific grounds:

1. **Shapely is planar.** Point-in-polygon on latitude/longitude treated as Cartesian
   coordinates is an approximation. For hand-drawn geofences in India the error is
   negligible, but it is wrong near the poles and across the antimeridian, and "correct
   for our current use" is a fragile property to build an alerting feature on. PostGIS
   `geography` is spherically correct by construction.
2. **No `SKIP LOCKED`.** The outbox claim would need emulation via a status column plus
   an immediate transaction — workable, but it is exactly the kind of hand-rolled
   concurrency primitive that produces a duplicate-notification bug six months later.
3. **`SQLITE_BUSY` under overlap.** WAL gives concurrent readers, but a dashboard
   aggregate overlapping a visitor write can still contend. Tolerable, yet it turns a
   visitor-facing path into something that depends on admin behaviour.
4. **Weaker enforcement of invariants.** Several invariants in `DATA_MODEL.md` are
   enforced by partial unique indexes, constraint triggers, and role grants. SQLite
   cannot express most of them, which pushes correctness into application code where a
   concurrent request can slip past it.

The cost of rejecting SQLite is 180 MB of RAM and a commitment to PostgreSQL-specific
SQL. That was judged the better trade: the memory budget has room (ARCHITECTURE section 6
shows roughly 379 MB of headroom), and the bugs avoided are subtle ones that are
expensive for a solo developer to diagnose.

### PostgreSQL without PostGIS, geometry in Python

Saves roughly 100 MB of **disk image**, not RAM — PostGIS is shared libraries, and its
runtime memory cost is negligible. You would still accept the planar-geometry compromise.
Strictly dominated by the chosen option; included because it clarifies that the PostGIS
cost is disk, not memory.

### MySQL / MariaDB

Spatial support is weaker, `SKIP LOCKED` behaviour is less mature, and there is no JSONB
equivalent with comparable indexing. No advantage here.

### A time-series engine (TimescaleDB, ClickHouse)

Overkill by three orders of magnitude at 90 k rows, and neither fits the memory envelope.

## Consequences

**Positive**

- Geofence evaluation is correct at any latitude, indexed by GiST, within the 5 ms budget
  (F6.AC8).
- Notification and visit commit atomically, satisfying NFR5.AC2 with no application-level
  coordination.
- Invariants live in the schema, so a concurrent request cannot violate them.
- The append-only audit log is enforced by role grants, meaning a SQL-injection foothold
  in the application path cannot erase its own traces.
- `visit_candidates` as a real table makes per-source accuracy a join rather than a
  JSONB-parsing exercise (F4.AC17).

**Negative, and accepted**

- **210 MB of a 1 GB budget**, roughly a third of the usable memory.
- **Committed to PostgreSQL-specific SQL.** We deliberately do **not** abstract the
  datastore behind a portability layer — using PostGIS properly is the entire point, and
  a portability shim would cost complexity for a migration that is not planned.
- Backups are `pg_dump` rather than a file copy: more moving parts, which is why
  restore-verification is automated (ADR-0014).
- Tuning matters. Default PostgreSQL settings on a 1 GB box will perform badly; the
  parameters above are part of the decision, not an afterthought.

**Neutral**

- Choosing PostGIS **removed** a Python dependency: Shapely is not needed at all
  (ARCHITECTURE §6.4). The frequently-assumed trade "heavier database for lighter
  application" partly reverses here.

## Revisit if

- Memory headroom falls below roughly 150 MB in steady state, in which case the first
  move is reducing to one Uvicorn worker (approximately 110 MB), not changing the
  database.
- The deployment moves to a host with 4 GB or more, at which point the tuning parameters
  above should be raised rather than the engine changed.
