# API — Tracelet

**Status:** approved at Gate 3, 2026-09-25.
Any change to an endpoint, payload, or error shape updates this document **and**
regenerates the TypeScript client in the same commit. CI fails on drift (F14.AC9).

**Base URL.** `https://{TRACELET_DOMAIN}`
**Admin API prefix.** `/api/v1`
**Content type.** `application/json` for all admin routes. `text/html` for the capture
page and `/privacy`.
**Time.** Every timestamp is ISO-8601 with an explicit UTC offset.
**Pagination.** Cursor-based on every list route: `?limit=50&cursor=<opaque>`; the
response carries `next_cursor`, which is `null` on the last page. **No offset
pagination** — it is unstable while rows are being inserted.

---

## 1. Route naming rule — not optional

No public path or query key may contain `track`, `collect`, `analytics`, `pixel`,
`beacon`, or `telemetry`. Content blockers match those substrings in URLs and will
silently kill the request; that is the root cause of B6, not bot detection. A CI test
asserts this (F2.AC11, F14.AC11).

This is why the capture route is `/r/{slug}`, enrichment is `/api/v1/s/{nonce}`, and the
honeypot is `/api/v1/hp/{token}`.

---

## 2. Authentication and authorisation

| Aspect | Behaviour |
|---|---|
| Mechanism | Opaque 256-bit session token in a cookie. **No JWT** — ADR-0008 |
| Cookie | `__Host-tracelet_session`, flags `HttpOnly; Secure; SameSite=Strict; Path=/` |
| MFA | TOTP mandatory. A session that has not completed TOTP reaches **no** `/api/v1` route except the auth routes |
| CSRF | Every state-changing request needs `X-CSRF-Token` matching the session secret **and** a valid `Origin` — F8.AC11 |
| Roles | `owner` full; `analyst` read-only. Checked server-side on every route |
| Revocation | Immediate, per session |

**Role failures return `403` with `code: "FORBIDDEN_ROLE"`. Unauthenticated requests
return `401` with `code: "UNAUTHENTICATED"`.** The distinction is deliberate: `403` for a
valid session lacking rights, `401` for no valid session.

---

## 3. Public capture surface

### `GET /r/{slug}`

The tracking link. **Returns 200 `text/html`, never a 3xx** (F2.AC1) — a 302 offers no
collection opportunity and is the pattern Safe Browsing classifies as an open
redirector (B4).

**`GET /r` and `GET /r/`** — the same, through the **default link** (F1.AC3 as amended). Both
spellings answer directly, never with a slash redirect. With no default, or an inactive one,
the answer is the same 404 as an unknown slug. It is not a fallback: a bad slug is still a
404.

| | |
|---|---|
| Auth | None |
| Rate limit | L2 GCRA per IP prefix: 30/min (burst 10) **and** 300/hr (burst 60) (F11.AC2) |
| Side effects | Commits a `visits` row with `stage='server'` **before responding** (F2.AC2) |

**Responses**

| Status | Meaning |
|---|---|
| `200 text/html` | Capture page: notice, privacy link, `Continue now` with the real destination href, `<noscript>` meta-refresh, hidden honeypot, inline nonce-scoped enrichment script |
| `302` | **Rate-limited only.** Redirects straight to the destination with no capture, recorded as `stage='rate_limited'`. The human is never punished for abuse control (F11.AC3) |
| `404 text/html` | Unknown, inactive, or archived slug. Leaks nothing about which links exist (F2.AC14) |
| `503 text/html` | The visit could not be recorded. **If the destination is known, the page still redirects** immediately (F15.AC6). It is known if the link was served by this worker before -- a small in-process cache of live links, consulted only when the database cannot answer |
| `503 text/html` | The database is unreachable **and** this link is not in the cache. The one outcome that cannot redirect, because there is nowhere to redirect to; the page asks the visitor to retry |

The response embeds the enrichment nonce: a single-use HMAC over `visit_id`, the IP
prefix, and an expiry, with a 60 s TTL (F2.AC6).

**As shipped in M2.** Every response carries its own CSP nonce authorising only the
inline blocks this server rendered, and `Cache-Control: no-store` -- a cached interstitial
would be served without reaching the server, and the visit would never be recorded. The
page is identical for link-preview fetchers; serving them something different would be
cloaking. No exception reaches a visitor: a failure while rendering falls back to a fixed
string that still redirects (F15.AC7).

---

### `POST /api/v1/s/{nonce}`

Client enrichment. **Additive and always allowed to fail** — nothing visitor-visible
depends on it (ADR-0004).

**Request**

```json
{
  "screen": { "w": 1170, "h": 2532, "dpr": 3.0, "colorDepth": 24, "touchPoints": 5 },
  "viewport": { "w": 390, "h": 664 },
  "hardware": { "cores": 6, "deviceMemoryGb": null },
  "gpu": { "vendor": "Apple Inc.", "renderer": "Apple A16 GPU" },
  "locale": { "tzIana": "Asia/Kolkata", "tzOffsetMin": 330, "languages": ["en-IN","en"] },
  "hashes": { "canvas": "…", "audio": "…", "font": "…", "webgl": "…" },
  "probes": {
    "webdriver": false, "chromeObject": true, "pluginCount": 0,
    "permissionsAnomaly": false, "fontCount": 84, "outerWidth": 390
  },
  "geolocation": {
    "state": "granted",
    "lat": 12.9716, "lng": 77.5946, "accuracyM": 18.0
  },
  "honeypot": { "linkClicked": false, "fieldFilled": false },
  "timing": { "pageLoadMs": 142, "collectMs": 37 }
}
```

Every field is optional. **The payload is attacker-controlled and treated only as a
claim** — it is cross-checked against server-observed signals, never trusted
(ARCHITECTURE section 5.2).

| Status | Meaning |
|---|---|
| `204` | Merged. Visit finalised, `stage='enriched'` |
| `410` | Nonce expired, already consumed, or bound to a different prefix. Logged |
| `413` | Payload above cap |
| `422` | Schema violation. Field-level `errors` returned |
| `429` | Over limit |

**As shipped in M2:**

* The payload is capped at **16 KB** (`413` above it; Caddy's 64 KB body cap is the outer
  bound). Unknown keys are **ignored, not rejected**: a newer page talking to an older API
  must degrade to fewer signals, never to a `422` that loses all of them.
* A `422` does **not** spend the nonce -- validation runs before the conditional update,
  so a malformed first attempt can be retried (the lesson of docs/ERRORS.md E15).
* `geolocation.state` is one of `granted`, `denied`, `prompt`, `unavailable`, `unsupported`
  or `timeout`. The page consults the Permissions API and **never shows a prompt** (F4.AC1
  as amended, RISKS R20). `prompt` -- permission not yet decided, so not asked -- is stored
  as `consent_state='not_asked'`. `unsupported` -- no Permissions API, so the page cannot
  know without prompting -- and `timeout` -- permission granted but no position before the
  redirect -- are stored, like `unavailable`, as `consent_state='unavailable'` with the
  reason in `signals`. Coordinates are stored
  only with `granted`; with anything else they are dropped, not rejected. See RISKS R20.
* `probes` is validated and **not yet persisted**: what a headless-browser probe *means*
  is M4's decision. `hashes.audio` is always `null` from the M2 page -- an
  `OfflineAudioContext` render is too slow for the interstitial.
* Client hashes are re-hashed to a fixed 16 bytes before storage, since the client can
  send anything in those fields.
* The page sends the enrichment ~150 ms before its redirect, with whatever geolocation
  answer exists by then.

### `GET /api/v1/hp/{token}`

Stealth honeypot (F5.AC6). Always returns `204` regardless of outcome, so a probe learns
nothing. Records the hit and marks the visit as automation. Deliberately not named in a
way a filter list would match.

The token is the visit's enrichment nonce. **Expiry is ignored** -- an automated client
can follow the link long after the page loaded, and that late hit is the evidence wanted --
but the MAC and the prefix binding still hold, so nobody can trip someone else's visit.
Limited to 10/min per prefix; over the limit it still answers `204`.

### `GET /privacy`

Public privacy notice (F2.AC13). Lists every data category collected, every inference
source **including any enabled external service**, retention periods, and the required
CC-BY attributions for DB-IP Lite and GeoNames.

Retention periods and the external-service statement are rendered from live
configuration, so the notice cannot drift from what the system does. It also carries the
GeoLite2, IP2Location LITE and IPinfo attributions. The inference sources are the full M3
set, named from M2 so the notice is already complete when M3 enables them -- **re-verify
against the enabled sources at M3 and before the M9 deploy.**

