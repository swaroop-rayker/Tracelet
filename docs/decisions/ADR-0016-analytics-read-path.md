# ADR-0016 — Analytics read path: rollup-first, today kept live, raw rows as a stated fallback

**Status:** Accepted (M5, 2026-10-02)
**Deciders:** repository owner (M5 kickoff), recorded before the code per CLAUDE.md §2
**Relates to:** F9.AC2–F9.AC13, F9.AC19, F9.AC20, NFR2.AC4, NFR5.AC4, ADR-0009, ADR-0015,
SPEC §11 row 13

---

## Context

F9.AC19 says analytics read from **nightly rollup tables**, not raw visit rows, to hold
NFR2.AC4 (dashboard p95 under 300 ms) on one shared vCPU. DATA_MODEL 9.1 defined one
rollup, keyed on `(day, link_id, country_code, admin1, classification, device_class,
connection_class)`. Two problems surfaced when M5 started building against it.

1. **The documented rollup cannot answer the documented endpoints.** F9.AC4 asks for
   breakdowns by city, ASN, ISP, browser and app medium, OS and screen resolution; F9.AC7,
   F9.AC9 and F9.AC11 need the source-to-level flow, confidence distributions and rule
   frequencies. None of those are rollup dimensions. F9.AC13's filters (ASN, city, consent
   state, `visitor_id`, minimum confidence, coordinate presence …) mostly are not either.
   Its `avg_confidence_*` and `unique_visitor_count` columns do not add across rows, so
   any query summing cells would report a wrong number.
2. **Refreshing only at night hides today.** A visit made at 10:00 would not appear on any
   chart until the next morning. On a tool whose first use is "I just sent the link —
   did it work?", an empty chart that is actually a stale one is B5 in a new form.

## Decision

**Rollup-first, with today kept live and raw rows as an explicit, stated fallback.**

### Tables (migration 0007)

- **`rollup_visit_daily` / `rollup_visit_hourly`** — the *cell* tables. One row per
  bucket and combination of `link_id, stage, classification, device_class,
  connection_class, country_code, admin1` (strict location). `stage` is added to the key
  so every response can state its stage mix (F9.AC20). Measures are **additive only**:
  counts, plus a sum and a count for each confidence, so an average is computed after
  summing and never averaged twice.
- **`rollup_visit_dim_daily`** — a long-format table: `(day, link_id, classification,
  dimension, value) → visit_count`. One row per visit per dimension it has a value for:
  every F9.AC4 breakdown, `signal` (one row per fired classification rule, F9.AC11),
  `source_flow` (`source>emitted_level`, F9.AC7) and `conf_<level>` deciles (F9.AC9).
  Adding a breakdown is a new dimension name, not a migration.

`unique_visitor_count` is dropped from the cells. A distinct count does not add across
cells, days or links, and storing one invites exactly the sum that would be wrong.
Unique visitors is the one figure always counted from raw rows (`count(DISTINCT
visitor_id)` over the `(visitor_id, occurred_at)` index), and is `null` with a reason for
any day older than the visit retention window.

### One projection, two consumers

Each dimension and measure is defined **once**, as a SQL expression over `visits`
(`analytics/projection.py`). The refresh job inserts `SELECT <projection> … GROUP BY`
into the rollups; the raw fallback runs the same expressions with the caller's filters.
The two paths therefore cannot disagree about what "city" or "enriched" means, and an
integration test asserts that they return identical figures for the same window.

### Refresh

- Job **`rollup`**, every **5 minutes** (ADR-0009 scheduler, advisory-locked): deletes and
  re-inserts the buckets for **yesterday and today** in one transaction. "Yesterday" is
  included so a visit finalised or inferred just after midnight is not stranded.
- Job **`rollup_settle`**, **daily**: the same for the last 7 days, so a late
  re-inference or re-classification is eventually reflected.
- `tracelet analytics rebuild [--since DATE]` rebuilds any range on demand. Needed after
  a reporting-timezone change, and after restoring a backup.
- Rollups are **not** purged with raw rows (NFR5.AC4). The hourly table keeps 14 days.

Every response carries `refreshed_at`, the oldest refresh time among the buckets it read,
so a chart says how fresh it is.

### Buckets are in a reporting timezone

