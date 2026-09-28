# MILESTONES — Tracelet v1

**Status:** approved at Gate 3, 2026-09-25.
**One PR per milestone** (ES6). Conventional commits, scoped to the F-ID where one applies:
`feat(F4): add rDNS city-code lexicon`.

After completing a milestone: tick its checklist here, update every affected doc, and only
then open the PR (CLAUDE.md section 2).

**Size key:** S ≈ 1–2 days, M ≈ 3–5 days, L ≈ 1–2 weeks, at solo pace.

---

## Progress

| | Milestone | Size | Status |
|---|---|---|---|
| M0 | Foundation and CI | M | **[x] done** — CI green on `main`, 69 tests |
| M1 | Admin auth and account security | L | **[x] done** — 285 tests, 15 bugs recorded as E8–E22 |
| M2 | Capture path, server-authoritative | L | [ ] |
| M3 | Location inference engine | L | [ ] |
| M4 | Anti-spoofing and classification | L | [ ] |
| M5 | Dashboard analytics and visualisation | L | [ ] |
| M6 | Geofencing and Telegram notifications | M | [ ] |
| M7 | System health and operations | L | [ ] |
| M8 | Accuracy hardening and ground truth | M | [ ] |
| M9 | Production hardening and deploy | M | [ ] |

**Spikes (run during M0/M1, before the milestones that depend on them):**

| | Spike | Blocks | Status |
|---|---|---|---|
| Spike A | rDNS city-code coverage for Indian residential IPs (RISKS R3) | **M3** | [ ] |
| Spike B | `fetch(keepalive)` survival in the Instagram webview (RISKS R5) | **M2** | [ ] |

---

## M0 — Foundation and CI · size M

**Goal:** an empty but fully-wired skeleton that proves the toolchain, not the product.
**F/AC-IDs:** F14.AC1, F14.AC3, F14.AC4, F14.AC5, F14.AC6, F14.AC7, F14.AC8, F14.AC10,
F14.AC11, F15.AC1, F15.AC2, F15.AC4, F15.AC5, F13.AC1, F13.AC2, ES1, ES2, ES6

**Scope**
- Repo layout per ARCHITECTURE section 9; `docker-compose.yml` with 3 services and hard
  `mem_limit`s
- PostgreSQL 16 + PostGIS container, Alembic baseline, `postgis` extension, the three
  database roles (`DATA_MODEL.md` section 12)
- FastAPI app: `/healthz`, `/readyz`, the typed error hierarchy and RFC 9457 handler
- `structlog` JSON logging, ULID `trace_id` propagation, redaction processors
- React + TS strict + Vite skeleton, multi-stage build, served by Caddy
- Caddy: automatic HTTPS, HSTS, nonce CSP, security headers, `trusted_proxies` toggle
- `Makefile`: bootstrap, up, verify, migrate, fmt, logs, down
- GitHub Actions: ruff, mypy --strict, pytest unit, pytest integration **against a real
  Postgres service container**, eslint, prettier, tsc --noEmit, OpenAPI-to-TS drift,
  docker build, commitlint, **guard-private-docs**
- `.env.example` with every variable documented

**Done checklist**
- [x] A deliberately-raised typed error returns the exact RFC 9457 shape with a `trace_id`
      — `tests/unit/test_errors.py`, 12 tests incl. 5xx leaking nothing but the id
- [x] A redaction unit test asserts known-sensitive keys never reach log output
      — `tests/unit/test_log_redaction.py`, 13 tests, plus free-text address masking (E4)
- [x] `guard-private-docs` **proven to fail** when a `docs/private/` path is staged
      — verified both directions, including a `git add -f` bypass
- [x] Route-name guard passes and is proven able to fail (B6 defence)
- [x] `ruff check`, `ruff format --check`, `mypy --strict` clean; 46 unit tests pass
- [x] `tsc --noEmit`, `eslint`, `prettier --check` clean
- [x] OpenAPI exported and the TypeScript client generated from it
- [x] **All 12 checks green** via `./scripts/tl verify` — 61 unit + 8 integration tests
- [x] `/healthz` returns **200 through Caddy over HTTPS**; port 80 issues a 308 to HTTPS
- [x] `/readyz` returns `ready: true` with database ok, **PostGIS 3.4.3**, migrations current
- [x] **Integration tests pass against real PostgreSQL + PostGIS**, never mocked (ES3).
      Includes a geodesic `ST_Covers` assertion and proof that `tracelet_app` is refused DDL
