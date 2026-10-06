# DATA MODEL — Tracelet

**Status:** approved at Gate 3, 2026-09-25.
PostgreSQL 16 + PostGIS 3.4 (ADR-0002). All timestamps are `timestamptz` in UTC.
Any change here needs an Alembic migration **in the same commit** — CLAUDE.md section 2.

**Conventions.** Primary keys are UUIDv7 where the row is externally referenced (time
ordered, so index locality is good) and `bigserial` where the row is append-only
internal volume. `NOT NULL` unless a `NULL` carries a specific documented meaning.
Enumerations are PostgreSQL `ENUM` types, listed in section 2.

Every index, constraint and key carries a deterministic name derived from its table and
columns (`ix_`, `uq_`, `ck_`, `fk_`, `pk_`), set by the SQLAlchemy naming convention in
`db/base.py`. An unnamed constraint is one that cannot reliably be dropped or altered by
a later migration, so the CHECK constraints below appear in the catalogue as
`ck_<table>_<name>` — for example `ck_admins_active_requires_password_and_totp`.

**An `inet` column does not come back as a string.** SQLAlchemy hands back an
`IPv4Interface`/`IPv6Interface`, so any comparison against a canonical prefix string must
go through `str(...)`, and the model annotation must say so. Getting this wrong silently
rejected every session, and the lie in the annotation is precisely why `mypy --strict`
could not see it (docs/ERRORS.md E13).

---

## 1. Entity relationships

```
   admins ──┬──< admin_recovery_codes
            ├──< admin_enrollment_tokens
            ├──< password_reset_tokens
            ├──< auth_challenges       (short-lived multi-step auth state)
            ├──< sessions
            ├──< audit_log            (actor, nullable: NULL = system)
            ├──< links                (created_by)
            ├──< geofences            (created_by)
            └──< ground_truth_labels  (labeled_by)

   links ───┬──< visits
            └──< (geofences.link_ids[] — optional soft scoping)

   visits ──┬──< visit_candidates     (evidence trail, one row per candidate)
            ├──── signals jsonb       (fired classification rules, denormalised)
            ├──── matched_geofence_ids uuid[]
            └──1  ground_truth_labels (0..1)

   outbox   (standalone queue; payload references a visit by id, no FK —
             a purged visit must not block a pending notification)

   REFERENCE / CONFIG (small, slow-changing, must never be lost)
   ├ asn_profiles        derived from the offline databases
   ├ rdns_city_codes     the lexicon
   ├ geo_databases       installed versions + freshness
   ├ inference_settings  versioned weights, thresholds, source toggles
   ├ retention_policy    singleton
   ├ app_settings        non-secret runtime configuration
   └ rate_limit_buckets  GCRA state (ephemeral)

   DERIVED (rebuildable from visits while visits exist)
   ├ rollup_visit_daily      cells per local day       (ADR-0016)
   ├ rollup_visit_hourly     cells per local hour, 14 days
   ├ rollup_visit_dim_daily  one row per dimension value per day
   ├ rollup_state            which days are built, in which zone
   ├ geo_cache
   └ accuracy_runs
```

---

## 2. Enumerated types

| Type | Values |
|---|---|
| `admin_role` | `owner`, `analyst` |
| `admin_status` | `pending_enrollment`, `active`, `disabled` |
| `auth_challenge_kind` | `mfa`, `totp_confirm`, `chat_verify` — added in M1, see section 3.7 |
| `visit_stage` | `server`, `enriched`, `server_only`, `rate_limited` |
| `classification` | `human`, `bot`, `crawler`, `datacenter`, `spam`, `spoofed`, `unknown` |
| `consent_state` | `granted`, `denied`, `unavailable`, `not_asked`, `blocked_by_webview` |
| `connection_class` | `broadband`, `mobile`, `datacenter`, `vpn_suspected`, `tor`, `business`, `unknown` |
| `asn_type` | `broadband`, `mobile`, `hosting`, `business`, `education`, `government`, `unknown` |
| `device_class` | `mobile`, `tablet`, `desktop`, `tv`, `server`, `bot`, `unknown` |
| `geo_level` | `country`, `admin1`, `admin2`, `city`, `point` |
| `geofence_state` | `inside`, `outside`, `undetermined` |
| `inference_source` | `gps`, `geolite2`, `ip2location`, `ipinfo`, `dbip`, `rdns`, `asn_org`, `cf_colo`, `external_api`, `latency`, `timezone` — `latency` (S10) is **unused since M3** (SPEC §11 row 12); kept because dropping a PostgreSQL enum value rewrites every table using the type |
| `notify_priority` | `high`, `normal`, `silent` |
| `outbox_kind` | `telegram.visit_alert`, `telegram.password_reset`, `telegram.health_alert`, `telegram.test` — `password_reset` is **unused**: reset links are sent synchronously, see the ADR-0009 amendment |
| `outbox_status` | `pending`, `in_flight`, `done`, `failed`, `dead` |
| `backup_kind` | `daily`, `weekly`, `manual` |
| `geo_db_status` | `installed`, `downloading`, `failed`, `stale` |
| `shape_kind` | `polygon`, `circle`, `region` — `region` added in M6 (ADR-0020) |

---

## 3. Identity and access

### 3.1 `admins`

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | UUIDv7 |
| `email` | `citext` UNIQUE | Login identifier. This is the **admin own** address, not visitor data |
| `display_name` | `text` | |
| `role` | `admin_role` | |
| `status` | `admin_status` | |
| `password_hash` | `text` NULL | Argon2id PHC string. `NULL` until enrollment completes |
| `password_params` | `jsonb` | Recorded so a future parameter change can rehash on next login |
| `password_changed_at` | `timestamptz` NULL | |
| `totp_secret_enc` | `bytea` NULL | AES-256-GCM, same envelope as `visits.ip_enc` |
| `totp_key_version` | `smallint` NULL | |
| `totp_enrolled_at` | `timestamptz` NULL | |
| `totp_last_counter` | `bigint` NULL | **Replay prevention** — F8.AC5 |
| `telegram_chat_id` | `bigint` NULL | Verified recovery channel |
| `telegram_verified_at` | `timestamptz` NULL | |
| `timezone` | `text` | Default `Asia/Kolkata` |
| `theme` | `text` | `semi_dark` default, `light`, `dark` |
| `failed_login_count` | `integer` | |
| `locked_until` | `timestamptz` NULL | |
| `created_at`, `updated_at` | `timestamptz` | |

**Indexes:** `UNIQUE(email)`; partial index on `status` where `status='active'`.

**Invariants**

