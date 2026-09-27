# DATA MODEL — Tracelet

**Status:** approved at Gate 3, 2026-09-25.
PostgreSQL 16 + PostGIS 3.4 (ADR-0002). All timestamps are `timestamptz` in UTC.
Any change here needs an Alembic migration **in the same commit** — CLAUDE.md section 2.

**Conventions.** Primary keys are UUIDv7 where the row is externally referenced (time
ordered, so index locality is good) and `bigserial` where the row is append-only
internal volume. `NOT NULL` unless a `NULL` carries a specific documented meaning.
Enumerations are PostgreSQL `ENUM` types, listed in section 2.

---

## 1. Entity relationships

```
   admins ──┬──< admin_recovery_codes
            ├──< admin_enrollment_tokens
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
   ├ rollup_visit_daily
   ├ rollup_visit_hourly
   ├ geo_cache
   └ accuracy_runs
```

---

## 2. Enumerated types

| Type | Values |
|---|---|
| `admin_role` | `owner`, `analyst` |
| `admin_status` | `pending_enrollment`, `active`, `disabled` |
| `visit_stage` | `server`, `enriched`, `server_only`, `rate_limited` |
| `classification` | `human`, `bot`, `crawler`, `datacenter`, `spam`, `spoofed`, `unknown` |
| `consent_state` | `granted`, `denied`, `unavailable`, `not_asked`, `blocked_by_webview` |
| `connection_class` | `broadband`, `mobile`, `datacenter`, `vpn_suspected`, `tor`, `business`, `unknown` |
| `asn_type` | `broadband`, `mobile`, `hosting`, `business`, `education`, `government`, `unknown` |
| `device_class` | `mobile`, `tablet`, `desktop`, `tv`, `server`, `bot`, `unknown` |
| `geo_level` | `country`, `admin1`, `admin2`, `city`, `point` |
| `geofence_state` | `inside`, `outside`, `undetermined` |
| `inference_source` | `gps`, `geolite2`, `ip2location`, `ipinfo`, `dbip`, `rdns`, `asn_org`, `cf_colo`, `external_api`, `latency`, `timezone` |
| `notify_priority` | `high`, `normal`, `silent` |
| `outbox_kind` | `telegram.visit_alert`, `telegram.password_reset`, `telegram.health_alert`, `telegram.test` |
| `outbox_status` | `pending`, `in_flight`, `done`, `failed`, `dead` |
| `backup_kind` | `daily`, `weekly`, `manual` |
| `geo_db_status` | `installed`, `downloading`, `failed`, `stale` |
| `shape_kind` | `polygon`, `circle` |

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
1. **At least one `active` `owner` must always exist.** Enforced by a `CONSTRAINT
   TRIGGER ... DEFERRABLE INITIALLY DEFERRED` on delete and on update of `role` or
   `status` — F8.AC13. Application-level checks are not sufficient, because a
   concurrent demotion of two owners would pass both checks.
2. `status='active'` requires `password_hash IS NOT NULL AND totp_enrolled_at IS NOT
   NULL` — F8.AC4. **No account can reach the dashboard without TOTP.**
3. `telegram_chat_id` must be verified before it can receive a reset link — F8.AC7.

### 3.2 `admin_recovery_codes`

`id bigserial` PK, `admin_id` FK cascade, `code_hash text` (Argon2id), `used_at
timestamptz NULL`, `created_at`.
**Indexes:** `(admin_id)`, `UNIQUE(admin_id, code_hash)`, partial `(admin_id)` where
`used_at IS NULL` for the remaining-count warning at F8.AC6.
**Invariant:** a code is single-use — setting `used_at` is conditional
(`WHERE used_at IS NULL`) so a concurrent double-submit cannot consume it twice.

### 3.3 `admin_enrollment_tokens`

`id bigserial` PK, `admin_id` FK, `token_hash bytea`, `expires_at`, `used_at NULL`,
`created_by` FK, `created_at`.
**Invariant:** single-use and time-limited. **No default password exists anywhere in the
system** — F8.AC15.

### 3.4 `sessions`

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

### 3.5 `audit_log`

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

---

## 4. Tracking links

### 4.1 `links`

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | |
| `slug` | `citext` UNIQUE | 4–32 chars, `[a-z0-9-]`, `CHECK` constrained |
| `label` | `text` | |
| `destination_url` | `text` | `CHECK`: starts `https://`, length ≤ 2048. Full validation in the application — F1.AC2 |
| `is_active` | `boolean` | |
| `is_default` | `boolean` | |
| `notify_policy` | `jsonb` | `{inside, outside, automated}` priorities — F1.AC5 |
| `interstitial_ms` | `integer` | `CHECK BETWEEN 300 AND 1500`, default 700 — F1.AC6 |
| `cloned_from` | `uuid` NULL FK self | F1.AC9 |
| `created_by` | `uuid` FK | |
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