- [x] Database initialised as designed: postgis + citext + pg_stat_statements, three roles,
      `public` owned by `tracelet_migrate`, default privileges granting the app role
- [x] Error contract holds **through the edge**: 404 and 405 both return RFC 9457
      `application/problem+json` with a matching `X-Trace-Id`
- [x] Security headers present on every response; SPA served with its own strict CSP,
      **zero inline script or style**, immutable asset caching, `no-cache` on index.html,
      and client-side route fallback
- [x] Measured memory, idle: api 136 MiB, db 20 MiB, caddy 16 MiB (~173 MiB total) —
      recorded in ARCHITECTURE §6.1 with an explicit note on what it does **not** prove
- [x] **CI green, all 12 jobs** — run #1 on `main`, 83s, zero failures
      github.com/swaroop-rayker/Tracelet/actions/runs/36302388201
- [x] Docs updated: ARCHITECTURE §6.1–§6.4, §9, §9.1 and the dependency ledger;
      ADR-0002, ADR-0014 cross-refs; CLAUDE.md §6; KICKOFF §4; ERRORS.md E1–E7

**Deviations from the original M0 scope, each with the doc updated in the same change:**
- `scripts/tl` is the task runner and the `Makefile` delegates to it, because `make` is absent
  from Git Bash on the Windows host (ARCHITECTURE §9.1).
- `api-tools` / `web-tools` compose services added behind a `tools` profile, so the running
  `api` image carries no dev dependencies.
- `max_connections` 20 → 24 with an enforced connection budget (ERRORS.md E2).
- `@types/node` added as a dev dependency (ledger updated).
- ACME contact-email setting **removed** rather than defaulted, because an empty value broke
  the Caddy config and a placeholder would have broken certificate issuance on a real domain
  (ERRORS.md E5). Caddyfile and `.env.example` both carry the reasoning.
- `.gitattributes` added, pinning LF for the committed generated artefacts. Without it the
  OpenAPI drift check fails permanently for a Windows developer and passes in CI
  (ERRORS.md E7).

**Bugs found and fixed during M0:** E1–E7 in `docs/ERRORS.md`. E2 (the connection budget) is
the one worth reading: the documented defaults would have left `pg_dump` unable to connect at
peak traffic.

**Two gates did not actually run on M0**, because it was pushed straight to `main`:
- `commitlint` triggers on `pull_request` only, so ES6 conventional-commit enforcement was
  skipped. The commit message does follow the convention, but nothing checked it.
- "One PR per milestone" (CLAUDE.md §3) was bypassed.

From M1 onward, work on a branch and open a PR so both gates execute. The CI workflow already
requires the single `CI` status check, which is the one to put behind branch protection.

**Do not** build a product feature in M0. Its only job is to make M1–M9 cheap.

---

## M1 — Admin auth and account security · size L

**Goal:** the dashboard is protected before it holds anything worth protecting.
**F/AC-IDs:** F8.AC1–F8.AC17, F10.AC9, F13.AC7, F12.AC3, F12.AC5

**Scope**
- `admins`, `sessions`, `admin_recovery_codes`, `admin_enrollment_tokens`,
  `password_reset_tokens`, `auth_challenges`, `audit_log`, `rate_limit_buckets`
- The two database invariants as **engine constraints**: last-owner constraint trigger
  (F8.AC13), `status='active'` requires TOTP (F8.AC4)
- Argon2id **pinned `m=32MiB, t=3, p=1`** — F8.AC1, CLAUDE.md section 5
- Opaque sessions, `__Host-` cookie, `SameSite=Strict`, binding, idle + absolute expiry
- CSRF double-submit + `Origin` validation
- TOTP enrolment, replay prevention via `totp_last_counter`
- 10 recovery codes, Argon2-hashed, shown once
- Telegram bot client + chat verification + reset-link delivery
- `tracelet admin` CLI: bootstrap, list, create, reset-password, reset-totp, telegram-test
- Login rate limiting, lockout, enumeration and timing resistance incl. dummy Argon2
- AES-GCM envelope module + key loading (used here for `totp_secret_enc`, reused in M2)
- Role middleware; `audit_log` append-only enforced by grants
- Frontend: `/enroll`, `/login`, `/recovery`, `/forgot`, `/reset` and the authenticated
  account shell — who am I, change password, recovery codes, Telegram channel, session
  list with revoke, sign out