### `GET /healthz` · `GET /readyz`

No auth, no detail. `/healthz` is liveness. `/readyz` returns `503` when the database is
unreachable or migrations are pending (F15.AC5).

---

## 4. Admin authentication

**Shipped in M1.** The table below is the implemented contract; `api/openapi.json` and the
generated TypeScript client are regenerated from it and checked for drift by CI (F14.AC9).

| Method | Path | Role | Purpose |
|---|---|---|---|
| `POST` | `/api/v1/auth/login` | — | Step 1. `{email, password}` → `200 {mfa_token, expires_at}` |
| `POST` | `/api/v1/auth/mfa` | — | Step 2. `{mfa_token, code}` → `204` + `Set-Cookie` + `X-CSRF-Token` |
| `POST` | `/api/v1/auth/recovery-code` | — | `{email, code}` → `204` + session + `X-Recovery-Remaining` |
| `POST` | `/api/v1/auth/logout` | any | `204`, revokes the current session |
| `GET` | `/api/v1/auth/me` | any | Current admin, role, TOTP state, `recovery_codes_remaining`, `csrf_token`, theme, timezone, and (M5) `reporting_tz`, the zone analytics buckets are cut in |
| `PATCH` | `/api/v1/auth/me/preferences` | any | `{theme?, timezone?}` → the `/me` body. Theme `semi_dark` (default), `light` or `dark`; timezone an IANA name. **Added in M5** (F9.AC16). Display only, so not audited |
| `POST` | `/api/v1/auth/reset/request` | — | `{email}` → **always `202`**. Telegram-delivered link |
| `POST` | `/api/v1/auth/reset/confirm` | — | `{token, new_password}` → `204`, revokes every session |
| `POST` | `/api/v1/auth/password` | any | `{current_password, new_password}` → `204`, revokes every **other** session |
| `POST` | `/api/v1/auth/enroll` | — | `{token, password}` → `200 {secret, otpauth_uri, recovery_codes[], hint, confirm_token}` — **returned exactly once** |
| `POST` | `/api/v1/auth/totp/confirm` | — | `{confirm_token, code}` → `204` + `Set-Cookie`. Activates the account **and signs in** |
| `POST` | `/api/v1/auth/totp/regenerate-codes` | any | `200 [10 codes]`, invalidates the old set |
| `GET` | `/api/v1/auth/sessions` | any | Own active sessions |
| `DELETE` | `/api/v1/auth/sessions/{id}` | any | Revoke one of your own. Another admin's is `404`, not `403` |
| `POST` | `/api/v1/auth/telegram/verify/start` | any | `{chat_id}` → `202`, sends a code to that chat |
| `POST` | `/api/v1/auth/telegram/verify/confirm` | any | `{code}` → `204`, trusts the chat for recovery |

### 4.1 Four things that differ from the Gate-3 design

Each is a consequence of building it, and each is recorded here rather than left for a
reader to discover from the code (CLAUDE.md §2).

**`/totp/enroll` became `/enroll`, and takes the invitation token.** Enrolment is the
*unauthenticated* first use of a one-time link, not an action by a signed-in admin. The old
path implied a session that cannot exist yet.

**`/totp/confirm` takes a `confirm_token` and returns a session.** The account has no
session and *cannot* have one — the CHECK constraint forbids `active` without
`totp_enrolled_at` — so confirmation needs some other proof of who is confirming. Taking an
admin id from the request would let anyone activate any pending account with a code from
their own authenticator, which is a complete authentication bypass. The one-time token
issued by `/enroll` is that proof.

It then issues the session directly. Both factors have just been proved — the password at
`/enroll`, a live code here — and asking for a second code would be refused as a replay,
because the step just accepted is now the stored high-water mark (F8.AC5).

**`/telegram/verify/start` and `/confirm` are new.** The design had a single
`POST /api/v1/admins/{id}/telegram/verify`. Verification is two steps by nature — send a
code to the proposed chat, then prove it arrived — and it is a *self-service* action on your
own recovery channel rather than an owner administering someone else, so it sits under
`/auth`.

**`X-Recovery-Remaining`** on the recovery-code response, so the UI can warn when the set
runs low without a second round trip.

### 4.2 Enumeration resistance (F8.AC10)

Login, reset-request and recovery-code responses are **identical in body and
indistinguishable in timing** for existing and non-existing accounts, including a dummy
Argon2 verification on an unknown identifier — measured at 1.01x by hand, and asserted
within 3x by an integration test. `/auth/reset/request` returns `202` whether or not the
account exists, whether or not it has a verified chat, and whether or not delivery
succeeded; the truth goes to the audit log. That is enumeration resistance, not a bug, and a
client must not add a friendlier per-status message that undoes it.

`/auth/mfa` reports a **replayed** code as invalid rather than as reused, for the same
reason: telling a caller their code was correct-but-spent confirms they hold a real code.

### 4.3 Sessions and CSRF

The session is an opaque server-side row, delivered as a `__Host-tracelet_session` cookie
with `HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/` and no `Domain` (F8.AC2, ADR-0008).
Nothing readable by script is ever handed to the client, so there is no token in
`localStorage` for an injection to steal.

Every state-changing request must carry **both** an acceptable `Origin` (or `Referer`) and
the session's CSRF secret in `X-CSRF-Token` (F8.AC11). The token is returned in that header
by `/auth/mfa`, `/auth/totp/confirm` and `/auth/recovery-code`, and in the body of
`/auth/me` as `csrf_token`.

> **Read that header case-insensitively.** The stack normalises `X-CSRF-Token` to
> `X-Csrf-Token`. A client that indexes a plain object by the exact name it sent finds
> nothing, and every subsequent state-changing request fails with a 403 that looks like a
> server bug.

A new session means a new CSRF secret, so a client that caches the token across a
re-authentication will start collecting 403s.

### 4.4 Rate limits

`login` 5 per 15 min per identifier (burst 5) and 20/hr per prefix (burst 10); `mfa` 10 per
15 min per prefix; `reset/request` 3/hr per identifier; `recovery-code` 5/hr per identifier
— deliberately the tightest, because each attempt costs ten Argon2 verifications at 32 MiB
and is therefore a memory-amplification vector as well as a credential one (F8.AC9,
ADR-0010).

Separately, **eight consecutive failed passwords lock the account for 30 minutes** and
answer `423 ACCOUNT_LOCKED` with a retry hint. The limiter throttles a network; the lockout
protects one identity from a distributed attempt that stays under the per-prefix limit.

**A 429 announces itself, and that is deliberate** (F8.AC9 as amended 2026-09-28, SPEC
section 11 row 6). `Retry-After` is required by F11.AC10, and a limit the admin cannot see
is one they keep retrying into. What a refusal may **not** do is differ between an existing
and a non-existing account — the bucket is keyed on the submitted identifier before any
lookup, so an address that has never existed is throttled identically, and an integration
test compares the two responses field by field.

---

## 5. Admin management — `owner` only

**Shipped in M1.** Every call writes an `audit_log` row (CLAUDE.md invariant 9).

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/admins` | List, with `last_login_at` derived from the audit log |
| `POST` | `/api/v1/admins` | `{email, display_name, role}` → `201 {admin, enrollment_url, enrollment_expires_at}`. Creates `pending_enrollment`. **No password is ever set here** (F8.AC15) |
| `GET` | `/api/v1/admins/{id}` | One admin |
| `PATCH` | `/api/v1/admins/{id}` | `{display_name?, role?, status?}`. Refuses anything that would remove the last active owner → `409 LAST_OWNER` |
| `DELETE` | `/api/v1/admins/{id}` | `204`. Your own account → `422`; see below |
| `POST` | `/api/v1/admins/{id}/enrollment-token` | `201 {url, expires_at}`, single-use, 24 hours |

There is deliberately **no way to set another admin's password.** An owner invites, and the
invitee sets their own through a one-time link, so no default password exists anywhere in
the system and an owner cannot impersonate a colleague with a password they know.

### 5.1 `409 LAST_OWNER`, and why DELETE answers `422` instead

`PATCH` returns `409 LAST_OWNER` for a demotion or a disable that would leave zero active
owners (F8.AC13).

`DELETE` does not, sequentially, and the reason is worth stating because the Gate-3 design
assumed otherwise. Deleting requires the owner role. If the actor is an active owner and the
target is somebody else, then the actor *is* a remaining active owner and the target was
never the last one. The only reachable case is an owner deleting themselves, which is
refused first, on its own terms, as `422 VALIDATION_FAILED` — it is a mistake at any role.

`409` on this route therefore exists only for the concurrent case: two owners deleting each
other at the same moment, where the row lock and the deferred trigger decide it. Both paths
are covered by integration tests.

**Changing a status to anything other than `active` revokes that admin's sessions
immediately**, because "disabled" that takes effect at session expiry means nothing for up
to twelve hours.

---

## 6. Tracking links

| Method | Path | Role | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/links` | any | List with per-link visit counts |
| `POST` | `/api/v1/links` | owner | Create |
| `GET` | `/api/v1/links/{id}` | any | Detail |
| `PATCH` | `/api/v1/links/{id}` | owner | Update. Destination change is audit-logged with old and new (F1.AC8) |
| `POST` | `/api/v1/links/{id}/clone` | owner | Rotate a burned slug, keeping configuration (F1.AC9) |
| `POST` | `/api/v1/links/{id}/default` | owner | Set default; clears the previous atomically |
| `POST` | `/api/v1/links/{id}/archive` | owner | Archive |
| `DELETE` | `/api/v1/links/{id}` | owner | `409 LINK_HAS_VISITS` if referenced — archive instead (F1.AC10); with `?with_visits=true`, a permanent delete of the link and its visits (SPEC §11 row 25) |
| `GET` | `/api/v1/links/{id}/delete-preview` | owner | Exactly what a permanent delete would remove. **M7** |

