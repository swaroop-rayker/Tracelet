# ADR-0015 — Location inference runs as an advisory-locked job over finalised visits

**Status:** Accepted (M3, 2026-09-29)
**Deciders:** repository owner (M3 go-ahead), recorded before the code per CLAUDE.md §2
**Relates to:** F4, F4.AC18, F15.AC7, NFR5.AC2, ADR-0004, ADR-0005, ADR-0009, ERRORS.md E27

---

## Context

ARCHITECTURE section 2 draws *finalisation* — fingerprint, classification, location
inference, geofence evaluation, outbox enqueue — as one stage shared by both ways a visit
ends: the enrichment POST (`stage='enriched'`) and the 90-second sweeper
(`stage='server_only'`). M2 built both endings and stopped short of the stage itself.

Location inference is the first part of that stage with real cost:

- **It does network I/O.** A PTR lookup (S6) is up to a second; the external APIs (S9)
  are bounded by a hard timeout; Nominatim (F4.AC4) is rate-limited to one request per
  second. None of it belongs inside the sweeper's `FOR UPDATE SKIP LOCKED` batch, which
  holds row locks for the whole of its transaction.
- **It can fail in ways the visit must survive** (F4.AC18, CLAUDE.md invariant 1). A
  broken resolver (E27), a corrupt database file or an engine bug must never roll back the
  visit row, never delay the enrichment response, and never be retried by a visitor.
- **Two code paths would drift.** Running it inline in both the enrichment handler and
  the sweeper means two call sites with two transaction shapes for the same work.

## Decision

**Inference is its own scheduled job, `infer`, over visits that are finalised and not yet
inferred.** It uses the ADR-0009 scheduler unchanged: an asyncio loop in each API worker,
one tick at a time cluster-wide via a transaction-scoped advisory lock.

- A new column, `visits.inferred_at timestamptz NULL`, marks completion. A partial index
  on `(finalized_at) WHERE finalized_at IS NOT NULL AND inferred_at IS NULL` is the work
  queue, exactly as the sweeper's partial index on `stage='server'` is its queue.
- Each tick claims a small batch with `FOR UPDATE SKIP LOCKED`, **reads what it needs and
  commits**, runs the sources concurrently **outside any transaction**, each under its own
  timeout, then writes the results — the location columns, every `visit_candidates` row,
  and `inferred_at` — in **one** short transaction. The write is conditional on
  `inferred_at IS NULL`, so a second worker that somehow picked the same visit writes
  nothing.
- **The job never raises past a visit.** A failing source is a candidate-less source with
  a reason recorded in the derivation trail. If the whole engine fails for a visit, the
  visit is written with `strict_country_code = NULL` and
  `abstain_reason = {"country": "engine_error"}` (F4.AC18) and marked inferred, so a
  poisoned row cannot wedge the queue.
- **Latency to a result is one tick** — two seconds — plus the slowest enabled source.
  Nothing the visitor sees waits for it (ADR-0004).
- `stuck_visits` gains a second clause: finalised but not inferred after ten minutes.

**Classification (M4), geofence evaluation and the outbox insert (M6) join this job's
write transaction** as they arrive. That transaction — not the enrichment request — is
where NFR5.AC2's atomicity lives: the data that decides an alert and the alert itself
commit together, or neither does. A visit awaiting inference is visible as such
(`inferred_at IS NULL`), in the same way M2 made a visit awaiting enrichment visible.

## Alternatives considered

| Option | Why rejected |
|---|---|
| **Inline in the enrichment handler and in the sweeper** | Two call sites; network I/O inside the sweeper's row locks; an engine failure in the enrichment handler becomes a failed enrichment. |
| **Inline in the enrichment handler only, sweeper enqueues** | Still two paths, and the enrichment POST is a `keepalive` request the browser is abandoning — the worst place to spend a second on DNS. |
| **Outbox rows per visit (`kind='infer'`)** | Reuses ADR-0009 machinery, but adds a row per visit to a table designed for delivery-critical messages, and duplicates the queue the partial index already is. |
| **A separate worker container** | Rejected for the same reason ADR-0009 rejected it: ~110 MB for milliseconds of work per minute. |

## Consequences

**Positive**
- One code path for both finalisation routes, testable by calling one function.
- A broken source degrades inference, never capture or enrichment.
- Re-inference after a settings change or a database update is the same job pointed at
  different rows — relevant to F4.AC14 and to M8's accuracy runs.

**Negative, and accepted**
- A result arrives up to one tick after finalisation, not at it. Irrelevant for a
  dashboard; acceptable for a Telegram alert.
- One more partial index on `visits`.
- The job, like the sweeper, is now on the correctness path: if it stops, visits are
  captured but never located. `stuck_visits` reports it, and from M7 so does System
  Health.

## Revisit if

- Visit volume grows enough that a two-second tick with a small batch falls behind —
  raise the batch size before adding a process.
- A source turns out to need per-visit retries with backoff — that is the point at which
  outbox-style rows (rejected above) start paying for themselves.
