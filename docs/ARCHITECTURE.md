# ARCHITECTURE — Tracelet

**Status:** approved at Gate 3, 2026-09-25.
Decisions here are recorded individually in `docs/decisions/ADR-0001` through `ADR-0014`.
This document says **what the parts are and how data moves between them**. The ADRs say
**why**. If you change anything in this file, the ADR comes first — CLAUDE.md section 2.

---

## 1. Shape of the system

One virtual machine, three containers, one database, no message broker, no separate
worker, no metrics stack. That minimalism is a deliberate consequence of a 1 GB memory
budget and a workload of 500 visits per day; each omission has a documented trigger for
revisiting it.

```
                       ┌──────────────────────────────────────────────┐
   visitor ───────────►│ Cloudflare (free)   PURCHASED-DOMAIN PATH    │
   • real browser      │ • edge TLS in BLR/BOM  → TTFB ~300ms         │
   • in-app webview    │ • CF-Ray colo → metro geo signal (S8)        │
   • crawler / bot     │ • CF-IPCountry → country voter               │
                       │ • L0 DDoS absorption, origin IP hidden       │
                       └────────────────────┬─────────────────────────┘
                                            │  HTTPS, keep-alive, ~250ms
   ═════════════ GCP e2-micro · 1 vCPU · 1 GB RAM · 2 GB swap · 30 GB ═════════════
                                            │
                       ┌────────────────────▼─────────────────────────┐
                       │ caddy                          mem_limit 64M │
                       │ • automatic TLS (Let's Encrypt), HTTP/2      │
                       │ • HSTS, nonce CSP, security headers          │
                       │ • L1: conn limits, body cap, slow-loris      │
                       │ • trusted_proxies (Cloudflare mode)          │
                       │ • serves the built SPA as static files       │
                       └───┬──────────────────────────────────────┬───┘
            /r/* /api/*    │                                      │  / (SPA)
                       ┌───▼──────────────────────────────────────┴───┐
                       │ api    gunicorn → 2 × uvicorn   mem_limit 420M│
                       │                                              │
                       │  ┌────────────────────────────────────────┐  │
                       │  │ capture     → classify → infer →       │  │
                       │  │ geofence    → outbox enqueue           │  │
                       │  ├────────────────────────────────────────┤  │
                       │  │ admin API · auth · L2 GCRA · health    │  │
                       │  ├────────────────────────────────────────┤  │
                       │  │ outbox worker + scheduler              │  │
                       │  │ (asyncio, advisory-locked)             │  │
                       │  └────────────────────────────────────────┘  │
                       └───┬─────────────────────┬────────────────┬───┘
               asyncpg 5-10 │         mmap, RO    │        httpx   │
                       ┌────▼──────────────┐ ┌────▼─────────┐ ┌────▼──────────┐
                       │ db  PG16+PostGIS  │ │ geo DB files │ │ outbound      │
                       │     mem_limit 260M│ │ • GeoLite2   │ │ • Telegram    │
                       │                   │ │ • IP2Loc     │ │ • ipwho.is    │
                       │ visits            │ │ • IPinfo     │ │   by /24 only │
                       │ visit_candidates  │ │ • DB-IP      │ │ • Nominatim   │
                       │ geofences (GiST)  │ │ • GeoNames   │ │ (all: timeout │
                       │ outbox · sessions │ └──────────────┘ │  + breaker)   │
                       │ rollups · audit   │                  └───────────────┘
                       └───────────────────┘
```

### 1.1 Why there is no fourth container

| Omitted | Would cost | Why omitted | Trigger to add |
|---|---|---|---|
| Redis / Valkey | ~40 MB, a third stateful service | PostgreSQL handles GCRA rate limiting, the geo cache, and a `SKIP LOCKED` outbox with correct semantics at this volume | Rate-limit write contention becomes measurable, or load exceeds roughly 50x NFR1 |
| Celery / ARQ worker | ~80 MB plus a broker | The outbox pattern gives at-least-once delivery *transactionally*, which is stronger than fire-and-forget | Job latency p95 above 5 s, or the event loop starves |
| Prometheus + Grafana | ~250 MB | Does not fit. Health API plus structured logs cover the need at this scale | Migration to a host with 4 GB or more |
| Node.js runtime | ~80 MB | The SPA is static after build | Never for v1; SSR is not required |

---

## 2. Capture data flow — the load-bearing design

This flow is the fix for B3 (Instagram webview), B5 (blank screen) and B6 (blocked
requests), and it is why `F2.AC2` is written the way it is. See ADR-0004.

**Governing rule: the HTTP request is the authoritative capture. The client is additive
enrichment that is always allowed to fail.**