**Create / update body**

```json
{
  "slug": "ig-bio",
  "label": "Instagram bio",
  "destination_url": "https://example.com/landing",
  "is_active": true,
  "interstitial_ms": 700,
  "notify_policy": { "inside": "high", "outside": "normal", "undetermined": "normal", "automated": "silent" },
  "ask_location": false
}
```

`destination_url` validation: `https` scheme, publicly-resolvable host, no embedded
credentials, length ≤ 2048 (F1.AC2). A rejection returns `422` with a field-level error.

`ask_location` (F1.AC11, ADR-0021): `true` makes the capture page ask for the visitor's
location, with consent text, and wait up to 15 s for the answer; default `false`. Changing it
is audited like any other field. *Added in M6.*

`notify_policy` (F1.AC5): each of `inside`, `outside` and `undetermined` is `high`, `normal`
or `silent`, defaulting to `high`, `normal`, `normal`. `automated` accepts only `silent`;
anything else is a field-level `422`, because automated traffic never notifies (CLAUDE.md
invariant 6). An omitted key takes its default. How the policy combines with a geofence's
`notify_priority` is SPEC §11 row 18. *`undetermined` and the `automated` restriction were
added in M6.*

**The destination is only ever read from this row. Never from a request parameter,
header, or path** (F1.AC7, F13.AC3).

### 6.1 As shipped in M2

* **Responses** carry `capture_url` (the full `/r/{slug}` address) and `visit_count`.
  `GET /api/v1/links` hides archived links unless `include_archived=true`.
* **Slugs are normalised to lower case** before they are validated, matching the capture
  path, which accepts any case (docs/ERRORS.md E24). A slug differing only in case is a
  duplicate.
* **Destination rejections** carry a field-level code: `NOT_HTTPS`, `CREDENTIALS_IN_URL`,
  `NO_HOST`, `TOO_LONG`, `SELF_REFERENCE` (a destination on this site would loop back into
  the capture surface), `UNRESOLVABLE`, `NOT_PUBLIC`. *Every* resolved address must be
  public, not the first: a split-horizon name with one private record is the case worth
  refusing. The server never fetches the destination, so this is not SSRF defence -- it
  stops an owner sending visitors to an internal host by mistake.
* **`PATCH` may change the slug**, which retires the old one immediately. To rotate a
  burned slug while keeping the old one working, `clone` instead.
* **`clone`** takes `{"slug": …, "label"?: …}`. The copy keeps the destination, interstitial
  and notification policy and is not the default; the original keeps its visits and is
  left as it was.
* **The default.** The first non-archived link becomes the default. `POST
  …/{id}/default` on an archived link is `422`. Archiving or deleting the default while
  another live link exists is `409 DEFAULT_LINK_REQUIRED`. What the default *does* is not
  yet specified by any requirement; see docs/DATA_MODEL.md section 4.1.
* **Every write** is owner-only and writes an `audit_log` row: `link.created`,
  `link.updated` (with `{from, to}` for each changed field -- F1.AC8), `link.cloned`,
  `link.default_changed`, `link.archived`, `link.deleted`.

### 6.2 Permanent delete — added in M7 (SPEC §11 row 25)

`GET /api/v1/links/{id}/delete-preview` returns `{slug, is_default, archived, visits,
visit_candidates, rollup_rows, geofences_updated: [{id, name}], geofences_deactivated: [{id,
name}]}` and deletes nothing. `DELETE /api/v1/links/{id}?with_visits=true` then removes, in one
transaction, the link, its visits and their candidates, and its rollup rows (foreign keys
cascade); removes it from every geofence's `link_ids`, switching off a geofence that was scoped
to it alone; and leaves queued alerts to send. `204`. It works on archived links too. The
default link while another live one exists is still `409 DEFAULT_LINK_REQUIRED`. Without
`with_visits`, a link with visits is `409 LINK_HAS_VISITS` as before. Audited `link.deleted`
with `slug`, `destination_url`, `with_visits` and the counts deleted.

---

## 7. Visits

### `GET /api/v1/visits`

**Implemented in M2:** `from`, `to`, `link_id`, `stage` and `classification` (both
repeatable), `webview_host`, `include_automated`, `limit` (1–200, default 50) and `cursor`.
The rest of the list below arrives with the milestone that fills its column -- location
(M3), identity and scores (M4), geofence (M6) -- and `search` and `sort` with the dashboard
(M5). The response is `{"items": [...], "next_cursor": "…" | null}`, newest first,
keyset-paginated over `(occurred_at, id)` so a page never repeats or skips a row.

`include_automated=false` excludes `crawler`, `bot`, `spam`, `spoofed` and `datacenter`.
An explicit `classification` filter overrides it.

Filters, all optional and composable (F9.AC13):

`from`, `to`, `link_id`, `classification` (repeatable), `country_code`, `admin1`,
`city`, `asn`, `device_class`, `connection_class`, `consent_state`, `geofence_id`,
`visitor_id`, `stage`, `min_confidence_admin1`, `min_confidence_city`, `has_gps`,
`is_proxy_suspected`, `include_automated` (**default `false`**), `webview_host`,
`search`, `limit`, `cursor`, `sort`.

`include_automated` defaults to `false` so bots and crawlers do not pollute the default
view; the flag makes their exclusion explicit rather than hidden.

**As shipped in M5:** every filter above except `geofence_id`'s data (M6 fills
`matched_geofence_ids`; the filter already works). One definition
(`analytics/filters.py`) serves the list, the export and every analytics endpoint, so a
chart and the table under it cannot disagree about what a filter means. Specifics:

- **Location filters match the best-guess (advisory) fields** (`country_code` is
  case-insensitive), as the breakdowns count them (ADR-0018), so a filter selects exactly
  the visits a chart counted. Strict fields stay on every visit for what is acted on.
- `visitor_id` is the 32-character hex shown on a visit (128 bits, ADR-0006); anything else is `422`.
- `is_proxy_suspected` matches `true` or `false` only; `NULL` (not assessed) matches
  neither.
- `search` (1–100 characters) is a case-insensitive substring of the link slug or label,
  ISP, strict or advisory city, browser, OS or webview host.
- `sort` is `newest` (default) or `oldest`; the cursor follows it.
- `is_returning` is now computed: `true` when the same `visitor_id` has an earlier visit
  on any link, `null` for a visit with no `visitor_id`.

**Response item** (summary form)

```json
{
  "id": "018f…",
  "occurred_at": "2026-09-25T14:03:11.482+00:00",
  "link": { "id": "018f…", "slug": "ig-bio", "label": "Instagram bio" },
  "stage": "enriched",
  "classification": "human",
  "bot_score": 4,
  "spoof_score": 0,
  "location": {
    "strict":   { "country_code": "IN", "admin1": "Karnataka", "admin2": null, "city": null },
    "advisory": { "country_code": "IN", "admin1": "Karnataka", "admin2": "Bangalore Urban", "city": "Bengaluru" },
    "confidence": { "country": 0.99, "admin1": 0.91, "admin2": 0.62, "city": 0.58 },
    "abstain_reason": { "city": "registry_artifact", "admin2": "below_threshold" },
    "primary_source": "rdns",
    "has_gps": false
  },
  "network": {
    "asn": 24560, "asn_org": "Bharti Airtel", "asn_type": "broadband",
    "connection_class": "broadband", "ip_prefix": "203.0.113.0/24",
    "is_datacenter": false, "is_vpn_suspected": false, "is_proxy_suspected": false
  },
  "device": {
    "class": "mobile", "os": "Android 14", "browser": "Chrome 131",
    "is_inapp_webview": true, "webview_host": "instagram",
    "screen": "1080x2400", "cpu_cores": 8, "device_memory_gb": null,
    "gpu_renderer": "Adreno (TM) 740"
  },
  "geofence": { "state": "outside", "matched": [] },
  "visitor_id": "9f2c…",
  "is_returning": false
}
```