**Done checklist**
- [x] `tracelet admin bootstrap` prints a one-time enrollment URL; **no default password
      exists anywhere** — the column is `NULL` until the invitee sets one (F8.AC15)
- [x] Full flow: enrol, TOTP, log in, change password, revoke a session — driven end to end
      against the running stack over HTTPS through Caddy, 30 assertions, and covered
      permanently by `tests/integration/test_auth_flow.py`
- [x] Password recovery via Telegram, on the real bot
      (`@swrp_insta_visitor_alert_bot`) — **partially, and the gap is recorded below.**
      Evidenced by the owner's audit trail and by the messages actually received: the
      chat-verification code was delivered, the reset link was delivered, two resets were
      applied, both tokens are spent (`used_at` set), sessions were revoked
      (`sessions_revoked: 1`), and TOTP was still required afterwards. The permanent suite
      covers the logic and intercepts the send — see the deviations for why it does not
      message a real chat
- [x] **`POST /auth/telegram/verify/confirm` completed against the live bot**, from the
      dashboard, on **2026-09-28 11:12:30 UTC** — producing the `admin.telegram_verified`
      row the owner's audit trail had never contained, and setting
      `telegram_chat_id` / `telegram_verified_at` through the endpoint rather than by hand.
      Reset-over-Telegram is now armed for the owner account.

      It had never run. The audit trail showed **no `admin.telegram_verified` row** while
      two password resets had succeeded — which is only possible with a verified chat. The
      previous session had sent the code through the real endpoint, never confirmed it, and
      set both columns **directly in SQL** to reach the reset path it wanted to test.

      **Worth keeping generally:** stubbing state in SQL to reach the code under test
      leaves the *setup* path unverified, and the audit log is where that shows up. A
      hand-run harness reporting "19/19 passed" could not see its own missing row — and
      the gap survived into a handoff document as a completed check.
- [x] Recovery code works, bypasses both factors, and cannot be reused;
      `X-Recovery-Remaining` decrements; a code typed in lower case with spaces is accepted
- [x] `tracelet admin reset-password` works with shell + DB access only, enforces the same
      password policy as the API, writes the same audit row, and leaves the second factor
      alone
- [x] `tracelet admin reset-totp` works **for the sole owner** — it did not, and could not,
      until E17 was fixed: clearing TOTP forces the status out of `active`, which the owner
      trigger refuses. Covered by `tests/integration/test_cli_recovery.py`, whose three
      sole-owner tests fail against the old code
- [x] **Last owner cannot be demoted or disabled** (`409 LAST_OWNER`) — integration tests,
      including two that race real concurrent demotions and deletions. Deletion of the last
      owner answers `422` rather than `409`, for a reason recorded in API.md §5.1
- [x] Login timing for existing vs non-existing accounts is indistinguishable — **measured
      at 1.01x** by hand, asserted within 3x by an integration test, and bodies are
      byte-identical
- [x] Lockout triggers after 8 failures and reports `423 ACCOUNT_LOCKED`; a successful login
      clears the counter
- [x] `audit_log` `UPDATE`/`DELETE` **rejected** for the app role — two integration tests
      attempt both, plus one that reads `information_schema.table_privileges` so a later
      migration cannot silently re-grant them
- [x] A rejected request still records its attempt: failed password, bad code, lockout and
      bad recovery code all leave rows, written on their own connection so they survive a
      rollback (F8.AC16)
- [x] Argon2 parameters asserted as **exact numbers** in a unit test, and the PHC string
      checked for `m=32768,t=3,p=1` — a library default here OOMs the box (CLAUDE.md §5)
- [x] The three engine-level invariants confirmed **in the catalogue**, not just in the
      migration: both constraint triggers present and `DEFERRABLE INITIALLY DEFERRED`, the
      `active` CHECK present, and the `audit_log` grants as intended
- [x] `ruff check`, `ruff format --check`, `mypy --strict` clean across 58 files
- [x] Unit tests: 169. Integration tests: 116, against real PostgreSQL + PostGIS, never
      mocked (ES3) — including fifteen over the break-glass CLI, which nothing covered
      before E17 was found, and three over Telegram delivery failures (E20)
- [x] `tsc --noEmit`, `eslint`, `prettier --check` clean; **no new frontend dependency**
- [x] OpenAPI document and TypeScript client regenerated and committed
- [x] Docs: API.md §4–5 rewritten against the implementation; DATA_MODEL.md §3 with the two
      new tables and the corrected owner invariant; ARCHITECTURE.md §5.5, §5.8, §9 and the
      dependency ledger; ADR-0009 amended; ERRORS.md **E8–E22**