1. **An operation may not remove the last `active` `owner`** — F8.AC13.

   Note the formulation. The obvious version — "an active owner must always exist" — is
   **false during bootstrap**: the first owner is created `pending_enrollment`, and every
   update on the way to activating them happens while no active owner exists. Asserting
   the stronger property rejected the first enrolment at COMMIT (docs/ERRORS.md E12).

   Enforced by two `CONSTRAINT TRIGGER ... DEFERRABLE INITIALLY DEFERRED` triggers, on
   `UPDATE` and on `DELETE`, each with a `WHEN` clause so the check runs only when the row
   *was* an active owner and is about to stop being one. Deferred, so a transaction may
   legitimately pass through a zero-owner state (promote B, then demote A). The `WHEN`
   clause also means an ordinary update — a failed-login counter bump on an owner — costs
   no count query.

   **Three layers, because each covers what the others cannot:**

   | Layer | Covers | Produces |
   |---|---|---|
   | `SELECT ... FOR UPDATE` on active owners | serialises concurrent changes to the owner set | the wait that makes the next layer truthful |
   | Application pre-check, before any mutation | the ordinary case | a clean `409 LAST_OWNER` |
   | Deferred constraint trigger | anything that bypasses the route | `IntegrityError` at COMMIT |

   The lock is not optional. Without it, two concurrent demotions each read an owner set
   that cannot see the other transaction, both pass, and both commit —
   and `SET CONSTRAINTS ALL IMMEDIATE`, added to turn a commit-time 500 into a clean 409,
   fires the deferred trigger early *in the same blind snapshot* and consumes the pending
   event so nothing is re-checked at COMMIT (docs/ERRORS.md E16).

2. `status='active'` requires `password_hash IS NOT NULL AND totp_enrolled_at IS NOT
   NULL` — F8.AC4. **No account can reach the dashboard without TOTP.** A CHECK
   constraint, so it holds for the CLI and for a psql session, not only for the API.

   **This interacts with invariant 1, and the interaction has no legal state.** Clearing
   TOTP forces the status out of `active`, which invariant 1 forbids for the last active
   owner — so the sole owner cannot have their TOTP cleared at all. Anything that needs to
   re-enrol that account must leave the row `active` and replace the secret in place, which
   is what `tracelet admin reset-totp` now does (docs/ERRORS.md E17). Worth knowing before
   writing the next operation that nulls `totp_enrolled_at`.
3. `telegram_chat_id` must be verified before it can receive a reset link — F8.AC7.
   Relaxed for `status='pending_enrollment'`, since a chat id may be recorded before the
   account is live.

### 3.2 `admin_recovery_codes`

`id bigserial` PK, `admin_id` FK cascade, `code_hash text` (Argon2id), `used_at
timestamptz NULL`, `created_at`.
**Indexes:** `(admin_id)`, partial `(admin_id)` where `used_at IS NULL` for the
remaining-count warning at F8.AC6.

**No unique constraint on `code_hash`**, deliberately, and the Gate-3 design was wrong to
list one: an Argon2id hash carries a random salt, so two rows holding the same plaintext
code hash differently and a unique index would never fire. Verification therefore checks
every unused row — ten Argon2 verifications at 32 MiB, roughly 500 ms. Acceptable because
recovery is rare and tightly rate-limited, and the alternative (an unsalted lookup hash)
would make a database leak considerably worse.
**Invariant:** a code is single-use — setting `used_at` is conditional
(`WHERE used_at IS NULL`) so a concurrent double-submit cannot consume it twice.

### 3.3 `admin_enrollment_tokens`

`id bigserial` PK, `admin_id` FK, `token_hash bytea`, `expires_at`, `used_at NULL`,
`created_by` FK, `created_at`.
**TTL:** 24 hours.

**Invariant:** single-use and time-limited. **No default password exists anywhere in the
system** — F8.AC15.

A token is *peeked* before the password is validated and consumed only once the password
passes, so a policy rejection does not destroy the invitation (docs/ERRORS.md E15). The
consuming UPDATE stays conditional (`WHERE used_at IS NULL`), so that ordering changes
nothing about the concurrent case.

### 3.4 `password_reset_tokens`

`id bigserial` PK, `admin_id` FK cascade, `token_hash bytea` UNIQUE, `expires_at`,
`used_at NULL`, `requested_ip_prefix inet NULL`, `created_at`.

**Indexes:** `UNIQUE(token_hash)`, `(admin_id)`. **TTL:** 30 minutes.

A separate table from enrolment rather than a `kind` column on one, because the two
genuinely differ: 24 hours versus 30 minutes, an owner-issued invitation versus a
self-service request, and a shared table would let an enrollment link reset an established
account's password. Delivered over Telegram — F8.AC7, ADR-0008.

Same peek-then-consume ordering as enrolment, and for a sharper reason: on a 30-minute
token, a rejected password used to mean requesting another link and waiting for it.

### 3.5 `sessions`

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | |
| `token_hash` | `bytea` UNIQUE | SHA-256 of the opaque 256-bit token. **The token itself is never stored** |
| `admin_id` | `uuid` FK cascade | |
| `csrf_secret` | `bytea` | Double-submit token derivation — F8.AC11 |
| `ip_prefix` | `inet` | Binding, /24 or /48 |
| `ua_hash` | `bytea` | Binding |
| `created_at`, `last_seen_at` | `timestamptz` | |
| `expires_at` | `timestamptz` | Absolute expiry |
| `idle_expires_at` | `timestamptz` | Sliding expiry |
| `revoked_at` | `timestamptz` NULL | |
| `revoked_reason` | `text` NULL | |

**Indexes:** `UNIQUE(token_hash)`; `(admin_id, created_at DESC)`; partial
`(expires_at)` where `revoked_at IS NULL` for the reaper.
**Invariant:** a session is valid only while `revoked_at IS NULL AND now() <
expires_at AND now() < idle_expires_at AND` bindings match. Instant revocation is
exactly what a JWT cannot provide — ADR-0008.

### 3.6 `audit_log`

`id bigserial` PK, `occurred_at`, `actor_admin_id uuid NULL` (**`NULL` means system**),
`actor_ip_prefix inet NULL`, `action text`, `target_type text NULL`, `target_id text
NULL`, `trace_id text`, `detail jsonb`.

**Indexes:** `(occurred_at DESC)`, `(actor_admin_id, occurred_at DESC)`,
`(action, occurred_at DESC)`, GIN on `detail`.

**Invariants**
1. **Append-only.** The application database role is granted `INSERT` and `SELECT`
   only — **no `UPDATE`, no `DELETE`** — F8.AC16, NFR5.AC5. Retention purging runs as a
   separate maintenance role.
2. `detail` is redacted before write: no plaintext IP, no coordinates, no tokens —
   F12.AC13.

### 3.7 `auth_challenges`

Short-lived multi-step authentication state: the MFA challenge between password and code,
the confirm token issued at enrolment, and the Telegram chat-verification code.

**This table did not exist in the Gate-3 design.** It is the fix for a real defect
(docs/ERRORS.md E10): the three tokens were held in a module-level dict, and with two
Gunicorn workers the follow-up request found the token in about half of attempts, so login
was a coin flip. Any state that must survive from one HTTP request to the next is shared
state, and on this deployment shared means PostgreSQL — the same argument ADR-0010 makes
for `rate_limit_buckets`.

| Column | Type | Notes |
|---|---|---|
| `id` | `bigserial` PK | |
| `kind` | `auth_challenge_kind` | `mfa`, `totp_confirm`, `chat_verify` |
| `admin_id` | `uuid` FK cascade | |
| `token_hash` | `bytea` | SHA-256 of a 256-bit token. For `chat_verify` the "token" is the admin id, because the secret is the six-digit code in `payload` |
| `payload` | `jsonb` | Kind-specific extras: the proposed `chat_id` and the numeric code for `chat_verify`. Never a credential that must survive a leak |
| `expires_at` | `timestamptz` | |
| `used_at` | `timestamptz` NULL | |
| `created_at` | `timestamptz` | |