Note what the example shows: **strict abstained at city with reason
`registry_artifact`, while advisory still reports Bengaluru.** That is RW-1 and
F4.AC12(a) working as designed — the engine records its guess without acting on it.

### `GET /api/v1/visits/{id}`

Full detail. Adds `candidates[]` (every source, its candidate, weight,
accepted/suppressed with reason, latency — F4.AC11), `signals[]` (every fired
classification rule with weight and evidence — F5.AC2), all raw client fields,
`inference_version`, `classifier_version`, `trace_id`, and `ground_truth_label` when one
exists.

**As shipped in M3:** each candidate is
`{source, level, country_code, admin1, admin2, city, lat, lng, raw_confidence, weight,
effective_weight, accepted, suppressed_reason, evidence, latency_ms}` (DATA_MODEL 5.4).
A source that produced **no** candidate appears in `signals[]` as
`inference.source_absent` with its status and reason — together the two lists cover
every source for every visit. `inferred_at` is added beside `finalized_at`; `null` means
the visit is still in the inference queue (ADR-0015), not that inference failed.
`location.primary_source` names the source behind the deepest advisory value.

**As shipped in M4:** `classification`, `bot_score` and `spoof_score` are the classifier's
(ADR-0011 amendment); every fired rule is in `signals[]` with its `category` and weight.
`client.probes` is the capture page's headless probes as reported — the evidence behind
the `client.*` rules. `visitor_id` is the hex of the stable identifier; `is_datacenter`,
`is_vpn_suspected`, `is_tor` and `is_proxy_suspected` are `null` where they could not be
assessed, never a defaulted `false`.

### `GET /api/v1/visits/{id}/ip` — `owner` only

Decrypts and returns the IP for one visit.

| Status | Meaning |
|---|---|
| `200 {"ip": "…", "decrypted_at": "…"}` | **Writes an `audit_log` row naming actor and visit** (F12.AC4) |
| `403` | Not `owner` |
| `410` | `IP_PURGED` — past its TTL. Expected, not an error condition |
| `429` | Decrypt rate limit: 10/hr per admin, burst 5 |
| `500` | `INTERNAL_ERROR` with a readable detail: the ciphertext failed authentication. The wrong key, a row it was not sealed for, or tampering -- indistinguishable by design (ADR-0007). Never the address |

**As shipped in M2:** in the summary and detail shapes, fields later milestones fill --
location, scores, `visitor_id`, `is_returning`, geofence -- are present and `null` (or
`"unknown"`), never absent and never a fabricated zero (F3.AC5).
**Since M6**, `geofence.state` is `inside`, `outside`, `undetermined` or `null`. `null`
means no active geofence applied to the visit, or it is not inferred yet; it is never
reported as `outside` (ADR-0020 decision 5). `matched` lists every geofence the visit is
inside (F6.AC7). The
device block's key is `class`, as documented. The detail view adds `request.headers`: the
header set **as sanitised at capture**, with no address-bearing or credential header.
`candidates` is `[]` until M3.

### `GET /api/v1/visits/{id}/export` · `GET /api/v1/visits/export`

Per-visit data-subject export (F12.AC14), and bulk export honouring active filters as
`csv` or `ndjson`, **streamed** rather than buffered (F9.AC15). Exports contain no
plaintext IP, ever.

**As shipped in M5:** the bulk export, `?format=csv|ndjson` (default `csv`) plus every
list filter and `sort`. Rows are read through a server-side cursor 500 at a time and
written as they arrive, from the export's own database session (the request's session
has already committed, ARCHITECTURE 5.8). NDJSON lines are the list's summary shape; CSV
flattens it into 38 columns. The network prefix is the only address-derived column, as
in the list. **CSV cells that a spreadsheet would read as a formula** (`=`, `+`, `-`,
`@`, tab, carriage return) are prefixed with `'`: user agents and ISP names are
attacker-controlled. The per-visit export (F12.AC14) is not in any milestone's scope
yet and is not built.

---

## 8. Analytics

All read from nightly rollups, not raw rows (F9.AC19). **Every response includes a
`stage_mix` object** stating what it was computed over, so a chart cannot silently
mislead when enrichment coverage shifts (F9.AC20).

| Method | Path | Returns |
|---|---|---|
| `GET` | `/api/v1/analytics/summary` | KPIs with period-over-period deltas (F9.AC2) |
| `GET` | `/api/v1/analytics/timeseries` | `?metric=&bucket=hour\|day&split_by=` (F9.AC3) |
| `GET` | `/api/v1/analytics/breakdown` | `?dimension=country\|admin1\|city\|asn\|isp\|device_class\|browser\|os\|screen\|connection_class\|classification` (F9.AC4) |
| `GET` | `/api/v1/analytics/geo` | Choropleth counts + clustered points (F9.AC5) |
| `GET` | `/api/v1/analytics/calendar` | Daily counts for the heatmap (F9.AC6) |
| `GET` | `/api/v1/analytics/source-flow` | Sankey nodes and links: source → emitted level (F9.AC7) |
| `GET` | `/api/v1/analytics/funnel` | requests → server → enriched → consented → notified (F9.AC8) |
| `GET` | `/api/v1/analytics/confidence` | Confidence histograms per level (F9.AC9) |
| `GET` | `/api/v1/analytics/signals` | Bot-signal firing frequency, ranked (F9.AC11) |
| `GET` | `/api/v1/analytics/accuracy` | Current precision and coverage per level, **with `label_count`** (F9.AC10) |
| `GET` | `/api/v1/analytics/visitor/{visitor_id}` | Every visit for one visitor, with location drift (F9.AC12) |

`/analytics/accuracy` always returns `label_count` alongside every metric. A precision
figure computed over 30 labels is not the same claim as one computed over 3000, and the
API must not let a caller forget that (RISKS R9).

### 8.1 As shipped in M5 (ADR-0016)

Every endpoint takes **the visit list's filters** (section 7) and the window `from`/`to`
(default: the last 30 local days, today included; at most 800 days). Every response has
a `meta` block:

```json
{
  "start": "2026-09-03T00:00:00+05:30",
  "end": "2026-10-03T00:00:00+05:30",
  "reporting_tz": "Asia/Kolkata",
  "computed_from": "rollup",
  "refreshed_at": "2026-10-02T12:05:00Z",
  "stage_mix": { "total": 412, "server": 3, "enriched": 301, "server_only": 96, "rate_limited": 12 }
}
```

`computed_from` is `rollup` when every filter set is a rollup dimension, the window is on
bucket boundaries in the reporting timezone, and every day in it has been built;
otherwise `raw`. Both paths use one projection, and a parity test asserts they return
identical figures. `refreshed_at` is the oldest refresh among the days read (rollup) or
the request time (raw).

**Conventions.** Rate-limited requests appear in `stage_mix` and the funnel's first step
and nowhere else. Location is the best guess (advisory, ADR-0018); a visit no source
could place is counted, under `unknown`, `abstained` or the key `''`, never dropped. Map
points are consented GPS or the best-guess city. `/accuracy` and `/source-flow` still
measure strict: what the engine was willing to state. A figure the system cannot produce yet is
`null` with a `reason`.

