# SPEC — Tracelet v1

**Status:** approved at Gate 3, 2026-09-25.
**Source of requirements:** the project brief (Gate 0) plus clarifications recorded at
Gate 1 and Gate 2. Nothing else. Where this document is amended later, log it in
section 11.

**Reading this document.** Requirements are `F<n>` (feature) with acceptance criteria
`F<n>.AC<m>`. Non-functional requirements are `NFR<n>` with `NFR<n>.AC<m>`. Constraints
are `C<n>`, engineering standards `ES<n>`, out-of-scope items `OOS<n>`, known bugs
`B<n>`, success criteria `SC<n>`. Every line of the original brief maps to at least one
ID; the traceability table in section 12 proves it.

---

## 1. Purpose and scope

Tracelet captures permitted telemetry from visitors to a public tracking link, derives
and scores intelligence about each visit, and redirects the visitor to an
admin-configured destination. A separate, protected dashboard lets admins analyse the
results, draw geofences, receive Telegram alerts, and operate the system.

**In scope for v1:** everything in section 4 (F1–F15) at the levels stated in section 5.
**Explicitly out of scope, permanently:** section 8 (OOS1–OOS6).

### 1.1 Two surfaces, one deployment

| Surface | Audience | Auth | Paths |
|---|---|---|---|
| Public capture surface | Anonymous visitors, including automated clients | None | `/r/{slug}`, `/api/v1/s/{nonce}`, `/api/v1/hp/{token}`, `/privacy` |
| Admin dashboard | 1..n admins | Session + TOTP | `/` (SPA), `/api/v1/*` (all other routes) |

They share a process and a database but have **disjoint** route namespaces, rate-limit
policies, and error verbosity.

---

## 2. Actors

| Actor | Description |
|---|---|
| **Owner** | Admin role with full rights: manage admins, configuration, geo databases, retention, backups, and IP decryption. At least one must always exist. |
| **Analyst** | Admin role with read-only dashboard access. No configuration, no destructive actions, no IP decryption. |
| **Visitor (human)** | A person who opens a tracking link. May grant or deny geolocation. |
| **Visitor (automated)** | Bot, crawler, scraper, link-preview fetcher, datacenter client, or spoofed client. Recorded, classified, never notified on. |
| **Telegram** | External notification channel, and the password-recovery channel. |
| **Cloudflare** | Optional edge. Present on the purchased-domain path only. |

---

## 3. Rewordings — flagged as required

The brief said nothing may be dropped and anything reworded must be flagged. Nothing
was dropped. Seven items were reworded, each agreed at a gate.

| ID | Original wording | Reworded as | Why | Agreed |
|---|---|---|---|---|
| **RW-1** | "Highest possible city accuracy, 100% state/region and country accuracy" | Dual **strict** (abstaining) + **advisory** (best-guess) fields per level, with measurable precision-and-coverage targets per F4.AC13 | 100 percent state accuracy is not achievable by anyone using free passive IP geolocation. Precision-with-abstention is achievable, measurable in CI, and never confidently wrong | Gate 1 |
| **RW-2** | "redirect url updation/editing" (singular) | **Many named tracking links** (F1), each with its own slug, destination, geofence set and notification policy, one marked default | The brief also describes embedding links across Instagram, LinkedIn and Reddit; per-platform slugs give source attribution that Referer and UA cannot supply in webviews, and let a burned link be rotated without touching the others | Gate 1 |
| **RW-3** | "when access is denied the data is usually restricted to city-level granularity and no area or IP" | Raw IP is **never stored in plaintext for any visit**. It is AES-256-GCM encrypted with a 30-day TTL, decryptable only by an owner with an audit record. `HMAC(ip)`, the /24 or /48 prefix, and the ASN are durable | Stricter than the brief on the denied path and explicitly bounded on the granted path. A stolen dump or backup yields ciphertext | Gate 1 |
| **RW-4** | "maybe even used in hidden layers to track while preserving visitor privacy" | Link **placement** is unrestricted (bio, QR, redirect chain, embedded). Every capture page renders a visible one-line notice with a privacy link and a Continue control. Consent state is recorded per visit | Notice-free collection conflicts with OOS3 in the brief itself and with India DPDP. Resolution: unnoticeable in duration, not in existence | Gate 1 |
| **RW-5** | "temperature(c)" as a system health metric | Reported when the host exposes thermal sensors; renders `N/A` with a reason when it does not | GCP virtual machines expose no thermal sensors. The metric is real on the Win11/WSL development box and absent in production. Graceful degradation rather than a broken panel | Gate 1 |
| **RW-6** | "Latency Triangulation(not that accurate sometimes,but optional)" | ~~Implemented, feature-flagged, default off~~ **Dropped in M3 — section 11 row 12.** It cannot be built without third-party requests from the capture page, which F2.AC12 forbids | Costs the visitor 3–5 extra requests and roughly 200 ms, and most of its value is already delivered by the `CF-Ray` colo signal | Gate 1; amended by the repository owner, M3 |
| **RW-7** | "p95 latency target" (no number given) | Concrete numbers in NFR2, stated separately for the Cloudflare and direct-origin paths | GCP free-tier e2-micro exists only in US regions, so an Indian visitor has a 250 ms RTT floor before any code runs. A single target would have been meaningless | Gate 2 |

### 3.1 Additions beyond the brief