**Indexes:** `UNIQUE(kind, token_hash)`, `(admin_id, kind)`, partial `(expires_at)` where
`used_at IS NULL` for the reaper.

**TTLs:** `mfa` 5 minutes, `totp_confirm` 20 minutes (longer, because before producing a
code the admin has to add the account to an authenticator app and save ten recovery
codes), `chat_verify` 10 minutes.

**Invariants**
1. **One outstanding challenge per admin per kind.** Issuing deletes any existing one, so
   starting a second sign-in invalidates the first rather than leaving two valid windows
   open.
2. **Single-use**, spent by a conditional UPDATE so two concurrent submissions of one token
   cannot both succeed.
3. **Peeked, not consumed, on a wrong answer.** Mistyping a six-digit code must not send
   the admin back to the password step.

One table with a `kind` discriminator rather than three, because all three have the same
shape and lifecycle. The enrolment and reset tokens stay separate (sections 3.3, 3.4)
because *their* lifetimes and delivery paths genuinely differ.

---

## 4. Tracking links

### 4.1 `links`

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | |
| `slug` | `citext` UNIQUE | 4–32 chars, `[a-z0-9-]`. The `CHECK` casts to `text` (`slug::text ~ …`): citext's own `~` is case-insensitive and would admit `IG-Bio` |
| `label` | `text` | |
| `destination_url` | `text` | `CHECK`: starts `https://`, length ≤ 2048. Full validation in the application — F1.AC2 |
| `is_active` | `boolean` | |
| `is_default` | `boolean` | |
| `notify_policy` | `jsonb` | `{inside, outside, undetermined, automated}` priorities — F1.AC5. `undetermined` added in M6, default `normal`; `automated` is always `silent` (`CHECK ck_links_automated_silent`, CLAUDE.md invariant 6). How they combine with a geofence's priority: SPEC §11 row 18 |
| `interstitial_ms` | `integer` | `CHECK BETWEEN 300 AND 1500`, default 700 — F1.AC6 |
| `ask_location` | `boolean` | Default `false`. When true the capture page shows consent text and the browser's location prompt, and waits up to 15 s for the answer — F1.AC11, ADR-0021. Added in migration 0011 |
| `cloned_from` | `uuid` NULL FK self, `ON DELETE SET NULL` | F1.AC9 |
| `created_by` | `uuid` NULL FK admins, `ON DELETE SET NULL` | Deleting an admin must neither delete nor be blocked by their links |
| `created_at`, `updated_at` | `timestamptz` | |
| `archived_at` | `timestamptz` NULL | |

**Indexes:** `UNIQUE(slug)`; **`UNIQUE(is_default) WHERE is_default AND archived_at IS
NULL`** — a partial unique index is what actually enforces "exactly one default"
(F1.AC3); partial `(slug)` where `is_active AND archived_at IS NULL` for the capture hot
path.

**Invariants**
1. Exactly one default among non-archived links, enforced by the partial unique index —
   not by application code alone.
2. **`destination_url` is the only source of a redirect target. Never a query
   parameter, header, or path** — F1.AC7, F13.AC3. This is the structural remedy for B4.
3. A link with visits cannot be deleted, only archived — F1.AC10. Enforced by
   `ON DELETE RESTRICT` on `visits.link_id`.
4. An archived link is never the default — `CHECK (archived_at IS NULL OR NOT
   is_default)`. "Exactly one default" is a statement about non-archived links.

**The engine enforces *at most one* default; the application enforces *at least one*.**
An empty table legitimately has none, so that half cannot be a constraint. Every
operation that can change which link is the default takes a transaction-scoped advisory
lock first, so two concurrent writes cannot both pass a count that neither can see the
other change — the shape of docs/ERRORS.md E16.

**What the default *does* is not specified.** RW-2 turned the brief's single redirect URL
into many links "one marked default", and F1.AC3 enforces exactly one, but no requirement
gives it behaviour. M2 maintains it as an invariant only.

---

## 5. Visits — the core table

### 5.1 `visits`

Grouped by concern. Roughly 90 k rows at 500/day over 180-day retention, so **no
partitioning in v1** — it would be complexity without benefit.

**Identity and lifecycle**

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | UUIDv7 — time-ordered, good index locality |
| `link_id` | `uuid` FK RESTRICT | |
| `occurred_at` | `timestamptz` | Server receive time |
| `stage` | `visit_stage` | F3.AC6 |
| `finalized_at` | `timestamptz` NULL | |
| `enrichment_consumed_at` | `timestamptz` NULL | **Makes the nonce single-use without a separate table** — F2.AC6 |
| `trace_id` | `text` | Ties the row to every log line for that request |
| `inferred_at` | `timestamptz` NULL | **Added in M3** (ADR-0015). Set by the inference job's conditional write; "finalised and not inferred" is its work queue |

**Network — see F12.AC1 and RW-3**

| Column | Type | Notes |
|---|---|---|
| `ip_hmac` | `bytea` NULL | HMAC-SHA256 with the stable pepper, domain-separated (`"ip.v1"`) from `visitor_id`. **Durable**. Nullable so a missing pepper degrades the column rather than losing the visit (CLAUDE.md invariant 1) |
| `ip_prefix` | `inet` | /24 for IPv4, /48 for IPv6. **Durable** |
| `ip_enc` | `bytea` NULL | AES-256-GCM `nonce ‖ ciphertext ‖ tag`, AAD = `id`. **Purged at TTL** |
| `ip_key_version` | `smallint` NULL | Rotation support — F12.AC5 |
| `ip_purge_after` | `timestamptz` NULL | |
| `asn` | `integer` NULL | |
| `asn_org` | `text` NULL | ISP name |
| `asn_type` | `asn_type` | |
| `rdns_ptr` | `text` NULL | |
| `connection_class` | `connection_class` | |
| `is_datacenter`, `is_vpn_suspected`, `is_tor`, `is_proxy_suspected` | `boolean` NULL | F5.AC7, F5.AC9. **`NULL` until assessed** — a default of `false` would assert "not a datacenter" with no evidence (F3.AC5). Assessed by the M4 classifier: `is_datacenter` from the ASN classification, `is_vpn_suspected` for a hosting ASN whose organisation reads as VPN or proxy, `is_tor` from the Tor Project exit list, `is_proxy_suspected` from fingerprint collision or impossible travel. **Each stays `NULL` unless its evidence existed**: no ASN known, no datacenter or VPN verdict; no exit list installed, no Tor verdict; no `fingerprint_id` (a `server_only` visit), no proxy verdict |
| `cf_colo` | `char(3)` NULL | Edge colo — source S8. **Only from a verified Cloudflare peer** (F13.AC6) |
| `cf_country` | `char(2)` NULL | Same gate. An unverified `CF-IPCountry` is a visitor choosing their own country |

**There is no plaintext IP column.** By design, permanently.

`asn`, `asn_org`, `asn_type`, `rdns_ptr` and `connection_class` exist from M2 and are
populated from M3, because they come from the offline databases and the resolver budget
M3 introduces. Until then they hold `NULL` or `'unknown'`, never a guess.

