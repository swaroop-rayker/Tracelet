# ADR-0013 — Error contract (RFC 9457) and observability without a metrics stack

**Status:** Accepted (Gate 2, 2026-09-25)
**Deciders:** repository owner
**Relates to:** ES4, F15, NFR4, NFR6, B5

---

## Context

The brief specified the error-handling pattern as "typed errors → consistent JSON error
shape" and required that exceptions and errors be handled efficiently, with graceful
degradation.

Separately, B5 reported blank and black screens where data does not load. A blank screen is
almost always an error that was neither surfaced nor handled — so the error contract and the
frontend error-state requirement (F9.AC18) are the same problem seen from two ends.

The observability question is constrained by memory. A conventional Prometheus plus Grafana
pairing costs roughly 250 MB, against roughly 379 MB of total headroom (ARCHITECTURE
section 6). Spending two-thirds of the remaining budget observing a system that handles 500
requests a day is not a defensible trade.

## Decision

### A typed exception hierarchy behind one handler

```
TraceletError                      (base: code, status, title, safe_detail)
├── ValidationError                → 422, field-level errors[]
├── AuthError
│   ├── Unauthenticated            → 401
│   ├── MfaRequired / MfaInvalid   → 401
│   └── ForbiddenRole              → 403
├── ConflictError
│   ├── LastOwner                  → 409
│   └── LinkHasVisits              → 409
├── GoneError
│   ├── NonceInvalid               → 410
│   └── IpPurged                   → 410
├── RateLimited                    → 429, Retry-After
├── DependencyUnavailable          → 503
└── InternalError                  → 500
```

One FastAPI exception handler translates the hierarchy. **There is exactly one place where
an error becomes a response**, which is what makes the contract actually consistent rather
than nominally consistent.

### RFC 9457 Problem Details as the wire format

```json
{
  "type": "https://tracelet/errors/validation-failed",
  "title": "Validation failed",
  "status": 422,
  "detail": "One or more fields are invalid.",
  "instance": "/api/v1/links",
  "code": "VALIDATION_FAILED",
  "trace_id": "01JBQ8X2K9YV3M7N4P6R8T0W2Z",
  "errors": [{ "field": "destination_url", "code": "SCHEME_NOT_HTTPS", "message": "…" }]
}
```

A standard format rather than a bespoke one, so the generated TypeScript client can have a
single typed error path and the frontend can render any failure without special-casing.

`code` is added beyond the RFC because `type` URIs are awkward to switch on in client code,
and `errors[]` because field-level validation is the most common failure and callers need
it structured.

### ULID `trace_id` everywhere

Generated per request, returned in the response, included in every error body, attached to
every log line, and **stored on the `visits` row**. A user report — or an owner noticing an
odd visit — becomes diagnosable from one identifier.

ULID rather than UUID4: it is time-ordered, so it sorts usefully in logs and gives better
index locality on the visits table.

### 5xx responses are deliberately uninformative

Only `type`, `title`, `status`, `code` and `trace_id`. **No stack trace, no SQL, no internal
hostname, no exception message.** The detail goes to the log under the same `trace_id`
(F15.AC3).

### The capture path never returns a JSON error

A visitor sees a page that redirects, or a 404. Any internal failure is logged and **the
redirect still happens** (F15.AC7). The JSON contract governs `/api/v1` only. This follows
from the CLAUDE.md invariant that the redirect must never fail.

### Observability: structured logs plus a health API. No metrics stack.

- **`structlog`** JSON output, with redaction processors that strip IP addresses,
  coordinates and tokens by default. **A log line must never contain what the database
  refuses to store in plaintext** (F12.AC13) — otherwise ADR-0007 is undone by the logger.
- **Health API** exposing host and database metrics to the System Health page (F10.AC1),
  plus a degradation endpoint driving the banner (F10.AC14).
- **No Prometheus, no Grafana, no OpenTelemetry collector, no APM.**

## Alternatives considered

| Option | Why rejected |
|---|---|
| **A bespoke error shape** (`{"error": "...", "message": "..."}`) | What most projects do. RFC 9457 costs nothing extra, is already understood by tooling, and gives the generated client one typed error path instead of a hand-maintained convention. |
| **Returning validation errors as plain strings** | Loses field association, which means the frontend cannot attach a message to the input that caused it — a direct contributor to the class of confusing failures behind B5. |
| **Exposing exception detail on 5xx in production** | Enormously convenient for a solo developer debugging their own system, and an information-disclosure problem. The `trace_id` plus log approach gives the same diagnostic power without the exposure. |
| **Prometheus + Grafana** | The right answer with memory to spare. Roughly 250 MB of a 379 MB headroom, to monitor 500 requests a day. Rejected on arithmetic, not on principle. |
| **A hosted free-tier APM (Sentry, Grafana Cloud, Better Stack)** | Genuinely free tiers exist and would give real error aggregation. Rejected because it sends error context — potentially including visitor data — to a third party, which conflicts with the privacy posture established in ADR-0007 and RW-3. Reconsidering would mean scrubbing payloads carefully, and is worth revisiting only if log-based diagnosis proves inadequate. |
| **OpenTelemetry with local export** | Correct instrumentation, and the collector plus storage does not fit. The `trace_id` approach is a poor person tracing: sufficient for a three-component system, inadequate for a distributed one. |
| **Logging to a file with rotation** | Container-native logging to stdout plus `docker compose logs` is simpler and avoids a volume. Journald handles rotation. |

## Consequences

**Positive**

- One error shape, one handler, one place to change it.
- `trace_id` makes any report diagnosable without reproduction.
- Log redaction means ADR-0007 is not silently defeated by a debug statement.
- The frontend can render any failure generically, which is a structural defence against
  the B5 blank-screen class: an unhandled shape cannot produce an unrendered state.
- Roughly 250 MB not spent.

**Negative, and accepted**

- **No metrics history.** There is no way to answer "what was p95 last Tuesday" — only
  current state from the health API and whatever the logs retain. For capacity questions
  this is a real gap, and the M9 load test is the only structured performance data that will
  exist.
- **No alerting on trends.** Degradation detection is threshold-based and instantaneous;
  a slow memory leak or gradual latency creep would not be caught automatically. The
  mitigation is the Telegram health-alert channel on discrete conditions, which is coarser
  than a metrics system.
- **Log-based diagnosis does not aggregate.** Finding "how often does this error happen"
  means grepping, not querying. Acceptable at this volume, painful above it.
- **Redaction must be maintained.** A new field carrying PII into a log context would leak
  until noticed. This warrants a unit test asserting that known-sensitive keys are stripped.
- **`trace_id` is per-request, not a span tree.** It correlates, it does not decompose. With
  three components that is enough; it would not be with ten.

## Revisit if

- The deployment moves to a host with 4 GB or more — then Prometheus plus Grafana becomes
  affordable and should be added, since the absence of trend data is the main real cost of
  this decision.
- Log-based diagnosis proves inadequate in practice, in which case a self-hosted error
  aggregator with careful payload scrubbing is the next step, ahead of a hosted service.
- A fourth component is introduced, at which point per-request correlation stops being
  sufficient and real tracing is warranted.
