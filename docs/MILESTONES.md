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
| M2 | Capture path, server-authoritative | L | **[x] done** — 631 tests, Spike B run (Android), 4 bugs recorded as E23–E26 |
| M3 | Location inference engine | L | **[x] done** — 738 tests (463 unit, 275 integration); real-visit check deferred to M9 (owner decision); 5 bugs recorded as E27–E31 |
| M4 | Anti-spoofing and classification | L | **[x] done** — 10 of 10 items (the UI item ticked in M5); bugs E32–E33; R19 and R21 closed |
| M5 | Dashboard analytics and visualisation | L | **[x] done** — 10 of 10 items; rollups p95 ≤ 70 ms at design load; raw fallback slow on long windows (R25); bugs E34–E36 |
| M5.5 | Design system and UI polish (owner-directed 2026-10-02, approved 2026-10-03; docs/DESIGN.md) | L | **[x] done** — 11 of 11 items; CSP 0 in Chromium, Firefox and WebKit; 72-image matrix; bugs E41–E47; hand keyboard walkthrough by the owner 2026-10-07 |
| M5.6 | Dashboard enhancements and the Settings redesign, UI only (owner-approved 2026-10-06; docs/plans/ENHANCEMENTS-PLAN.md Phase A, SETTINGS-REDESIGN-PLAN.md) | M | **[x] built** — dashboard 6/6, Settings 6/6; CSP 0 in three engines; no server change; bugs E48–E50 |
| M6 | Geofencing and Telegram notifications | M | **[x] done** — 11 of 11; both Telegram alerts received on the owner's phone 2026-10-07 (inside: high, as the same-day upgrade); bugs E52–E65 |
| M7 | System health and operations | L | **built** — 10 of 10 items, plus the owner's additions (SPEC §11 rows 24–25); CSP 0 in three engines; ADR-0022; bugs E66–E71 (E69 cleared 164 dev IP ciphertexts) |
| M8 | Accuracy hardening and ground truth | M | [ ] |
| M9 | Production hardening and deploy | M | [ ] |

**Spikes (run during M0/M1, before the milestones that depend on them):**

| | Spike | Blocks | Status |
|---|---|---|---|
| Spike A | rDNS city-code coverage for Indian residential IPs (RISKS R3) | **M3** | [x] 2026-09-29 — 2.0 %, below the 30 % line |
| Spike B | `fetch(keepalive)` survival in the Instagram webview (RISKS R5) | **M2** — runs inside M2 against the real capture page (owner decision 2026-09-28) | **[x] Android: survives. iOS unmeasured** — R5 |

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
- **CI no longer runs twice per push.** A push to a PR branch matched both the `push`
  and `pull_request` triggers, running the full 12-check suite twice — about nine minutes
  of Actions time for one commit's worth of signal, against a free-tier budget. `push` is
  now scoped to `main`; `workflow_dispatch` covers a branch with no PR open yet.
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

**Goal:** a visit is recorded no matter what the client does. **Spike B runs inside M2** —
owner decision 2026-09-28: the server-authoritative core is identical whatever the spike
finds, and the spike needs a real capture page on a public URL to test anything.
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
- [x] A visit is recorded with **no JavaScript at all** — integration test, and `curl`
      against the running stack (a client that runs no script)
- [x] The `<noscript>` refresh redirects **in a real browser with JavaScript disabled** —
      Android Chrome, Spike B: visit recorded, swept to `server_only`, visitor landed on
      the destination
- [x] Visit recorded from a real Instagram bio link on a real phone — Samsung, Android 16,
      Spike B. **Enriched**, 2.4 s after the visit, i.e. after the redirect. **iOS not
      tested** — no iPhone available; RISKS R5 stays open for it
- [x] Enriched visit records screen, GPU, CPU, timezone, consent state **from a real
      browser** — Instagram webview, Android Chrome and desktop Chrome, all complete
- [x] Nonce replay returns 410; a nonce from a different prefix returns 410 — plus five
      concurrent submissions of one nonce producing exactly one 204
- [x] Sweeper finalises an abandoned visit at 90 s as `server_only` — tested, and observed
      on the running stack at 109 s (the 30 s tick puts it between 90 and 120)
- [x] A Meta preview fetcher appears as `crawler` and is excluded from default views —
      with the real `facebookexternalhit` user agent, and live in Spike B: four Meta preview
      fetches, all `crawler`. **But a JavaScript-running Meta scanner was not caught** —
      RISKS R21, a requirement on M4
- [x] Rate-limited request **still redirects** (302), recorded `stage='rate_limited'` with
      nothing the client supplied
- [x] Forged `CF-Connecting-IP` from a non-Cloudflare peer is **ignored** — integration
      tests in direct mode and behind Cloudflare, plus the positive case from a real edge