**`rdns_ptr` is stored masked.** A PTR record frequently *contains* the address
(`49.204.83.225.actcorp.in`), so every digit run and hex-group run is replaced with `#`
before storage — `triband-mum-#.mtnl.net.in`. The lexicon matches the real name in memory
only (`inference/sources/rdns.py::mask_ptr`); a test searches the row and the candidate
evidence for the visitor's address. Without this, S6 would be a plaintext-IP column
(CLAUDE.md invariant 4).

**Client identity — ADR-0006**

| Column | Type | Notes |
|---|---|---|
| `visitor_id` | `bytea` NULL | HMAC, **stable** pepper. New-vs-returning |
| `session_fp` | `bytea` NULL | HMAC, **rotating** pepper. Limits long-term linkability |
| `fingerprint_id` | `bytea` NULL | Canonical fingerprint hash, for collision detection — F5.AC7 |

**Device and browser — F3.AC2, F3.AC3**

`user_agent text`, `ua_family`, `ua_version`, `os_family`, `os_version`,
`device_class device_class`, `is_inapp_webview boolean`, `webview_host text NULL`,
`screen_w`, `screen_h`, `viewport_w`, `viewport_h` `integer NULL`, `dpr numeric(4,2)
NULL`, `color_depth`, `touch_points`, `cpu_cores integer NULL`, `device_memory_gb
numeric(4,1) NULL`, `gpu_vendor text NULL`, `gpu_renderer text NULL`, `languages
text[] NULL`, `tz_iana text NULL`, `tz_offset_min integer NULL`, `canvas_hash`,
`audio_hash`, `font_hash`, `webgl_hash` `bytea NULL`.

**All nullable.** A `NULL` means "not provided", with the reason in `signals` —
F3.AC5. **Never coerce a missing client signal to zero.**

**Classification — F5**

`classification classification`, `bot_score smallint CHECK 0..100`, `spoof_score
smallint CHECK 0..100`, `agreement_score numeric(4,3)`, `conflict_score numeric(4,3)`,
`honeypot_tripped boolean`, `header_order_hash bytea` (**always `NULL` — see below**),
`http_version text`, `tls_version text NULL`, `classifier_version text`,
`request_headers jsonb NULL` (**added in M2**, see below),
`client_probes jsonb NULL` (**added in M4**, migration 0006: the capture page's headless
probes as reported — `webdriver`, `chromeObject`, `pluginCount`, `mimeTypeCount`,
`fontCount`, `outerWidth`, `permissionsAnomaly`, `cdpArtefacts`; an unreported probe is
absent, never `false`),
**`signals jsonb`** — array of fired rules `[{rule_id, category, weight, detail}]`, and
the reasons for absent client values (F3.AC5), which use `category: "absence"` and
weight 0. M2 records three: `ua.link_preview_fetcher`, `edge.unverified_cf_header` and
`client.geolocation_absent`. M3 adds `inference.source_absent` — one per source that
produced no candidate, with `{source, status, latency_ms, reason}`, where `status` is
`empty`, `disabled`, `timeout`, `unavailable` or `error` — and `inference.engine_error`.
`inference.street_address_absent` records why a consented visit has no street address
(`disabled`, `circuit_open`, `rate_budget_spent`, `request_failed:*`, `no_address_known`).
Together with `visit_candidates` they make every source visible for every visit
(F4.AC11), including the ones that said nothing.

**M4 adds the classifier's rules** (ADR-0011 amendment): one entry per fired rule, with
`category` `bot`, `spoof`, `spam` or `network` and the rule's configured weight, so
`bot_score` and `spoof_score` are the capped sums of the `bot` and `spoof` weights.
`network` entries (`net.hosting_asn`, `net.tor_exit`, `net.shared_gateway`) carry weight
0: they decide the class or record a fact without scoring. Capture-time entries
(`ua.link_preview_fetcher`, `capture.exploit_probe` with the matched pattern *names*) are
weight 0 and read by the classifier. Absences: `identity.server_only`,
`identity.pepper_missing`, `classifier.engine_error` (F5.AC14).

**`request_headers` — added in M2, not in the Gate-3 model.** F3.AC1 requires the "full
header set", and the Gate-3 model had nowhere to put it. It is also the one column that
could smuggle a plaintext IP into the database, so values are sanitised before storage
(`capture/signals.py`): address-bearing, credential, hop-by-hop and internal headers are
dropped by name; the observed client address is masked wherever it appears; any other IP
literal is masked except in version-bearing headers, where `131.0.0.0` is a Chrome
version and not an address (docs/ERRORS.md E23). A test searches the whole row for the
visitor's address.

**`header_order_hash` is never populated, because header order cannot be observed.** Caddy
is written in Go, whose HTTP server parses headers into a map before any handler runs and
writes them back out sorted. Measured on 2026-09-28: a request sent `Zzz-Last`,
`Aaa-First`, `Mmm-Middle`, and the application received them alphabetically. A hash of
that order would be identical for every client with the same header *set* — a value that
looks exactly like a fingerprint and carries none of the information. The column is kept
so M4 can populate it if a way to observe order is ever found (docs/RISKS.md R19); F5.AC8
now uses the header *set* instead (SPEC section 11 row 7).

> **Why `signals` is JSONB but `visit_candidates` is a table.** Signals are read as a
> whole for one visit and filtered with a GIN containment query; only fired rules are
> stored, roughly 3–5 per visit. Candidates need relational joins against ground-truth
> labels to compute **per-source accuracy** (F4.AC17), which JSONB would make painful.
> The asymmetry is deliberate: it also avoids roughly 1.8 M narrow rows on a 30 GB disk.

**Location — F4**

| Column | Type | Notes |
|---|---|---|
| `consent_state` | `consent_state` | F4.AC2 |
| `gps_lat`, `gps_lng` | `numeric(9,6)` NULL | **Only when `consent_state='granted'`** |
| `gps_accuracy_m` | `numeric(8,1)` NULL | |
| `resolved_address` | `text` NULL | Street level, **only when granted** — F4.AC3 |
| `strict_country_code` | `char(2)` NULL | `NULL` = abstained |
| `strict_admin1`, `strict_admin2`, `strict_city` | `text` NULL | |
| `strict_lat`, `strict_lng` | `numeric(9,6)` NULL | |
| `advisory_country_code` | `char(2)` NULL | Best guess |
| `advisory_admin1`, `advisory_admin2`, `advisory_city` | `text` NULL | |
| `advisory_lat`, `advisory_lng` | `numeric(9,6)` NULL | |
| `confidence_country`, `confidence_admin1`, `confidence_admin2`, `confidence_city` | `numeric(4,3)` NULL | 0..1 |
| `abstain_reason` | `jsonb` | Per level, why strict is `NULL` |
| `geo_source_primary` | `inference_source` NULL | Which source won |
| `inference_version` | `text` | F4.AC16 |
| `geopoint` | `geography(Point,4326)` NULL | From GPS, else strict coordinates. Geofence input |

**Geofence — F6**

`matched_geofence_ids uuid[]` (default empty), `geofence_state geofence_state NULL`. NULL means
no active geofence applied, or the visit is not inferred yet; it is distinct from all three
states, so `outside` never means "there were no geofences" (ADR-0020 decision 5; nullable since
migration 0009).

**Referral**

`referer text NULL`, `utm jsonb NULL`.

### 5.2 Indexes on `visits`