**Deviations from the original M1 scope, each with the doc updated in the same change:**

- **`auth_challenges` added.** Not in the Gate-3 design at all. The MFA challenge, the
  enrolment confirm token and the chat-verification code were held in a module-level dict,
  which with two Gunicorn workers made login a coin flip (ERRORS.md E10). Migration `0003`,
  DATA_MODEL §3.7.
- **`password_reset_tokens` documented as its own table** (DATA_MODEL §3.4). Implied by
  F8.AC7 but never written down; kept separate from enrolment because the lifetimes and
  delivery paths genuinely differ.
- **`rate_limit_buckets` pulled forward from M7.** Login limiting (F8.AC9) needs shared
  state across workers from the first milestone that has a login, and the same table serves
  the L2 limiter in M2 (ADR-0010). DATA_MODEL §8.7.
- **Password reset is sent synchronously, not through the outbox.** ADR-0009 listed
  `telegram.password_reset` as an outbox kind; the outbox exists for visit/notification
  atomicity (NFR5.AC2), which a reset has no equivalent of, and the admin is waiting on a
  30-minute link. The ADR is **amended** with the reasoning and an explicit condition for
  revisiting, rather than left contradicting the code.
- **`/totp/enroll` became `/enroll`**, and `/totp/confirm` now takes a `confirm_token` and
  returns a session. Enrolment is the unauthenticated first use of a one-time link, and the
  account cannot hold a session until TOTP is confirmed — so the token is what identifies
  who is confirming. API.md §4.1.
- **`/auth/telegram/verify/start` and `/confirm` replace
  `POST /admins/{id}/telegram/verify`.** Verification is two steps by nature, and it is a
  self-service action on your own recovery channel rather than an owner administering
  someone else. API.md §4.1.
- **No QR code at enrolment.** Rendering one means another dependency for one or two manual
  entries; the page shows the base32 secret and the `otpauth://` URI instead. Recorded as a
  rejection in the ledger.
- **No `react-router` and no `zod` yet.** Five flat routes use a 60-line `src/router.ts`,
  and the six auth payloads are narrowed by hand. Both dependencies arrive in M5 where they
  pay for themselves. Ledger updated.
- **`email-validator` rejected.** `pydantic.EmailStr` pulled it in along with `dnspython`
  and crash-looped the API (ERRORS.md E9). This system sends no email at all, so an admin
  address is purely a login identifier.
- **`tracelet admin reset-totp` behaves differently for the last active owner.** It leaves
  the account active and lets the enrollment link replace the secret, because clearing in
  place is impossible for that account (ERRORS.md E17). The strict clear-in-place behaviour
  is kept for everyone else.
- **`SELECT ... FOR UPDATE` added to the owner routes.** A bug found while writing the
  concurrency test the E14 fix implied: `SET CONSTRAINTS ALL IMMEDIATE` had inadvertently
  disabled the protection the deferred trigger existed for (ERRORS.md E16).
- **The permanent suite does not message a real Telegram chat.** Delivery was verified by
  hand against the live bot; in the suite the one test that asserts the success branch
  intercepts `telegram.send_message`. CI has neither a token nor a route to Telegram, and a
  test that messages a real person is one nobody runs twice. Not a database double — ES3 is
  untouched.
- **The integration suite manages the admin table it shares.** It creates accounts under the
  reserved `example.test` domain and deletes them afterwards, and the last-owner assertions
  temporarily disable any *other* active owner, restoring it in a `finally`. The rule is a
  statement about the whole table, so it cannot be asserted while a real owner sits in it.
  Documented at the top of `tests/integration/conftest.py`, including the one-line psql
  command that repairs a killed run.

**SPEC F8.AC9 was amended rather than the code changed** — proposed during M1, decided by
the repository owner on 2026-09-28, recorded as row 6 of SPEC section 11.

Two clauses did not describe what was built:

1. *"Over-limit responses are indistinguishable from wrong credentials."* They are not, and
   should not be: F11.AC10 requires `Retry-After` on a 429, the error catalogue approved at
   Gate 3 documents both `RATE_LIMITED` and `ACCOUNT_LOCKED`, and a limit a legitimate admin
   cannot see is one they keep retrying into. The property actually wanted is enumeration
   resistance, now stated as **"must not differ between an existing and a non-existing
   account"** — which holds by construction, because the bucket is keyed on the submitted
   identifier before any lookup. Now asserted by
   `test_an_over_limit_response_is_the_same_for_a_known_and_unknown_account`.