- [x] **No plaintext IP anywhere**: the schema has no column for one; the whole stored row
      is searched after sending the address through six headers, the referer and a UTM
      value; the API detail view; the application log **before** redaction (`caplog`); and
      Caddy's edge log, which *did* hold every address and is now filtered (ERRORS.md E26)
- [x] IP purge nulls `ip_enc` and leaves `ip_hmac`/`ip_prefix` intact
- [x] Owner decrypt writes an audit row; analyst gets 403; a transplanted ciphertext fails
      to open (the AAD is the visit id)
- [x] CI route-name job passes and **fails on a deliberately-bad route mounted on a real
      application**, not only on a string
- [x] `ruff`, `mypy --strict` clean. Unit tests: 400. Integration tests: 231, against real
      PostgreSQL + PostGIS (ES3)
- [x] Docs: DATA_MODEL sections 4–5, API sections 3, 6, 7 and 13 verified against the
      implementation; ARCHITECTURE 2.1, 5.6, 6.3, 8, 9; RISKS R5, R7, R19, R20;
      ERRORS.md E23–E26

**Deviations from the original M2 scope, each with the doc updated in the same change:**

- **`request_headers jsonb` added to `visits`.** F3.AC1 requires the full header set and
  the Gate-3 model had nowhere to put it. Sanitised before storage — it is the one column
  that could otherwise carry a plaintext address. DATA_MODEL 5.1.
- **`header_order_hash` is never populated**, because header order cannot be observed
  behind Caddy — measured, not assumed. This removes the JA4 substitute that SPEC F5.AC8
  (amendment 5) and RISKS R7 depend on. **Needs an owner decision before M4** — RISKS R19
  has the options and a recommendation.
- **ASN, ISP, rDNS and connection class** have columns from M2 and values from M3, which
  installs the offline databases and the resolver budget they come from.
- **Three CHECK constraints beyond the Gate-3 model:** `stage='server'` exactly when
  unfinalised; ciphertext and key version present or absent together; an archived link is
  never the default. `is_datacenter` and its siblings are nullable, not `false` by default.
- **The capture route owns its own transaction** rather than using the request middleware,
  which would turn a failed commit into a JSON 500 for a visitor. ARCHITECTURE 2.1.
- **A per-worker cache of live links**, consulted only when the database cannot answer, so
  a database outage still redirects a link this worker has served before. RSS stated in
  ARCHITECTURE 6.3.
- **`tracelet.net` now verifies the Cloudflare edge for the admin API as well.** M1 left a
  placeholder that deliberately did not read `CF-Connecting-IP` at all.
- **Enrichment accepts the whole documented payload but does not yet persist `probes`**,
  and the page sends no audio hash. What a headless probe *means* is M4's decision.
- **`visit_candidates` is created in M3**, with the engine that writes it.
- **The visits list implements the filters M2 has data for**; the rest arrive with their
  columns. API section 7.
- **Caddy:** L1 slow-loris and header-size limits; the client's HTTP and TLS versions
  forwarded to the application; and the access log filtered (E26).
- **The default link's behaviour is unspecified.** The invariant is enforced; what the
  default *does* is not written anywhere. Raised, not invented.

**Open for the owner:**

1. ~~**R19 — amend F5.AC8**~~ **Decided 2026-09-29:** header set + re-weighting, SPEC
   section 11 row 7. Built in M4.
2. ~~**R20 — the location prompt cannot be answered inside the interstitial.**~~
   **Decided 2026-09-29:** never prompt; read an existing grant. SPEC section 11 row 8,
   applied at the start of M3.
3. ~~**What the default link is for.**~~ **Decided 2026-09-29:** the bare `/r` and `/r/`
   capture through it. SPEC section 11 row 10, applied at the start of M3.
4. **R21 — the Meta scanner.** No decision needed now; it is a stated requirement on M4.

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
- ~~S10 latency triangulation, implemented and flag-off~~ — **dropped** (SPEC §11 row 12)

**Done checklist**
- [ ] A real visit shows the full derivation table: every source, weight, accepted or
      suppressed with reason, latency
      — ***deferred to M9** (owner decision 2026-09-29).* Locally the visitor is the Docker
      gateway, because `CF-Connecting-IP` is trusted only from a verified Cloudflare peer
      (F13.AC6) and a quick tunnel's peer is local; a development-only trust setting was
      declined. The same pipeline has run end to end on a real address inside the
      production image (step 2); what is deferred is a *captured* visit.
- [x] **Registry-artifact suppression demonstrated** on a real Indian broadband IP that the
      databases place in the NCR — city collapses to the country with the reason recorded
      — *Tikona (AS45528) address placed in Delhi, real DB-IP data and the production
      readers + consensus (a script, not a captured visit): admin1 and city abstain with
      `registry_artifact`, advisory still Delhi. 2026-09-29.*