| Index | Serves |
|---|---|
| `(occurred_at DESC)` | Timeline default ordering |
| `(link_id, occurred_at DESC)` | Per-link analytics |
| `(visitor_id, occurred_at DESC)` | New-vs-returning, impossible-travel, F9.AC12 |
| `(classification, occurred_at DESC)` | Human-only default filter |
| `(ip_prefix)` | Rate-limit correlation, NAT detection |
| `(fingerprint_id, occurred_at DESC)` | Fingerprint-collision proxy detection |
| `GIST (geopoint)` | Geofence evaluation — F6.AC8 |
| `(strict_country_code, strict_admin1)` | Strict location lookups; breakdowns read the rollups (best-guess keys, ADR-0018) |
| `GIN (signals)` | "which visits fired rule X" |
| `(occurred_at) INCLUDE (visitor_id, classification, stage, link_id)` | **Unique visitors as an index-only scan** -- added in M5 (migration 0007, ADR-0016); the only analytics figure always counted from raw rows |
| partial `(occurred_at)` where `gps_lat` or `advisory_city` is set | **Map points** (F9.AC5), always read raw -- added in M5 over strict coordinates, moved to the best-guess city by migration 0008 (ADR-0018) |
| partial `(occurred_at)` where `stage='server'` | **The 90 s sweeper** — F2.AC7 |
| partial `(ip_purge_after)` where `ip_enc IS NOT NULL` | The IP purge job — F12.AC2 |
| partial `(occurred_at)` where `finalized_at IS NULL` | Stuck-visit detection |
| partial `(finalized_at)` where finalised, `inferred_at IS NULL` and not `rate_limited` | **The inference job's queue** — ADR-0015, added in M3 |

### 5.3 Invariants on `visits`

1. A row exists **before** any response is returned — F2.AC2. The capture handler
   commits, then renders.
2. `consent_state <> 'granted'` implies `gps_lat`, `gps_lng`, `gps_accuracy_m` and
   `resolved_address` are all `NULL`. **`CHECK` constraint**, not a convention —
   F4.AC3, OOS4.
3. `strict_city IS NOT NULL` requires `confidence_city >= threshold` for the recorded
   `inference_version`. Application-enforced; the threshold is versioned configuration.
4. `strict_*` set to `NULL` requires a matching key in `abstain_reason`. **Abstention
   always carries a reason** — F4.AC10.
5. **An abstaining inference is never "outside" or "inside"** — F6.AC6. Restated in M6
   (ADR-0020 decision 6): `outside` requires a strict location at some level (`geopoint` or
   `strict_country_code`), and `inside` requires at least one `matched_geofence_ids` entry.
   The original wording, "`undetermined` whenever `geopoint IS NULL`", became false once a
   strict state can place a visit inside or outside a region geofence.
6. `enrichment_consumed_at` is set by a conditional update, so a replayed nonce cannot
   double-enrich.
7. `ip_enc IS NULL` after `ip_purge_after`, while `ip_hmac` and `ip_prefix` persist —
   F12.AC2.
8. `stage='rate_limited'` rows carry no client columns and no inference; they exist to
   make shedding visible — F11.AC3. They keep `ip_prefix`, because the network is what
   was limited, and nothing else about the address. Finalised at birth.
9. **`(stage = 'server') = (finalized_at IS NULL)`** — `CHECK`, added in M2. `server` means
   "awaiting enrichment or the sweeper" and nothing else. The sweeper's partial index and
   its `UPDATE` both key on `stage = 'server'`; this makes it impossible for them to
   disagree about what is pending.
10. **`(ip_enc IS NULL) = (ip_key_version IS NULL)`** — `CHECK`, added in M2. A purge nulls
    both; a key version pointing at nothing is a bug.
11. **`strict_lat`/`strict_lng` — and the `geopoint` derived from them — exist only when
    `strict_city` does.** Added in M3 (ERRORS.md E28). A strict *state* is not a coordinate:
    the admin1 winners' records carry city coordinates, and geofencing would act on them.
    Application-enforced in `inference/consensus.py`. The advisory point has no such limit;
    it is a guess and is displayed as one.
12. **Rate-limited visits are never inferred** — the inference queue's partial index
    excludes `stage = 'rate_limited'` (invariant 8).
13. **Advisory extends strict** (ADR-0018, engine m3.4). Wherever `strict_<level>` is set,
    `advisory_<level>` equals it; and every level any candidate named has an advisory
    value, mobile networks included. Application-enforced in `inference/consensus.py`,
    unit-tested per suppression scenario. Visits stamped m3.3 or earlier may lack a mobile
    visitor's advisory city.

Invariants 2 and 5 are `CHECK` constraints (`ck_visits_gps_requires_consent`;
`ck_visits_outside_needs_strict` and `ck_visits_inside_has_match`, which replaced
`ck_visits_no_geopoint_is_undetermined` in migration 0009), not conventions. Each is exercised by a test
that goes around the application with raw SQL, because the guarantee has to survive a
code path that forgets it.

`geopoint` is added with plain DDL in migration 0004 rather than through SQLAlchemy:
`geography(Point, 4326)` has no core type without geoalchemy2, which M6 brings. It is
unmapped in the ORM until then.

### 5.4 `visit_candidates` — the derivation trail

**Created in M3** (migration 0005), with the inference engine that writes it.

**What a row means.** `level` is the deepest level the candidate asserts; its shallower
fields are filled in. `weight` is the source's prior at that level × `raw_confidence`;
`effective_weight` is `weight` after S11's timezone penalty. `accepted` means the
candidate is on the winning side at every level it asserts **and** no suppression rule
rejected it. The reasons the M3 engine writes are `registry_artifact`, `mobile_asn`,
`hosting_asn`, `tz_mismatch` (it lost, and the browser timezone contradicted it) and
`outvoted`; `below_threshold` is allowed by the CHECK but reserved — a level that fails
its threshold is recorded in `visits.abstain_reason`, not held against the candidates
that agreed with it. An S11 row always has weight 0: it rejects, never votes (F4.AC9).

| Column | Type | Notes |
|---|---|---|
| `id` | `bigserial` PK | |
| `visit_id` | `uuid` FK cascade | |
| `source` | `inference_source` | |
| `level` | `geo_level` | |
| `country_code` `char(2)` NULL, `admin1`, `admin2`, `city` `text` NULL | | |
| `lat`, `lng` | `numeric(9,6)` NULL | |
| `raw_confidence` | `numeric(4,3)` | As claimed by the source |
| `weight` | `numeric(6,4)` | Source prior × evidence quality |
| `effective_weight` | `numeric(6,4)` | After agreement bonus and timezone penalty |
| `accepted` | `boolean` | |
| `suppressed_reason` | `text` NULL | `registry_artifact`, `mobile_asn`, `hosting_asn`, `tz_mismatch`, `below_threshold`, `outvoted` |
| `evidence` | `jsonb` | e.g. `{"ptr":"abts-kk-static-…airtelbroadband.in","matched_code":"kk","lexicon_version":"3"}` |
| `latency_ms` | `integer` | Per-source cost, for the health page |
| `produced_at` | `timestamptz` | |

**Indexes:** `(visit_id)`, `(source, produced_at DESC)`, partial `(visit_id)` where
`accepted`.