| Path | Parameters | Shape |
|---|---|---|
| `/summary` | — | `kpis[]`: `{key, unit: count\|ratio, value, previous, change, reason}` for `visits`, `unique_visitors`, `human_share`, `bot_share`, `consent_grant_rate`, `geofence_hit_rate`, `enrichment_completion_rate`; `previous_start`. The previous period is the equal-length window before. `change` is relative for counts, percentage points for ratios. Shares are over every classification whatever the filter. `unique_visitors` is raw and `null` (`past_visit_retention`) beyond retention |
| `/timeseries` | `bucket=day\|hour`, `split_by=none\|classification\|device_class\|connection_class\|country\|admin1\|link`, `metric=visits\|consented` | `buckets[]` (local bucket starts, zero-filled), `series[]` `{key, label, values[]}`; at most 7 keys plus `other`. Hourly windows ≤ 31 days |
| `/breakdown` | `dimension=country\|admin1\|city\|asn\|isp\|device_class\|browser\|app_medium\|os\|screen\|connection_class\|classification`, `limit` 1–100 (20) | `total`, `rows[]` `{key, count, share}`, `unknown`, `other`. `admin1`/`city` keys are qualified (`IN\|Karnataka`) |
| `/geo` | `cell_degrees` 0.01–10 (0.25) | `countries[]`, `admin1[]`, `abstained`, `points[]` `{lat, lng, count}` clustered on a grid server-side, `points_computed_from: "raw"`, `points_truncated` (over 2000 clusters) |
| `/calendar` | — | `days[]` `{day, count}`, every day in the window |
| `/source-flow` | — | `sources[]`, `levels[]`, `links[]` `{source, target, value}`; `visits` = inferred visits in scope |
| `/funnel` | — | `steps[]` `{step, count, reason}`: requests, captured, enriched, consented, notified. **`notified` is `null`, reason `notifications_not_built`, until M6** |
| `/confidence` | — | `levels[]` `{level, bins[10], unscored}` |
| `/signals` | — | `visits`, `rows[]` `{rule_id, category, count, share}`; categories bot, spoof, spam, network |
| `/accuracy` | — | `inferred`, `levels[]` `{level, label_count, precision, coverage, emission_rate, reason}`. **Until M8 builds the ground-truth set, `label_count` is 0 and precision and coverage are `null` with reason `no_ground_truth_labels`.** `emission_rate` is how often strict answered, explicitly not accuracy |
| `/visitor/{visitor_id}` | — | `visit_count`, `first_seen`, `last_seen`, `visits[]` (summary shape, oldest first, at most 500, `truncated`), `drift[]` `{at, from_visit, to_visit, location_changed[], distance_km, device_changed[], network_changed}`. Drift compares advisory location, labelled as such |

---

## 9. Geofences

| Method | Path | Role | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/geofences` | any | List, geometry as GeoJSON, with `matches_7d` |
| `POST` | `/api/v1/geofences` | owner | Create |
| `GET`/`PATCH`/`DELETE` | `/api/v1/geofences/{id}` | any / owner / owner | |
| `GET` | `/api/v1/geofences/regions` | any | Every country and first-order division a region geofence can name (ADR-0020) |
| `GET` | `/api/v1/geofences/places` | any | A country's cities and towns by population tier, for the editor's map |
| `POST` | `/api/v1/geofences/test` | any | A coordinate → each geofence's result, **creates no visit** (F6.AC10) |
| `POST` | `/api/v1/geofences/import` | owner | GeoJSON `FeatureCollection` (F6.AC9) |
| `GET` | `/api/v1/geofences/export` | any | The same `FeatureCollection` format, so an export re-imports unchanged |

Every write is owner-only and writes an `audit_log` row (CLAUDE.md invariant 9):
`geofence.created`, `geofence.updated` (with the fields that changed, old and new),
`geofence.deleted`, `geofence.imported` (with the count).

**Three shapes** (ADR-0020). The body names one with `shape_kind` and carries only that
shape's fields:

```json
{ "name": "Karnataka", "shape_kind": "region", "region_keys": ["IN|Karnataka"],
  "priority": 10, "is_active": true, "notify_priority": "high", "link_ids": null }

{ "name": "Home 2km", "shape_kind": "circle",
  "center": { "lat": 12.9716, "lng": 77.5946 }, "radius_m": 2000,
  "priority": 100, "is_active": true, "notify_priority": "high", "link_ids": null }

{ "name": "Office campus", "shape_kind": "polygon",
  "geometry": { "type": "Polygon", "coordinates": [[[77.60, 12.97], [77.61, 12.97], [77.61, 12.98], [77.60, 12.97]]] },
  "priority": 20, "is_active": true, "notify_priority": "normal", "link_ids": ["…"] }
```

- `name` 1 to 100 characters, `description` optional. `priority` an integer, higher wins on
  overlap (F6.AC7); default 0. `notify_priority` `high`, `normal` or `silent`, default
  `high`; it is combined with the link's `notify_policy.inside`, the less urgent winning
  (SPEC §11 row 18). `link_ids` `null` for every link, or a non-empty list of existing links.
- **region:** 1 to 1000 keys. A key is a country, `IN`, or a division qualified by its
  country, `IN|Karnataka`, spelled exactly as `/geofences/regions` lists it. Matched on the
  strict country and strict state only, never on advisory fields.
- **circle:** `radius_m` from 50 to 1 000 000. Stored buffered as a 64-sided polygon; the
  centre and radius are kept, so the circle edits as a circle.
- **polygon:** a GeoJSON `Polygon` in longitude, latitude order (RFC 7946), holes allowed,
  each ring closed, at most 2000 points in all.
- `PATCH` takes any subset. Changing `shape_kind` requires that shape's fields too.

**Response** — the body above plus `id`, `geometry` for a circle as well (the buffered
polygon, for drawing), `unknown_region_keys` (keys no longer in the catalogue, for example
after a GeoNames rename; flagged, never silently dropped), `matches_7d` (visits inside it in
the last 7 days), `created_by`, `created_at`, `updated_at`.

**Errors** (`422`, field-level): `GEOFENCE_INVALID_GEOMETRY` for a self-intersecting or
otherwise invalid ring, carrying PostGIS's reason as the message and the offending point as
the field error's `location: {lat, lng}` (§12), so the editor can mark it on the map; `GEOFENCE_TOO_MANY_VERTICES` above 2000
(F6.AC4); `GEOFENCE_UNKNOWN_REGION` for a key the catalogue does not list, addressed as
`region_keys.{i}`; `UNKNOWN_LINK` for a `link_ids` entry that is not a link. In a `PATCH`,
`NOT_FOR_SHAPE` names a field that belongs to another shape and `REQUIRED` one the shape
needs. A circle's centre or radius may change alone. A key that
becomes unknown *later* is kept and reported in `unknown_region_keys`.

### `GET /api/v1/geofences/regions`

```json
{
  "countries": [{ "key": "IN" }],
  "divisions": [{ "key": "IN|Karnataka", "code": "IN.19", "country": "IN", "name": "Karnataka" }]
}
```

Built from the GeoNames admin1 table the engine names states from, so every key is
spelled as a strict state is. `code` is the GeoNames admin1 code the map's outlines carry
(`/geo/admin1/IN.json`), which is how a clicked outline becomes a key; a division with no
outline (about 13 %) is still listed and can be picked from the list. Countries are the ISO
codes the table covers; their names are the browser's (`Intl.DisplayNames`), because the API
has no country-name table and matching needs none. `503 GEO_DB_UNAVAILABLE` until the
GeoNames admin1 file is installed, and creating a region geofence fails the same way.
`Cache-Control: private, max-age=3600`; it changes only with a geo-database update (M7).

### `GET /api/v1/geofences/places?country=IN`

The country's cities and towns of 50,000 people or more, largest first, for the editor's map
(DESIGN §16): `{country, places: [{name, admin1, lat, lng, population, tier}]}`. From the
GeoNames table the engine names cities from, so a name is spelled as a strict city is. `tier`
is a population band, the same for every country (owner decision 2026-10-06): `metro` 4M+,
`tier1` 1M+, `tier2` 300k+, `tier3` 50k+. Populations are GeoNames' city-proper figures.
`country` must be an upper-case ISO code (`422` otherwise); `503 GEO_DB_UNAVAILABLE` until
GeoNames is installed. `Cache-Control: private, max-age=3600`.

### `POST /api/v1/geofences/test`

`{lat, lng}` → the coordinate is treated as a consented GPS fix: it is the geopoint, and
its country and state are named from GeoNames, as S1's are (F4.AC5).

```json
{
  "placed": { "country_code": "IN", "admin1": "Karnataka" },
  "state": "inside",
  "results": [
    { "geofence_id": "…", "name": "Karnataka", "result": "inside", "reason": null },
    { "geofence_id": "…", "name": "Office campus", "result": "outside", "reason": null }
  ]
}
```

Only active geofences are tested, for every link. `state` combines them as a visit's
would (ADR-0020 decision 5), `null` with no active geofence. `reason` explains an
`undetermined` result, for example `no_strict_admin1` for a coordinate GeoNames cannot
place in a state.

### Import and export

A `FeatureCollection`. A polygon is a `Polygon` feature; a circle is a `Point` feature with
`properties.radius_m`; a region is a feature with `geometry: null` and
`properties.region_keys`. Every other field is a property (`name`, `description`,
`priority`, `is_active`, `notify_priority`, `link_ids`). Import is **all or nothing**: one
invalid feature fails the request with field-level errors addressed as
`features.3.geometry`, and nothing is saved. Up to 200 features, inside the 1 MiB body cap.
Imported geofences are new; `id` properties are ignored. The export is served as `application/geo+json` with
`Content-Disposition: attachment; filename="geofences.geojson"`; import writes one
`geofence.imported` audit row with the count and names.

### Evaluation (ADR-0015, ADR-0020)

Geofences are evaluated inside the inference job's transaction, never on the capture path:
a visit's `geofence_state` and `matched_geofence_ids` change only when it is inferred.
Editing a geofence does not re-evaluate past visits; `matches_7d` counts what was recorded.

---

## 9a. Notifications (M6, F7)

| Method | Path | Role | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/notifications/settings` | any | Whether Telegram is configured, and quiet hours |
| `PATCH` | `/api/v1/notifications/settings` | owner | Change quiet hours (F7.AC9). Audited as `settings.changed`, old and new |