Days and hours are bucketed in **`TRACELET_REPORTING_TZ`** (default `Asia/Kolkata`).
India is UTC+05:30: hours bucketed in UTC would straddle every Indian hour, and days
bucketed in UTC would split an Indian evening across two days. The admin's own timezone
(`admins.timezone`) is for displaying timestamps, not for choosing buckets. Changing the
reporting timezone requires `tracelet analytics rebuild`.

### When the raw fallback is used

A request is served **from rollups** when all of its filters are rollup dimensions
(`link_id`, `classification` / `include_automated`, and for cell-table endpoints
`country_code`, `admin1`, `device_class`, `connection_class`, `stage`), and its `from` and
`to` fall on bucket boundaries in the reporting timezone. Otherwise it is served from
**raw rows** with the same projection. Never silently: every response states
`computed_from: "rollup" | "raw"` beside `stage_mix`.

Endpoints that are inherently per-visit are always raw: clustered map points (F9.AC5,
coordinates exist for few visits by design, ADR-0005), the returning-visitor view
(F9.AC12), unique visitors, the visit list and export.

The raw path is bounded by retention (≤ 90 k rows at the design load, DATA_MODEL 5.1),
and its p95 is measured in M5 next to the rollup path's, against NFR2.AC4.

## Alternatives considered

| Option | Why not |
|---|---|
| **Strictly nightly, as F9.AC19 was written** | Today invisible until tomorrow; filters the rollup cannot serve would have to be disabled in the UI, losing most of F9.AC13 |
| **Raw rows only** | Simplest, and probably inside NFR2.AC4 at 90 k rows — but leaves no history past retention (NFR5.AC4) and puts every dashboard load on the same vCPU as the capture path |
| **One wide rollup keyed on every dimension** | The key would be nearly as fine-grained as the visit itself, so the table approaches the size of `visits` while still not covering multi-valued dimensions (signals, sources) |
| **PostgreSQL materialised views** | `REFRESH … CONCURRENTLY` rewrites the whole view each time; there is no "only today" refresh, and the 5-minute cadence would rewrite 180 days every time |
| **HyperLogLog for distinct visitors** | Needs the `hll` extension in the database image, for one figure on a small table |

## Consequences

**Positive**

- Today's visits appear within about five minutes, and the response says when it was
  computed.
- Every F9.AC4 breakdown and every F9.AC13 filter works; the caller learns which path
  served it.
- One definition of every measure, tested for equality across the two paths.
- History survives raw-row purging, as NFR5.AC4 requires.

**Negative, and accepted**

- Two refresh jobs and a rebuild command to maintain.
- A filter outside the rollup dimensions is a raw query. Bounded by retention, measured,
  and named in the response — but not free.
- Unique visitors is unavailable for days past retention.
- The dimension table holds one row per distinct `(day, link, classification, dimension,
  value)`. Identical values collapse, so 500 visits a day produce a few thousand rows a
  day, mostly narrow text — the largest rollup, and still well inside the disk budget.

## Measured in M5 (2026-10-02)

90 k visits over 180 days (the design load), API and database each capped to one CPU,
20 requests per endpoint from inside the compose network:

| Path | Result |
|---|---|
| **Rollups**, every endpoint | p95 **≤ 70 ms** (summary 68, 365-day calendar 25, breakdowns 16–59, geo 20) |
| Raw fallback, most endpoints | p95 50–220 ms over 30 days |
| Raw fallback, slow cases | summary 4.3 s, source flow 0.9 s, 365-day calendar 10 s -- RISKS R25 |
| Refresh, yesterday + today (~1 000 visits) | 0.63 s |
| Refresh, last 7 days | 0.8–1.2 s |
| Rollup storage, 180 days | 8.2 MB (5 454 daily, 3 477 hourly, 27 138 dimension rows) |

Two changes came out of the measurement: days before the oldest retained visit count as
built (ERRORS E35: the year-long calendar had fallen back to raw rows), and a covering
index makes unique visitors an index-only scan (317 ms cold to 9 ms). Map points use a
partial index over the visits that have coordinates.

## Revisit if

- Raw-path p95 exceeds NFR2.AC4 at the design load: add the offending filter as a rollup
  dimension.
- Traffic grows past roughly 5 000 visits a day: the 5-minute rebuild of "today" becomes
  the job to make incremental.