**Invariants:** losers and suppressed candidates are retained — that *is* the audit
trail (F4.AC6, F4.AC11). **`accepted = (suppressed_reason IS NULL)`** is a `CHECK`
(`rejection_has_reason`), as is the list of known reasons, so a rejection without a
reason cannot be stored. `evidence` never holds an unmasked PTR (section 5.1).
Cascade-deleted with the visit.

---

## 6. Geofencing

### 6.1 `geofences`

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | |
| `name`, `description` | `text` | |
| `shape_kind` | `shape_kind` | `polygon`, `circle` or `region` (ADR-0020) |
| `area` | `geography(Polygon,4326)` NULL | Polygons and circles; circles stored buffered. NULL exactly for a region |
| `center` | `geography(Point,4326)` NULL | Circles only, retained for round-trip editing |
| `radius_m` | `numeric(10,2)` NULL | Circles only, retained for round-trip editing; `> 0` |
| `region_keys` | `text[]` NULL | Regions only: `IN` (a country) or `IN\|Karnataka` (a first-order division, spelled as GeoNames spells it, which is what the engine emits). 1 to 1000 keys, none NULL |
| `priority` | `integer` | Default 0. Higher wins on overlap — F6.AC7 |
| `is_active` | `boolean` | Default true |
| `notify_priority` | `notify_priority` | Default `high`. Combined with the link's `notify_policy.inside`, the less urgent winning (SPEC §11 row 18) |
| `link_ids` | `uuid[]` NULL | `NULL` = applies to all links; never empty (`is_active` says "applies to none") |
| `created_by` | `uuid` NULL FK admins, `ON DELETE SET NULL` | As for links |
| `created_at`, `updated_at` | `timestamptz` | |

The planned `notify_on_enter boolean` was not built: `notify_priority = 'silent'` says the
same thing, and F6.AC3 names no such field.

**Indexes:** `GIST (area)`; partial `(priority DESC)` where `is_active`.

**Invariants** (all `CHECK`, migration 0009):
- each shape carries exactly its own columns: `area` NULL exactly for a region
  (`ck_geofences_area_iff_shape`), `region_keys` exactly for a region
  (`ck_geofences_region_keys_iff_region`, `_region_keys_bounded`), `center` and `radius_m`
  exactly for a circle (`ck_geofences_center_iff_circle`, `_radius_iff_circle`,
  `_radius_positive`);
- `ST_IsValid(area::geometry)` and `ST_NPoints(area::geometry) <= 2000` to bound evaluation
  cost (F6.AC4: `ck_geofences_area_valid`, `_vertex_cap`). The application rejects a
  self-intersecting ring first, with its own error code; these are the floor beneath it;
- `link_ids` NULL or non-empty; `name` 1 to 100 characters.

**Evaluation** (ADR-0020 decision 4), per applicable geofence (active, and `link_ids` NULL
or containing the visit's link):
- **polygon, circle:** `ST_Covers(area, geopoint)` — **geodesically correct because the
  column is `geography`, not `geometry`**, a principal reason PostGIS was chosen (ADR-0002).
  `undetermined` when `geopoint` is NULL;
- **region:** a key `CC` is inside when `strict_country_code = CC`, outside when the strict
  country is stated and different, otherwise undetermined. A key `CC|State` is inside when
  the strict country is `CC` and `strict_admin1 = State`, outside when either strict level
  is stated and different, otherwise undetermined. The geofence is inside if any key is,
  outside if every key is, otherwise undetermined. **Advisory fields are never read.**

The visit records every inside geofence in `matched_geofence_ids` (F6.AC7) and combines the
results into `geofence_state`: inside if any, else undetermined if any, else outside; NULL
when none applied (section 5.3 invariant 5).

---

## 7. Notifications

### 7.1 `outbox`

| Column | Type | Notes |
|---|---|---|
| `id` | `bigserial` PK | |
| `kind` | `outbox_kind` | |
| `dedup_key` | `text` UNIQUE NULL | `visit_alert:{link_id}:{visitor_id}:{local_date}`, the visit's arrival date in the reporting timezone (SPEC §11 row 17). A visit with no `visitor_id` is keyed by its `ip_hmac` instead (`…:ip:{hex}:…`), so it still alerts once per network per day rather than on every request |
| `priority` | `notify_priority` | `high` or `normal`, resolved at enqueue (SPEC §11 row 18). **Never `silent`**: a silent alert is not enqueued. Quiet hours hold `normal` (F7.AC9). Added in M6 |
| `payload` | `jsonb` | Self-contained: the message's facts, rendered at send time; references a visit by id with **no FK** |
| `status` | `outbox_status` | `pending` → `in_flight` → `done`; on failure `failed` (retried) or `dead` |
| `attempts` | `integer` | Incremented when claimed |
| `max_attempts` | `integer` | Default 8 |
| `next_attempt_at` | `timestamptz` | Exponential backoff with full jitter: up to 30 s × 2^(attempts−1), capped at 1 h; Telegram's own `retry_after` when it gives one |
| `locked_by` | `text` NULL | Worker identity |
| `locked_at` | `timestamptz` NULL | |
| `last_error` | `text` NULL | Never contains the bot token |
| `created_at`, `completed_at` | `timestamptz` | |

**Indexes:** `UNIQUE(dedup_key)`; partial `(next_attempt_at)` where `status IN
('pending','failed')`; partial `(status)` where `status='dead'` for the health panel;
`(created_at DESC)` for the delivery log.

**`CHECK`s** (migration 0010): `priority <> 'silent'`; `(status = 'in_flight') = (locked_at
IS NOT NULL)`; `(status IN ('done','dead')) = (completed_at IS NOT NULL)`; `attempts >= 0`;
`max_attempts BETWEEN 1 AND 20`.

**Invariants**
1. Inserted in the **same transaction** as visit finalisation — NFR5.AC2, F7.AC5. A
   visit never exists without its queued alert; a rolled-back visit never emits one.
2. **`UNIQUE(dedup_key)` is what actually implements the once-per-local-day deduplication rule**
   (F7.AC2). Doing it in application code would race under concurrent visits. It also holds
   the rule's one upgrade (SPEC §11 row 20): a high-priority alert whose day key is already
   held by a *normal* alert is queued under that key plus `:upgrade`, unique too. A high
   alert holding the day key leaves no upgrade, and a normal alert never takes one -- so at
   most two alerts per link and visitor per day, the second always high.
3. Claimed with `SELECT … FOR UPDATE SKIP LOCKED LIMIT n` — correct with two workers.
4. **No FK to `visits`.** A retention purge must not block a pending notification, and
   the payload is already self-contained.
5. `attempts >= max_attempts` sets `status='dead'`, surfaced with a manual retry —
   F7.AC6, F10.AC13.
6. **Only `classification = 'human'` is ever enqueued** (CLAUDE.md invariant 6, F7.AC1), and
   only after geofence evaluation, from the same savepoint (ADR-0015).
7. An `in_flight` row whose lock is older than 5 minutes was abandoned by a crashed worker
   and returns to `failed`. Delivery is therefore **at least once**: a crash between
   Telegram accepting a message and the row being marked `done` can repeat one alert, which
   is preferred to losing it (F7.AC5).

---

## 8. Reference and configuration data

All of section 8 is in the **must never be lost** set (F12.AC12, NFR5.AC3).

### 8.1 `asn_profiles` — the B1 fix, precomputed

`asn integer` PK, `org text`, `asn_type asn_type`, `modal_lat`, `modal_lng`
`numeric(9,6)`, `modal_city text`, `modal_admin1 text`, **`modal_share numeric(4,3)`**
(fraction of that ASN database entries sitting at the modal centroid),
`is_registry_artifact_source boolean`, `is_mobile boolean`, `is_hosting boolean`,
`is_cgnat boolean`, `computed_at`, `source_db_versions jsonb`.

**Recomputed after every geo-database update.** A high `modal_share` is the signature of
a registry artifact: it means the database has collapsed a whole ISP onto one point.
That is exactly the Bangalore-recorded-as-Faridabad failure — F4.AC12(a).

**As computed in M3** (`inference/geodb/profiles.py`, in a memory-capped subprocess):
every IPv4 network of the installed ASN database is looked up in every installed city
database and weighted by its address count. Only records that **name a city** count; a
country-only record carries the country's centroid, not a placement (ERRORS.md E30).
Only networks placed in India are counted: that is the primary audience, and an ASN
elsewhere simply has no profile, so rule (a) cannot fire for it. An ASN smaller than a
**/18** keeps its centroid but gets `modal_share = NULL`, because one point is not
evidence when there was nowhere else to be. `modal_city`/`modal_admin1` are respelled
through GeoNames exactly as candidates are, or the rule's name comparison could never
match. `is_registry_artifact_source` uses the default 0.30 for display only; the engine
applies the *active settings* threshold to `modal_share` (RISKS R24). The table is
rebuilt after any location or ASN database update.

### 8.2 `rdns_city_codes` — the lexicon

`id bigserial` PK, `pattern text` (regex), `code text`, `city`, `admin1`, `country_code`,
`lat`, `lng`, `confidence numeric(4,3)`, `isp_hint text NULL`, `notes text`, `is_active
boolean`, `lexicon_version integer`, `created_at`, `updated_at`.

**Indexes:** `(is_active, country_code)`, `UNIQUE(pattern, lexicon_version)`.
Seeded from a versioned data file, editable from the dashboard. **Spike A in KICKOFF
section 6 measures whether this table can carry the weight the accuracy plan puts on
it** — RISKS R3. **Measured: 2.0 %.**

The seed is `api/src/tracelet/inference/data/rdns_lexicon.json`, one row per code, loaded
once per `lexicon_version` the first time inference runs and never re-applied over an
admin's edits. Every `IN` entry applies only to a PTR under an Indian domain (the file's
`in_domain_gate`), because short codes such as `del` or `pat` are meaningless elsewhere.
Airtel telecom-circle codes (`kk`, `tn`, …) are admin1-only entries restricted to
`airtelbroadband.in`; circles spanning two states are deliberately absent, with the
reasons in the file.