```
GET /r/{slug}        (bare /r and /r/ resolve the default link instead — F1.AC3)
 │
 ├─1  resolve slug ─── unknown/inactive ──► 404, leaks nothing               [F2.AC14]
 │
 ├─2  extract client IP
 │      Cloudflare mode: CF-Connecting-IP, ONLY if peer is a verified CF addr [F13.AC6]
 │      direct mode:     peer address
 │
 ├─3  L2 GCRA check ─── over limit ──► 302 to destination, no capture,
 │                                     row stage='rate_limited'              [F11.AC3]
 │
 ├─4  server-side signal extraction (always available, no JS involved)
 │      IP → HMAC + /24|/48 prefix + AES-GCM ciphertext                      [F12.AC1]
 │      ASN, ASN org, ASN type, ISP        ← offline ASN databases
 │      rDNS PTR                          ← resolver, 50 ms budget, cached
 │      header set + HEADER ORDER hash, UA, UA-CH, Accept-Language
 │      HTTP version, TLS version
 │      CF-Ray colo, CF-IPCountry          ← Cloudflare mode only
 │      Referer, UTM
 │
 ├─5  cheap bot gate: facebookexternalhit, LinkedInBot, Twitterbot,
 │      redditbot, WhatsApp, TelegramBot, Slackbot, header anomalies
 │      → classification='crawler', never notified, excluded by default      [F2.AC8]
 │
 ├─6  ══ COMMIT ══  visits row, stage='server'
 │      THE VISIT NOW EXISTS. Everything after this point is optional.       [F2.AC2]
 │
 ├─7  mint enrichment nonce = HMAC(visit_id ‖ ip_prefix ‖ exp), 60 s TTL     [F2.AC6]
 │
 └─8  200 text/html — Jinja2, inline critical CSS, zero external assets      [F2.AC1]
       ├── visible notice + /privacy link + "Continue now" (real href)       [RW-4]
       ├── <noscript><meta http-equiv="refresh" content="0;url=…">           [F2.AC3]
       ├── CSS-hidden honeypot link + hidden form field                      [F5.AC6]
       └── inline nonce'd JS, additive only:
             • screen, viewport, DPR, colour depth, touch points
             • hardwareConcurrency, deviceMemory
             • WebGL vendor + renderer
             • timezone, language list
             • canvas / audio / font / WebGL hashes
             • headless + spoof probes                                      [F5.AC3-5]
             • geolocation only if already granted, never prompts           [F4.AC1]
             • POST /api/v1/s/{nonce}   fetch(…, {keepalive: true})
             • hard redirect timer at link.interstitial_ms, cap 1500 ms      [F2.AC5]
                    │
   ┌────────────────┴─────────────────┐
   │                                  │
   ▼ enrichment ARRIVES               ▼ enrichment NEVER ARRIVES
   POST /api/v1/s/{nonce}             (webview blocked it, uBlock killed it,
   • nonce single-use, else 410        JS disabled, tab closed immediately)
   • merge client signals             sweeper, every 30 s:
   • stage='enriched'                 • claims visits older than 90 s
   │                                  • stage='server_only'
   └──────────────┬───────────────────┘
                  ▼
        FINALISATION (identical for both paths)
        ├─ fingerprint → visitor_id, session_fp, fingerprint_id     [ADR-0006]
        ├─ classification: weighted rules → bot_score, spoof_score  [F5]
        ├─ location inference: 11 sources → consensus → strict +    [F4]
        │    advisory + agreement/conflict + candidate rows
        ├─ geofence eval: ST_Covers over GiST                       [F6.AC5]
        └─ ══ SAME TRANSACTION ══ outbox INSERT with dedup_key      [F7.AC5]
                  ▼
        outbox worker → Telegram, backoff + jitter, dead-letter      [F7.AC6]
```

### 2.1 Five details that are easy to get wrong

1. **`/r/{slug}` returns 200, never 302.** A 302 gives no collection opportunity, and an
   endpoint that redirects to a URL is the pattern Google Safe Browsing classifies as an
   open redirector — which is B4. The destination comes only from a `links` row.
2. **Route names are deliberately boring.** `/r/`, `/api/v1/s/`, `/api/v1/hp/`.
   EasyPrivacy and uBlock Origin match the substrings `track`, `collect`, `analytics`,
   `pixel`, `beacon`, `telemetry` in URLs. That, not bot detection, is the actual cause
   of B6. A CI test asserts no public route contains them.
3. **The sweeper is not an error path.** For social traffic it may be the *primary* path.
   Spike B (RISKS R5) found enrichment survives the Android Instagram webview; iOS is
   unmeasured. Treat `stage='server_only'` as an expected outcome, not a failure.
4. **The capture route owns its transaction** — the one exception to 5.8. The request
   middleware turns a failed commit into a `500` Problem Details response, right for the
   API and wrong for a visitor, who must be redirected whatever happened (F15.AC7). So
   `capture/service.py` opens and commits its own session, and the route wraps even the
   rendering in a fallback that cannot throw.
5. **Step 4's "header order" cannot be observed.** Caddy is Go, and Go's HTTP server parses
   headers into a map before any handler runs, then writes them back sorted. Measured in
   M2; see RISKS R19. The header *set* survives and is stored (sanitised);
   `header_order_hash` stays `NULL` rather than hold a value that looks like a fingerprint
   and is not one. ASN, ISP, rDNS and connection class in step 4 arrive with M3.

---

## 3. Location inference pipeline

Eleven candidate producers, one consensus stage, three suppression rules, two output
field sets. See ADR-0005; the deep explanation with worked numbers is in
`docs/private/03-LOCATION-INFERENCE-DEEP-DIVE.md`.

```
                    ┌─────────── CANDIDATE PRODUCERS ───────────┐
  consented GPS ───►│ S1  browser geolocation      conf 0.99    │
                    │ S2  GeoLite2-City            offline mmdb │
  IP address   ────►│ S3  IP2Location LITE DB11    offline BIN  │
                    │ S4  IPinfo Lite              offline mmdb │
                    │ S5  DB-IP Lite               offline mmdb │
  PTR record   ────►│ S6  rDNS city-code lexicon   ★ India key  │
  ASN + org    ────►│ S7  ISP org-name parsing                  │
  CF-Ray       ────►│ S8  edge colo → metro        ★ webview-safe│
  IP /24 (out) ───►│ S9  ipwho.is, by prefix      toggleable    │
  browser tz   ────►│ S11 timezone cross-check     rejects only  │
                    └────────────────────┬──────────────────────┘
                                         │  candidates persisted, winners AND losers
                                         ▼
                    ┌─── WEIGHTED CONSENSUS ────────────────────┐
                    │ weight = source_prior                     │
                    │        × evidence_quality                 │
                    │        × agreement_bonus                  │
                    │        × tz_penalty (S11)                 │
                    │ grouped by admin1, then by city           │
                    └────────────────────┬──────────────────────┘
                                         ▼
                    ┌─── SUPPRESSION (each records its reason) ─┐
                    │ (a) REGISTRY ARTIFACT                     │
                    │     winning city == ASN modal centroid    │
                    │     AND no non-DB corroboration           │
                    │     → collapse to country       ★ FIXES B1│
                    │ (b) MOBILE / CGNAT ASN                    │
                    │     → discard all city candidates         │
                    │ (c) HOSTING / VPN / TOR ASN               │
                    │     → all strict levels abstain           │
                    └────────────────────┬──────────────────────┘
                                         ▼
              ┌──────────────────────────┴──────────────────────────┐
              ▼                                                     ▼
    STRICT (threshold-gated)                          ADVISORY (argmax)
    country / admin1 / admin2 / city                  same four levels
    NULL + abstain_reason below threshold             always populated if any
    → drives geofencing, Telegram, exports              candidate existed
                                                      → greyed-out in the UI
              └──────────────────────────┬──────────────────────────┘
                                         ▼
                    agreement_score · conflict_score · inference_version
```