```json
{
  "telegram": { "bot_token_set": true, "chat_id_set": true, "chat_verified": true },
  "quiet_hours": { "enabled": true, "start": "23:00", "end": "07:00", "timezone": "Asia/Kolkata", "active_now": false }
}
```

The bot token and the owner chat id are secrets and deployment facts: they stay in the
environment (`TRACELET_TELEGRAM_BOT_TOKEN`, `TRACELET_TELEGRAM_OWNER_CHAT_ID`; F12.AC3), and
this API says only whether they are set. `chat_verified` is whether an admin has verified
that chat through the bot (F8.AC7). `PATCH` takes `{quiet_hours: {enabled, start, end,
timezone}}`: times `HH:MM`, a window whose end is before its start crosses midnight, the
timezone an IANA name. Quiet hours hold `normal` alerts until the window closes; `high`
alerts are never held.

**What is sent, and when** (SPEC §11 rows 17 and 18, ADR-0020 decision 7). A visit is
evaluated when it is inferred; if it is `human` (never otherwise, CLAUDE.md invariant 6)
its alert is queued in the same transaction (F7.AC5), at a priority resolved from the
link's `notify_policy` and the deciding geofence:

| Visit's geofence state | Priority | Message headline |
|---|---|---|
| `inside` | the less urgent of the link's `inside` and the highest-priority matching geofence's `notify_priority` | "Inside *geofence name*" |
| `outside` | the link's `outside` | "New visitor, outside your geofences" |
| `undetermined` | the link's `undetermined` | "Location not confirmed: could not be checked against your geofences" |
| `null` (no geofence applies) | the link's `outside` | "New visitor" |

`silent` queues nothing. At most one alert per link and visitor per local day in the
reporting timezone (F7.AC2), with one upgrade: if that alert was `normal`, a later visit that
resolves to `high` (a confirmed inside) still queues one, once (SPEC §11 row 20). And one
confirmation: if that alert was a `normal` "Location not confirmed", a later visit confirmed
`outside` still queues one, once, unless a `high` alert has already been queued that day
(row 21). Otherwise later visits that day queue nothing, whatever their state.
The message lists the time, the link, the strict location with its confidence (or the best
guess, marked so, where strict abstained), device and browser, connection class, ASN and
ISP, the classification with its bot score, and a link to the visit (F7.AC4).


---

## 10. System health and operations