### 8.3 `geo_databases`

`id`, `name text` UNIQUE-per-status, `version text`, `released_at`, `installed_at`,
`file_path`, `sha256`, `size_bytes`, `status geo_db_status`, `last_check_at`,
`last_error text NULL`, `is_enabled boolean`, `staleness_threshold_days integer`.

**Invariant:** at most one row per `name` with `status='installed'` — partial unique
index. Atomic symlink swap means a failed update leaves the previous version serving
(F10.AC4).

**As built in M3.** Every attempt is a row: `downloading` while in flight, then
`installed`, or `failed` with a `last_error` that never contains a URL or credential.
When a new version installs, the previous `installed` row becomes `stale`, meaning
*superseded*; whether an installed copy is too old is a verdict computed from
`installed_at` and `staleness_threshold_days`, not a status. An attempt that found the
file unchanged leaves no row. On disk: `<geo_data_dir>/<name>/<version>-<sha8>/<file>`,
with `<name>/current` a symlink to the serving version; the previous version is kept and
older ones pruned.

### 8.4 `inference_settings`

`id`, `version integer` UNIQUE, `settings jsonb` (source toggles, weights, thresholds,
suppression parameters), `is_active boolean`, `note text`, `created_by`, `created_at`.

**Invariants:** exactly one `is_active` (partial unique index); **every version is
retained**, so a bad tuning change can be rolled back and old `inference_version`
stamps stay interpretable — F4.AC14.

**Enforced by privilege, since M3:** the application role holds `INSERT` and
`UPDATE (is_active)` only — it cannot rewrite a version's `settings` or delete one
(migration 0005, the same shape as `audit_log`). Version 1 is the code default
(`inference/config.py::DEFAULT_CONFIG`), written the first time anything asks for the
active version. `inference_version` on a visit is `<engine revision>+s<settings version>`,
e.g. `m3.1+s1`, naming both halves of what produced it. `tracelet inference reset-defaults`
saves the current built-in defaults as a new version from the host (audited, `via:
cli_reset_defaults`), for a database seeded before the defaults changed.

### 8.5 `retention_policy`

Singleton (`CHECK (id = 1)`): `visit_days` default 180, `ip_days` default 30,
`audit_days` default 365, `rollup_forever boolean` default true, `updated_by`,
`updated_at`. *Quiet hours moved to `app_settings` in M6, which needs them before M7
builds this table.*

### 8.6 `app_settings`

`key text` PK, `value jsonb`, `updated_by uuid NULL` FK admins `ON DELETE SET NULL`,
`updated_at`. **Invariant: no secrets.** Secrets come from the environment and `0400`
files only — F12.AC3, F14.AC5. Created in M6 (migration 0010). Keys:

| Key | Value | Default when absent |
|---|---|---|
| `notifications.quiet_hours` | `{enabled, start: "HH:MM", end: "HH:MM", timezone: IANA}` — F7.AC9. A window whose end is before its start crosses midnight | `{enabled: false, start: "23:00", end: "07:00", timezone: "Asia/Kolkata"}` |

Every change is owner-only and writes `settings.changed` with the old and new value
(invariant 9); the audit row is the history, and restoring a value is another change.

### 8.7 `rate_limit_buckets` — GCRA state

`key text` PK (e.g. `cap:203.0.113.0/24`, `login:user@example.com`), `tat timestamptz`
(theoretical arrival time), `updated_at`.

**Index:** `(updated_at)` for the cleanup job. Ephemeral and rebuildable — the only
table deliberately **excluded** from the must-never-be-lost set. Shared across both
Uvicorn workers, which is why it lives in PostgreSQL rather than in process memory
(F11.AC8).

**Created in M1, not M7 as originally planned.** Login rate limiting (F8.AC9) needs shared
state across workers from the first milestone that has a login, and the same table serves
the full L2 limiter on the capture path in M2 — so bringing it forward costs one table in
an earlier migration and avoids a per-process limiter that would have to be replaced.
Recorded as a deviation in docs/MILESTONES.md.

---

## 9. Derived and cached data

### 9.1 `rollup_visit_daily`

**As built in M5 (migration 0007, ADR-0016).** The Gate-3 design keyed this table on
seven dimensions and stored `avg_confidence_*` and `unique_visitor_count`. Neither
average nor distinct count adds across rows, so any query summing cells would have
reported a wrong number; both were replaced before the table was created.

