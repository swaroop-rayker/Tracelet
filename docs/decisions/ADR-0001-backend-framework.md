# ADR-0001 — Backend framework: FastAPI

**Status:** Accepted (Gate 2, 2026-09-25)
**Deciders:** repository owner
**Relates to:** C1, ES1, ES4, NFR2, NFR6

---

## Context

The brief fixes Python as the backend language (C1). Within that, the framework must
satisfy several constraints that are not all obvious:

1. **Async is not optional.** The capture path calls a DNS resolver for the rDNS PTR
   record and, when enabled, up to two external geolocation APIs. These must run
   concurrently with hard timeouts, or the 50 ms p95 server-processing target (NFR2.AC1)
   is unreachable. A synchronous framework would serialise them.
2. **Strict typing must be enforceable** (ES1), and the error contract must be one
   consistent JSON shape (ES4).
3. **The frontend needs generated types.** F14.AC9 requires the TypeScript client to be
   generated from the API schema with a CI drift check, so the framework must emit an
   accurate OpenAPI document without hand-maintenance.
4. **Memory is 1 GB total** (NFR6), with roughly 230 MB budgeted for the API container.

A secondary consideration: the dashboard is custom and polished (F9.AC16, F9.AC17), so a
framework that ships an admin UI provides no leverage here — we would not use it.

## Decision

**FastAPI**, with:

- **Pydantic v2** for request, response and configuration models. Rust-backed core, so
  validation is fast and memory-lean.
- **Gunicorn supervising 2 Uvicorn workers**, with `max_requests` recycling.
- **SQLAlchemy 2.0 async + Alembic** for admin CRUD and migrations, with **raw SQL on the
  same `asyncpg` pool for the hot capture path**.
- **Jinja2** for the server-rendered capture page (F2.AC3).

Two workers rather than one: a single worker makes any restart a total outage and leaves
no margin for a GC pause on a shared vCPU. Two workers on 1 shared vCPU is acceptable
because the workload is I/O-bound.

## Alternatives considered

| Option | Why rejected |
|---|---|
| **Django + DRF** | Sync-first; async support is partial and the ORM pushes you back to sync patterns. Heavier baseline (roughly 150–200 MB vs 110 MB). Its strongest asset — the admin UI — is unusable here because we are building a custom dashboard. DRF serializers do not generate an OpenAPI document as faithfully as Pydantic models, which weakens F14.AC9. |
| **Litestar** | Technically a good fit, with better dependency injection than FastAPI. Rejected on ecosystem depth: fewer answered questions, fewer worked examples, and for a **solo developer** that is a real maintenance cost, not an abstract one. |
| **Flask / Quart** | Would require hand-building validation, OpenAPI generation, and dependency injection — precisely the parts ES1 and ES4 need to be rigorous. More code to own, for no gain. |
| **Starlette alone** | FastAPI is a thin layer over Starlette that supplies exactly the validation and schema generation we need. Dropping it means reimplementing it. |

## Consequences

**Positive**

- Concurrent outbound calls with timeouts on the capture path, which is what makes
  NFR2.AC1 achievable.
- Pydantic v2 models are the single definition of every boundary, feeding both
  `mypy --strict` and the generated TypeScript client.
- The exception-handler hook gives exactly one place to implement the RFC 9457 contract
  (ADR-0013).
- Small memory footprint relative to the alternatives.

**Negative, and accepted**

- **No batteries.** Authentication, sessions, TOTP, CSRF, admin management and the audit
  log are all hand-rolled (ADR-0008). This is more code and more security surface that we
  own. Mitigated by keeping to well-understood primitives — `argon2-cffi`, `pyotp`,
  `cryptography` — rather than inventing anything, and by covering each with unit tests.
- **Two workers means shared state cannot live in process memory.** Rate limits must go
  to PostgreSQL (ADR-0010) and the scheduler must take an advisory lock (ADR-0009). Both
  are consequences of this decision, recorded in their own ADRs.
- Async discipline is required throughout: one accidental blocking call inside a
  coroutine stalls every concurrent request on that worker. A CI lint rule for known
  blocking calls in async contexts is warranted.

**Neutral**

- SQLAlchemy plus raw SQL is a deliberate split: typed models where schema clarity and
  migrations matter, raw SQL where the capture path needs predictable plans. Reviewers
  sometimes read a mixed approach as indecision; the reasoning is recorded here so it
  reads as intent.

## Revisit if

- The capture path exceeds 50 ms p95 in server processing on the target hardware, which
  would indicate the bottleneck is not framework overhead and should be measured before
  any framework change.
- A single Uvicorn worker proves sufficient under the M9 load test, in which case
  dropping to one frees roughly 110 MB — worth reconsidering only if the memory budget
  becomes tight.