- [x] Mobile ASN visit emits **no** city — *Jio (AS55836), real data, same method: `mobile_asn`, no city even advisory*
- [x] Hosting ASN visit abstains on all strict levels — *AWS Mumbai (AS16509), real data, same method: `hosting_asn` on every level*
- [x] Consented visit resolves street address; **non-consented stores no coordinates** —
      `CHECK` constraint proven
      — *`test_a_consented_visit_gets_a_street_address`,
      `test_a_visit_without_consent_is_never_sent_to_nominatim`; the CHECK is proven by
      `test_the_engine_itself_refuses_coordinates_without_consent` (M2)*
- [x] Disabling every source still redirects and records `country=NULL` + reason (F4.AC18)
      — *`test_with_every_source_disabled_the_visit_still_abstains_with_reasons`; the
      redirect is independent of inference by construction (ADR-0015)*
- [x] External API timeout opens the breaker; inference completes on remaining sources
      — *`test_timeouts_open_the_breaker_and_inference_completes_without_it`*
- [x] Settings version bump then rollback, both audit-logged — *integration tests, `test_inference_settings.py`*
- [x] Geo-database update succeeds; a **deliberately corrupted** download leaves the
      previous version serving
      — *seven real databases installed 2026-09-29; corruption, truncation, bad checksum,
      size cap and 404 fallback in `test_geodb_installer.py`*
- [x] Docs: ARCHITECTURE section 3, DATA_MODEL sections 5.4, 8.1, 8.2 verified — *against the
      code at `b6aa0e0`; section 3.1 and each DATA_MODEL "as built" note were written with it*

**Progress — step 1 of 3 (2026-09-29): the engine, without the databases.**

Built and tested: migration 0005 (candidates, reference tables, versioned settings, the
inference queue); ADR-0015 (inference is a scheduled job, not part of a request);
weighted consensus with family-discounted agreement, hierarchical strict output and the
three suppression rules (unit-tested, including B1's own Faridabad case); S1 (point
only), S6 with the E27 resolver canary and PTR masking, S7 classification, S8, S11;
settings read / new version / rollback, owner-only and audited, with the table
privilege-protected; `candidates[]` in visit detail.