2. *"with exponential backoff."* GCRA produces a `Retry-After` that grows with how far the
   caller is over the sustained rate, and the lockout is a flat 30 minutes. Per-attempt
   exponential backoff was considered and **not** wanted, so the wording now describes what
   GCRA does.

No behaviour changed. The code already matched API.md and the tests; it was the requirement
text that had drifted from both.

**Known state, carried into M2:** the bot token in `.env` should be rotated in @BotFather
(`/revoke`, then `/token`). It was handled in plaintext during development and was written
to the log in clear text until E19 was fixed, and it is a password-recovery channel
(RISKS R18). E19 is deployed, so a replacement token will not be logged.

The owner's Telegram chat **is** verified as of 2026-09-28, so all three recovery routes —
codes, Telegram, CLI — are live for that account. The bot token in `.env` should also be rotated in @BotFather: it
was handled in plaintext during development, and it is a password-recovery channel
(RISKS R18).

---

## M2 — Capture path, server-authoritative · size L

**Goal:** a visit is recorded no matter what the client does. **Depends on Spike B.**
**F/AC-IDs:** F1.AC1–F1.AC10, F2.AC1–F2.AC14, F3.AC1, F3.AC5, F3.AC6, F3.AC7,
F12.AC1–F12.AC4, F11.AC1–F11.AC4, F11.AC8, F11.AC10, F13.AC3, F13.AC6, F15.AC7

**Scope**
- `links` table + CRUD + **partial unique index for exactly-one-default** (F1.AC3)
- `destination_url` validation; **no open redirect** (F1.AC7)
- `visits` table, full schema, all indexes including the sweeper and purge partials
- `GET /r/{slug}`: server signals, **commit before response**, 200 HTML never 302
- Jinja2 capture page: zero-JS, inline CSS, notice, Continue, `<noscript>` refresh,
  hidden honeypot
- Enrichment: single-use HMAC nonce, `POST /api/v1/s/{nonce}`,
  `fetch(keepalive)`, redirect timer
- **90 s sweeper** + stuck-visit detection
- Crawler gate: `facebookexternalhit`, LinkedInBot, Twitterbot, redditbot, and the rest
- In-app webview detection + open-in-browser affordance
- **IP handling: HMAC + prefix + AES-GCM ciphertext. No plaintext column exists**
- IP TTL purge job; owner-only decrypt endpoint with audit row
- L1 Caddy limits; L2 GCRA in PostgreSQL; **over-limit still redirects**
- Cloudflare real-IP extraction, trusted only from verified CF ranges
- CI route-name assertion (F2.AC11)

**Done checklist**
- [ ] Visit recorded **with JavaScript fully disabled** — the `<noscript>` path works
- [ ] Visit recorded from a real Instagram bio link on a real phone
- [ ] Enriched visit records screen, GPU, CPU, timezone, consent state
- [ ] Nonce replay returns 410; nonce from a different prefix returns 410
- [ ] Sweeper finalises an abandoned visit at 90 s as `server_only`
- [ ] Instagram prefetch appears as `crawler`, excluded from default views
- [ ] Rate-limited request **still redirects**, recorded `stage='rate_limited'`
- [ ] Forged `CF-Connecting-IP` from a non-Cloudflare peer is **ignored** — integration test
- [ ] **No plaintext IP anywhere** in schema, logs, or API responses — grep + test asserted
- [ ] IP purge nulls `ip_enc` and leaves `ip_hmac`/`ip_prefix` intact
- [ ] Owner decrypt writes an audit row; analyst gets 403
- [ ] CI route-name job passes and **fails on a deliberately-bad route name**
- [ ] Docs: DATA_MODEL section 4–5, API sections 3, 6, 7 verified

---

## M3 — Location inference engine · size L

**Goal:** the B1 fix, with a visible derivation trail. **Depends on Spike A.**
**F/AC-IDs:** F4.AC1–F4.AC12, F4.AC14, F4.AC16, F4.AC17, F4.AC18, F3.AC4, F11.AC7