**Why both field sets exist:** the strict set is what you act on — a geofence alert must
never fire on a guess. The advisory set is what you *learn from* — it is how you see that
the engine thought "Faridabad" and why suppression was right to reject it. Gate 1 chose
this hybrid explicitly (RW-1).

### 3.1 As built in M3

**Where it runs — ADR-0015.** Not in the enrichment request and not in the sweeper: an
`infer` job on the ADR-0009 scheduler, every two seconds, over visits that are finalised
and not yet inferred (`visits.inferred_at IS NULL`). It reads what it needs, runs the
sources **outside any transaction**, then writes the location columns, the network
columns and every candidate in one short conditional write, **one savepoint per visit**.
A failing source is an outcome in the trail; a failing engine or a write the database
refuses becomes `abstain_reason = engine_error | write_refused` with `inferred_at` set,
so no visit can wedge the queue (F4.AC18).

**The address is decrypted in memory, for the length of one inference.** ADR-0007 kept
the ciphertext precisely so inference can run and be re-run; the owner-only, audited
decrypt of API section 7 governs a *person* reading an address, and is unchanged. The
engine never logs the address and never writes it — including inside a PTR record,
which is masked before storage (DATA_MODEL 5.1).

**How consensus weighs sources** (`inference/consensus.py`). Levels are decided
shallowest first, and a candidate only votes at a level if it agrees with every level
already chosen — B2's tolerance made structural. Support for a value is a noisy-OR over
source **families**: the four registry databases and S9 are one family, S6/S7/S8
another, GPS a third. Agreement *across* families counts in full; agreement *within* the
registry family counts at `within_family_bonus` (0.25), because four databases repeating
one registry record is the B1 mechanism, not corroboration. Confidence is support × the
value's share of all weight at that level, so disagreement lowers it.

**Consent outranks the network.** The three suppression rules describe what an *address*
can and cannot say. Consented GPS is not derived from the address, so it is exempt: a
visitor on a VPN who has already granted location is located by GPS rather than abstained
on (F4.AC1).

**A strict point exists only with a strict city** (DATA_MODEL 5.3 invariant 11, ERRORS.md
E28). A strict state is not a coordinate, and `geopoint` feeds geofencing.

**Every source appears for every visit.** Candidates go to `visit_candidates`; a source
that produced nothing — disabled, empty, timed out, unavailable, failed — is an
`inference.source_absent` entry in `signals` with its reason and latency.