Not yet: S2–S5 and the installer (step 2 — DB-IP Lite and GeoNames download approved; the
MaxMind, IP2Location and IPinfo keys are the owner's to add to `.env`); GeoNames reverse
geocoding, without which S1 names no place; `asn_profiles` computation, without which
rule (a) cannot fire on real traffic; S9 and Nominatim (step 3). None of the checklist
items above is ticked: each needs real databases or a real visit.

**Raised for the owner during step 1, and decided:** RISKS R22 — the artifact collapse is
to country (SPEC §11 row 11); R23 — S10 dropped (row 12).

**Progress — step 2 of 3 (2026-09-29): the offline databases.** Installer (staging, size
cap, SHA-256, vendor checksum, memory-capped validation, atomic swap), memory-mapped
readers for S2–S5 and the two ASN files, GeoNames reverse geocoding and name
canonicalisation (+9 MB per worker, measured), `asn_profiles`, the daily
`geodb_update` job and `tracelet geodb status|update|profiles`. All seven databases
installed in development. The full job ran end to end on a real Airtel Bengaluru
address: four database candidates, network classified, every silent source recorded
with its reason, no plaintext address in the row.

Calibrated on real lookups: the admin1 defaults were raised (threshold 0.75; priors
0.72 / 0.68 / 0.68), because with three agreeing databases the state could never be
strict — which would have made F4.AC13's admin1 coverage unreachable. **This development
database still runs settings version 1 from before that change** (versions are
immutable); the new values reach it as version 2 through the settings API, an owner
action. Found and fixed: E30 (MaxMind's country centroid read as a placement), E31
(credentials in a repr). Raised: RISKS R24 (a regional ISP looks like a registry
collapse).

**Progress — step 3 of 3 (2026-09-29): outbound.** S9 on ipwho.is only (ip-api.com
is HTTP-only and non-commercial; RISKS R2), asked about the /24 network address and cached
by prefix; Nominatim street addresses for consented visits, in-memory cache; shared GCRA
budgets and per-process circuit breakers (`inference/outbound.py`); the privacy page
discloses both and carries the ODbL attribution.

A captured real visit was deferred to M9 by the owner (see the first checklist item).
`tracelet inference reset-defaults` saves the built-in defaults as a new, audited version;
this development database now runs them as version 31.

---

## M4 — Anti-spoofing and classification · size L

**Goal:** know what is human, and be able to explain why.
**F/AC-IDs:** F5.AC1–F5.AC14, F3.AC2, F3.AC3, F12.AC6

**Scope**
- Canonical fingerprint with **stability bucketing**; `visitor_id`, `session_fp`,
  `fingerprint_id` (three peppers, ADR-0006)
- Server rules: UA denylist, **header set** (was "header-order hash"; SPEC §11 row 7), missing `Accept-Language`, HTTP/1.0,
  `Accept: */*`, TLS mismatch, hosting ASN, rDNS scanner patterns, rate anomaly,
  malicious ASN/UA lists
- Client rules: `webdriver`, missing `window.chrome`, SwiftShader/Mesa/llvmpipe,
  zero screen dims, empty plugins, permissions anomaly, font count, CDP artefacts
- **Cross-checks:** GPU vs OS, **`deviceMemory` + iOS UA**, UA-CH vs UA, tz vs country,
  screen vs device class, impossible travel
- **Honeypot** endpoint + hidden link + hidden field
- **Fingerprint collision → proxy**, and the inverse → NAT/gateway, **not** proxy
- ~~Header-order and HTTP/2 fingerprinting (the JA4 substitute)~~ — **header-set** fingerprinting
  instead; order is unobservable behind Caddy (RISKS R19, SPEC §11 row 7)
- Datacenter/VPN/Tor from ASN type, org keywords, rDNS, curated lists
- `agreement_score`, `conflict_score`, `bot_score`, `spoof_score`, `signals` JSONB + GIN
- `classifier_version` stamping; thresholds in versioned config

**Done checklist**
- [x] Headless Chrome against a real link is classified with the specific reasons listed
      — *real Playwright Chromium through Caddy, 2026-10-02: bot 100 on eight rules; with a
      real Chrome UA and `webdriver` hidden, still bot 100 on six (ARCHITECTURE 4.1)*
- [x] `curl` and a Python `requests` client are both caught by the **header set** alone
      (amended from "header-order", SPEC §11 row 7) — *`test_an_http_library_wearing_a_chrome_ua_is_caught_by_its_header_set_alone`,
      `test_a_library_with_a_browser_ua_is_a_bot_from_its_headers`*
- [x] Spoofed UA (Windows UA + Apple GPU) raises `spoof_score` with the cross-check named
      — *`test_a_windows_ua_with_an_apple_gpu_is_spoofed`*
- [x] `deviceMemory` + iOS UA flagged — *`test_device_memory_under_an_ios_ua_is_flagged`*
- [x] Honeypot hit classifies as automation — *`test_a_honeypot_hit_classifies_as_automation` (real `/api/v1/hp` hit)*
- [x] Same fingerprint across 3+ ASNs sets `is_proxy_suspected` — *`test_one_device_on_three_networks_is_a_proxy`*
- [x] **Many fingerprints behind one prefix classified as NAT, not proxy** — the regression
      that would otherwise misclassify most Indian mobile traffic — *`test_many_devices_behind_one_prefix_is_a_gateway_not_a_proxy` (unit and integration)*
- [x] Every verdict shows its fired rules with weights in the UI — *the API serves them
      (`signals[]` with category and weight, `client.probes`); the UI is M5's, and this
      item was ticked there, 2026-10-02*
- [x] A `server_only` visit still classifies from server signals alone — *`test_a_server_only_visit_classifies_from_server_signals_alone`*
- [x] Docs: ARCHITECTURE section 4 verified — *4.1 written with the code; the diagram's header line amended*

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
- [x] Every chart in F9.AC1–F9.AC12 renders with real data — *2026-10-02, in the browser:
      160 visits from real Airtel, Jio, ACT, BSNL and Vi addresses (the Spike A set) sent
      through the real capture path, enrichment, sweeper, inference engine (all eight
      databases) and classifier, inside the compose network; timestamps then spread over 30
      days so the time charts have shape. Every page rendered with no empty or error state.
      **Not real visitors** -- that is M9. Two charts render explicit "not measured" states
      by design: accuracy (no ground truth until M8, `label_count` 0) and the funnel's
      notified step (M6). The state choropleth is worldwide (owner, M5 review): checked by
      screenshot with strict states in India, the US, the UK, France, Italy and Brazil, all
      15 shaded and none left undrawn; no map tiles (ADR-0017)*
- [x] **No panel can render blank** — empty, loading and error states all exercised by test
      — *`web/src/components/Panel.test.tsx`: every state renders readable text, including
      the trace id on error and the reason when empty; every chart and table renders through
      `Panel`, whose `empty` prop is required*
- [x] Filters compose; a shared URL reproduces the exact view — *`test_list_filters_compose`
      (API, real database); `web/src/filters.test.ts` (URL round trip, local-day windows).
      A custom range reproduces exactly; a preset ("last 7 days") is deliberately relative*
- [x] Dashboard API p95 under 300 ms **reading rollups** on target-equivalent hardware —
      *2026-10-02: 90 k visits (design load), API and database capped to one CPU each:
      every endpoint p95 ≤ 70 ms from rollups (ADR-0016, "Measured"). "Target-equivalent"
      here is a CPU cap on the dev machine, not the e2-micro itself, which M9's load test
      covers. **The raw fallback misses 300 ms on three endpoints over long windows --
      RISKS R25***
- [x] Export streams and contains no plaintext IP — *`test_the_csv_export_streams_every_row_without_the_address`
      (also: formula cells neutralised), `test_the_ndjson_export_is_one_visit_per_line`;
      server-side cursor, 500 rows at a time*
- [x] All three themes pass WCAG AA contrast; no chart relies on colour alone —
      *`web/src/theme.test.ts`, 45 checks read from the stylesheet (it caught light-theme
      `--ok`/`--warn` at 4.45 and 4.27 on the new panel tint; fixed); decal patterns on every
      series and every chart's data as a table (`web/src/charts.test.ts`)*
- [x] Keyboard navigation works across all controls — *2026-10-02, in the browser: 51
      focusable controls on the overview, all native (links, selects, inputs, buttons,
      summaries), none interactive but unreachable; Tab lands first on the skip link with a
      visible focus ring; the map pans and zooms from the keyboard. Not tested with a screen
      reader*
- [x] A visit's detail shows every fired rule with its weight and evidence, and the full
      location derivation (every source, accepted or suppressed with reason) — *carried
      from M4 and M3; the API already serves both* — *2026-10-02, in the browser on real
      visits: a visit on a mobile ASN shows five candidates with weights, three of them
      "suppressed: mobile ASN",
      and five sources that said nothing with their reasons; a curl visit shows
      `ua.automation_tool`, weight 80, evidence `curl/`, and the score arithmetic*
- [x] OpenAPI drift check fails on a deliberate response-model change — *2026-10-02, on a
      clean tree: a field added to `StageMix` made `./scripts/tl openapi-check` exit 1 with
      the diff; reverted*
- [x] Docs: API section 8 verified — *8.1 "As shipped in M5" written with the code;
      section 7 (filters, export) and 14 (zod) amended*

---

## M5.5 — Design system and UI polish · size L

**Goal:** the dashboard looks and behaves like one coherent, premium analytics product, with a
design system every later milestone builds on. **No functionality or business-logic change.**
**Added:** 2026-10-02 at the owner's direction, before M6; approved 2026-10-03 with enhancements
E4–E10. **Plan:** docs/DESIGN.md Part II; **decision:** ADR-0019. **F/AC-IDs:** F9.AC16, F9.AC17
(extended to 390 px, SPEC §11 row 15), F9.AC18, F9.AC20, NFR7.

**Scope** (DESIGN §11, phases 0–5)
- Tokens for three themes (five surface layers, text tiers, one accent, status, data-viz),
  Inter Variable self-hosted, the Lucide icon registry, the chart theme
- About 25 owned primitives in `web/src/components/ui/` and a development-only gallery at
  `/__design`
- New shell: collapsible sidebar with groups, compact header with breadcrumb, a ⌘K command
  palette, help and the user menu; page template; FilterToolbar with chips (same URL state)
- Skeleton loading, empty and error states inside the existing four-state `Panel`
- Every page recomposed per DESIGN §10; breakdowns and signals as ranked lists
- Mobile layout (drawer navigation, single column)
- Approved enhancements (DESIGN §12): command palette, shortcuts, click-to-filter, E4 Overview
  cards, E5 visit drawer, E6 previous-period overlay, E7 freshness, E8 glossary, E9 per-chart
  CSV, E10 recovery-codes download, relative timestamps

**Done checklist**
- [x] ADR-0019 accepted with the Phase 0 spike results (CSP: zero violations in Chromium,
      Firefox and WebKit) — *accepted 2026-10-03. Firefox 155 and WebKit 26.6 were run with
      Playwright in a container (owner's choice), against Caddy itself, with positive controls
      (ADR-0019 "Spike results"). WebKit is Safari's engine; Safari itself was not run*
- [x] All three themes pass the extended contrast tests (DESIGN §8) — *`theme.test.ts`: every
      text tier on all five surface layers, status text on its tint, text on the accent, six chart
      colours, an ordered ramp; plus scans proving no hex colour outside the theme blocks, in
      `styles/*.css`, or in any TypeScript source (the scan asserts it saw > 20 files)*
- [x] Every primitive in the gallery in every state; each has a state test — *`/__design`
      (development builds only; absent from `dist`, checked by grep); 20 primitive tests in
      `components/ui/primitives.test.tsx`*
- [x] Filters round-trip through the URL exactly as before — `filters.test.ts` unchanged and
      green — *unchanged; and in the browser every editor kind wrote the M5 keys
      (`device_class=mobile&device_class=desktop`, `asn=55836` from "AS55836",
      `include_automated=true`, chip removal, Clear all, `range=7d`); click-to-filter tested in
      `charts.test.ts`*
- [x] Every data surface shows loading skeleton, error with trace id, empty with reason, data —
      *`Panel` keeps its four states (`Panel.test.tsx` unchanged in intent); loading is now a
      skeleton shaped by `kind`, with the sentence kept for screen readers*
- [x] Screenshot matrix: 3 themes × 1440/1024/390 px × 8 pages, attached to the PR — *72
      images in Chromium, plus Firefox and WebKit at 1440 and 390 px in the default theme (32),
      taken with Playwright. Each image also checks for horizontal overflow, which found
      Breakdowns at 390 px and Visits at 1024 px wider than the screen (E47, fixed). After the
      fix, no page overflows at any width, in any theme or engine*
- [x] Keyboard walkthrough of every page, menu, dialog and the command palette, mouse-free —
      *menu arrow keys and typeahead, dialog focus-in/return and Escape, palette combobox
      (arrows, Enter, id jump), shortcuts (`g`, `?`, `/`, `[`) verified by script in the
      browser; every page has one `h1`, no skipped heading level, and no unnamed button, link or
      field (automated sweep). By hand, by the owner on 2026-10-07: the shortcuts, then a
      Tab-through of every page (including the M5.6 Settings pages and the M6 geofence and
      alerts pages), menu, dialog, drawer, filter editor, popover and the palette -- focus
      always visible, a sensible order, no trap, every control operable, focus returned on
      close. Nothing failed*
- [x] Zero CSP violations on the Caddy-served build, every page, every theme — *Caddy itself,
      in Chromium, Firefox and WebKit, with Playwright: the signed-out pages, all 11 signed-in
      routes in all 3 themes, then every popup, filter editor, palette, help, tooltip, chart
      hover, Data toggle, CSV download, row expansion, visit drawer, map interaction and the
      phone drawer — **0 violations in each engine**, with positive controls reported in each.
      The first, browser-pane Chromium sweep had recorded 0 wrongly: its listener was attached
      after load and missed zod's `eval` probe on every page (E44, fixed)*
- [x] Bundle within budget: ≤ +40 KB gzipped initial, ≤ +10 KB per route — *initial JS 150.4 KB
      gz (+15.5) and CSS 11.7 KB gz (+5.1) against the phase 0 build; every page route ≤ 3.8 KB gz.
      The Latin font is a separate cached 48 KB file; DESIGN §11 corrected (it had counted the
      font inside the 40 KB, which it alone exceeds)*
- [x] No API, schema or endpoint change (`openapi-check` clean, no migration) — *`git diff
      6a9a9a0..HEAD` touches only `web/`, `docs/` and `CLAUDE.md`; `./scripts/tl verify` 13/13*
- [x] Docs: DESIGN.md accepted; ARCHITECTURE §5 and §8 ledger updated; CLAUDE.md rules live —
      *plus ERRORS E41–E47 and R27 updated*

---

## M5.6 — Dashboard enhancements and the Settings redesign, UI only · size M

**Goal:** a dashboard that reads at a glance and feels alive, with no server change.
**Approved:** 2026-10-06 by the owner, as Phase A of `docs/plans/ENHANCEMENTS-PLAN.md`.
**Design:** DESIGN §10.1, §10.11 and §12 E16–E22. **F/AC-IDs:** F9.AC2, F9.AC3, F9.AC5,
F9.AC6, F9.AC16–F9.AC18, NFR7. No new requirement: every item reads an existing endpoint.

**Scope**
- E16 sparklines on Visits, Human share and Location consent
- E17 the state map card on Overview, lazily loaded
- E18 the live feed in place of "Recent visits"
- E19 the hour × weekday heatmap
- E20 the Links index and link detail pages
- E21 the Daily volume fix (zero-visit days are empty)
- E22 device and network icons in tables and ranked lists
- **Settings redesign** (added 2026-10-06, `docs/plans/SETTINGS-REDESIGN-PLAN.md`, DESIGN §10.8,
  §10.9, E23–E28): six Settings pages, confirmations, the shown-once dialog, the Toast, Team
  management on the existing `/admins` routes, and sign-in polish. No QR code

**Done checklist**
- [x] Every item in DESIGN §12 E16–E22 built, with a state or unit test where it has logic —
      *8 new vitest cases: the sparkline's gap, silence under two points and spoken text; the
      hour × weekday fold in IST (a UTC 15:30 bucket lands on Monday 21h); zero days left out
      of the calendar series but kept in its table; glyphs on device rows and none on states*
- [x] No API, schema or migration change: the diff touches only `web/` and `docs/`
      — *with one exception, found by this milestone's testing: ERRORS E51, the audit detail of
      `PATCH /admins/{id}` recorded the new role as "from". Fixed in `admins_router.py` with an
      integration test; no endpoint, payload, schema or migration changed*
- [x] Zero CSP violations in Chromium, Firefox and WebKit on the Caddy-served build — *the M5.5
      Playwright sweep against Caddy, now over 11 signed-in routes (Links, a link's page and an
      unknown slug added) in 3 themes plus every overlay, with positive controls reported in each
      engine. The only reports are the known ones: Firefox's own favicon fetch on the API's
      `/privacy` page, and WebKit reacting to Playwright's screenshot stylesheet*
- [x] Screenshots: Overview, Links and a link's detail in 3 themes at 1440, 1024 and 390 px,
      with no horizontal overflow — *54 in Chromium over six changed pages (Visits, Geography
      and Breakdowns too), plus 24 in Firefox and WebKit; none overflows. Two fixes came out of
      the review: heatmap legends now band in whole visits, and the live feed drops its Device
      column and keeps times on one line on phones*
- [x] Overview's own chunk within +10 KB gzipped (UI-24); Leaflet stays in the map chunk —
      *Overview 0.9 KB + shared panels 5.3 KB gz; the map is its own lazy 46 KB chunk shared
      with Geography; initial JS +1.8 KB (icons), CSS +0.3 KB*
- [x] `./scripts/tl verify` 13/13 — *512 unit, 318 integration, 120 vitest*

*Settings redesign (E23–E28):*
- [x] Six Settings pages; `/account` redirects; sidebar, user menu and palette point at them
- [x] Every request is one the old page sent, or an existing `/admins` route; no server change —
      *`git diff` touches `web/` and `docs/` only; four client functions added for the shipped
      `/admins` routes (create, update, delete, new setup link)*
- [x] Destructive actions confirm (UI-16); shown-once dialogs lock until "I have saved these" —
      *in the browser: regenerating asks first; the codes dialog survives two Escapes and a
      backdrop click, has no ×, and Done stays disabled until ticked*
- [x] Sign-in, enrolment and recovery keep their steps and their messages word for word — *only
      a step indicator, show/hide, the checklist, a grouped secret and monospace codes were
      added; enrolment walked end to end with a real TOTP code*
- [x] Team: invite, role, status, new link and delete work against the real API, `409
      LAST_OWNER` shows inline, and each action is in `audit_log` — *27 browser checks against
      the real API with a throwaway invitee, and each action found in `audit_log`.
      `LAST_OWNER` was not provoked (it needs the only owner's account); the dialog shows any
      refusal with its trace id*
- [x] Zero CSP violations in three engines; screenshots of every Settings page and the auth
      pages in 3 themes × 3 widths; `./scripts/tl verify` 13/13 — *0 in Chromium, Firefox and
      WebKit over 17 signed-in routes; 54 + 24 Settings screenshots with no overflow, plus
      sign-in, recovery, reset request and all three enrolment steps; `tl verify` 13/13 (512 unit,
      318 integration, 125 vitest)*

---

## M6 — Geofencing and Telegram notifications · size M

**Goal:** draw a boundary, cross it, get the alert.
**F/AC-IDs:** F6.AC1–F6.AC10, F7.AC1–F7.AC9, F10.AC13, NFR5.AC2

**Scope**
- `geofences` with `geography(Polygon,4326)`, GiST, `ST_IsValid` + vertex-cap constraints
- Leaflet + Geoman drawing: polygon and circle, vertex edit, drag, delete -- **Geoman only if
  its three-engine CSP spike is clean**, else small own tools on Leaflet (ADR-0020 decision 8)
- ~~First, choose a basemap for drawing, with an ADR~~ -- **decided: no basemap** (ADR-0020,
  accepted 2026-10-06). The editor draws over the M5 outlines, with typed coordinates
- **Region geofences** (`shape_kind='region'`, `region_keys`), matched on strict country and
  strict state only (ADR-0020 decisions 2, 4)
- Circle round-trip via retained `center` + `radius_m`
- `ST_Covers` evaluation on finalisation; priority resolution; `matched_geofence_ids`
- **Visit state: inside if any, else undetermined if any, else outside; NULL with no applicable
  geofence** (F6.AC5, F6.AC6 as amended; ADR-0020 decision 5)
- `undetermined` sends a normal alert worded "Location not confirmed" (ADR-0020 decision 7)
- GeoJSON import/export; coordinate test endpoint
- `outbox` + worker: `SKIP LOCKED`, backoff + jitter, dead-letter, manual retry
- **`dedup_key` unique constraint: one alert per link and visitor per local day** (F7.AC2 as
  amended, SPEC §11 row 17)
- Inside = high priority, outside = normal, automated = never (F7.AC1, F7.AC3)
- Quiet hours; Telegram test-message control
- Advisory-locked scheduler

**Done checklist**
- [x] Draw a polygon and a circle; both round-trip through edit without distortion -- API tests
  and the browser walk (drawn, vertex-edited, saved, reloaded)
- [x] Self-intersecting ring rejected with the specific error code -- `GEOFENCE_INVALID_GEOMETRY`,
  with the crossing marked on the map
- [x] Physically enter a geofence and **receive the high-priority alert on Telegram** -- owner,
  on a phone, 2026-10-07: location allowed, strict IN/Karnataka, inside a Karnataka region
  geofence; the alert went out as the day's one upgrade over an earlier "Location not
  confirmed" (SPEC §11 row 20)
- [x] Outside visit receives a normal alert -- owner, on a phone, 2026-10-07: strict
  IN/Karnataka against a Maharashtra geofence, received at normal priority
- [x] Bot/crawler visit receives **nothing** -- integration test
- [x] Second visit from the same visitor on the same local day sends **nothing** — proven
  concurrently (two racing transactions, one row)
- [x] **Abstaining inference yields `undetermined`, not `outside`** — integration test
- [x] Telegram outage: job retries, dead-letters, is visible, and manual retry delivers
- [x] **Rolled-back visit emits no notification** — transactional atomicity test (NFR5.AC2)
- [x] Geofence evaluation under 5 ms at p95 -- 40 geofences on one link
- [x] Docs: DATA_MODEL sections 6, 7 and API section 9 verified (and §9a, §10, §12)

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
- [x] All metrics real and reflecting the **host**, not the container -- the host's /proc and
  /sys mounted read-only; on the dev box the API reports Docker Desktop's VM (16 CPUs, 7.4
  GB), not its own 420 MB limit, and says `container` with the reason when unmounted
- [x] Temperature `N/A` with reason on GCP and on the dev box (neither exposes a sensor); the
  reading path proved by a test against a fake `/sys` with a thermal zone (SPEC §11 row 23)
  -- *the dev box and the tests; GCP itself is seen at the M9 deploy*
- [x] Flow diagram shows enabled sources and what fired for a chosen visit -- integration
  test, and the owner's own phone visit overlaid in the browser
- [x] Toggling a source changes behaviour **without a restart** -- the next visit inferred
  in the same process is inferred under it (test); switched off and on in the browser
- [x] Retention preview counts **exactly** match what the purge then deletes -- equal
  category by category (test); the purge takes the preview's instant and policy back
- [x] Manual backup, download, and **automated restore-verify all pass** -- into a scratch
  database, counts equal to the dump's own (ADR-0022) -- tests, the browser, and the live
  scheduler's own nightly backup and monthly check on 2026-10-07 (passed)
- [x] Dead-lettered job visible and retryable from the UI -- Alerts (M6), and now the
  degradation banner on every page
- [x] Every row of the F15.AC6 degradation matrix exercised — **each one still redirects**
  -- `test_degradation_matrix.py`; swap thrashing is new: shedding on memory pressure
  (ADR-0010 amendment), thresholds checked on the e2-micro in M9 (R6)
- [x] Geo-database update under memory cap; failure leaves previous version serving -- the
  M3 installer and its tests; M7 adds the update control and its endpoint test
- [x] Docs: API section 10, DATA_MODEL sections 8, 11 verified (and §2, §12; DESIGN §16 M7)

**Added at the owner's request, 2026-10-07 (SPEC §11 rows 24 and 25)**
- [x] Geo databases state up to date / update available / updating n % / update failed /
  unable to update / not installed, with a per-database auto-update switch and a release
  check (HEAD only; never IP2Location) -- unit and integration tests, and a real update watched
  in the browser (which found E71)
- [x] Links: create, edit, archive, make default, and **delete permanently with the visits**
  after a preview and the slug typed -- archived links too -- integration tests and the full
  cycle in the browser. *This is what makes F10.AC6 true: until now the Links page only read,
  so the M7 tick above leaned on a page that could not create or edit a link.*

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
- ~~Measure S10 latency triangulation~~ — S10 was dropped in M3 (SPEC §11 row 12)
- Re-run inference on retained ciphertext IPs to validate tuning (the ADR-0007 payoff)

**Done checklist**
- [ ] 30+ labels collected across Airtel, Jio, ACT, BSNL, Vi; Wi-Fi and mobile data;
      VPN on and off
- [ ] Accuracy metrics computed, stored, and displayed with sample size
- [ ] CI job fails on a deliberately-regressed threshold
- [ ] Per-source accuracy reported; any consistently-wrong source down-weighted **with
      evidence from `visit_candidates`**, not intuition
- [x] ~~S10 measured~~ — not applicable: S10 dropped in M3 (SPEC §11 row 12, RISKS R23)
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
- [ ] **Real visits through the Cloudflare path show the full derivation** (deferred from
      M3, owner decision 2026-09-29): one on mobile data, one on Wi-Fi; every source
      present in `candidates[]` or `inference.source_absent`, S8 colo populated
- [ ] **The production resolver answers PTR** (ERRORS.md E27): S6's canary reports healthy
      on the GCP host, and a real visit's `rdns_ptr` is populated where one exists
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