The brief repeatedly invited additions ("feel free to add more", "anything else you
would like to add"). Everything added is tagged **[ADD]** at the acceptance-criterion
level so the original scope stays distinguishable from the extension.

---

## 4. Functional requirements

### F1 — Tracking link management

Admin-managed redirect targets. **Reworded from a single redirect URL, see RW-2.**

| ID | Acceptance criterion |
|---|---|
| F1.AC1 | An owner can create a link with a URL-safe `slug` (4–32 chars, `[a-z0-9-]`), a human `label`, and an `https://` `destination_url`. |
| F1.AC2 | `destination_url` is validated: scheme must be `https`, host must resolve to a public address, no credentials in the URL, length ≤ 2048. A rejected URL returns a field-level validation error. |
| F1.AC3 | Exactly one non-archived link is marked `is_default`. Enforced by a partial unique index, not only by application code. **The bare capture path — `/r` and `/r/`, with no slug — captures and redirects through the default link**, exactly as its own slug would; an inactive default answers it with the F2.AC14 404. It is not a fallback: an unknown, inactive or archived *slug* is still a 404 (F1.AC4, F2.AC14). **Amended — section 11 row 10.** |
| F1.AC4 | A link can be deactivated (`is_active=false`) and archived. An inactive slug returns 404 from the capture surface and is never redirected. |
| F1.AC5 | Each link carries its own `notify_policy` for geofence-inside, geofence-outside and automated traffic. **[Amended, row 18]** It also carries `undetermined` (default `normal`). `automated` is fixed at `silent`: any other value is rejected, because automated traffic never notifies (F7.AC1). |
| F1.AC6 | Each link carries its own `interstitial_ms` (default 700, floor 300, cap 1500). Values outside the range are rejected. |
| F1.AC7 | **The destination is never read from a request parameter, header, or path segment.** Only from a `links` row. Violating this re-triggers B4. |
| F1.AC8 | Editing a destination takes effect on the next request with no restart, and writes an `audit_log` row naming the old and new values. |
| F1.AC9 | **[ADD]** A link can be cloned, so a burned slug can be replaced while retaining its configuration and keeping historical visits attached to the old slug. |
| F1.AC10 | Deleting a link is refused while visits reference it; archiving is offered instead. Historical data must never be orphaned. |

### F2 — Public capture and redirect

| ID | Acceptance criterion |
|---|---|
| F2.AC1 | `GET /r/{slug}` returns **200 text/html**, never a 3xx redirect. Rationale: a 302 offers no collection opportunity and is the exact pattern Safe Browsing classifies as an open redirector (B4). |
| F2.AC2 | Before any response is produced, a `visits` row is committed with `stage='server'`, containing every server-derived signal from F3.AC1. **The visit exists even if the client never runs a line of JavaScript.** |
| F2.AC3 | The returned page requires **no JavaScript** to function: inline critical CSS, no external assets, a visible notice, a working `Continue now` link to the destination, and a `<noscript>` meta-refresh fallback. |
| F2.AC4 | Additive inline JavaScript collects the client signals in F3.AC2, requests geolocation permission, and POSTs to `/api/v1/s/{nonce}` using `fetch` with `keepalive: true`. |
| F2.AC5 | A hard client-side timer redirects at `interstitial_ms` regardless of enrichment state, capped at 1500 ms. The visitor is never held waiting on telemetry. |
| F2.AC6 | The enrichment nonce is a single-use HMAC bound to `visit_id` and the IP prefix, with a 60 s TTL. A replayed, expired, or mismatched nonce returns 410 and is logged. |
| F2.AC7 | If enrichment never arrives, a sweeper finalises the visit at 90 s with `stage='server_only'`, runs inference on server signals alone, and enqueues notification. **No visit is ever lost to a failed client.** |
| F2.AC8 | Known link-preview fetchers (`facebookexternalhit`, `LinkedInBot`, `Twitterbot`, `redditbot`, `WhatsApp`, `TelegramBot`, `Slackbot`, and others) are recorded with `classification='crawler'`, excluded from analytics by default, visible in a dedicated view, and **never** notified on. |
| F2.AC9 | In-app webviews are detected and recorded (`is_inapp_webview`, `webview_host`), covering Instagram, Facebook, Messenger, LinkedIn, Reddit, Snapchat, X, TikTok, Line and WeChat. |
| F2.AC10 | **[ADD]** When an in-app webview is detected, the page offers an "open in your browser" affordance, which on Android uses an `intent://` handoff. iOS has no reliable equivalent and the affordance degrades to a copy-link control. |
| F2.AC11 | No public path or query key contains the substrings `track`, `collect`, `analytics`, `pixel`, `beacon`, or `telemetry`. Enforced by a CI route-name test. Rationale: content blockers match those substrings and silently kill the request (B6). |
| F2.AC12 | The capture page issues **no third-party requests** and sets no cross-site cookie. |
| F2.AC13 | `/privacy` is publicly reachable, lists every category of data collected, every inference source including any enabled external service, the retention periods, and the required CC-BY attributions for DB-IP Lite and GeoNames. |
| F2.AC14 | An unknown, inactive, or archived slug returns a 404 page that leaks no information about which links exist. |

### F3 — Data captured per visit

The brief enumerated the required fields. This table states which are server-derived
(always available) and which are client-derived (best effort).

| ID | Acceptance criterion |
|---|---|
| F3.AC1 | **Server-derived, always captured:** exact timestamp (`timestamptz`, UTC); source IP (encrypted, hashed, prefixed per RW-3); ASN, ASN organisation and ASN type; ISP name; reverse-DNS PTR record; connection class (broadband, mobile, datacenter, VPN-suspected, Tor, unknown); full header set (names and values — **not order**, which the reverse proxy discards: RISKS R19, section 11 row 7); User-Agent; UA Client Hints; `Accept-Language`; HTTP version; TLS version; `CF-Ray` colo and `CF-IPCountry` when behind Cloudflare; Referer; UTM parameters. |
| F3.AC2 | **Client-derived, best effort:** screen resolution, viewport, device pixel ratio, colour depth, touch points; `hardwareConcurrency` (CPU cores); `deviceMemory` (RAM, GB); GPU vendor and renderer via WebGL; timezone (IANA) and offset; language list; canvas, audio, font and WebGL hashes; consented geolocation coordinates and accuracy. |
| F3.AC3 | Device class is derived: `mobile`, `tablet`, `desktop`, `tv`, `server`, `bot`, `unknown`. Browser or app medium is recorded as family plus version, including the webview host where applicable. |
| F3.AC4 | Every visit records `classification` from F5.AC1 and the full derivation trail from F4.AC11 — **the brief requirement that the source of every inference be recorded and displayed.** |
| F3.AC5 | Absent client signals are stored as `NULL` and rendered as "not provided" with the reason (JS blocked, webview restriction, permission denied, adblocker). Never as zero or as a fabricated value. |
| F3.AC6 | **[ADD]** `stage` records how complete the visit is: `server`, `enriched`, `server_only`, `rate_limited`. Analytics state which stage mix they are computed over, so a 40-percent webview population cannot silently skew a chart. |
| F3.AC7 | **No personal content is collected.** No names, no email addresses, no usernames, no page content, no keystrokes, no clipboard, no contacts, no cross-site history. Enforced by review and by OOS4. |

### F4 — Location inference

Multi-candidate inference with explicit confidence, abstention, and a recorded
derivation trail. **See RW-1 for the restated accuracy criterion.**

| ID | Acceptance criterion |
|---|---|
| F4.AC1 | Every capture page checks, **first**, whether geolocation permission is **already granted**, using the Permissions API. It **never shows a permission prompt**: one cannot be answered inside the interstitial (F2.AC5, RISKS R20). If already granted, the browser coordinates are authoritative (`confidence 0.99`) and outrank every other source. If undecided, `consent_state='not_asked'`. **Amended — section 11 row 8.** |
| F4.AC2 | If permission is denied, unavailable, or blocked by a webview, inference proceeds from the remaining sources, and `consent_state` records which of those four it was. |
| F4.AC3 | When consent is **granted**: coordinates, accuracy radius, and a reverse-geocoded street-level address are stored. When consent is **not granted**: no coordinates and no street address are stored, and city is the deepest level that may be emitted. |
| F4.AC4 | Reverse geocoding resolves city, district and state **offline** from GeoNames. Street-level address uses Nominatim, rate-limited to 1 request per second, cached, with a descriptive User-Agent per its usage policy, and only for consented visits. |
| F4.AC5 | Ten candidate sources are implemented: S1 consented browser geolocation; S2 GeoLite2-City; S3 IP2Location LITE DB11; S4 IPinfo Lite; S5 DB-IP Lite; S6 rDNS PTR city-code lexicon; S7 ASN and ISP organisation-name parsing; S8 `CF-Ray` edge colo; S9 external no-key HTTPS APIs; S11 timezone and locale cross-check. The numbering keeps S11: **S10, latency triangulation, was dropped** (section 11 row 12). |
| F4.AC6 | Each source emits zero or more candidates as `{level, country, admin1, admin2, city, lat, lng, raw_confidence, evidence}` and is persisted to `visit_candidates`, **one row per candidate**, including candidates that lost or were suppressed. |
| F4.AC7 | S9 (external APIs) is **per-source toggleable** from System Health, results are cached by IP prefix so repeat visitors trigger no outbound call, every call has a hard timeout and a circuit breaker, and a failure degrades silently to the remaining sources. |
| F4.AC8 | ~~S10 (latency triangulation) is feature-flagged and default off — see RW-6.~~ **Withdrawn in M3 — section 11 row 12.** |
| F4.AC9 | S11 does not propose locations; it **reduces the weight** of candidates whose country contradicts the browser timezone. |
| F4.AC10 | Candidates are combined by weighted consensus producing, for each of country, admin1, admin2 and city: a **strict** value that is `NULL` with a recorded `abstain_reason` when confidence is below threshold, and an **advisory** best guess with a 0..1 confidence: the argmax over every locating candidate, which equals the strict value wherever strict emitted one. Plus an `agreement_score` and a `conflict_score`. **Amended — section 11 row 14.** |
| F4.AC11 | The dashboard renders the full derivation for any visit: every source, its candidate, its weight, whether it was accepted or suppressed and why, and its latency. This satisfies the brief requirement for "source (how data was inferenced/derived)". |
| F4.AC12 | Three suppression rules are applied and each records its reason on the affected candidates: |
| | **(a) Registry-artifact suppression** — each ASN modal centroid is precomputed across the offline databases. If the winning city equals that centroid and no non-database source (rDNS, colo, GPS) corroborates it, **city, admin2 and admin1 all abstain** and the country is the deepest strict level — the registry record that placed the city placed the state too. **This is the fix for B1. Amended — section 11 row 11.** |
| | **(b) Mobile and CGNAT ASNs** — city candidates are discarded from strict; admin1 is the deepest emittable strict level. The advisory city is still shown. **This is the brief requirement "Mobile Carrier Gateway Adjustments". Amended — section 11 row 14.** |
| | **(c) Hosting, VPN and Tor ASNs** — all strict location fields abstain. The address describes the infrastructure, not the person. |
| F4.AC13 | Accuracy targets, measured in CI against the ground-truth set (F4.AC15): country strict accuracy ≥ 99.5 percent at near-100-percent coverage; admin1 strict **precision** ≥ 99 percent at coverage ≥ 85 percent, advisory accuracy ≥ 92 percent; city strict precision ≥ 95 percent, advisory accuracy ≥ 70 percent; consented visits city accuracy ≈ 100 percent. **City strict coverage has no floor yet:** it is measured and reported, separately for the Cloudflare and free-subdomain paths, and a floor is set from ground-truth data in M8. Abstaining at city with a recorded reason is the expected outcome for most non-consented visits. Measured separately for consented and non-consented populations. **Amended — section 11 row 9.** |
| F4.AC14 | Weights and thresholds live in **versioned configuration**, not code. Retuning needs no deployment, and every historical `inference_settings` version is retained so a change can be rolled back. |
| F4.AC15 | **[ADD]** A ground-truth labelling interface and CLI let the owner record the true location of a visit. Accuracy metrics are computed against these labels by a CI job and surfaced in the dashboard. |
| F4.AC16 | **[ADD]** Every visit is stamped with `inference_version`, so metrics remain comparable across engine changes. |
| F4.AC17 | **[ADD]** Per-source accuracy is reportable, because `visit_candidates` is normalised. A source that is consistently wrong can be identified and down-weighted with evidence. |
| F4.AC18 | If **every** source fails, the visit still records `country=NULL` with `abstain_reason`, and the redirect still happens. Inference failure is never visitor-visible. |

### F5 — Anti-spoofing, bot detection and classification

| ID | Acceptance criterion |
|---|---|
| F5.AC1 | Each visit is classified as exactly one of `human`, `bot`, `crawler`, `datacenter`, `spam`, `spoofed`, `unknown`, with a `bot_score` and a `spoof_score` in 0..100. |
| F5.AC2 | Every classification stores the **list of rules that fired**, each with its weight and evidence. No verdict is unexplainable — required both by the brief "source" requirement and by ES3. |
| F5.AC3 | **Headless browser detection:** `navigator.webdriver`; absence of `window.chrome` under a Chrome UA; WebGL renderer matching SwiftShader, Mesa OffScreen or llvmpipe; zero-valued `screen`/`outerWidth`; empty plugin and mimeType lists under a desktop UA; Permissions-API anomalies; CDP artefacts; implausible font counts. |
| F5.AC4 | **GPU and OS cross-checking:** a WebGL renderer inconsistent with the claimed platform (for example an Apple GPU under a Windows UA) raises `spoof_score`. |
| F5.AC5 | **iOS memory protection:** Safari on iOS never exposes `navigator.deviceMemory`. Its presence under an iOS UA is a spoofing signal. |
| F5.AC6 | **Stealth honeypot trap:** the capture page contains a CSS-hidden link and a hidden form field. Any interaction with either is recorded and treated as automation. The honeypot path is not named in a way a filter list would block. |
| F5.AC7 | **Device fingerprint collision for proxy detection:** the same `fingerprint_id` observed across at least N distinct ASNs within a rolling window sets `is_proxy_suspected`. The inverse — many distinct fingerprints behind one prefix — is classified as NAT or a carrier gateway, **not** a proxy. |
| F5.AC8 | **[ADD] Header-set fingerprinting, with re-weighting:** which request headers are present, and their values, are compared against what the claimed client always sends (for example `Sec-Fetch-*`, `Sec-CH-UA*` and `Accept-Language` under a Chromium UA). An absent or inconsistent header raises `spoof_score` and `bot_score`. Header **order** and HTTP/2 frame detail are not used — the reverse proxy discards both (RISKS R19) — and the weight they would have carried moves to UA/UA-CH consistency, client-hint cross-checks, headless probes (F5.AC3), the honeypot (F5.AC6) and network reputation (F5.AC9). JA4/TLS fingerprinting remains out of v1 — RISKS R7. **Amended — section 11 row 7.** |
| F5.AC9 | Datacenter, hosting, VPN and Tor detection is derived from ASN type, ASN organisation-name keywords, rDNS patterns, and curated hosting-ASN lists. No paid API. |
| F5.AC10 | **Agreement and conflict scoring:** `agreement_score` measures how strongly independent sources concur, `conflict_score` how strongly they contradict. Both are stored, visible, and filterable. |
| F5.AC11 | **[ADD]** Additional signals: request-rate anomaly per prefix; missing `Accept-Language`; HTTP/1.0; `Accept: */*` only; TLS version inconsistent with the claimed browser; timezone inconsistent with inferred country; screen dimensions inconsistent with the claimed device class; impossible travel for a known `visitor_id`. |
| F5.AC12 | Detection is **rules-based and weighted, not machine-learned** — ADR-0011. Thresholds are versioned configuration; each visit is stamped with `classifier_version`. |
| F5.AC13 | The brief lists "malware" as a classification target. Tracelet cannot inspect a visitor device, so this is scoped as **malicious-automation signals** — known-malicious ASN and UA lists, scanner and exploit-probe patterns on the capture path — surfaced under `classification='spam'` with the specific signals attached. **This is a scope clarification, logged in section 11.** |
| F5.AC14 | A classification failure defaults to `unknown` with a recorded reason, and the redirect still happens. |

### F6 — Geofencing

| ID | Acceptance criterion |
|---|---|
| F6.AC1 | An owner can draw polygons and circles on an interactive map to define a geofence, with vertex editing, drag, and delete. **[Amended, row 16]** The map is the self-hosted country and state outlines, with no basemap (ADR-0017, ADR-0020): vertices and a circle's centre and radius can also be typed as coordinates, and an owner can instead pick countries and states as a **region geofence**, on the map or from a searchable list. |
| F6.AC2 | Geofences are stored as PostGIS `geography(Polygon,4326)` and evaluated with `ST_Covers`, which is geodesically correct. Circles retain `center` and `radius_m` for round-trip editing and are stored buffered as polygons. **[Amended, row 16]** A region geofence stores region keys (`IN`, `IN\|Karnataka`) instead of an area, and matches only the strict country and strict state, never advisory fields (ADR-0020). |
| F6.AC3 | A geofence has a name, description, priority, active flag, notification priority (`high`, `normal`, `silent`), and an optional restriction to specific links. |
| F6.AC4 | Validity is enforced on write: `ST_IsValid`, a vertex cap (2000) to bound evaluation cost, and rejection of self-intersecting rings with a descriptive error. |
| F6.AC5 | On finalisation, each visit is evaluated against all active geofences and records `matched_geofence_ids` plus `geofence_state` (`inside`, `outside`, `undetermined`). **[Amended, row 16]** Evaluation happens when the visit is inferred (ADR-0015). The state is `inside` if any applicable geofence matches, else `undetermined` if any could not be decided, else `outside`; it stays NULL when no active geofence applies to the visit's link. |
| F6.AC6 | `geofence_state='undetermined'` when no location of sufficient confidence exists. **An abstaining inference must never be silently treated as "outside".** **[Amended, row 16]** "Sufficient" is per geofence: a polygon or circle needs `geopoint`; a region needs the strict level it names, or a strict level that rules it out (a strict country or state elsewhere). A visit with no strict location at any level can never be `inside` or `outside`, enforced by `CHECK` constraints. |
| F6.AC7 | On overlap, the highest-priority matching geofence determines notification behaviour; all matches are still recorded. |
| F6.AC8 | Evaluation uses a GiST index and adds no more than 5 ms at p95 to finalisation. |
| F6.AC9 | **[ADD]** GeoJSON import and export, so boundaries can be prepared in external tools. |
| F6.AC10 | **[ADD]** A test control evaluates an arbitrary coordinate against current geofences without creating a visit. |

### F7 — Telegram notifications

| ID | Acceptance criterion |
|---|---|
| F7.AC1 | A notification fires **only** when `classification='human'`. Bots, crawlers, datacenter clients, spam and spoofed visits never notify. |
| F7.AC2 | A notification fires only for a **new** visitor: a `visitor_id` not seen for that link in the preceding 24 hours. Deduplication is enforced by a unique `dedup_key` on the outbox, so it holds even under concurrent writes and retries. **[Amended, row 17]** "New" is per calendar day: at most one alert per link and `visitor_id` per local day in the reporting timezone (`TRACELET_REPORTING_TZ`, default `Asia/Kolkata`), the day of the visit's arrival. |
| F7.AC3 | A visit inside a geofence produces a **high-priority** message distinguished in format and content. A visit outside produces a **normal** message. Both fire — the brief specifies a special case and a normal case, not one or the other. **[Amended, row 18]** Those are the defaults; the priority is resolved per visit. `inside`: the less urgent of the link's `inside` and the `notify_priority` of the highest-priority matching geofence, so either can lower or silence it. `outside`: the link's `outside`. `undetermined`: the link's `undetermined`, worded "Location not confirmed" (ADR-0020). No applicable geofence: the link's `outside`, with no geofence line. `silent` sends nothing. |
| F7.AC4 | The message contains timestamp, link label, strict location with confidence, advisory location where strict abstained, device and browser, connection class, ASN and ISP, classification with score, geofence name where matched, and a deep link to the visit detail page. |
| F7.AC5 | Notifications are enqueued in the **same database transaction** as the visit finalisation. A Telegram outage cannot lose an alert, and a rolled-back visit cannot emit a phantom one. |
| F7.AC6 | Delivery retries with exponential backoff and jitter, to a maximum attempt count, after which the job is dead-lettered and visible in System Health with a manual retry control. |
| F7.AC7 | A Telegram failure never affects the visitor path and never fails the capture request. |
| F7.AC8 | **[ADD]** A test-message control verifies bot token and chat ID from the dashboard. |
| F7.AC9 | **[ADD]** A configurable quiet-hours window holds non-high-priority notifications until the window closes, so 500 visits per day cannot become 500 night-time messages. |

### F8 — Admin authentication and account security

| ID | Acceptance criterion |
|---|---|
| F8.AC1 | Passwords are hashed with Argon2id, pinned to `m=32MiB, t=3, p=1`. **Library defaults would exhaust the 1 GB box on a single login** — CLAUDE.md section 5. |
| F8.AC2 | Sessions are **opaque 256-bit random tokens**, stored server-side as a hash, in a cookie named with the `__Host-` prefix and flags `HttpOnly; Secure; SameSite=Strict; Path=/`. No JWT — ADR-0008. |
| F8.AC3 | Sessions carry an absolute expiry and an idle timeout, are bound to the IP prefix and a UA hash, are individually revocable, and are listable by their owner. |
| F8.AC4 | **TOTP is mandatory** for every admin, enrolled at first login. An account cannot reach any dashboard route before enrolment completes. |
| F8.AC5 | TOTP validates within a one-step clock skew and records the last accepted counter, so a code cannot be replayed. |
| F8.AC6 | Enrolment issues **10 single-use recovery codes**, displayed exactly once, stored Argon2-hashed. Use of one is audit-logged and warns when fewer than 3 remain. |
| F8.AC7 | Password recovery is delivered over **Telegram** to the verified owner chat as a single-use, time-limited, signed link. There is no email channel in this design. |
| F8.AC8 | A break-glass CLI (`tracelet admin reset-password`) works with database and shell access only, and writes an audit record. |
| F8.AC9 | Login is rate-limited per identifier **and** per IP prefix, using GCRA with a burst allowance so ordinary mistyping is not punished, and an account locks after repeated consecutive failures. A refusal carries `Retry-After` (F11.AC10). **Over-limit responses must not differ between an existing and a non-existing account** — the limiter is keyed on the submitted identifier before any lookup, so both are throttled identically. Amended 2026-09-28, see section 11 row 6. |
| F8.AC10 | No user enumeration anywhere: login, password reset and recovery-code endpoints return identical responses and take indistinguishable time for existing and non-existing accounts, including a dummy Argon2 verification on unknown identifiers. |
| F8.AC11 | All state-changing requests require a CSRF token (double-submit) **and** a validated `Origin` header. |
| F8.AC12 | Two roles: `owner` (full) and `analyst` (read-only). Role checks are server-side on every route; the UI merely reflects them. |
| F8.AC13 | **At least one `owner` must always exist.** The last owner cannot be deleted, demoted, or disabled. Enforced in the database, not only in application code. |
| F8.AC14 | Admin profile management: display name, timezone, theme preference, password change (requiring the current password), TOTP re-enrolment, recovery-code regeneration, and active-session revocation. |
| F8.AC15 | New admins are created by an owner and activated through a **one-time enrollment token**. No seeded or default password exists anywhere in the system. |
| F8.AC16 | Every authentication event, privilege change, configuration change and destructive action writes an append-only `audit_log` row. The application database role has no `UPDATE` or `DELETE` grant on that table. |
| F8.AC17 | **[ADD]** A password-strength policy: minimum 12 characters, checked against a compromised-password list, with rejection of values containing the site domain or the account identifier. |

### F9 — Dashboard analytics and visualisation

| ID | Acceptance criterion |
|---|---|
| F9.AC1 | A **visit timeline** shows visits chronologically with expandable detail, at hour and day granularity. |
| F9.AC2 | A summary view shows total visits, unique visitors, human share, bot share, consent-grant rate, geofence-hit rate, and enrichment-completion rate, each against the selected period with period-over-period comparison. |
| F9.AC3 | Time-series charts of visits by hour and by day, split by any of classification, device class, connection class, country, admin1, or link. |
| F9.AC4 | Breakdown charts for country, state, city, ASN and ISP, device class, browser and app medium, OS, screen resolution, connection class, and classification. Location breakdowns count each visit at its **best-guess** location (advisory); a visit no source could place is counted as unknown. **Amended — section 11 row 14.** |
| F9.AC5 | A **geographic map** plots visits by best-guess location, with a choropleth by country and state and clustered points at consented GPS or the best-guess city. **Amended — section 11 row 14.** |
| F9.AC6 | **[ADD]** A **calendar heatmap** of daily volume, for spotting weekly and campaign-driven patterns. |
| F9.AC7 | **[ADD]** An **inference-source Sankey** showing how visits flow from the sources that produced candidates to the level finally emitted, making abstention visible at a glance. |
| F9.AC8 | **[ADD]** A **stage funnel**: requests, server-captured, enriched, consented, notified — the fastest way to see webview and adblocker loss. |
| F9.AC9 | **[ADD]** A **confidence distribution** histogram per level, which is how thresholds get tuned. |
| F9.AC10 | **[ADD]** An **accuracy panel** showing current precision and coverage per level against the ground-truth set, with the labelled sample size stated next to every figure. |
| F9.AC11 | **[ADD]** A **bot-signal frequency** chart, ranking which detection rules fire most often. |
| F9.AC12 | **[ADD]** A **returning-visitor view** for a single `visitor_id`: every visit, with location drift and device changes. |
| F9.AC13 | Filtering by absolute and relative time period, and by link, classification, country, state, city, ASN, device class, connection class, consent state, geofence, `visitor_id`, minimum confidence, and coordinate presence. Filters compose and are shareable via URL. Location filters match the best-guess location, as the breakdowns count it (section 11 row 14). |
| F9.AC14 | Every visit detail view shows the full inference derivation (F4.AC11) and the full list of fired classification signals (F5.AC2). |
| F9.AC15 | Export to CSV and NDJSON, honouring the active filters, streamed rather than buffered. Exports contain no plaintext IP. |
| F9.AC16 | **Semi-dark is the default theme**, with light and dark selectable and persisted per admin. All three themes meet WCAG AA contrast for text and chart elements. |
| F9.AC17 | The dashboard is responsive to 1280 px and usable at tablet width **and at 390 px phone width** (drawer navigation, single column, tables that scroll or become row cards). Charts degrade to legible fallbacks rather than overflowing. **Amended — section 11 row 15.** |
| F9.AC18 | Every chart and table has explicit empty, loading and error states. **A blank panel is a defect** — B5. |
| F9.AC19 | Analytics queries read from rollup tables, not raw visit rows, to hold NFR2 on one shared vCPU. Closed days settle daily; **today is refreshed every five minutes** so a new visit appears the same day. A filter or range the rollups cannot serve is answered from raw rows with the same definitions, and the response says which path served it (ADR-0016, §11 row 13). |
| F9.AC20 | **[ADD]** Every analytics response states the stage mix it was computed over, so a chart cannot silently mislead when enrichment coverage shifts. |

### F10 — System health and operations

| ID | Acceptance criterion |
|---|---|
| F10.AC1 | Live host metrics: CPU percent, RAM used over total, swap used over total, disk used over total, load average, uptime, and database size on disk. Polled, with configurable interval. |
| F10.AC2 | Temperature in Celsius where the host exposes sensors, otherwise `N/A` with a reason — **RW-5**. |
| F10.AC3 | A geo-database panel lists each database with installed version, release date, install date, file size, SHA-256, and a clear **up-to-date or stale** indicator. |
| F10.AC4 | An update control downloads, verifies and atomically swaps a geo database without restarting the application or dropping a request. Failure leaves the previous version in place and surfaces the error. |
| F10.AC5 | Database updates stream to disk and validate in a memory-capped subprocess. **A failed update must never OOM the capture endpoint** — RISKS R4. |
| F10.AC6 | Tracking-link management (F1) is reachable from System Health, satisfying the brief item "redirect url updation/editing" as reworded in RW-2. |
| F10.AC7 | Each of the eleven inference sources can be individually enabled or disabled at runtime, taking effect without a restart. |
| F10.AC8 | An **inference flow diagram** renders the source levels and their order, showing which are enabled, which fired for a chosen sample visit, and where suppression occurred. This is the brief item "a diagram flow model that displays different inference/derivation system levels". |
| F10.AC9 | Login, logout, password change, TOTP re-enrolment and session revocation are reachable from the dashboard. |
| F10.AC10 | Admin profile management (F8.AC14) and admin lifecycle management (F8.AC15) are reachable by an owner. |
| F10.AC11 | Backup management: list backups with status, size and checksum; trigger a manual backup; download a backup; view the last restore-verification result and its timestamp. |
| F10.AC12 | Retention management: per-category periods (visits, encrypted IP, audit log), a **dry-run preview showing exactly what would be deleted**, and only then an executable purge. |
| F10.AC13 | Outbox visibility: queue depth, in-flight count, failed and dead-lettered jobs with their last error, and a manual retry. |
| F10.AC14 | **[ADD]** A degradation banner appears whenever any subsystem is unhealthy — a stale geo database, a tripped external-API circuit breaker, a dead-lettered notification, low disk, a failed backup. |
| F10.AC15 | **[ADD]** Host metrics are read from the host `/proc` and `/sys` mounted read-only, so figures reflect the VM rather than the container. |

### F11 — Rate limiting and abuse control

| ID | Acceptance criterion |
|---|---|
| F11.AC1 | Four layers: L0 Cloudflare (purchased-domain path only), L1 Caddy connection, body-size and slow-loris limits, L2 application GCRA limits in PostgreSQL, L3 a bounded connection pool providing backpressure instead of collapse. |
| F11.AC2 | Capture-path limits are keyed by IP prefix and route class, with a burst allowance, and defaults calibrated to NFR1 with headroom. |
| F11.AC3 | **A rate-limited visitor is still redirected to the destination**, immediately and without capture, and the event is recorded as `stage='rate_limited'`. Abuse control must never punish a human. |
| F11.AC4 | The enrichment endpoint accepts exactly one successful request per nonce, ever. |
| F11.AC5 | Admin login limits are keyed by identifier and by prefix, using GCRA with a burst allowance, plus account lockout (F8.AC9). |
| F11.AC6 | Dashboard API limits are keyed by session, sized so normal use never trips them and a runaway client cannot saturate the vCPU. |
| F11.AC7 | Both directions are covered — the brief "upstream and downstream": **inbound** request limits, and **outbound** limits on Nominatim (1 rps), the external geo APIs, and the Telegram API, each with its own budget and circuit breaker. |
| F11.AC8 | Rate-limit state is shared across worker processes, because two Uvicorn workers must not each grant a full allowance. |
| F11.AC9 | Every limit is configurable without redeployment, and every change is audit-logged. |
| F11.AC10 | **[ADD]** Over-limit responses carry `Retry-After` and are logged with enough context to distinguish an attack from a legitimate traffic spike. |

### F12 — Privacy and data lifecycle

| ID | Acceptance criterion |
|---|---|
| F12.AC1 | **Raw IP is never persisted in plaintext.** `HMAC(ip)` with a server-side pepper, the /24 or /48 prefix, and the ASN are durable; the full address is AES-256-GCM only — RW-3. |
| F12.AC2 | Encrypted IP is purged after its TTL (default 30 days). `HMAC(ip)` and the prefix survive, so analytics develop no hole. |
| F12.AC3 | The encryption key is read from a `0400` file **outside the database volume**, loaded into process memory at boot, never written to the database, never logged, and never returned by an API. |
| F12.AC4 | Decryption is `owner`-only, rate-limited, and writes an `audit_log` row naming the visit and the actor. |
| F12.AC5 | Key rotation is supported through a `key_version` column and a re-encryption job. |
| F12.AC6 | `visitor_id` is a non-reversible HMAC. The pepper is never exposed; rotating it breaks historical linkage by design, and rotation is scheduled at the retention boundary. |
| F12.AC7 | Retention periods are configurable per category with these defaults: visit rows 180 days, encrypted IP 30 days, audit log 365 days, rollup aggregates retained indefinitely. |
| F12.AC8 | Purges are transactional, batched to avoid long locks, dry-runnable, and audit-logged with the row counts actually deleted. |
| F12.AC9 | Nightly `pg_dump`, compressed, with a SHA-256 manifest, 7 daily plus 4 weekly rotation. |
| F12.AC10 | A **monthly automated restore-verification** restores the newest backup into a scratch schema and asserts row counts. A backup that has never been restored is not treated as a backup. Result and timestamp are surfaced in System Health. |
| F12.AC11 | Off-VM backup copies are **manual download** from the dashboard, per the Gate 1 decision to add no cloud-storage account. **Losing the VM loses everything since the last manual download** — this is an accepted risk, RISKS R11. |
| F12.AC12 | **Must never be lost:** `admins`, `admin_recovery_codes`, `links`, `geofences`, `inference_settings`, `retention_policy`, `audit_log`. These are small, slow-changing, and included in every backup. Visit rows are valuable but reconstructible-in-principle only going forward; aggregates are covered by NFR5. |
| F12.AC13 | Structured logs redact IP addresses, coordinates and tokens by default. A log line must never contain what the database refuses to store in plaintext. |
| F12.AC14 | **[ADD]** A per-visit data-subject export, so a visitor who asks what was collected about a given visit can be answered from the visit ID in the redirect chain. |

### F13 — Transport and browser-trust hardening

| ID | Acceptance criterion |
|---|---|
| F13.AC1 | HTTPS only, HTTP/2, automatic certificate issuance and renewal, with HSTS including `preload`-eligible directives. |
| F13.AC2 | Security headers on every response: a strict nonce-based `Content-Security-Policy` with no `unsafe-inline` and no `unsafe-eval`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`, `X-Frame-Options` or `frame-ancestors`. CSP is verified by a CI test, not by inspection. |
| F13.AC3 | **No open redirect** — F1.AC7. This is the primary structural remedy for B4. |
| F13.AC4 | The capture page does not imitate a login form, a payment form, or any third-party brand. Deceptive patterns are what Safe Browsing classifies on. |
| F13.AC5 | Deployment works on a purchased domain and on a free dynamic-DNS subdomain, selected by configuration, with Cloudflare proxy mode as an independent toggle. |
| F13.AC6 | When behind Cloudflare, the real client IP is taken from `CF-Connecting-IP` **only** when the peer is a verified Cloudflare address. Otherwise the peer address is used. A forged header must never be trusted. |
| F13.AC7 | Input validation on every external input — path, query, header, body, cookie — with typed schemas and an explicit rejection response. No implicit coercion. |
| F13.AC8 | **[ADD]** A documented browser-trust checklist for M9: domain age, Search Console registration, a Safe Browsing review request, no URL shortener in the chain, and a reachable privacy page. **RISKS R8 records that none of this guarantees an outcome.** |

### F14 — Platform, deployment and CI

| ID | Acceptance criterion |
|---|---|
| F14.AC1 | Three containers — `caddy`, `api`, `db` — orchestrated by Docker Compose, each with a hard `mem_limit`, a healthcheck, and a restart policy. |
| F14.AC2 | Runs within the GCP e2-micro envelope: 1 vCPU, 1 GB RAM, 2 GB swap, 30 GB disk, verified by a load test at NFR1 levels during M9. |
| F14.AC3 | Fresh clone to running in **5 commands or fewer** — SC2. |
| F14.AC4 | The frontend is built at image-build time and served as static files. **No Node.js runtime in production.** |
| F14.AC5 | All configuration comes from environment variables with a documented `.env.example`. Secrets are never committed and never stored in the database. |
| F14.AC6 | Schema changes are Alembic migrations, forward-only, reviewed, and applied by `make migrate`. |
| F14.AC7 | CI runs, and must pass: `ruff check`, `ruff format --check`, `mypy --strict`, `pytest` unit, `pytest` integration **against a real PostgreSQL + PostGIS service container**, `eslint`, `prettier --check`, `tsc --noEmit`, the OpenAPI-to-TypeScript drift check, `docker build`, commitlint, and `guard-private-docs`. |
| F14.AC8 | `guard-private-docs` **fails the build if any path under `docs/private/` is tracked by git.** |
| F14.AC9 | The TypeScript API client is generated from the FastAPI OpenAPI schema. CI fails if the committed client differs from a fresh generation — one source of truth for the contract. |
| F14.AC10 | Conventional commits, enforced by commitlint. One PR per milestone. |
| F14.AC11 | **[ADD]** A CI job asserts route naming (F2.AC11) and the absence of blocked substrings in public paths. |
| F14.AC12 | **[ADD]** A CI job computes location accuracy against the ground-truth fixture and fails if a metric regresses below its F4.AC13 target. |

### F15 — Error handling, observability and graceful degradation

| ID | Acceptance criterion |
|---|---|
| F15.AC1 | A typed exception hierarchy maps to **RFC 9457 Problem Details** JSON with `type`, `title`, `status`, `detail`, `instance`, an application `code`, a `trace_id`, and a field-level `errors` array for validation failures. |
| F15.AC2 | Every response and every log line carries the same ULID `trace_id`. A user-reported problem is diagnosable from that identifier alone. |
| F15.AC3 | A 5xx response exposes only `trace_id`, `code`, `status` and a generic title. Stack traces, SQL, and internal hostnames never reach a client. |
| F15.AC4 | Logs are structured JSON with PII redaction (F12.AC13) and a configurable level. |
| F15.AC5 | `/healthz` is liveness with no detail and no auth. `/readyz` reports readiness including database reachability and migration state. |
| F15.AC6 | **Graceful degradation, specified per dependency:** |
| | database unreachable — the redirect still happens, uncaptured, and the failure is logged |
| | an offline geo database missing or corrupt — that source is skipped, others continue, System Health raises the fault |
| | an external geo API timing out — the circuit breaker opens, inference proceeds on remaining sources |
| | Nominatim unavailable — no street address, coarser levels unaffected |
| | Telegram unavailable — the outbox retries, nothing is lost |
| | client JavaScript blocked — server-only capture, sweeper finalises |
| | geolocation denied — city-level ceiling per F4.AC3 |
| | disk nearly full — backups and purges take priority, capture continues, banner raised |
| | swap thrashing — request shedding at L2 before the kernel OOM killer engages |
| F15.AC7 | **No unhandled exception may reach a visitor.** The capture path is wrapped so that any failure still results in a redirect. |
| F15.AC8 | **[ADD]** Every solved defect is recorded in `docs/ERRORS.md` with symptom, root cause, fix, and the prevention measure that stops it recurring. |
| F15.AC9 | **[ADD]** Observability is the health API plus structured logs. **Prometheus and Grafana are deliberately excluded** — roughly 250 MB, which does not fit the budget. ADR-0013 records the trigger for revisiting. |

---

## 5. Non-functional requirements

| ID | Requirement | Acceptance |
|---|---|---|
| **NFR1** | Throughput | NFR1.AC1: at least **10 concurrent** capture requests sustained with no error and no target breach. NFR1.AC2: at least **500 capture requests per day**. NFR1.AC3: at least **2 concurrent admin** sessions performing dashboard work. NFR1.AC4: verified by load test in M9 with results recorded. NFR1.AC5: designed headroom of at least 3x on the capture path, so a modest spike degrades rather than fails. |
| **NFR2** | Latency (p95) | NFR2.AC1: capture-path **server processing** under **50 ms**, excluding network. NFR2.AC2: visitor time-to-first-byte approximately **300 ms** behind Cloudflare, approximately **800–1000 ms** direct to a US origin — **RW-7**. NFR2.AC3: total visitor time to destination approximately **1.0 s** behind Cloudflare, **1.7 s** direct, inclusive of the interstitial. NFR2.AC4: dashboard API server-side p95 under **300 ms**. NFR2.AC5: geofence evaluation under **5 ms**. NFR2.AC6: enrichment and inference are off the critical path and cannot delay a redirect. |
| **NFR3** | Graceful degradation | NFR3.AC1: every dependency has a specified degraded mode — F15.AC6. NFR3.AC2: **no single dependency failure prevents the redirect.** NFR3.AC3: degraded state is visible in System Health, never silent. |
| **NFR4** | Error handling | NFR4.AC1: one consistent JSON error shape everywhere — F15.AC1. NFR4.AC2: no unhandled exception reaches any client — F15.AC7. NFR4.AC3: every error is traceable by `trace_id`. |
| **NFR5** | Data: retention, consistency, and what must never be lost | NFR5.AC1: retention is configurable per category with the F12.AC7 defaults. NFR5.AC2: a visit and its notification are committed **atomically** — a visit never exists without its queued alert, and an alert never fires for a rolled-back visit. NFR5.AC3: **must never be lost** — the F12.AC12 set. NFR5.AC4: rollup aggregates survive raw-row purging, so history older than the retention window remains analysable. NFR5.AC5: the append-only `audit_log` cannot be modified by the application role. NFR5.AC6: backups are verified by restore, monthly — F12.AC10. |
| **NFR6** | Resource envelope | NFR6.AC1: steady-state RSS at or below **700 MB** of the 1 GB, leaving at least 300 MB headroom. NFR6.AC2: every container has a hard `mem_limit`, so the failure mode is a single container restarting. NFR6.AC3: no dependency that drags in `numpy`, `scipy` or `pandas`. NFR6.AC4: disk growth projected and bounded within 30 GB including backups. |
| **NFR7** | Accessibility and usability | NFR7.AC1: WCAG AA text and chart contrast in all three themes. NFR7.AC2: keyboard navigation for all dashboard controls. NFR7.AC3: no chart conveys meaning by colour alone. |

---

## 6. Constraints

| ID | Constraint | Resolution |
|---|---|---|
| **C1** | Backend must be Python | FastAPI + Pydantic v2 + SQLAlchemy 2.0 async — ADR-0001 |
| **C2** | Frontend is our recommendation | React 19 + TypeScript strict + Vite, static-built — ADR-0003 |
| **C3** | Database is our recommendation | PostgreSQL 16 + PostGIS — ADR-0002 |
| **C4** | Deploy target Docker + GCP e2-micro, 1 vCPU, 1 GB RAM, 2 GB swap, 30 GB disk | Three containers with hard memory limits; budget in ARCHITECTURE — ADR-0012 |
| **C5** | Development on Windows 11 + Docker via WSL2, 16 GB RAM, Ryzen 7 7840HS | Identical Compose stack locally; the only divergences are host thermal sensors (RW-5) and no Cloudflare in front |
| **C6** | Free tier only; all external services fully free; ask before requiring an email or key | Accounts confirmed at Gate 1: Telegram, MaxMind, IP2Location LITE, IPinfo Lite. **No email or SMTP provider** — recovery runs over Telegram. Domain cost (1–12 USD/yr) is the sole non-free item, and the free-subdomain path exists as a degraded alternative |

---

## 7. Engineering standards

| ID | Standard | Enforcement |
|---|---|---|
| **ES1** | Typed everywhere, strict mode | `mypy --strict` and `tsc --noEmit` with `strict: true` in CI. No bare `Any` or `any`; `type: ignore` requires an inline reason |
| **ES2** | Linter and formatter enforced in CI | `ruff check`, `ruff format --check`, `eslint`, `prettier --check` |
| **ES3** | Unit tests for domain logic; integration tests for API and DB; **no mocking the DB in integration tests** | Integration suite runs against a real PostgreSQL + PostGIS service container — F14.AC7 |
| **ES4** | Typed errors to a consistent JSON error shape | RFC 9457 — F15.AC1 |
| **ES5** | No new dependency without justification | Dependency ledger in ARCHITECTURE, one line of reasoning each, plus the rejected alternative |
| **ES6** | Conventional commits; one PR per milestone | commitlint in CI; MILESTONES defines the PR boundaries |

---

## 8. Out of scope — permanently

| ID | Excluded |
|---|---|
| **OOS1** | Hacking or malice of any kind |
| **OOS2** | Illegally accessing data |
| **OOS3** | Violating privacy, platform guidelines, or legal restrictions |
| **OOS4** | Extracting real personal data — names, home address without an explicit grant, usernames, email addresses, or any sensitive or private information |
| **OOS5** | Illegal or non-consensual tracking |
| **OOS6** | **[ADD]** Any collection of page content, keystrokes, clipboard, contacts, installed applications, or cross-site browsing history; any attempt to bypass a browser permission prompt, defeat a content blocker by obfuscation, or fingerprint by exploiting a browser vulnerability. Recorded so the boundary is testable, not merely aspirational |

**Note on OOS4 and F4.AC3.** A street-level address is derived **only** when the visitor
explicitly grants the browser geolocation permission. That grant is the consent OOS4
requires. Without it, city is the deepest level ever emitted and no coordinates are stored.

---

## 9. Known bugs and the requirements that address them

Full diagnoses are in `docs/ERRORS.md`.

| ID | Reported symptom | Addressed by |
|---|---|---|
| **B1** | Wi-Fi visitor resolves to the ISP head office — a Bangalore visitor recorded as Faridabad or Noida | F4.AC12(a) registry-artifact suppression, F4.AC5 S6 rDNS lexicon and S8 colo, F4.AC10 consensus |
| **B2** | Wrong city inside the correct state is tolerable, but accuracy should still improve | F4.AC10 hierarchical abstention, F4.AC13 targets, F4.AC15 ground truth |
| **B3** | Capture link opened inside the Instagram in-app browser records nothing and simply redirects | F2.AC2 server-authoritative capture, F2.AC7 sweeper, F2.AC9 webview detection, F2.AC10 open-in-browser, F4.AC5 S8 colo which survives the webview |
| **B4** | Browser marks the site unsafe | F1.AC7 and F13.AC3 no open redirect, F13.AC1 TLS and HSTS, F13.AC2 headers, F13.AC4 no deceptive patterns, F13.AC8 trust checklist. **RISKS R8 records that no guarantee exists** |
| **B5** | Black or blank screen where data does not load | F2.AC3 zero-JS capture page, F9.AC18 mandatory empty/loading/error states, F14.AC1 memory limits, F15.AC7 no unhandled exception |
| **B6** | The visitor browser refuses to provide data; capture endpoints are mistaken for bot endpoints | F2.AC11 neutral route naming, F2.AC12 no third-party requests, F14.AC11 CI route-name assertion. **Root cause is content blockers matching URL substrings, not bot detection** |

---

## 10. Success criteria

| ID | Criterion | Verification |
|---|---|---|
| **SC1** | All in-scope flows work end to end on the deployed URL | M9 acceptance run against production, all F-IDs exercised |
| **SC2** | CI green; setup in 5 commands or fewer from a fresh clone | F14.AC3, F14.AC7 |
| **SC3** | Highest achievable city accuracy; country and state accuracy as high as the method permits | **Restated per RW-1** to the measurable targets in F4.AC13, verified by the CI accuracy job (F14.AC12) against the ground-truth set (F4.AC15) |
| **SC4** | Every architectural decision is explainable and defensible | 14 ADRs with Context, Decision, Alternatives, Consequences, Status; plus `docs/private/02-WHY-THESE-DECISIONS.md` |

---

## 11. Amendment log

| # | Date | Change | Authority |
|---|---|---|---|
| 1 | 2026-09-25 | RW-1 through RW-7 recorded | Gate 1, Gate 2 |
| 2 | 2026-09-25 | F5.AC13 — "malware" classification scoped to malicious-automation signals, because Tracelet cannot inspect a visitor device | Gate 3, flagged here for confirmation |
| 3 | 2026-09-25 | Accounts confirmed: Telegram, MaxMind, IP2Location LITE, IPinfo Lite. No email or SMTP provider; recovery over Telegram plus recovery codes plus CLI | Gate 1 |
| 4 | 2026-09-25 | Both the purchased-domain and free-subdomain paths are in scope, config-switched, and explicitly **not** equivalent | Gate 1, Gate 2 |
| 5 | 2026-09-25 | JA4/TLS fingerprinting deferred out of v1; header-order and HTTP/2 fingerprinting substituted — F5.AC8, RISKS R7 | Gate 2 |
| 6 | 2026-09-28 | **F8.AC9 reworded, and F11.AC5 with it.** Two clauses did not describe what was built, and were raised during M1 rather than resolved unilaterally. (a) *"Over-limit responses are indistinguishable from wrong credentials"* → *"must not differ between an existing and a non-existing account"*. The original asks the limiter to hide **itself**, which conflicts with F11.AC10 (`Retry-After` on a 429) and with the error catalogue approved at Gate 3, and leaves a legitimate admin retrying into a limit they cannot see. The property actually wanted is enumeration resistance, and it holds: the bucket is keyed on the submitted identifier before any lookup. (b) *"with exponential backoff"* → a description of the GCRA behaviour, which produces a `Retry-After` that grows with how far the caller is over the sustained rate, plus a flat 30-minute lockout. Per-attempt exponential backoff was considered and **not** wanted | Repository owner |
| 7 | 2026-09-29 | **F5.AC8 reworded, and F3.AC1 with it: header *set*, not header order.** Measured in M2 (RISKS R19): Caddy is a Go server, which parses headers into a map before any handler runs and forwards them sorted, and consumes HTTP/2 frames itself, so neither header order nor HTTP/2 detail ever reaches the application. Row 5's substitute for JA4 therefore cannot be built in this stack. F5.AC8 now fingerprints the header set and values against the claimed client and re-weights the remaining signals; F3.AC1 no longer promises header order. `visits.header_order_hash` stays NULL. Weaker than order — a careful client copies the set — and R7's ceiling is lower for it. Observing below Go's HTTP server (a listener wrapper or a TLS-terminating proxy) was considered and declined for v1: another component and more memory on the 1 GB box | Repository owner |
| 8 | 2026-09-29 | **F4.AC1 reworded: read already-granted permission, never prompt.** This departs from the brief's "ask location permission first" (section 12), knowingly. Spike B (RISKS R20) measured every real browser — Instagram webview, Android Chrome, desktop Chrome — unable to answer a prompt inside the 700 ms interstitial, so a prompt only ever vanished under the visitor. **Consequence, stated plainly:** because the capture page never asks, a visitor will have granted this origin permission only in unusual cases, so consented (S1) location will be rare in practice, and nearly every visit will be inferred without consent at city level at most (F4.AC3). Holding the redirect while a prompt is open was rejected: it violates F2.AC5 | Repository owner |
| 9 | 2026-09-29 | **F4.AC13: city strict coverage floor removed until M8; precision kept.** Spike A (RISKS R3) measured rDNS city-code coverage at **2.0 %** across 750 Indian ISP IPv4 addresses — 0 % for Jio, Vi and all IPv6 — against a pre-set 30 % line whose outcome was "amend F4.AC13 before building M3". The rule's prescribed fallback, S8 plus consented GPS, is itself weakened: row 8 makes consented location rare and R10 removes S8 on the free-subdomain path. So the ≥ 50 % city coverage floor is unreachable. **Kept:** city strict precision ≥ 95 % (ADR-0005: never confidently wrong), advisory city ≥ 70 %, and every country and admin1 target. **Changed:** city strict coverage is measured and reported per path, with its floor set in M8 from the ground-truth set (F4.AC15) rather than guessed now. Relaxing precision to buy coverage was considered and declined: it reintroduces B1 | Repository owner |
| 10 | 2026-09-29 | **F1.AC3: the default link now does something.** The bare capture path `/r` and `/r/` goes through it. Two alternatives were declined: serving the domain root through it collides with the dashboard, which the SPA serves at `/`, and would need an ADR to move it; a fallback for unknown, inactive or archived slugs contradicts F1.AC4 (a deactivated link must stop redirecting) and F2.AC14, and would record scanner probes as visits | Repository owner |
| 11 | 2026-09-29 | **F4.AC12(a): a registry-artifact city collapses to country, not admin1.** Found writing the rule (RISKS R22) and proven by a unit test: in B1's own example — a Bangalore visitor placed in Faridabad — the databases placed the *state* on the registry address too (Haryana), so collapsing to admin1 emitted a strict wrong state, the error B2 says is not tolerable and CLAUDE.md invariant 5 forbids. Advisory fields still show the registry's guess | Repository owner |
| 12 | 2026-09-29 | **S10 latency triangulation dropped: F4.AC5 now lists ten sources, F4.AC8 is withdrawn, RW-6 amended.** Triangulating needs the visitor's browser to time requests to endpoints in several places; this server is in one, so the endpoints would be third parties — forbidden by F2.AC12, and the content-blocker pattern B6 records. S8 already carries most of S10's value (RW-6's own reasoning). Spike D is retired. The brief listed latency triangulation as *optional*; the traceability table records that it was dropped, not delivered | Repository owner |
| 13 | 2026-10-02 | **F9.AC19: rollup-first, today live, raw fallback stated (ADR-0016).** Two problems with "nightly rollups" surfaced when M5 started. The documented rollup's seven dimensions cannot answer F9.AC4's city, ASN, ISP, browser, OS and screen breakdowns, nor most F9.AC13 filters. And a nightly-only refresh leaves today's visits off every chart until the next morning. Now: closed days settle daily; today and yesterday are rebuilt every five minutes; a long-format dimension rollup serves every breakdown; a filter outside the rollup dimensions is answered from raw rows using the same SQL definitions; and every response states `computed_from` beside `stage_mix` (F9.AC20). Two alternatives were declined: strictly nightly with the unservable filters disabled, and raw rows only, which would lose history past retention (NFR5.AC4) | Repository owner |
| 14 | 2026-10-02 | **Best-guess location is what the dashboard shows (ADR-0018); F4.AC10, F4.AC12(b), F9.AC4, F9.AC5 and F9.AC13 amended, CLAUDE.md invariant 5 reworded.** Measured on 160 real Indian ISP visits: strict city 0, advisory city 66 -- every broadband visit and no mobile one. Two causes. (1) A bug (ERRORS E38): rule (b) removed mobile visitors' city candidates from advisory as well as strict, so "advisory is always the argmax" was false for most of India's traffic. (2) A design choice: the dashboard counted, plotted, filtered and labelled by strict only, so with Spike A's coverage (row 9) every city read "Abstained". The owner judged that unusable and directed that the highest-confidence city be shown. Now: advisory is the argmax over every candidate and always equals strict where strict emitted; the dashboard shows it everywhere with its confidence, marked confirmed where strict agrees; strict alone still drives geofencing, alert wording and accuracy, and is unchanged. Declined: lowering the strict threshold (breaks F4.AC13 precision and geofencing), a second "nearly strict" threshold (no ground truth to set it until M8) | Repository owner |
| 15 | 2026-10-03 | **F9.AC17 extended to phones (390 px), with the M5.5 redesign (docs/DESIGN.md, ADR-0019).** The owner directed a full UI redesign before M6 with no change to functionality; DESIGN §9.4 recomposes the dashboard for phones (drawer navigation, single column, row-card tables) rather than shrinking the desktop layout. Tablet-only was the original bar; a phone is where an alert from M6 will usually be opened | Repository owner |
| 16 | 2026-10-03 | **Geofences without a basemap; region geofences on strict codes (ADR-0020); F6.AC1, F6.AC2, F6.AC5 and F6.AC6 amended, DATA_MODEL §5.3 invariant 5 restated.** F6 assumed polygons drawn over a street map and matched against `geopoint`. There is no street map (ADR-0017: CARTO's keyless tiles ended; RISKS R26), and `geopoint` exists only with consented GPS or a strict city, which is 0 of 160 on the dev set of real Indian ISP visits (R3: rDNS city coverage 2 %). A polygon geofence would be `undetermined` for nearly every visitor. The owner decided: no basemap; a region geofence (countries and states) matched only on the strict country and state, which the engine does state (39 of 160 strict states); polygons and circles stay, on the strict point. Following from that, and approved with the ADR on 2026-10-06: each geofence returns inside, outside or undetermined; the visit is inside if any geofence is, undetermined if any is undecided, otherwise outside, and NULL with no applicable geofence; an `undetermined` visit still sends a normal alert, worded as unconfirmed (F7.AC3 names only inside and outside). Invariant 5 ("undetermined whenever `geopoint` is NULL") became false, because a strict state can now place a visit inside a region geofence. It is replaced by two `CHECK` constraints: outside needs some strict location, and inside needs a match. Declined: a tile basemap (key, Referer leak or gigabytes), advisory matching (acts on a guess; B1), regions as outline polygons (still needs `geopoint`; 13 % of divisions have no outline) | Repository owner |
| 17 | 2026-10-06 | **F7.AC2: alert deduplication is per local calendar day, not a rolling 24 hours.** F7.AC2 said "not seen in the preceding 24 hours"; DATA_MODEL §7.1 and ADR-0009 keyed `dedup_key` by UTC date. They disagreed, and a UTC day would end at 05:30 IST, in the early morning. The owner chose one alert per link and visitor per local day in the reporting timezone, the same day the analytics charts cut (`TRACELET_REPORTING_TZ`, default `Asia/Kolkata`), taken from the visit's arrival time so a retry or re-inference produces the same key. A plain `UNIQUE(dedup_key)` then enforces it with no race. Accepted: two visits at 23:50 and 00:10 alert twice. Declined: rolling 24 hours from the last alert (an exclusion constraint over a time range, needing `btree_gist`), rolling from the last visit (a visitor coming daily would never alert again, and it needs a per-visitor lock), and the UTC day | Repository owner |
| 18 | 2026-10-06 | **F1.AC5 and F7.AC3: how a link's `notify_policy` and a geofence's `notify_priority` combine.** F1.AC5 gave every link a priority for inside, outside and automated traffic; F6.AC3 gave every geofence its own; ADR-0020 added `undetermined`, which neither covered, and said only that it sends a normal alert. Nothing said which wins. The owner chose "either can mute": an inside visit takes the less urgent of the link's `inside` and the top matching geofence's priority; outside, undetermined and "no applicable geofence" take the link's `outside`, a new `undetermined` key (default `normal`) and `outside` respectively. The defaults reproduce ADR-0020 decision 7 exactly. Separately, the API accepted `automated: high`, which CLAUDE.md invariant 6 forbids ever acting on; it is now fixed at `silent` and anything else is rejected, rather than offering a setting that does nothing. Declined: the geofence alone deciding inside (a link could not mute it), the link alone deciding (a geofence's `silent` would do nothing), and removing `automated` (a breaking change for no behaviour) | Repository owner |

Any future change to a requirement follows CLAUDE.md section 2: propose, wait, then
amend here with a new row.

---

## 12. Traceability — every line of the brief to an ID

| Brief text | ID(s) |
|---|---|
| Visitor intelligence platform, admin dashboard, public tracking endpoint capturing telemetry before redirecting | F1, F2, F9 |
| Coarse geolocation, technical metadata, anti-spoofing, bot/VPN signals, fingerprint-derived signals, differentiating metadata, no personal content | F3, F4, F5, F3.AC7, OOS6 |
| Admins to protected dashboard; public visitors to capture link to redirect | F2, F8, section 1.1 |
| Standalone site, page or link; link embedded in a social bio (Instagram, LinkedIn, Reddit); hidden layers while preserving privacy | F1, F2.AC9, **RW-4** |
| Dashboard and capture site; fail-proof and protected | F8, F9, F10, NFR3 |
| Production-grade security, auth, secure cookies, input validation, password recovery, rate limiting, plus recommendations | F8, F11, F13, F13.AC7 |
| Secure login, auth, professional profiling systems | F8.AC2, F8.AC14, F8.AC15 |
| Advanced analytics, data viz, visit timeline | F9.AC1–F9.AC12 |
| Advanced geofencing with a UI to draw and set boundaries | F6 |
| Telegram notification, special case geofenced, normal case outside, only for a new real visitor | F7.AC1, F7.AC2, F7.AC3 |
| Comprehensive visualisations plus your own additions | F9.AC6–F9.AC12 |
| Exact time and date | F3.AC1 |
| Visitor (fingerprinted) | F3.AC2, F5.AC7, ADR-0006 |
| Location: exact area or address only if allowed; city, state, country with confidence | F4.AC1, F4.AC3, F4.AC10 |
| Network, ASN, type of network, ISP | F3.AC1 |
| Device, browser or app medium | F3.AC3 |
| CPU, GPU, RAM, screen resolution | F3.AC2 |
| Type of device: mobile, desktop, server, datacentre | F3.AC3, F5.AC9 |
| Classification: human, bot, crawler, malware, spam | F5.AC1, **F5.AC13** |
| Source — how data was inferenced or derived | F3.AC4, F4.AC11, F5.AC2 |
| UI for filtering by data and time period, plus your own additions | F9.AC13, F9.AC6–F9.AC12 |
| Ask location permission first; if denied use other production-grade methods; no hacking; preserve privacy; denied means city-level and no area or IP; if granted use the grant | F4.AC1 (**amended, section 11 row 8 — reads an existing grant, does not ask**), F4.AC2, F4.AC3, OOS1, **RW-3** |
| Advanced dashboard UI/UX, semi-dark default with light and dark, polished and clean | F9.AC16, F9.AC17, NFR7 |
| System health: CPU, RAM, disk, temperature, DB size, uptime | F10.AC1, **F10.AC2 / RW-5** |
| Interface to update location databases with indicators and an up-to-date check | F10.AC3, F10.AC4, F10.AC5 |
| Redirect URL updation and editing | F10.AC6, F1, **RW-2** |
| Toggle location inference systems with a diagram flow model of levels | F10.AC7, F10.AC8 |
| Logout and login, change passwords, manage admin profiles | F10.AC9, F10.AC10, F8.AC14 |
| Manage backup and retention by time period, data lifecycle | F10.AC11, F10.AC12, F12 |
| Bot, crawler, spam, malware, spoofing detection | F5.AC1, F5.AC13 |
| Headless browser detection | F5.AC3 |
| GPU and OS cross-checking | F5.AC4 |
| iOS memory protection | F5.AC5 |
| Stealth honeypot trap | F5.AC6 |
| Device fingerprint collision for proxy detection | F5.AC7 |
| Anything else advanced used in real production systems | F5.AC8, F5.AC11 |
| Mobile carrier gateway adjustments | **F4.AC12(b)** |
| HMAC-based visitor hashing | F12.AC6, ADR-0006 |
| Scraper and bot classification | F5.AC1, F2.AC8 |
| Agreement and conflict scoring | F5.AC10 |
| Multi-candidate location inference: passive GeoIP, ISP name parsing, rDNS parsing, latency triangulation (optional), consented browser geolocation, plus more | F4.AC5 (S1–S9, S11), **RW-6** — latency triangulation **dropped**, section 11 row 12 |
| Rate limiting for visits and admins, upstream and downstream; control visitor hits; prevent DDoS and spam; control admin login and dashboard use | F11.AC1–F11.AC10 |
| Discuss architecture and choose the best fit | Gate 2; ADR-0001 to ADR-0014 |
| Out of scope: hacking, illegal access, privacy violation, extracting personal data | OOS1–OOS6 |
| Perf: 10 concurrent visits, 500 per day, 2 concurrent admins | NFR1 |
| p95 latency target | **NFR2 / RW-7** |
| Graceful degradation | NFR3, F15.AC6 |
| Handling exceptions and errors efficiently | NFR4, F15 |
| Data: retention, consistency, what must never be lost | NFR5, F12 |
| Stack: Python back end; front end and DB your choice | C1, C2, C3 |
| Deploy: Docker + GCP e2-micro | C4, F14.AC2 |
| Dev: Windows 11 + Docker WSL | C5 |
| Budget: free tier only | C6 |
| External services fully free; ask whether email or key is required | C6, KICKOFF section 3 |
| Typed everywhere, strict; linter and formatter in CI | ES1, ES2 |
| Unit for domain, integration for API and DB, no DB mocking | ES3 |
| Error handling pattern: typed errors to consistent JSON | ES4, F15.AC1 |
| No new dependency without justification | ES5 |
| Conventional commits, one PR per milestone | ES6 |
| Six known bugs | B1–B6, section 9, `docs/ERRORS.md` |
| Documentation deliverables | This document and its siblings |
| Doc maintenance rules copied into CLAUDE.md | `CLAUDE.md` section 2 |
| Success criteria for v1 | SC1–SC4 |