**The offline databases** (`inference/geodb/`, M3 step 2). Seven files: GeoLite2 City
and ASN, IP2Location LITE DB11, IPinfo Lite, DB-IP City and ASN Lite, and GeoNames
(`cities1000` + admin1 names). Each is installed by streaming to staging with a size cap
and SHA-256 (checked against MaxMind's published hash), unpacking, validating in a
memory-capped subprocess that must answer known lookups, then an atomic rename of a
`current` symlink — so a corrupt download never replaces the serving version (F10.AC4).
Readers are memory-mapped and reopened when `current` moves; no restart. The daily
`geodb_update` job and `tracelet geodb update` are the same code. No download URL is ever
logged or stored, and `Download`'s repr hides URL and credentials (ERRORS.md E31).

**GeoNames does two jobs.** It names a GPS point, and it gives every source one
spelling: a candidate with coordinates is renamed from the GeoNames place it is most
*central* to, among those whose population-scaled cover contains it — so "Bangalore"
and "Bengaluru", or DB-IP's "Kukatpally" and MaxMind's "Hyderabad", vote together
instead of splitting. Only fields a candidate asserts are renamed, and country-only
records are never placed: their coordinates are the country's centroid (ERRORS.md E30).

**`asn_profiles`** walk each ASN's IPv4 space through every installed city database,
counting only records that name a city, and only give an ASN a `modal_share` once it holds
a /18 or more — below that, one point is not evidence of a registry collapse.

**Outbound (step 3).** S9 asks ipwho.is — the only external API, ip-api.com having
failed F4.AC5's HTTPS requirement (RISKS R2) — about the visitor's **/24 network
address**, never the host, and caches the answer by prefix for seven days, negative
answers included. Consented visits get a street address from Nominatim, after the
decision; its cache is in memory (rounded to ~11 m, 256 entries, a day), deliberately not
a table, so no second copy of consented addresses outlives visit retention. Both go
through `inference/outbound.py`: a shared GCRA budget in PostgreSQL (ipwho.is 900/day;
Nominatim 4/min, the policy's figure for scheduled use) and a per-process circuit
breaker (five consecutive failures open it for two minutes, then one trial call).
`TRACELET_EXTERNAL_GEO_ENABLED=false` stops both; `street_address_enabled` in the
versioned settings stops Nominatim alone. S10 was dropped (SPEC section 11 row 12, RISKS
R23). A registry-artifact city collapses to the country, not admin1 (row 11, R22).

---

## 4. Classification pipeline

Weighted rules, not machine learning. ADR-0011; attacker-perspective analysis in
`docs/private/04-ANTI-BOT-DEEP-DIVE.md`.

```
  SERVER-SIDE RULES (always evaluated)          CLIENT-SIDE RULES (when enriched)
  ├ UA denylist / crawler regex                 ├ navigator.webdriver
  ├ header-order hash vs claimed client         ├ window.chrome absent + Chrome UA
  ├ missing Accept-Language                    ├ WebGL renderer: SwiftShader /
  ├ HTTP/1.0, or Accept: */* only               │   Mesa OffScreen / llvmpipe
  ├ TLS version vs claimed browser             ├ screen.width==0, outerWidth==0
  ├ hosting / VPN / Tor ASN                    ├ empty plugins + desktop UA
  ├ rDNS scanner patterns                      ├ Permissions-API anomaly
  ├ request-rate anomaly per prefix            ├ implausible font count
  └ known-malicious ASN / UA lists   [F5.AC13] └ CDP artefacts
                    │                                        │
                    └──────────────┬─────────────────────────┘
                                   ▼
                    CROSS-CHECKS (the high-signal ones)
                    ├ GPU renderer vs claimed OS      (Apple GPU + Windows UA)
                    ├ deviceMemory present + iOS UA   (Safari never exposes it)
                    ├ UA-CH platform vs UA string
                    ├ browser timezone vs inferred country
                    ├ screen dimensions vs device class
                    └ impossible travel for a known visitor_id
                                   ▼
                    FINGERPRINT COLLISION            [F5.AC7]
                    same fingerprint_id across ≥N ASNs  → is_proxy_suspected
                    many fingerprints behind one prefix → NAT/carrier gateway,
                                                          NOT a proxy
                                   ▼
                    HONEYPOT                          [F5.AC6]
                    hidden link or hidden field touched → automation
                                   ▼
        classification ∈ {human, bot, crawler, datacenter, spam, spoofed, unknown}
        bot_score 0..100 · spoof_score 0..100 · fired signals[] with weights
        agreement_score · conflict_score · classifier_version
```

Every fired rule is stored with its weight and evidence, so no verdict is
unexplainable — required by F5.AC2 and by the brief requirement that the *source* of
every derivation be visible.

---

## 5. Cross-cutting concerns

### 5.1 Configuration and secrets

| Kind | Where it lives | Notes |
|---|---|---|
| Secrets (DB password, HMAC peppers, IP key, Telegram token, API keys) | environment variables and `0400` files; the IP key is a file **outside** the DB volume | Never in the database, never logged, never returned by an API — F12.AC3 |
| Deployment shape (domain, Cloudflare mode, worker count, memory limits) | `.env`, documented in `.env.example` | Domain-agnostic so one image serves both domain paths — F13.AC5 |
| Runtime behaviour (inference toggles, weights, thresholds, rate limits, retention, quiet hours) | database, **versioned** | Editable from the dashboard, no restart, every change audit-logged and rollback-able — F4.AC14 |

### 5.2 Trust boundaries

```
  UNTRUSTED  visitor request: every header, cookie, path, query, body.
             Including CF-Connecting-IP unless the peer is a verified
             Cloudflare address. Including the enrichment payload, which is
             attacker-controlled and only ever treated as a claim.       [F13.AC6]
  ─────────────────────────────────────────────────────────────────────────────
  SEMI       admin session: authenticated, but role-checked server-side on
             every route, CSRF-validated, and audit-logged.            [F8.AC12]
  ─────────────────────────────────────────────────────────────────────────────
  TRUSTED    offline geo databases (checksum-verified on install),
             the process environment, the IP key file.
  ─────────────────────────────────────────────────────────────────────────────
  EXTERNAL   Telegram, ipwho.is, Nominatim: responses are data,
             never instructions; every call has a timeout and a breaker.
```

**The enrichment payload is attacker-controlled.** A client can claim any screen size,
any GPU, any timezone. This is why cross-checks exist and why `spoof_score` is a
first-class output: contradictions between what a client *claims* and what the network
*shows* are the signal. Nothing from the client is ever trusted to make a security
decision.

### 5.3 Error handling

Typed exception hierarchy → one FastAPI handler → RFC 9457 Problem Details. ADR-0013.

```json
{
  "type": "https://tracelet/errors/rate-limited",
  "title": "Rate limited",
  "status": 429,
  "detail": "Too many requests from this network.",
  "instance": "/api/v1/visits",
  "code": "RATE_LIMITED",
  "trace_id": "01JBQ8X2K9YV3M7N4P6R8T0W2Z",
  "errors": []
}
```

A 5xx returns only `type`, `title`, `status`, `code` and `trace_id`. The detail goes to
the log under the same id. Full catalogue in `docs/API.md` section 12.1.

Where the transaction is committed relative to this handler is not an implementation
detail — see 5.8.

### 5.4 Observability

- **Structured JSON logs** via `structlog`, with IP, coordinates and tokens redacted by
  default. A log line must never contain what the database refuses to store in
  plaintext (F12.AC13).
- **`trace_id`** (ULID) generated per request, returned in the response and in the error
  body, attached to every log line, and stored on the visit row.
- **Health API** exposes host and database metrics to the System Health page.
- **No Prometheus, no Grafana** — ADR-0013, with the trigger recorded.

### 5.5 Concurrency model

Two Uvicorn workers under Gunicorn, with request recycling. Async throughout, so the
capture path never blocks on the rDNS resolver, an outbound geo API, or Telegram.
Consequences of running two processes, each handled explicitly:

- **Rate limits must be shared**, so GCRA state lives in PostgreSQL (F11.AC8).
- **Multi-step auth state must be shared**, so the MFA challenge, the enrolment confirm
  token and the Telegram verification code live in `auth_challenges` rather than in
  process memory. Held per process, login succeeded only when the follow-up request
  happened to reach the same worker — about half the time (docs/ERRORS.md E10).
- **The scheduler must be single-instance**, so periodic jobs take a PostgreSQL
  advisory lock; only one worker runs them (ADR-0009).
- **The outbox claim must be safe**, so jobs are claimed with `FOR UPDATE SKIP LOCKED`.

The general rule, since this has now cost one real bug: **any state that must survive from
one HTTP request to the next is shared state, and on this deployment shared means
PostgreSQL.** There is no third option — no Redis (ADR-0010) and no sticky sessions at the
edge.

### 5.6 Graceful degradation

Full matrix in SPEC F15.AC6. The one rule that governs all of it:

> **The redirect must never fail.** If the database is down, if inference collapses, if
> Telegram is unreachable, if the visitor is rate-limited — the visitor still reaches the
> destination. Degrade the telemetry, never the journey.

**The one case that needed a mechanism: the database is down.** The destination lives in
the database, so an outage would leave nowhere to send anyone. Each worker keeps a small
cache of live links, refreshed on every successful lookup and consulted **only** when the
database cannot answer. A link this worker has served before still redirects; a link it
has never seen gets an honest "temporarily unavailable". The cache is never the source of
truth while the database is up, so an edited destination takes effect on the next request
(F1.AC8).

Missing configuration degrades a column, not the visit: no stable pepper means no
`ip_hmac`, no key file means no `ip_enc`, no session secret means no enrichment nonce. Each
is logged on every request, and the visit is still recorded and redirected.

### 5.7 Time

`timestamptz` in UTC everywhere. ISO-8601 in every API payload. Rendered in the admin
preferred timezone, defaulting to `Asia/Kolkata`. Rollup day boundaries are computed in
a configured reporting timezone, stored explicitly, so a chart never silently shifts.

### 5.8 The request transaction boundary

Added in M1, after a bug that reported a failed transaction as `200 OK` and in doing so hid
a second bug for most of a session (docs/ERRORS.md E11).

**The session is owned by middleware, not by a dependency.** The conventional FastAPI
pattern —

```python
async def get_db():
    async with session_scope() as session:
        yield session
```

— commits in the dependency's **teardown**, and FastAPI runs teardown *after* the response
has been generated. A commit that fails there cannot change the status code, so a
constraint violation at COMMIT reaches the client as complete success. Middleware can do
what the dependency cannot: it holds the response object, so it commits first and replaces
the response with a `500` if the commit fails.

**The rollback policy is deliberate:**

| Outcome | Transaction | Why |
|---|---|---|
| 2xx / 3xx | commit | the obvious case |
| **4xx** | **commit** | the handler chose this outcome, and what it wrote is part of the decision — a failed login must keep its audit row (F8.AC16) |
| 5xx or an unhandled exception | roll back | nobody chose this outcome |

Committing on a 4xx is the unusual half, and it is load-bearing: the audit rows that matter
most to anyone investigating an intrusion are written by requests that then fail. Without
it, the only authentication events in the log are the successful ones.

**It has a price, and the price must be respected.** A handler that mutates and *then*
rejects will commit that mutation. Such a handler must validate **before** mutating, or roll
back explicitly. Getting this wrong turned a `409 LAST_OWNER` into a commit-time `500`
(docs/ERRORS.md E14), and the same rule applies to every route added from here on.

Events that must be recorded *although the request failed* — a rejected password, a bad
code, a lockout — are written on their own connection and committed immediately
(`audit.record(..., independent=True)`), so they survive even a 5xx rollback.

`/healthz` skips session creation entirely: it runs every ten seconds and would otherwise
spend a pool slot reserved for maintenance (6.2).

---

## 6. Memory budget

The binding constraint. NFR6 requires steady state at or below 700 MB of 1024 MB.

| Component | Tuning | Expected RSS |
|---|---|---|
| Debian minimal host + dockerd | | ~180 MB |
| `db` PostgreSQL 16 + PostGIS | `shared_buffers=96MB`, `max_connections=24`, `work_mem=4MB`, `effective_cache_size=256MB`, `maintenance_work_mem=32MB` | ~210 MB |
| `api` gunicorn + 2 × uvicorn | pydantic-core, asyncpg, SQLAlchemy; `.mmdb` files `mmap`'d read-only so they are page cache, **not** RSS | ~230 MB |
| `caddy` | static file serving + TLS | ~25 MB |
| **Steady state** | | **~645 MB** |
| **Headroom** | | **~379 MB** |
| Swap | | 2048 MB |

### 6.1 First measurement — M0, idle, development hardware

Measured 2026-09-26 with `docker stats`, all three containers healthy, after 200
requests through Caddy:

| Container | Measured | Limit | Projection |
|---|---|---|---|
| `api` | **136 MiB** | 420 M | ~230 MB |
| `db` | **20 MiB** | 260 M | ~210 MB |
| `caddy` | **16 MiB** | 64 M | ~25 MB |
| **Containers total** | **~173 MiB** | | ~465 MB |

**What this does and does not tell us.** The `api` and `caddy` figures are encouraging
and are unlikely to fall further. The `db` figure is **not** evidence the projection
was wrong: `shared_buffers` is shared memory allocated lazily, and this instance holds
one table, no data and two connections. It will rise substantially with real data,
concurrent backends and the rollup queries.

Above all, this was measured on a 16-core development machine with 8 GB available to
Docker, **idle**. It is a sanity check that nothing is grossly over budget, not
evidence for NFR6. The only measurement that settles NFR6 is the M9 load test on the
actual e2-micro, under load, with CPU steal and swap in play (RISKS R6, E in the
spike log).

### 6.2 Connection budget

The pool is **per process**, so the peak is `pool_max x workers`, and the
maintenance reserve comes out of the same `max_connections`:

```
  10 pool_max  x  2 workers   =  20 peak application connections
  24 max_connections  -  4 reserved  =  20 available
```

The 4 reserved connections exist so `pg_dump`, the monthly restore-verify job and
an interactive `psql` can still connect while the application pool is saturated --
which is exactly when a backup must not fail. The arithmetic is validated at boot
by `Settings`, and a violation refuses to start rather than failing later under
load. `TRACELET_DB_MAX_CONNECTIONS` must be kept in step with the
`max_connections` value in `docker-compose.yml`.

### 6.3 Transient spikes, each mitigated

| Spike | Size | Mitigation |
|---|---|---|
| Argon2id verification | 32 MB × concurrent logins | **Pinned to `m=32MiB, t=3, p=1`. Library defaults of 64 MiB to 1 GiB would OOM the box on one login.** Logins are serialised by rate limit |
| `pg_dump` | ~40 MB | Scheduled off-peak, streamed and compressed |
| Geo database update | ~150 MB | **The riskiest one.** Streams to disk, validates in a memory-capped subprocess, atomic symlink swap, off-peak. A failed update must never take down capture — RISKS R4 |
| Analytics aggregate | `work_mem` bounded | Dashboard reads rollups, not raw rows — F9.AC19 |

**Long-lived in-process state added in M2** (CLAUDE.md section 5 requires stating it):

| State | Bound | Expected RSS |
|---|---|---|
| Links cache, per worker | 1,000 entries, oldest evicted | Under 0.5 MB at the bound; a few KB at realistic link counts |
| Scheduler, per worker | Two asyncio tasks | Negligible. The lock-holding session is one pooled connection for the length of a tick |
| Jinja2 environment, per worker | Four compiled templates | Under 1 MB |
| Cloudflare range table | 22 networks, module constant | Negligible |

**Added in M3:**

| State | Bound | Expected RSS |
|---|---|---|
| GeoNames index, per worker | ~35 k places: all of India plus 15 000+ elsewhere, `array` coordinates, interned names | **Measured +9 MB** (78 to 87 MB max RSS, production image, 2026-09-29) |
| Open geo-database readers, per worker | Seven files, all memory-mapped | Page cache, not RSS: the files total ~470 MB, and a lookup touches a handful of pages |
| S6 resolver pool | 4 threads, module constant | Negligible |
| rDNS canary state | One boolean, ten-minute TTL | Negligible |

`asn_profiles` computation is **not** in-process: it runs in a subprocess capped at 1.5 GB
of address space, once after each database update, for under a minute.

### 6.4 Two dependency decisions that bought headroom

- **Shapely was eliminated** because PostGIS does the geometry. Choosing PostGIS
  removed a Python dependency rather than adding one.
- **`reverse_geocoder` was rejected** because it drags in `numpy` and `scipy`, roughly
  80 MB. Offline reverse geocoding is implemented against GeoNames `cities1000`, filtered
  to India plus places of 15 000+ elsewhere, with 1-degree grid buckets and haversine in
  pure Python — **measured at 9 MB per worker** in M3.

Before merging anything that adds a dependency, a container, or a long-lived in-process
cache, state its expected RSS. That is a review gate, not a suggestion.

---

## 7. Disk budget

30 GB total. At 500 visits per day with 180-day retention:

| Item | Estimate |
|---|---|
| OS + Docker images | ~4 GB |
| PostgreSQL data: ~90 k visits, ~720 k candidate rows, rollups, indexes | ~1.5 GB |
| Offline geo databases: ~470 MB serving (IP2Location 231, DB-IP City 127, GeoLite2 City 65, GeoNames 32, IPinfo 24, two ASN files 22), the previous version of each kept for rollback, one download staged at a time | **~1.2 GB** (revised in M3 from 0.6 GB) |
| Backups: 7 daily + 4 weekly, compressed | ~1.5 GB |
| WAL, logs, scratch | ~1 GB |
| **Total** | **~9.2 GB**, comfortable |

Growth is monitored on the System Health page, with a banner below a configurable
threshold (F10.AC14). No table partitioning in v1: 90 k rows does not need it, and
adding it early would be complexity without benefit.

---

## 8. Dependency ledger

ES5 requires justification for every dependency, including what was considered instead.

This ledger is the **complete v1 list**. Installation is incremental: each
milestone installs only what it uses, so it stays obvious which milestone owns
which dependency and nothing unused occupies memory on a 1 GB box. M0 installed
the framework, database and tooling rows only.

**M1 added four Python rows and no frontend rows:** `argon2-cffi`, `pyotp`,
`cryptography` and `httpx`. `httpx` was previously a *dev* dependency used by the test
client; Telegram delivery makes it a runtime one (ADR-0008 makes Telegram the recovery
channel, so it is on a user-facing path). Nothing else moved.

**M1 added no frontend dependency**, which is worth stating because two ledger rows
tempted it. `react-router` is listed for M5, where nested dashboard layouts and
URL-shareable filter state earn it; M1 has five flat routes and uses a ~60-line
`src/router.ts` instead. `zod` is listed for M5 with the generated client; M1 narrows the
six auth payloads by hand, the same way M0 narrowed `/readyz`. Both arrive when they pay
for themselves.

### Python

| Dependency | Justification | Considered instead |
|---|---|---|
| `fastapi` | ASGI framework; async is mandatory for parallel geo lookups with timeouts on the capture path; OpenAPI generation feeds the typed TS client | Django (sync-first, heavier), Litestar (thinner ecosystem) |
| `uvicorn[standard]`, `gunicorn` | ASGI server plus worker supervision with request recycling | uvicorn alone (no supervision or recycling) |
| `pydantic`, `pydantic-settings` | Strict typing at every boundary and typed configuration from the environment | hand-rolled validation |
| `sqlalchemy[asyncio]` 2.0 | Typed 2.0 mappings for admin CRUD; the hot capture path uses raw SQL on the same pool | raw asyncpg only (loses migrations tooling and typed models) |
| `geoalchemy2` | PostGIS column types in SQLAlchemy. **Not installed until M6**, which is the first code to read or write geometry; M2 adds `visits.geopoint` with plain DDL and leaves it unmapped | raw SQL for all geometry |
| `alembic` | Forward-only reviewed migrations. Worth the dependency on its own | hand-written SQL migrations |
| `asyncpg` | Fastest async PostgreSQL driver | psycopg3 async (comparable; asyncpg chosen for pool ergonomics) |
| `psycopg[binary]` | **Sync** driver, used by Alembic only. Migrations have no reason to be async, and a sync driver makes a failed migration far easier to read | running Alembic on asyncpg (works, but every failure arrives wrapped in async machinery) |
| `argon2-cffi` | Argon2id password hashing, the current standard | bcrypt (no memory-hardness) |
| `pyotp` | TOTP; small, focused, well-audited | hand-rolled RFC 6238 |
| `cryptography` | AES-256-GCM for IP at rest; HMAC | `pycryptodome` |
| `httpx` | Async outbound with timeouts, to external geo APIs and Telegram | `aiohttp` (heavier), `requests` (sync, would block the loop) |
| `maxminddb` | Reads `.mmdb` — GeoLite2, IPinfo Lite **and** DB-IP Lite all ship this format — memory-mapped, so a database is page cache, not RSS (CLAUDE.md section 5). **Zero dependencies.** Replaced `geoip2` in M3 with the owner's approval: `geoip2` is a thin wrapper over this reader plus a web-service client, and installs aiohttp, requests and about ten more packages for a client Tracelet never uses (it has `httpx`) | `geoip2` (same reader, twelve extra packages), a hand-written mmdb parser |
| `IP2Location` | IP2Location LITE ships a proprietary BIN format. Zero dependencies | converting BIN to mmdb ourselves, a maintenance liability |
| `jinja2` | Server-rendered capture page; already a FastAPI-adjacent standard. **Installed in M2.** Autoescaping is the reason: the destination and the nonce are interpolated into attributes and an inline script | f-string templating, unsafe for HTML |
| `structlog` | Structured JSON logs with redaction processors | stdlib logging plus a custom formatter |
| `psutil` | Host CPU, RAM, disk, swap, uptime for System Health | parsing `/proc` by hand |
| `python-ulid` | Sortable `trace_id`, better index locality than UUID4 | `uuid4` (no time ordering) |
| **Rejected: `shapely`** | — | Unnecessary once PostGIS owns the geometry |
| **Rejected: `reverse_geocoder`** | — | Drags in numpy + scipy, roughly 80 MB on a 1 GB box |
| **Rejected: `pandas`** | — | Aggregation belongs in SQL; would not fit the budget |
| **Rejected: `celery` / `arq`** | — | Requires a broker; the outbox is transactionally stronger — ADR-0009 |
| dev: `pytest`, `pytest-asyncio`, `ruff`, `mypy` | Test and quality toolchain | — |
| **Rejected: `testcontainers`** | — | CI PostgreSQL is a GitHub Actions service container; no dependency needed |
| **Rejected: `email-validator` + `dnspython`** | — | Arrived as a side effect of `pydantic.EmailStr` and crash-looped the API (docs/ERRORS.md E9). This system sends **no email at all** — Gate 1 declined SMTP and recovery runs over Telegram — so an admin address is purely a login identifier, and two packages for RFC 5322 conformance on a string nothing is delivered to fails ES5. Replaced with a constrained string in `auth/types.py` |
| **Rejected for now: `ua-parser`** | — | Its rule set compiles into every worker for a precision nothing in M2 consumes. `capture/useragent.py` answers the questions M2 needs -- which webview, which preview fetcher, roughly what device -- and returns `None` rather than guessing. M4's classifier is the place to revisit it, with evidence of what the modest parser gets wrong |
| **Rejected: a QR-code renderer** | — | `qrcode`/`segno` would save one or two admins a single manual entry at enrolment. Every authenticator app accepts a typed base32 secret, so the enrolment page shows the secret and the `otpauth://` URI instead (`auth/totp.py::provisioning_hint`). Worth revisiting if the admin count grows |

### Frontend

| Dependency | Justification | Considered instead |
|---|---|---|
| `react`, `react-dom` | Component model with the deepest ecosystem for the specific libraries this project needs | SvelteKit (thinner Geoman and ECharts integration), HTMX (wrong tool for a drawing canvas and cross-filtered charts) |
| `typescript`, `vite` | Strict typing; fast build to static assets | — |
| `@tanstack/react-query` | Server-state cache, polling for System Health, request deduplication | hand-rolled fetch hooks |
| `react-router` | Routing with URL-shareable filter state (F9.AC13) | hash routing |
| `tailwindcss` | Utility CSS with design tokens; three themes via CSS custom properties | plain CSS modules |
| `echarts` | **One** dependency covering line, bar, calendar heatmap, Sankey, gauge, geo and treemap, with canvas rendering that survives 90 k points | Recharts (no calendar heatmap or Sankey, SVG struggles at volume), visx (much more assembly), Chart.js (weaker chart variety) |
| `leaflet` + `@geoman-io/leaflet-geoman-free` | Polygon and circle drawing with vertex editing; CARTO raster basemap needs **no API key** | MapLibre GL (prettier vector, but free vector styles need a key you declined), Mapbox (paid) |
| `zod` | Validates API payloads at runtime. Generated types prove the *contract*; zod proves the *payload* | trusting generated types (a schema drift becomes a runtime crash) |
| dev: `openapi-typescript` | Generates the TS client from FastAPI OpenAPI; CI fails on drift | hand-maintained types |
| dev: `@types/node` | `vite.config.ts` and `eslint.config.js` are Node code, so `tsc --noEmit` needs Node types. Dev-only, zero runtime cost | dropping the `@/*` path alias to avoid `node:url` — rejected, the alias is worth more than the type package costs |
| dev: `eslint`, `prettier`, `vitest` | Quality toolchain | — |
| **Not a dependency: shadcn/ui** | Component source is copied into the repository, so it adds no runtime package | a component library as a dependency |
| **Rejected: `echarts-for-react`** | — | A roughly 30-line hook does the job without another package |

---

## 9. Repository layout

```
tracelet/
├── CLAUDE.md
├── Makefile                        # thin wrapper; delegates to scripts/tl
├── scripts/
│   ├── tl                          # THE task runner. Single implementation
│   └── guard_private_docs.sh       # CI: fails if docs/private/ is ever tracked
├── docker-compose.yml              # caddy, api, db + profiled *-tools services
├── .env.example
├── .gitignore                      # includes docs/private/
├── .dockerignore
├── Caddyfile
├── commitlint.config.cjs
├── db/
│   └── init/01-extensions-and-roles.sh   # first-boot: PostGIS, citext, 3 roles
├── api/
│   ├── Dockerfile                  # stages: deps → dev (toolchain) → production
│   ├── pyproject.toml
│   ├── openapi.json                # GENERATED, committed; CI fails on drift
│   ├── alembic/
│   └── src/tracelet/
│       ├── main.py                 # app factory, middleware, lifespan
│       ├── config.py               # pydantic-settings
│       ├── errors.py               # typed hierarchy → RFC 9457
│       ├── db/                     # engine, pool, models, repositories
│       │                           #   request_session.py owns the per-request
│       │                           #   transaction and commits BEFORE the
│       │                           #   response leaves (5.8)
│       ├── net.py                  # which client address to believe; Cloudflare
│       │                           #   range verification (F13.AC6)
│       ├── capture/                # /r/{slug}, enrichment, honeypot, /privacy,
│       │                           #   links and visits APIs, templates/
│       ├── inference/              # sources/ S1..S11, consensus, suppression,
│       │                           #   engine.py (the ADR-0015 job), router.py
│       │                           #   (settings versions), data/ seed files
│       ├── classification/         # rules/, scoring, cross-checks, honeypot
│       ├── identity/               # HMAC fingerprint, visitor_id
│       ├── geofence/               # PostGIS evaluation, GeoJSON import/export
│       ├── notify/                 # outbox, Telegram client, formatters
│       ├── audit/                  # the append-only audit writer
│       ├── auth/                   # sessions, TOTP, recovery, CSRF, challenges,
│       │                           #   and the owner-only admins router
│       ├── ratelimit/              # GCRA in PostgreSQL
│       ├── admin_api/              # dashboard routers (M2+; M1 routers live
│       │                           #   beside the feature they serve, e.g.
│       │                           #   auth/admins_router.py)
│       ├── health/                 # psutil, geo DB freshness, flow diagram
│       ├── lifecycle/              # retention, purge, backup, restore-verify
│       ├── crypto/                 # AES-GCM envelope, key loading, rotation
│       ├── worker/                 # scheduler: sweeper, IP purge (M2), infer
│       │                           #   (M3); outbox M6
│       └── cli/                    # tracelet admin …, database update, labelling
│   ├── spikes/                     # measurement scripts + raw results (Spike A);
│   │                               #   linted and typed, never in the image
│   └── tests/
│       ├── unit/                   # inference, scoring, GCRA, crypto, policy
│       └── integration/            # API + real PostgreSQL + PostGIS
│                                   #   helpers.py drives the real auth flow;
│                                   #   never a database double (ES3)
├── web/
│   ├── Dockerfile                  # builds the SPA, then serves it from Caddy
│   ├── Dockerfile.tools            # eslint/prettier/tsc/openapi-typescript
│   ├── package.json
│   └── src/
│       ├── api/                    # hand-written clients + generated/ schema.d.ts
│       ├── components/             # shadcn-derived primitives, charts, map
│       ├── pages/                  # M1: enrol, login, recovery, reset, dashboard
│       ├── router.ts               # M1 only; react-router arrives with M5
│       ├── features/               # visits, analytics, geofences, health, admins
│       └── theme/                  # semi-dark default, light, dark
├── data/                           # geo databases + GeoNames (gitignored)
└── docs/
    ├── KICKOFF.md SPEC.md ARCHITECTURE.md DATA_MODEL.md API.md
    ├── MILESTONES.md RISKS.md ERRORS.md
    ├── decisions/ADR-0001..0014
    └── private/                    # GITIGNORED — owner explainer track
```

---

### 9.1 Why the task runner is a script, not a Makefile

`scripts/tl` is the single implementation of every developer task; the `Makefile`
only delegates to it. The reason is the documented dev environment: Windows 11 with
Docker via WSL (SPEC C5). `make` is not present in Git Bash on a stock Windows
host, so a Makefile-only interface would fail exactly where the project is
developed. POSIX `sh` runs in Git Bash, in WSL and in CI alike.

Both entry points work and cannot diverge, because only one of them contains logic:

```bash
make verify            # where make exists (WSL, CI, Linux)
./scripts/tl verify    # everywhere, including Git Bash
```

Every task runs inside a container, so neither Python nor Node is required on the
host. The `api-tools` and `web-tools` compose services exist for that and sit
behind the `tools` profile, so `up` never starts them and the running `api` image
stays production-lean with no dev dependencies.

## 10. Change protocol

| Change | Required before the code |
|---|---|
| New or altered component, data flow, or cross-cutting concern | Update this document |
| Any architectural decision | **New ADR first** |
| Schema, index, or invariant | `DATA_MODEL.md` + an Alembic migration in the same commit |
| Endpoint, payload, or error shape | `API.md` + regenerate the TS client |
| New dependency | A row in the section 8 ledger, with the rejected alternative and the expected RSS |
| Requirement change | Propose, wait, then amend `SPEC.md` section 11 |