**Scope**
- Geo-database installation, checksum verification, atomic swap, `geo_databases` table
- Readers: GeoLite2 + IPinfo Lite + DB-IP Lite via `geoip2`; IP2Location LITE via its lib
- **Offline GeoNames reverse geocode** — grid bucket + haversine, **pure Python, no numpy**
- Sources S1–S11 as independent producers with per-source timeouts
- **rDNS city-code lexicon** (`rdns_city_codes`) seeded from a versioned data file
- **`asn_profiles` computation incl. `modal_share`** — the registry-artifact detector
- Weighted consensus; `visit_candidates` persisting **winners, losers and suppressed**
- **The three suppression rules** — registry artifact, mobile/CGNAT, hosting/VPN/Tor
- Dual strict/advisory output, per-level confidence, `abstain_reason`
- `inference_settings` versioned config + rollback
- S9 external APIs: per-source toggle, prefix cache, timeout, circuit breaker
- Nominatim client at 1 rps, cached, descriptive UA, consented visits only
- S10 latency triangulation, **implemented and flag-off**

**Done checklist**
- [ ] A real visit shows the full derivation table: every source, weight, accepted or
      suppressed with reason, latency
- [ ] **Registry-artifact suppression demonstrated** on a real Indian broadband IP that the
      databases place in the NCR — city collapses to admin1 with the reason recorded
- [ ] Mobile ASN visit emits **no** city
- [ ] Hosting ASN visit abstains on all strict levels
- [ ] Consented visit resolves street address; **non-consented stores no coordinates** —
      `CHECK` constraint proven
- [ ] Disabling every source still redirects and records `country=NULL` + reason (F4.AC18)
- [ ] External API timeout opens the breaker; inference completes on remaining sources
- [ ] Settings version bump then rollback, both audit-logged
- [ ] Geo-database update succeeds; a **deliberately corrupted** download leaves the
      previous version serving
- [ ] Docs: ARCHITECTURE section 3, DATA_MODEL sections 5.4, 8.1, 8.2 verified

---

## M4 — Anti-spoofing and classification · size L

**Goal:** know what is human, and be able to explain why.
**F/AC-IDs:** F5.AC1–F5.AC14, F3.AC2, F3.AC3, F12.AC6

**Scope**
- Canonical fingerprint with **stability bucketing**; `visitor_id`, `session_fp`,
  `fingerprint_id` (three peppers, ADR-0006)
- Server rules: UA denylist, header-order hash, missing `Accept-Language`, HTTP/1.0,
  `Accept: */*`, TLS mismatch, hosting ASN, rDNS scanner patterns, rate anomaly,
  malicious ASN/UA lists
- Client rules: `webdriver`, missing `window.chrome`, SwiftShader/Mesa/llvmpipe,
  zero screen dims, empty plugins, permissions anomaly, font count, CDP artefacts
- **Cross-checks:** GPU vs OS, **`deviceMemory` + iOS UA**, UA-CH vs UA, tz vs country,
  screen vs device class, impossible travel
- **Honeypot** endpoint + hidden link + hidden field
- **Fingerprint collision → proxy**, and the inverse → NAT/gateway, **not** proxy
- Header-order and HTTP/2 fingerprinting (the JA4 substitute)
- Datacenter/VPN/Tor from ASN type, org keywords, rDNS, curated lists
- `agreement_score`, `conflict_score`, `bot_score`, `spoof_score`, `signals` JSONB + GIN
- `classifier_version` stamping; thresholds in versioned config

**Done checklist**
- [ ] Headless Chrome against a real link is classified with the specific reasons listed
- [ ] `curl` and a Python `requests` client are both caught by header-order alone
- [ ] Spoofed UA (Windows UA + Apple GPU) raises `spoof_score` with the cross-check named
- [ ] `deviceMemory` + iOS UA flagged
- [ ] Honeypot hit classifies as automation
- [ ] Same fingerprint across 3+ ASNs sets `is_proxy_suspected`
- [ ] **Many fingerprints behind one prefix classified as NAT, not proxy** — the regression
      that would otherwise misclassify most Indian mobile traffic
- [ ] Every verdict shows its fired rules with weights in the UI
- [ ] A `server_only` visit still classifies from server signals alone
- [ ] Docs: ARCHITECTURE section 4 verified

---

## M5 — Dashboard analytics and visualisation · size L

**Goal:** the data becomes legible.
**F/AC-IDs:** F9.AC1–F9.AC20, F14.AC9, NFR2.AC4, NFR7.AC1–NFR7.AC3

**Scope**
- `rollup_visit_daily`, `rollup_visit_hourly`, nightly refresh job
- Analytics endpoints; **every response carries `stage_mix`** (F9.AC20)
- Visit list with cursor pagination; visit detail with candidates + signals
- Timeline, summary KPIs with period-over-period, time series, breakdowns
- Geo map (choropleth + clustered points), calendar heatmap, **source-flow Sankey**,
  stage funnel, confidence histograms, signal-frequency chart, returning-visitor view