| Method | Path | Role | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/health/system` | any | CPU %, RAM, swap, disk, load, uptime, DB size, **temperature or `null` with a reason** (F10.AC1, RW-5) |
| `GET` | `/api/v1/health/databases` | any | Each geo database: version, dates, size, sha256, staleness verdict (F10.AC3) |
| `POST` | `/api/v1/health/databases/{name}/update` | owner | `202` job. Streams, verifies, atomic swap. Failure leaves the previous version serving (F10.AC4) |
| `PATCH` | `/api/v1/health/databases/{name}` | owner | `{auto_update}`: whether the scheduler updates it (SPEC §11 row 24). **M7** |
| `POST` | `/api/v1/health/databases/check` | owner | Check every database for a newer release now; no download. **M7** |
| `GET` | `/api/v1/health/inference` | any | Active settings version: source toggles, weights, thresholds |
| `PATCH` | `/api/v1/health/inference` | owner | Creates a **new version**; old versions retained for rollback (F4.AC14) |
| `POST` | `/api/v1/health/inference/rollback/{version}` | owner | Reactivate an earlier version |
| `GET` | `/api/v1/health/inference/flow` | any | Flow-diagram model: levels, order, enabled state, optional `?sample_visit_id=` to overlay what actually fired (F10.AC8) |
| `GET`/`PATCH` | `/api/v1/health/retention` | any / owner | Retention periods (F10.AC12) |
| `POST` | `/api/v1/health/retention/preview` | owner | **Dry run: exact counts that would be deleted, deletes nothing** |
| `POST` | `/api/v1/health/retention/purge` | owner | `202` job. Batched, transactional, audit-logged with real counts |
| `GET` | `/api/v1/health/backups` | any | List with status, size, checksum, last restore-verify result |
| `POST` | `/api/v1/health/backups` | owner | `202` manual backup |
| `GET` | `/api/v1/health/backups/{id}/download` | owner | Streamed. **The only off-VM path** (F12.AC11) |
| `POST` | `/api/v1/health/backups/{id}/verify-restore` | owner | `202`. Restores into a scratch schema and asserts row counts (F12.AC10) |
| `GET` | `/api/v1/health/outbox` | any | Counts (`pending`, `in_flight`, `failed`, `dead`, and `held` by quiet hours) and the delivery log, newest first; `?status=`, `limit` (≤ 100), `cursor` (F10.AC13). **As built in M6** |
| `POST` | `/api/v1/health/outbox/{id}/retry` | owner | Requeue a dead letter with fresh attempts; `409 OUTBOX_NOT_DEAD` otherwise; audited `outbox.retried`. **M6** |
| `POST` | `/api/v1/health/telegram/test` | owner | Send a test message now, not through the outbox (F7.AC8): `{delivered_at, message_id}`, or `502 TELEGRAM_DELIVERY_FAILED` with Telegram's own reason, never the token. **M6** |
| `GET` | `/api/v1/health/degradation` | any | Active degradation conditions for the banner (F10.AC14) |
| `GET`/`PATCH` | `/api/v1/health/ratelimits` | any / owner | Limits, editable without redeployment (F11.AC9) |

`/health/retention/preview` exists because a purge is irreversible. **The UI must call
preview before purge**; the API does not enforce ordering, but the dashboard does and
the audit log records both.

### System health — as built in M7 (F10.AC1–AC4, F10.AC14, F11.AC9)

`GET /system` returns `{scope, scope_reason, sampled_at, cpu: {percent, count, load}, memory,
swap, disk, uptime_seconds, database_bytes, temperature, poll_seconds}`. `memory`, `swap` and
`disk` are `{used, total, percent, warn_percent, state: "ok"|"warn"|"critical"}` (bytes);
`disk` also has `path`, the backups volume, which lives on the host's disk. `scope` is
`host` when the host's `/proc` is mounted at `/host/proc` (F10.AC15) and `container` -- with
the reason -- when it is not, so a figure is never passed off as the host's. `cpu.percent` is
measured over a quarter of a second. `temperature` is `{celsius, sensor, reason}`: on a host
with no sensor (GCP, Docker Desktop's VM) `celsius` is `null` and `reason` says why (RW-5).
Thresholds come from `TRACELET_DISK_WARN_PERCENT` (85), `_DISK_CRITICAL_PERCENT` (95),
`_MEMORY_WARN_PERCENT` (90) and `_SWAP_WARN_PERCENT` (50).

`GET /databases` returns `{databases[]}`, one per catalogue entry whether or not it was ever
installed: `{name, kind, feeds, attribution, configured, auto_update, staleness_days, stale,
state, progress, age_days, installed, latest, last_attempt, check_error, checked_at}`.
`installed` is `{version, released_at, installed_at, size_bytes, sha256}` or `null`; `latest` is
what the last release check found, `{version, released_at}` or `null`.

`state` (SPEC §11 row 24) is one of, in this order of precedence: `updating` (an attempt in
flight; `progress` is `{phase, percent}` -- `percent` may be `null` while downloading from a
vendor that sends no length), `update_failed` (the newest attempt failed; the installed copy
keeps serving; `last_attempt.error` says why), `unable_to_update` (no credentials, or the last
release check could not reach the vendor: `check_error`), `not_installed`, `update_available`
(the check found a newer release, or -- for a database that cannot be checked -- its refresh
schedule says it is due), `up_to_date`. `stale` is separately true when the installed copy is
older than its staleness threshold; it raises the degradation banner.

`POST /databases/{name}/update` (owner) **asks the vendor first** (SPEC §11 row 26): if
nothing is newer than the installed copy it downloads nothing and answers `200 {name, status:
"up_to_date"}`; otherwise it starts the install and answers `202 {name, status: "started"}`. It
downloads without asking when the database is not installed, its file is missing, it is never
checked (IP2Location), the check fails, or the dates cannot be compared. `?force=true` is
**Download again**: no check, always `202`. Either way, whatever `auto_update` says. `404` for
an unknown name, `409 LIFECYCLE_JOB_RUNNING` while it is updating; audited
`geodb.update_requested` with `force` and the outcome.
`PATCH /databases/{name}` (owner) takes `{auto_update}` and returns the database; audited
`geodb.toggled` with the old and new value. `POST /databases/check` (owner) runs the release
check for every database now and returns `{databases[]}`; the six-hourly update job runs it
too. The check is a HEAD request for the vendor's `Last-Modified` (DB-IP: whether this month's
edition is published); IP2Location is never checked over the network, because its URL is
metered per token. **The scheduler trusts a successful check** made in the last 12 hours: it
downloads a checked database only when the check found a newer release; the refresh period
applies to IP2Location, to a failed or old check, and when the dates cannot be compared.

`GET /degradation` returns `{conditions[], checked_at}`, most severe first. Each condition is
`{key, severity: "critical"|"warning"|"notice", title, detail, still_works}`. Keys: `shedding`
(capture is being shed on memory pressure, F15.AC6), `disk`,
`swap`, `backups` (not set up, failed, or none in 36 h), `restore` (failed, or none passed in
35 days), `download` (notice: no backup downloaded within the reminder period), `outbox`
(dead letters), `breaker:<name>` (an open circuit breaker), `geodb:<name>` (a configured
database stale or missing). Read from shared state, so both workers agree.

`GET /ratelimits` returns `{limits[], applies_within_seconds: 30}`; each limit is `{name,
group: "capture"|"admin"|"outbound", label, description, per_period, period_seconds, burst,
default: {per_period, period_seconds, burst}, overridden, ceiling_per_second}`. `PATCH
/ratelimits` (owner) takes `{limits: {<name>: {per_period, period_seconds, burst} | null}}`:
`null`, or a value equal to the default, removes the override; names left out are unchanged.
Any refused entry -- an unknown name, a value out of range, or an outbound limit above its
third party's terms -- is `422` with one field error per entry, and nothing is saved.
Audited `settings.changed` with the old and new overrides; this worker applies it at once and
the other within 30 seconds.

### Retention — as built in M7 (F10.AC12, F12.AC7–AC8)

`GET /retention` returns `{policy: {visit_days, ip_days, audit_days}, delivered_alerts_days:
30, rollups: "kept forever", updated_at, purge_running, last_purge}`. `last_purge` is the
newest `retention.purged` audit row: `{at, trigger: "manual"|"scheduled", counts, by}`.
`purge_running` is read from PostgreSQL's lock table, so it is true whichever worker runs it.

`PATCH /retention` takes **all three** periods: `visit_days` 8–3650 (rollups re-settle the
last 7 days), `ip_days` 1–`visit_days`, `audit_days` 1–3650; anything else is `422`. A changed
`ip_days` also re-dates the IP expiry of every visit still holding one. Audited
`retention.changed` with `from` and `to`. Nothing is deleted by a `PATCH`.

`POST /retention/preview` returns `{as_of, policy, cutoffs: {visits, ip, audit, outbox},
counts: {visits, visit_candidates, ip_addresses, audit_rows, delivered_alerts}}` and deletes
nothing; it is audited `retention.previewed` with the counts. `ip_addresses` counts only
visits that are being kept -- a visit about to be deleted is counted once, as a visit.

`POST /retention/purge` takes `{as_of, policy}` **exactly as the preview returned them**, and
deletes against the cutoffs derived from them, so its counts equal the preview's. It is
`409 RETENTION_PREVIEW_STALE` if `as_of` is over 15 minutes old or the policy has changed,
and `409 LIFECYCLE_JOB_RUNNING` if a purge holds the lock. Otherwise `202 {as_of, status:
"started"}`; the purge runs as `tracelet_maint` in batches of 1 000, and its result is the
`retention.purged` audit row (`trigger`, `as_of`, `policy`, `cutoffs`, `counts`,
`duration_ms`), shown as `last_purge`. The scheduled purges -- every night an hour before the
backup, and the IP purge every 10 minutes -- may take some of the previewed rows first; the
manual purge then reports the fewer it deleted.

### Backups — as built in M7 (F10.AC11, F12.AC9–AC12, ADR-0022)

`GET /backups` returns `{backups[], last_restore_check, backup_running,
restore_check_running, download}`, newest first (up to 60). Each backup is `{id, kind:
"scheduled"|"manual", status: "running"|"ok"|"failed"|"pruned", file_name, size_bytes, sha256,
tables, rows, error, started_at, finished_at, last_downloaded_at, last_restore_check}`, where
`tables` and `rows` come from the counts taken in the dump's own snapshot, and
`last_restore_check` is that backup's newest check `{id, backup_id, kind, status:
"running"|"passed"|"failed", mismatches, error, started_at, finished_at}`. `mismatches` maps a
table to `{expected, restored}`. `download` is `{last_downloaded_at, reminder_days, overdue}`:
overdue when nothing has been downloaded within `TRACELET_BACKUP_DOWNLOAD_REMINDER_DAYS`, the
only off-machine copy being the download (RISKS R11).

`POST /backups` (owner) is `202 {id, status: "started"}`, or `409 LIFECYCLE_JOB_RUNNING`;
audited `backup.requested`. The dump is written as `.partial` and renamed only when complete
and checksummed; then rotation keeps the newest of each of the last 7 local days and 4 ISO
weeks, and always the newest, and marks the rest `pruned` (their files deleted, rows kept).

`GET /backups/{id}/download` (owner) streams the file as `tracelet-<file_name>`, with the
checksum in `X-Content-SHA256`; audited `backup.downloaded`. `409 BACKUP_UNAVAILABLE` for a
backup without a file.

`POST /backups/{id}/verify-restore` (owner) is `202 {id: <restore check id>}`: the backup is
restored into the scratch database `tracelet_verify` and passes only if **every table's count
equals** the backup's. Audited `backup.restore_check_requested`. `409 BACKUP_UNAVAILABLE` for a
backup that is not `ok`, `409 LIFECYCLE_JOB_RUNNING` if a check is running. Without the
one-time `tl db-setup`, the check fails with that step named in `error`.

The scheduler runs the backup nightly at `TRACELET_BACKUP_HOUR` (local; 3 by default), retrying
a failure up to three times that night, and the restore check monthly, on the 1st, an hour
later, on the newest `ok` backup.

### `/api/v1/health/inference` — as shipped in M3

`GET` returns `{engine_revision, active_version, inference_version, settings, versions[]}`,
where `inference_version` is exactly what a visit inferred now will be stamped with
(`m3.1+s2`), and `versions[]` lists every retained version with `is_active`, `note`,
`created_at` and `created_by`. The first read seeds version 1 from the built-in defaults.

`PATCH` takes `{settings, note?}` where **`settings` is the complete object**, not a
fragment: it becomes the next version as a whole, so a version always means one exact,
reviewable configuration. Validation is total — a threshold of `1.7` is a `422` and
nothing is saved. `POST /rollback/{version}` reactivates an existing version (`404` if
there is none; rolling back to the active version is a no-op and writes no audit row).
Both writes are owner-only and record `inference.settings_changed` (with the dotted paths
that changed) or `inference.settings_rolled_back`. `/flow` is M7's (F10.AC8), below. Since M4 the settings object also has a `classifier` section: per-rule
`weights`, the bot/spoof/spam thresholds, the human ceilings, and the collision, gateway,
rate and impossible-travel parameters — changed and rolled back exactly like the rest.


### `/api/v1/health/inference/flow` — as built in M7 (F10.AC8)

`GET /flow` returns `{inference_version, stages, families, sources, rules, levels, sample}`.
`stages` is the pipeline in order: `capture`, `sources`, `suppression`, `consensus`,
`classification`, `geofence`, `alert`. `sources` lists S1–S9 and S11 in their order, each
`{source, code, label, family, order, enabled, timeout_ms, priors}` under the **active**
settings, so a toggle shows at once; `families` groups them (`client`, `database`,
`network`, `edge`, `context`); `rules` are the suppression rules with a sentence each;
`levels` are the four levels with their strict thresholds.

With `?sample_visit_id=`, `sample` is what inference **recorded** for that visit --
nothing is recomputed: `{visit_id, inference_version, classification, geo_source_primary,
geofence_state, sources[], levels[], rules_fired, alert}`. Each source is `{source, status,
reason, candidates[]}` with `status` `fired` (a candidate was accepted), `suppressed`
(every candidate was, `reason` naming the rule), `disabled`, `unavailable` or `empty` (as
inference recorded the absence, with its reason), or `silent` (nothing recorded). Each level
is `{level, strict, advisory, confidence, abstain_reason}`. `alert` is the visit's first
queued alert `{priority, status, upgrade}`, or `null`. `inference_version` may differ from
the active one: the overlay shows the settings the visit was inferred under. `404` for an
unknown visit.
---

## 11. Ground truth and accuracy

| Method | Path | Role | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/ground-truth` | any | List labels with the engine prediction beside each |
| `POST` | `/api/v1/ground-truth` | owner | Label a visit (F4.AC15) |
| `PATCH`/`DELETE` | `/api/v1/ground-truth/{id}` | owner | |
| `GET` | `/api/v1/ground-truth/candidates` | any | Visits worth labelling, prioritised by disagreement — highest information gain first |
| `GET` | `/api/v1/ground-truth/metrics` | any | Precision and coverage per level, per source, with `label_count` |