**Network — see F12.AC1 and RW-3**

| Column | Type | Notes |
|---|---|---|
| `ip_hmac` | `bytea` | HMAC-SHA256 with the stable pepper. **Durable** |
| `ip_prefix` | `inet` | /24 for IPv4, /48 for IPv6. **Durable** |
| `ip_enc` | `bytea` NULL | AES-256-GCM `nonce ‖ ciphertext ‖ tag`, AAD = `id`. **Purged at TTL** |
| `ip_key_version` | `smallint` NULL | Rotation support — F12.AC5 |
| `ip_purge_after` | `timestamptz` NULL | |
| `asn` | `integer` NULL | |
| `asn_org` | `text` NULL | ISP name |
| `asn_type` | `asn_type` | |
| `rdns_ptr` | `text` NULL | |
| `connection_class` | `connection_class` | |
| `is_datacenter`, `is_vpn_suspected`, `is_tor`, `is_proxy_suspected` | `boolean` | F5.AC7, F5.AC9 |
| `cf_colo` | `char(3)` NULL | Edge colo — source S8 |
| `cf_country` | `char(2)` NULL | |

**There is no plaintext IP column.** By design, permanently.

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
`honeypot_tripped boolean`, `header_order_hash bytea`, `http_version text`,
`tls_version text NULL`, `classifier_version text`,
**`signals jsonb`** — array of fired rules `[{rule_id, category, weight, detail}]`.

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

`matched_geofence_ids uuid[]`, `geofence_state geofence_state`.

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
| `(strict_country_code, strict_admin1)` | Geographic breakdowns |
| `GIN (signals)` | "which visits fired rule X" |
| partial `(occurred_at)` where `stage='server'` | **The 90 s sweeper** — F2.AC7 |
| partial `(ip_purge_after)` where `ip_enc IS NOT NULL` | The IP purge job — F12.AC2 |
| partial `(occurred_at)` where `finalized_at IS NULL` | Stuck-visit detection |

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
5. `geofence_state='undetermined'` whenever `geopoint IS NULL`. **An abstaining
   inference is never silently treated as "outside"** — F6.AC6.
6. `enrichment_consumed_at` is set by a conditional update, so a replayed nonce cannot
   double-enrich.
7. `ip_enc IS NULL` after `ip_purge_after`, while `ip_hmac` and `ip_prefix` persist —
   F12.AC2.
8. `stage='rate_limited'` rows carry no client columns and no inference; they exist to
   make shedding visible — F11.AC3.

### 5.4 `visit_candidates` — the derivation trail

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
trail (F4.AC6, F4.AC11). Every `accepted=false` row has a `suppressed_reason`.
Cascade-deleted with the visit.

---

## 6. Geofencing

### 6.1 `geofences`

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | |
| `name`, `description` | `text` | |
| `shape_kind` | `shape_kind` | |
| `area` | `geography(Polygon,4326)` NOT NULL | Circles stored buffered as polygons |
| `center` | `geography(Point,4326)` NULL | Retained for circle round-trip editing |
| `radius_m` | `numeric(10,2)` NULL | Retained for circle round-trip editing |
| `priority` | `integer` | Higher wins on overlap — F6.AC7 |
| `is_active` | `boolean` | |
| `notify_on_enter` | `boolean` | |
| `notify_priority` | `notify_priority` | |
| `link_ids` | `uuid[]` NULL | `NULL` = applies to all links |
| `created_by` | `uuid` FK | |
| `created_at`, `updated_at` | `timestamptz` | |

**Indexes:** `GIST (area)`; partial `(priority DESC)` where `is_active`.

**Invariants:** `CHECK (ST_IsValid(area::geometry))`; `CHECK (ST_NPoints(area::geometry)
<= 2000)` to bound evaluation cost (F6.AC4); `shape_kind='circle'` requires `center` and
`radius_m` non-null. Evaluation uses `ST_Covers(area, geopoint)` — **geodesically
correct because the column is `geography`, not `geometry`**. That correctness is a
principal reason PostGIS was chosen (ADR-0002).

---

## 7. Notifications

### 7.1 `outbox`