- Composable URL-shareable filters
- Streaming CSV/NDJSON export, **no plaintext IP**
- Three themes, semi-dark default, WCAG AA verified
- **Mandatory empty / loading / error states on every panel** (F9.AC18 — the B5 defence)
- Generated TS client + zod schemas, CI drift check

**Done checklist**
- [ ] Every chart in F9.AC1–F9.AC12 renders with real data
- [ ] **No panel can render blank** — empty, loading and error states all exercised by test
- [ ] Filters compose; a shared URL reproduces the exact view
- [ ] Dashboard API p95 under 300 ms **reading rollups** on target-equivalent hardware
- [ ] Export streams and contains no plaintext IP
- [ ] All three themes pass WCAG AA contrast; no chart relies on colour alone
- [ ] Keyboard navigation works across all controls
- [ ] OpenAPI drift check fails on a deliberate response-model change
- [ ] Docs: API section 8 verified

---

## M6 — Geofencing and Telegram notifications · size M

**Goal:** draw a boundary, cross it, get the alert.
**F/AC-IDs:** F6.AC1–F6.AC10, F7.AC1–F7.AC9, F10.AC13, NFR5.AC2

**Scope**
- `geofences` with `geography(Polygon,4326)`, GiST, `ST_IsValid` + vertex-cap constraints
- Leaflet + Geoman drawing: polygon and circle, vertex edit, drag, delete
- Circle round-trip via retained `center` + `radius_m`
- `ST_Covers` evaluation on finalisation; priority resolution; `matched_geofence_ids`
- **`geofence_state='undetermined'` when `geopoint IS NULL`** (F6.AC6)
- GeoJSON import/export; coordinate test endpoint
- `outbox` + worker: `SKIP LOCKED`, backoff + jitter, dead-letter, manual retry
- **`dedup_key` unique constraint implementing 24h dedup** (F7.AC2)
- Inside = high priority, outside = normal, automated = never (F7.AC1, F7.AC3)
- Quiet hours; Telegram test-message control
- Advisory-locked scheduler

**Done checklist**
- [ ] Draw a polygon and a circle; both round-trip through edit without distortion
- [ ] Self-intersecting ring rejected with the specific error code
- [ ] Physically enter a geofence and **receive the high-priority alert on Telegram**
- [ ] Outside visit receives a normal alert
- [ ] Bot/crawler visit receives **nothing**
- [ ] Second visit from the same visitor within 24h sends **nothing** — proven concurrently
- [ ] **Abstaining inference yields `undetermined`, not `outside`** — integration test
- [ ] Telegram outage: job retries, dead-letters, is visible, and manual retry delivers
- [ ] **Rolled-back visit emits no notification** — transactional atomicity test (NFR5.AC2)
- [ ] Geofence evaluation under 5 ms at p95
- [ ] Docs: DATA_MODEL sections 6, 7 and API section 9 verified

---

## M7 — System health and operations · size L

**Goal:** the system is operable without a shell.
**F/AC-IDs:** F10.AC1–F10.AC15, F12.AC7–F12.AC13, F11.AC9, F15.AC6

**Scope**
- `psutil` metrics from host `/proc` and `/sys` mounted read-only
- **Temperature renders `N/A` with a reason on GCP** (RW-5)
- DB size, uptime, load, swap, disk with a threshold banner
- Geo-database panel: versions, staleness verdict, update control, memory-capped subprocess
- **Per-source inference toggles + the inference flow diagram** with optional sample-visit
  overlay (F10.AC8)
- Link management surfaced here (F10.AC6)
- Retention config + **dry-run preview** + batched audit-logged purge
- Backups: list, manual trigger, download, **monthly automated restore-verify**
- Outbox panel; rate-limit config; degradation banner
- Admin management and profile UI
- Full graceful-degradation matrix implemented and tested (F15.AC6)

**Done checklist**
- [ ] All metrics real and reflecting the **host**, not the container
- [ ] Temperature `N/A` with reason on GCP; real on the WSL box
- [ ] Flow diagram shows enabled sources and what fired for a chosen visit
- [ ] Toggling a source changes behaviour **without a restart**
- [ ] Retention preview counts **exactly** match what the purge then deletes
- [ ] Manual backup, download, and **automated restore-verify all pass**
- [ ] Dead-lettered job visible and retryable from the UI
- [ ] Every row of the F15.AC6 degradation matrix exercised — **each one still redirects**
- [ ] Geo-database update under memory cap; failure leaves previous version serving
- [ ] Docs: API section 10, DATA_MODEL sections 8, 11 verified