`/ground-truth/candidates` ranks by `conflict_score` descending: labelling the visits
where sources disagreed most teaches the tuning process more per label than labelling
easy ones. With only 30 to 60 labels available, which ones you spend effort on matters.

---

## 12. Error contract

RFC 9457 Problem Details, from a typed exception hierarchy through a single handler
(F15.AC1, ES4, ADR-0013).

```json
{
  "type": "https://tracelet/errors/validation-failed",
  "title": "Validation failed",
  "status": 422,
  "detail": "One or more fields are invalid.",
  "instance": "/api/v1/links",
  "code": "VALIDATION_FAILED",
  "trace_id": "01JBQ8X2K9YV3M7N4P6R8T0W2Z",
  "errors": [
    { "field": "destination_url", "code": "SCHEME_NOT_HTTPS", "message": "Destination must use https." },
    { "field": "interstitial_ms", "code": "OUT_OF_RANGE", "message": "Must be between 300 and 1500." }
  ]
}
```

A field error may carry `location: {lat, lng}`, a point on a map the error refers to --
today only where a geofence ring crosses itself (§9). It is **omitted**, not `null`, when
there is none, so every other error keeps its shape. *Added in M6.*

**A 5xx returns only `type`, `title`, `status`, `code` and `trace_id`.** No stack trace,
no SQL, no internal hostname (F15.AC3). The detail is written to the log under the same
`trace_id`, which is how a user report becomes diagnosable from one identifier
(F15.AC2).

### 12.1 Catalogue

| Code | Status | Meaning |
|---|---|---|
| `VALIDATION_FAILED` | 422 | Schema or field violation; `errors[]` populated |
| `UNAUTHENTICATED` | 401 | No valid session |
| `MFA_REQUIRED` | 401 | Password accepted, TOTP outstanding |
| `MFA_INVALID` | 401 | Wrong or replayed code |
| `TOTP_NOT_ENROLLED` | 403 | Enrolment incomplete; no dashboard access (F8.AC4) |
| `FORBIDDEN_ROLE` | 403 | Valid session, insufficient role |
| `CSRF_INVALID` | 403 | Missing or mismatched token, or bad `Origin` |
| `ACCOUNT_LOCKED` | 423 | Too many failures; `Retry-After` set |
| `NOT_FOUND` | 404 | Resource absent or not visible to this caller |
| `LAST_OWNER` | 409 | Would leave zero active owners (F8.AC13) |
| `LINK_HAS_VISITS` | 409 | Delete refused; archive instead (F1.AC10) |
| `DEFAULT_LINK_REQUIRED` | 409 | Would leave no default link |
| `NONCE_INVALID` | 410 | Enrichment nonce expired, consumed, or mismatched |
| `IP_PURGED` | 410 | Encrypted IP past its TTL. **Expected, not a fault** |
| `GEOFENCE_INVALID_GEOMETRY` | 422 | Failed `ST_IsValid`; carries the reason and the location |
| `GEOFENCE_TOO_MANY_VERTICES` | 422 | Above 2000 |
| `GEOFENCE_UNKNOWN_REGION` | 422 | A region key `/geofences/regions` does not list (ADR-0020) |
| `OUTBOX_NOT_DEAD` | 409 | Only a dead-lettered delivery is retried by hand (F7.AC6) |
| `TELEGRAM_DELIVERY_FAILED` | 502 | The test message did not arrive; `detail` is Telegram's reason, without the token (F7.AC8) |
| `RETENTION_PREVIEW_STALE` | 409 | A purge was sent with a preview over 15 minutes old, or the periods changed since it (F10.AC12). Preview again. **M7** |
| `LIFECYCLE_JOB_RUNNING` | 409 | A purge, backup or restore check of that kind is already running. **M7** |
| `BACKUP_UNAVAILABLE` | 409 | That backup has no file: it failed, is still running, or was rotated away. **M7** |
| `PAYLOAD_TOO_LARGE` | 413 | Body above cap |
| `RATE_LIMITED` | 429 | `Retry-After` set (F11.AC10) |
| `GEO_DB_UNAVAILABLE` | 503 | A source is missing or corrupt; inference degraded, not failed |
| `EXTERNAL_SOURCE_UNAVAILABLE` | 503 | Circuit breaker open. Informational |
| `DEPENDENCY_UNAVAILABLE` | 503 | Database or another hard dependency down |
| `MAINTENANCE_UNAVAILABLE` | 503 | The maintenance role is not configured or not set up (ADR-0022); `detail` names the missing step. **M7** |
| `INTERNAL_ERROR` | 500 | Unexpected. `trace_id` only |

### 12.2 Errors on the capture path

**The capture path never returns a JSON error to a visitor.** A visitor sees a page that
redirects, or a 404. Any internal failure is logged and the redirect still happens
(F15.AC7, NFR3.AC2). The JSON contract above governs `/api/v1` only.

---

## 13. Rate limits

| Route class | Limit | Key |
|---|---|---|
| `GET /r/{slug}`, `/r`, `/r/` | 30/min, 300/hr, burst 10 | IP prefix |
| `POST /api/v1/s/{nonce}` | Once per nonce, ever (F11.AC4); and 60/min (burst 20) | nonce; IP prefix |
| `GET /api/v1/hp/{token}` | 10/min | IP prefix |
| `POST /api/v1/auth/login` | 5 per 15 min (burst 5); 20/hr (burst 10) | identifier; IP prefix |
| `POST /api/v1/auth/mfa` | 10 per 15 min (burst 5) | IP prefix |
| `POST /api/v1/auth/reset/request` | 3/hr (burst 3) | identifier |
| `POST /api/v1/auth/recovery-code` | 5/hr (burst 3) | identifier |
| `GET /api/v1/visits/{id}/ip` | 10/hr | admin |
| All other `/api/v1` | 120/min | session |
| **Outbound** Nominatim | 1/s, cached | global |
| **Outbound** external geo APIs | per-source budget + breaker | global |
| **Outbound** Telegram | per-bot budget + backoff | global |

`/auth/mfa` is keyed on the network prefix rather than on an identifier because the
caller presents an opaque challenge token at that step, not an address — there is
nothing else to key on, and the challenge is already single-use and short-lived.

Both directions are covered, which is the brief requirement for upstream and downstream
limiting (F11.AC7). State lives in PostgreSQL so both Uvicorn workers share one
allowance (F11.AC8). All limits are editable without redeployment, and every change is
audit-logged (F11.AC9).

---

## 14. Client generation

The TypeScript client and its zod schemas are generated from the FastAPI OpenAPI
document by `openapi-typescript`. **CI regenerates and fails if the committed output
differs** (F14.AC9). Consequences to respect:

- Never hand-edit `web/src/api/generated/*`.
- A response-model change is an API change: update this document in the same commit.
- Generated types prove the **contract**; the zod schemas validate the **payload** at
  runtime. Both exist on purpose — a schema drift should surface as a caught validation
  error, not a `TypeError` deep in a chart component.

**As shipped in M5:** `openapi-typescript` generates types only. The zod schemas
(`web/src/api/schemas.ts`) are written by hand and each is annotated with its generated
type, so a response-model change that the schema does not follow fails `tsc`. Checked in
M5: adding a field to `StageMix` makes `./scripts/tl openapi-check` fail until the client
is regenerated and committed.