| Column | Type | Notes |
|---|---|---|
| `id` | `bigserial` PK | |
| `kind` | `outbox_kind` | |
| `dedup_key` | `text` UNIQUE NULL | e.g. `visit_alert:{link_id}:{visitor_id}:{utc_date}` |
| `payload` | `jsonb` | Self-contained; references a visit by id with **no FK** |
| `status` | `outbox_status` | |
| `attempts` | `integer` | |
| `max_attempts` | `integer` | Default 8 |
| `next_attempt_at` | `timestamptz` | |
| `locked_by` | `text` NULL | Worker identity |
| `locked_at` | `timestamptz` NULL | |
| `last_error` | `text` NULL | |
| `created_at`, `completed_at` | `timestamptz` | |

**Indexes:** `UNIQUE(dedup_key)`; partial `(next_attempt_at)` where `status IN
('pending','failed')`; partial `(status)` where `status='dead'` for the health panel.

**Invariants**
1. Inserted in the **same transaction** as visit finalisation — NFR5.AC2, F7.AC5. A
   visit never exists without its queued alert; a rolled-back visit never emits one.
2. **`UNIQUE(dedup_key)` is what actually implements the 24-hour deduplication rule**
   (F7.AC2). Doing it in application code would race under concurrent visits.
3. Claimed with `SELECT … FOR UPDATE SKIP LOCKED LIMIT n` — correct with two workers.
4. **No FK to `visits`.** A retention purge must not block a pending notification, and
   the payload is already self-contained.
5. `attempts >= max_attempts` sets `status='dead'`, surfaced with a manual retry —
   F7.AC6, F10.AC13.

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

### 8.2 `rdns_city_codes` — the lexicon

`id bigserial` PK, `pattern text` (regex), `code text`, `city`, `admin1`, `country_code`,
`lat`, `lng`, `confidence numeric(4,3)`, `isp_hint text NULL`, `notes text`, `is_active
boolean`, `lexicon_version integer`, `created_at`, `updated_at`.

**Indexes:** `(is_active, country_code)`, `UNIQUE(pattern, lexicon_version)`.
Seeded from a versioned data file, editable from the dashboard. **Spike A in KICKOFF
section 6 measures whether this table can carry the weight the accuracy plan puts on
it** — RISKS R3.

### 8.3 `geo_databases`

`id`, `name text` UNIQUE-per-status, `version text`, `released_at`, `installed_at`,
`file_path`, `sha256`, `size_bytes`, `status geo_db_status`, `last_check_at`,
`last_error text NULL`, `is_enabled boolean`, `staleness_threshold_days integer`.

**Invariant:** at most one row per `name` with `status='installed'` — partial unique
index. Atomic symlink swap means a failed update leaves the previous version serving
(F10.AC4).

### 8.4 `inference_settings`

`id`, `version integer` UNIQUE, `settings jsonb` (source toggles, weights, thresholds,
suppression parameters), `is_active boolean`, `note text`, `created_by`, `created_at`.

**Invariants:** exactly one `is_active` (partial unique index); **every version is
retained**, so a bad tuning change can be rolled back and old `inference_version`
stamps stay interpretable — F4.AC14.

### 8.5 `retention_policy`

Singleton (`CHECK (id = 1)`): `visit_days` default 180, `ip_days` default 30,
`audit_days` default 365, `rollup_forever boolean` default true, `quiet_hours jsonb`,
`updated_by`, `updated_at`.

### 8.6 `app_settings`

`key text` PK, `value jsonb`, `updated_by`, `updated_at`.
**Invariant: no secrets.** Secrets come from the environment and `0400` files only —
F12.AC3, F14.AC5.

### 8.7 `rate_limit_buckets` — GCRA state

`key text` PK (e.g. `cap:203.0.113.0/24`, `login:user@example.com`), `tat timestamptz`
(theoretical arrival time), `updated_at`.

**Index:** `(updated_at)` for the cleanup job. Ephemeral and rebuildable — the only
table deliberately **excluded** from the must-never-be-lost set. Shared across both
Uvicorn workers, which is why it lives in PostgreSQL rather than in process memory
(F11.AC8).

---

## 9. Derived and cached data

### 9.1 `rollup_visit_daily`

PK `(day, link_id, country_code, admin1, classification, device_class,
connection_class)`, with `visit_count`, `unique_visitor_count`, `human_count`,
`bot_count`, `consented_count`, `enriched_count`, `geofence_inside_count`,
`avg_confidence_admin1`, `avg_confidence_city`, `refreshed_at`.

**Refreshed nightly.** The dashboard reads these, not raw rows — F9.AC19, NFR2.AC4.
**Survives raw-row purging**, so history older than the retention window stays
analysable — NFR5.AC4.

### 9.2 `rollup_visit_hourly`

Same shape at hour granularity, retained 14 days, for the timeline view.

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