---

## M8 — Accuracy hardening and ground truth · size M

**Goal:** turn SC3 from a claim into a measured number.
**F/AC-IDs:** F4.AC13, F4.AC15, F4.AC17, F9.AC10, F14.AC12, RW-6

**Scope**
- `ground_truth_labels`, `accuracy_runs`
- Labelling UI + CLI; **candidate ranking by `conflict_score`** so scarce labels buy the
  most information
- Metrics: precision + coverage per level, split consented vs not, **per source**
- CI accuracy job failing on regression below F4.AC13 targets
- Dashboard accuracy panel with **`label_count` beside every figure**
- Threshold tuning against real labels; lexicon expansion from observed PTR records
- **Measure S10 latency triangulation** and decide whether default-off stands (RW-6)
- Re-run inference on retained ciphertext IPs to validate tuning (the ADR-0007 payoff)

**Done checklist**
- [ ] 30+ labels collected across Airtel, Jio, ACT, BSNL, Vi; Wi-Fi and mobile data;
      VPN on and off
- [ ] Accuracy metrics computed, stored, and displayed with sample size
- [ ] CI job fails on a deliberately-regressed threshold
- [ ] Per-source accuracy reported; any consistently-wrong source down-weighted **with
      evidence from `visit_candidates`**, not intuition
- [ ] S10 measured; decision recorded in RISKS and, if changed, an ADR-0005 amendment
- [ ] **F4.AC13 targets either met or formally amended in SPEC section 11 with data**
- [ ] Docs: SPEC F4.AC13 reconciled with measured reality

---

## M9 — Production hardening and deploy · size M

**Goal:** SC1 and SC2 on the real URL.
**F/AC-IDs:** F13.AC1–F13.AC8, F14.AC2, F14.AC3, NFR1.AC1–NFR1.AC5, NFR2, NFR6, SC1, SC2

**Scope**
- Domain, DNS, Cloudflare setup; `trusted_proxies`; verify `CF-Ray` and `CF-IPCountry`
- Free-subdomain mode verified as the documented degraded path
- Security headers and CSP verified by test, not inspection
- **Browser-trust checklist** (F13.AC8): Search Console, Safe Browsing review request,
  no shortener in the chain, reachable privacy page
- Load test at NFR1 with headroom; record results
- Degradation drills: DB down, Telegram down, geo-DB update failure, external API timeout,
  disk near-full, swap pressure
- **One-time manual restore drill onto a fresh VM, including the out-of-band secrets**
- Operations runbook; backup-download reminder
- Final doc reconciliation pass

**Done checklist**
- [ ] All in-scope flows work end to end on the deployed URL (**SC1**)
- [ ] Fresh clone to running in 5 commands (**SC2**)
- [ ] CI green (**SC2**)
- [ ] Load test: 10 concurrent sustained, 500/day, 2 admins — NFR1 met, results recorded
- [ ] NFR2 p95 targets measured on production hardware
- [ ] Steady-state RSS at or below 700 MB; headroom confirmed (NFR6.AC1)
- [ ] Every degradation drill leaves **the redirect working**
- [ ] **Restore drill onto a fresh VM succeeds, with `ip_enc` readable** — proving the
      out-of-band secret backup actually works (ADR-0014)
- [ ] Safe Browsing review submitted; outcome recorded in RISKS R8 **whatever it is**
- [ ] Every doc reconciled with the deployed system
- [ ] **SC4**: every ADR still accurately describes what was built

---

## Definition of done — applies to every milestone

1. All linked AC-IDs implemented and demonstrable
2. `make verify` green: lint, format, `mypy --strict`, `tsc --noEmit`, unit, integration
3. Integration tests run against **real PostgreSQL + PostGIS**, never a mock (ES3)
4. Docs updated **in the same PR** — schema to DATA_MODEL, interfaces to API,
   architecture to ARCHITECTURE, decisions to a **new ADR** (CLAUDE.md section 2)
5. This file ticked
6. Any bug found and fixed on the way recorded in `docs/ERRORS.md` (F15.AC8)
7. Conventional commits; one PR; CI green before merge
8. Demoable: you can show it working, not describe it working