PK `(day, link_id, stage, classification, device_class, connection_class, country_code,
admin1)`. `day` is a **local date in the reporting timezone** (`TRACELET_REPORTING_TZ`,
default `Asia/Kolkata`). `country_code` and `admin1` are the **best-guess (advisory)**
fields since migration 0008 (ADR-0018), `''` when no source could place the visit -- a key
column cannot be NULL, and an unplaced visit is worth counting. Days built before 0008 used
the strict fields; 0008 forgot every such day that still had raw visits, so it was rebuilt,
and a day already past raw retention keeps strict keys.
`stage` is a key so each response can state its stage mix (F9.AC20).

Measures, **all additive**: `visit_count`, `consented_count`, `geofence_inside_count`,
`geofence_outside_count`, `has_gps_count`, `has_point_count` (GPS or strict
coordinates), `inferred_count`, `strict_admin2_count`, `strict_city_count`, and for each
level of country, admin1, admin2 and city a `conf_<level>_sum numeric(12,3)` and
`conf_<level>_n integer`, so an average is computed after summing.

Unique visitors are **not** stored: a distinct count does not add across cells, days or
links. They are counted from raw rows on request and are `null` for days past the visit
retention window.

**Refreshed by the `rollup` job every 5 minutes (yesterday and today) and the
`rollup_settle` job daily (the last 7 days, plus up to 31 never-built days).** Each
refresh deletes and re-inserts whole days in one transaction, under an advisory lock.
The dashboard reads these, not raw rows -- F9.AC19, NFR2.AC4. **Survives raw-row
purging**, so history older than the retention window stays analysable -- NFR5.AC4.

### 9.2 `rollup_visit_hourly`

Same shape, keyed on `hour timestamp` (a local wall-clock hour, no tzinfo; India is
UTC+05:30, so UTC hours would straddle every Indian one). Retained 14 days; older hours
are deleted by each refresh.

### 9.2a `rollup_visit_dim_daily` -- added in M5

PK `(day, link_id, classification, dimension, value)`, with `visit_count`. Index
`(dimension, day)`. Long format: one row per distinct value of each dimension per day,
so a new breakdown is a new `dimension` name, not a migration.

| `dimension` | `value` |
|---|---|
| `country`, `asn`, `isp`, `device_class`, `browser`, `os`, `connection_class`, `classification` | the column's value; `''` when unknown |
| `admin1`, `city` | qualified -- `IN\|Karnataka`, `IN\|Karnataka\|Bengaluru` -- because names repeat across countries |
| `app_medium` | the webview host app, or `browser` |
| `screen` | `1080x2400` |
| `conf_country`, `conf_admin1`, `conf_admin2`, `conf_city` | decile `0`..`9`; `''` when unscored (F9.AC9) |
| `signal` | `category\|rule_id`, one row per fired rule of category bot, spoof, spam or network (F9.AC11) |
| `source_flow` | `source>level`: each source that proposed a candidate, by the deepest strict level emitted; `none>none` for a visit no source spoke for (F9.AC7). Inferred visits only |

Rate-limited visits are excluded: they carry no client columns and no inference
(invariant 8). They are counted in the cell tables' stage mix and the funnel.

### 9.2b `rollup_state` -- added in M5

`day date` PK, `refreshed_at timestamptz`, `reporting_tz text`. A day with no row, or a
row for another timezone, has never been built: a request touching it is answered from
raw rows rather than reading the missing rollup as "no visits" (ADR-0016).

### 9.3 `geo_cache`

PK `(source, ip_prefix)`, with `payload jsonb`, `fetched_at`, `expires_at`,
`hit_count`.
**Index:** `(expires_at)`. Caching by **prefix** rather than by address means a repeat
visitor triggers no outbound call at all — F4.AC7, and it is also a privacy
improvement.

---

## 10. Accuracy measurement

### 10.1 `ground_truth_labels`

`id`, `visit_id uuid` **UNIQUE** FK cascade, `true_country_code char(2)`,
`true_admin1`, `true_admin2`, `true_city` `text`, `true_lat`, `true_lng`
`numeric(9,6) NULL`, `connection_kind text` (`wifi`, `mobile_data`, `ethernet`, `vpn`),
`vpn_used boolean`, `labeled_by uuid` FK, `labeled_at`, `notes text`.

One label per visit. Populated by the owner through the labelling UI and CLI — F4.AC15.

### 10.2 `accuracy_runs`

`id`, `run_at`, `inference_version`, `classifier_version`, `git_sha`,
`label_count integer`, `metrics jsonb` (precision and coverage per level, split by
consented and non-consented), `passed boolean`.

Written by the CI accuracy job — F14.AC12. **`label_count` is stored beside every
metric on purpose:** 30 to 60 labels give wide confidence intervals, and any figure
Tracelet reports about itself must be read with the sample size visible (RISKS R9).

---

## 11. Data lifecycle summary

| Data | Retention | Purge behaviour |
|---|---|---|
| `visits.ip_enc` | 30 days (configurable) | Column set to `NULL`; `ip_hmac` and `ip_prefix` persist |
| `visits` + `visit_candidates` | 180 days (configurable) | Batched delete, cascade to candidates, aggregates already rolled up |
| `audit_log` | 365 days (configurable) | Deleted by a maintenance role, since the app role cannot delete |
| `sessions` | Expiry-driven | Reaped continuously |
| `auth_challenges` | Minutes | Reaped once consumed or expired |
| `admin_enrollment_tokens` | 24 hours | Reaped once consumed or expired |
| `password_reset_tokens` | 30 minutes | Reaped once consumed or expired |
| `rate_limit_buckets` | Ephemeral | Cleaned when stale |
| `geo_cache` | TTL | Cleaned on expiry |
| `rollup_*` | **Indefinite** | Never purged; small and the long-term history |
| `outbox` `done` rows | 30 days | `dead` rows retained until acknowledged |
| Reference and config (section 8) | **Indefinite** | Never purged, always backed up |

**Must never be lost** (F12.AC12, NFR5.AC3): `admins`, `admin_recovery_codes`, `links`,
`geofences`, `inference_settings`, `retention_policy`, `app_settings`, `audit_log`,
`rollup_*`. All small, all slow-changing, all in every backup.

**Purge rules:** transactional, batched to avoid long locks, dry-runnable with exact
counts before execution, and audit-logged with the counts actually deleted —
F12.AC8, F10.AC12.

---

## 12. Database roles

| Role | Grants | Why |
|---|---|---|
| `tracelet_app` | `SELECT`, `INSERT`, `UPDATE`, `DELETE` on data tables; **`INSERT` and `SELECT` only on `audit_log`** | Makes the audit log append-only at the engine level, not by convention — NFR5.AC5 |
| `tracelet_maint` | Additionally `DELETE` on `audit_log`; `VACUUM`; used by purge, backup and restore-verify | Separates routine traffic from destructive maintenance |
| `tracelet_migrate` | DDL | Used only by Alembic, never by the running application |

A SQL-injection foothold in the application path therefore cannot erase the evidence of
itself.

**How the grant is actually arrived at.** `ALTER DEFAULT PRIVILEGES` grants all four verbs
on every table Alembic creates, so `audit_log` **revokes** `UPDATE` and `DELETE` from
`tracelet_app` explicitly in migration `0002`, and `UPDATE` from `tracelet_maint` (which
keeps `DELETE`, for retention purging). That explicit step is easy to lose in a later
migration, so an integration test reads
`information_schema.table_privileges` and asserts the grant set — and two more attempt an
`UPDATE` and a `DELETE` as the application role and require `permission denied`.
