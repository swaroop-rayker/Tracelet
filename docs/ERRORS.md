# ERRORS — Tracelet

**Status:** live document. Every bug encountered and solved gets an entry (F15.AC8).

**Entry format:** ID, symptom as observed, root cause, fix, **prevention** — the test,
constraint, or CI job that stops it recurring. An entry without a prevention line is
incomplete: a fix without a guard is a bug waiting for a second visit.

**B1–B6 are the bugs reported in the original brief.** They are diagnosed here and
addressed by design before the code exists, so they are recorded as **Diagnosed, fix
designed** rather than Fixed — that status changes when the addressing milestone verifies
them.

---

## B1 — Wi-Fi visitor resolves to the ISP head office

**Status:** Diagnosed, fix designed. Verified in **M3**.
**Reported:** an actual visitor in Bangalore, visiting from Bangalore, recorded as Faridabad
or Noida — a tier-1/2 city far away but within the correct country.

**Root cause — this is not a coding defect.**

Free GeoIP databases map an entire ISP netblock to the address in the Regional Internet
Registry allocation record, or to the ISP peering city. Indian broadband netblocks — Airtel,
Jio Fiber, ACT, BSNL — are largely registered centrally in the Delhi NCR region, so a
Bangalore subscriber inherits an NCR coordinate. MaxMind publishes India city-level accuracy
within 50 km at roughly 50–60 percent.

The database entry is wrong. **No amount of application code makes a wrong lookup right** —
which is why the fix is not "use a better lookup" but "detect that the lookup is unreliable
and refuse to emit a city".

**The detection signature.** An ASN whose database entries overwhelmingly collapse onto a
single coordinate is not describing subscriber geography — it is describing a registration
address. That is measurable as `asn_profiles.modal_share`.

**Fix (F4.AC12, ADR-0005)**

1. **Registry-artifact suppression.** Precompute each ASN modal centroid and `modal_share`.
   If the winning city equals that centroid **and** no non-database source (consented GPS,
   rDNS, Cloudflare colo) corroborates it, **collapse city to admin1** and record
   `suppressed_reason='registry_artifact'`.
2. **rDNS PTR city-code lexicon (S6).** Indian ISPs commonly embed metro codes in PTR
   records — `blr`, `mum`, `del`, `hyd`, `maa`, `pnq`, `abts-kk-static-*`. This is the
   highest-value free corroborating signal available, and RISKS R3 measures whether it
   carries the weight placed on it.
3. **Cloudflare `CF-Ray` colo (S8).** A metro-level hint, server-side, free, and it works
   even inside the Instagram webview.
4. **Multi-source consensus** across four independent databases, so agreement raises
   confidence and disagreement triggers abstention rather than a coin flip.
5. **Mobile and CGNAT ASNs never emit a city at all** — F4.AC12(b).

**Prevention**
- The registry-artifact rule is a unit-tested function over `asn_profiles`, not a heuristic
  buried in a query.
- M3 done-checklist requires demonstrating suppression on a **real** Indian broadband IP
  that the databases place in the NCR.
- The CI accuracy job (F14.AC12) fails if admin1 precision regresses.
- `visit_candidates` retains suppressed candidates, so a regression is visible as data
  rather than inferred from a missing value.

---

## B2 — Wrong city tolerated inside the right state, but accuracy should improve

**Status:** Diagnosed, fix designed. Verified in **M3** and **M8**.
**Reported:** a visitor from Mangalore or Belagavi recorded as Bengaluru is not a total
error, because the state is correct. A wrong state would be.

**Root cause.** Not a bug — a statement about **which errors matter**. It defines an error
hierarchy: country errors are unacceptable, state errors are serious, city errors are
tolerable. Most geolocation implementations treat all three identically, which is why they
report a confident city that happens to be wrong.

**Fix (F4.AC10, F4.AC13, ADR-0005)**

Encode the hierarchy directly. Each level — country, admin1, admin2, city — gets its own
confidence and its own threshold. When a level falls below threshold, **collapse upward**
rather than guessing: report `Karnataka, city undetermined` instead of `Bengaluru`.

Two field sets per level:

| | Behaviour | Used for |
|---|---|---|
| **Strict** | Abstains with a recorded reason | Geofencing, alerts, exports — anything acted on |
| **Advisory** | Always the best guess, with confidence | Displayed greyed-out — what you learn from |

Measurable targets replacing the unachievable "100 percent": country ≥99.5 % accuracy;
admin1 ≥99 % **precision** at ≥85 % coverage; city ≥95 % precision at ≥50 % coverage.

**Prevention**
- `strict_*` being `NULL` requires a matching `abstain_reason` key — a schema-level
  invariant (DATA_MODEL section 5.3, invariant 4), so an abstention can never be silent.
- CI accuracy job enforces precision per level separately.
- Sample size is displayed beside every accuracy figure (RISKS R9).

---

## B3 — Instagram in-app browser captures nothing and just redirects

**Status:** Fixed in **M2**. Confirmed on Android by **Spike B** (2026-09-28): a real
Instagram bio-link tap was recorded and enriched, and the preview fetches were recorded
as `crawler`. iOS not yet confirmed (RISKS R5). A JavaScript-running Meta scanner is a
fifth cause of false visits, found by the same spike (RISKS R21).
**Reported:** when the capture link is opened from an Instagram bio, the visitor is simply
redirected and no data is captured.

**Root cause — four distinct causes stacked, which is why partial fixes failed.**

1. Instagram in-app WebView **suppresses or ignores the Geolocation and Permissions APIs**,
   so any consent-dependent path silently yields nothing.
2. Instagram **prefetches shared links with `facebookexternalhit`** before a human taps
   them. A naive implementation records the crawler and may then treat the real human as a
   repeat visitor, suppressing their alert.
3. **`navigator.sendBeacon` is frequently dropped on unload inside WebViews** — the standard
   choice for exactly this job is the unreliable one here.
4. **If the redirect is not gated on the client round-trip, the redirect wins the race.**
   This is the dominant cause: the page navigates away before collection completes.

**Fix (ADR-0004) — architectural, not a patch**

> **The HTTP request is the authoritative capture. The client is additive enrichment that is
> always allowed to fail.**

- The `visits` row is **committed before the response is generated**, carrying IP-derived
  values, ASN, ISP, rDNS, header order, UA, UA-CH, `Accept-Language`, HTTP and TLS version,
  and `CF-Ray` colo. **None of that requires the client to cooperate.**
- The page requires **no JavaScript**: server-rendered, `<noscript>` meta-refresh fallback.
- Enrichment is a separate POST using **`fetch(..., {keepalive: true})`**, not `sendBeacon`.
- A **90-second sweeper** finalises unenriched visits as `stage='server_only'`, runs
  inference on server signals, and enqueues notification. **No visit is lost.**
- Webviews are detected and recorded, with an open-in-browser affordance.
- **Link-preview fetchers are classified `crawler` and never notified on**, so a prefetch
  cannot consume the human first-visit alert.

**Prevention**
- M2 done-checklist requires a visit to be recorded **with JavaScript fully disabled** and a
  visit from a **real Instagram bio link on a real phone**.
- The sweeper has its own health check, and a partial index on `finalized_at IS NULL`
  surfaces stuck visits.
- F9.AC20 requires every analytics response to state its stage mix, so a webview-heavy
  population cannot silently skew a chart.
- **RISKS R5** tracks the open question of whether `keepalive` actually survives; if not, the
  design already degrades correctly rather than failing.

---

## B4 — Browser characterises the site as unsafe

**Status:** Diagnosed, fix designed — **partially outside our control.** Verified in **M9**.
**Reported:** the browser marks the site unsafe, for the admin and probably for visitors.

**Root cause — three contributors, one of which we cannot fix.**

1. **The open-redirector pattern.** Google Safe Browsing classifies endpoints whose function
   is "accept a request, redirect elsewhere" as social-engineering infrastructure. An
   endpoint taking a destination from a parameter is the textbook signature. **This is the
   one we can eliminate structurally.**
2. **Transport and header weaknesses** — invalid or missing certificate, no HSTS, missing
   security headers, deceptive page patterns.
3. **Domain reputation.** A newly-registered domain has no history, and a free subdomain
   inherits *other people* reputation damage. **We cannot fix this with code.**

**Fix**

| Contributor | Action |
|---|---|
| Open redirector | **Destinations come only from an admin-configured `links` row — never a query parameter, header, or path** (F1.AC7, F13.AC3). `/r/{slug}` returns **200 HTML, never a 302** (F2.AC1) |
| Transport | Automatic Let's Encrypt, HTTP/2, HSTS, strict nonce CSP, full security headers, all **verified by CI test rather than inspection** (F13.AC1, F13.AC2) |
| Deceptive patterns | The capture page imitates no login form, no payment form, no third-party brand (F13.AC4) |
| Reputation | Browser-trust checklist (F13.AC8): real domain, Search Console registration, Safe Browsing review request, **no URL shortener anywhere in the chain**, reachable privacy page |

**Honest limit.** **There is no guarantee.** A review request is the only lever and reviews
can be declined. The free-subdomain path is materially worse because the reputation damage
is inherited before the first request. Tracked as **RISKS R8** and **R10**.

**Prevention**
- `CLAUDE.md` invariant 3 states the no-open-redirect rule as non-negotiable, because
  re-introducing a `?to=` parameter would silently re-trigger this bug.
- A CI test asserts the destination cannot be influenced by request input.
- M9 records the Safe Browsing outcome **whatever it is**, including a negative result.

---

## B5 — Black or blank screen where data does not load

**Status:** Diagnosed, fix designed. Verified in **M2** and **M5**.
**Reported:** a black or blank screen where data fails to load.

**Root cause — two different failures with the same symptom, which is why it seemed
intermittent.**

*On the capture page:* a JavaScript bundle that fails to load or a CSP violation leaves the
page with nothing rendered **and** no redirect, because both depended on the same script.

*On the dashboard:* an unhandled fetch rejection or a render error with no error boundary
unmounts a subtree and leaves empty space. A 1 GB box under swap pressure makes slow
responses more likely, widening the window.

**Fix**

*Capture page (F2.AC3):* **zero JavaScript required.** Server-rendered Jinja2, inline
critical CSS, no external assets, a real `Continue now` href, and a `<noscript>` meta-refresh
fallback. JavaScript is strictly additive. **There is no state in which the visitor sees
nothing.** The capture page shares no bundle with the SPA, deliberately.

*Dashboard (F9.AC18):* **explicit empty, loading and error states on every chart and table
are a specification requirement, not a nicety.** Route-level error boundaries, a generic
renderable error shape from the RFC 9457 contract (ADR-0013), and zod validation at the
boundary so a schema mismatch surfaces as a caught error instead of a `TypeError` deep in a
chart component.

*Platform (F14.AC1):* hard per-container `mem_limit`s, so one process cannot starve the
others into timeouts.

**Prevention**
- M5 done-checklist: **"no panel can render blank"** — empty, loading and error states all
  exercised by test.
- The RFC 9457 contract means any backend failure has a shape the frontend can render, so an
  unanticipated error cannot produce an unrendered state.
- CSP is verified by CI test, since a CSP change is a classic cause of this symptom.

---

## B6 — Visitor browser refuses to provide data; endpoints mistaken for bot endpoints

**Status:** Fixed in M2, every fix point under test (2026-09-29). **Not yet observed in a
browser running a content blocker** — see *Verification* below.
**Reported:** the visitor browser sometimes refuses to provide data because the capture and
collection endpoints get mistaken for bot, scraper or crawler endpoints.

**Root cause — the diagnosis in the report is wrong, in a useful way.**

The requests are not being refused by bot detection. **They are being blocked by content
blockers.** EasyPrivacy, uBlock Origin and similar filter lists match **URL substrings**:
`track`, `collect`, `analytics`, `pixel`, `beacon`, `telemetry`, and query keys like `utm_`.
A request to `/api/v1/track` or `/collect` is aborted by the extension before it ever leaves
the browser. Nothing on the server sees it, which is exactly why it looks like the browser
"refusing".

Secondary contributors: third-party requests from the capture page, cross-site cookies, and
pixel-shaped GETs — all patterns filter lists target.

**Fix (F2.AC11, F2.AC12)**

1. **Neutral first-party route names.** `/r/{slug}`, `/api/v1/s/{nonce}`, `/api/v1/hp/{token}`.
   No blocked substring in any public path or query key.
2. **Same-origin only.** The capture page issues **no third-party requests** and loads no
   third-party script.
3. **No cross-site cookies.**
4. **JSON POST, not a tracking pixel.**
5. Genuine bot-detection concerns are handled separately by classification (ADR-0011) — the
   two problems were conflated and needed separating.

**Prevention**
- **A CI job asserts no public route or query key contains a blocked substring**
  (F14.AC11), and the M2 checklist requires proving that job **fails** on a deliberately-bad
  route name.
- `CLAUDE.md` invariant 7 states the rule, because the natural instinct when adding an
  endpoint is to name it descriptively — `/api/v1/telemetry` — which would silently
  reintroduce this.

**Verification — one test per fix point**

| Fix | Test |
|---|---|
| 1. Neutral route names | `tests/unit/test_route_names.py` — the CI route-name job, proven in M2 to **fail** on a deliberately-bad route mounted on a real application |
| 2. Same-origin only | `test_the_csp_permits_nothing_from_another_origin`, `test_the_page_references_no_external_resource` (`tests/unit/test_capture_pages.py`) |
| 3. No cookies | `test_the_public_surface_sets_no_cookie` (`tests/integration/test_capture_path.py`) — neither the capture page nor the enrichment response sets one. Added 2026-09-29; until then this point was designed but unasserted |
| 4. JSON POST, not a pixel | `test_enrichment_is_a_json_post_not_a_pixel` (`tests/unit/test_capture_pages.py`). Added 2026-09-29 |

**Still unverified:** no request has been observed passing a real browser with uBlock
Origin and EasyPrivacy enabled. The tests prove the page avoids what those lists match;
they do not run the lists. Spike B's browsers delivered enrichment, but none was recorded
as running a blocker.

---

## Implementation-phase entries

### E1 — `pip install -e ./api` fails on the Windows host

**Status:** Fixed (by not doing it). **Milestone:** M0. **Date:** 2026-09-26.

**Symptom.** Installing the API package into a host virtualenv fails while building
`asyncpg`, with MSVC emitting `error C5299: a label appearing at the end of a compound
statement requires at least '/std:clatest'`.

**Root cause.** The host has Python 3.14. `asyncpg` 0.30 ships no cp314 wheel, so pip falls
back to compiling from source, and its C extension does not build against the 3.14 headers
with the default MSVC standard flags. Nothing to do with Tracelet's code.

**Fix.** None needed in the project — **the container is the supported environment**, and it
pins `python:3.13-slim` where wheels exist. This is precisely why `scripts/tl` runs every
task (`ruff`, `mypy`, `pytest`, `alembic`) inside `api-tools` rather than on the host.

**Prevention.**
- `pyproject.toml` declares `requires-python = ">=3.13"`, and the image pins 3.13.
- `scripts/tl` never invokes host Python, so the ordinary workflow cannot hit this.
- If you do want a host venv for editor integration, install Python 3.13 alongside 3.14.

**Related:** SPEC C5, ADR-0001.

---

### E2 — Default connection pool consumed the entire `max_connections`

**Status:** Fixed. **Milestone:** M0. **Date:** 2026-09-26.

**Symptom.** The application refused to start: `TRACELET_DB_POOL_MAX (10) x
TRACELET_WEB_CONCURRENCY (2) exceeds the 18 connections available`. Raised by Tracelet's own
configuration validator on the **documented default values**.

**Root cause.** A genuine design arithmetic error, not a validator bug. The pool is **per
process**. Two Uvicorn workers with `pool_max = 10` can hold **20** connections between them,
and the server was configured with `max_connections = 20` — so at peak the application could
consume every slot, leaving **none** for `pg_dump`, the monthly restore-verify job, or an
interactive `psql`. A backup would fail exactly when traffic was highest.

The original docs said "application pool 5–10 connections" and `max_connections=20` without
ever multiplying the first by the worker count.

**Fix.** Made the budget explicit and enforced:
- `max_connections` raised 20 → **24** in `docker-compose.yml`.
- New settings `TRACELET_DB_MAX_CONNECTIONS` (mirrors the server value) and
  `TRACELET_DB_RESERVED_CONNECTIONS` (default 4), so the check runs against reality rather
  than a magic number.
- Validator now asserts `pool_max x workers <= max_connections - reserved`, i.e.
  `10 x 2 = 20 <= 24 - 4`.
- `docs/ARCHITECTURE.md` §6.2 and `ADR-0002` updated with the arithmetic in the same change.

**Prevention.** The invariant is a boot-time `model_validator`, so a bad combination
**refuses to start** instead of failing later under load. Raising the pool or the worker
count without raising `max_connections` is now impossible to do silently.

**Related:** ADR-0002, ADR-0010 (the pool is rate-limit layer 3), NFR6.

**Worth noting:** this was caught by a validator written in the same session, on default
values, before any load. The cheap check found a real operational bug for free.

---

### E3 — `AttributeError: 'PrintLogger' object has no attribute 'name'` on every log call

**Status:** Fixed. **Milestone:** M0. **Date:** 2026-09-26.

**Symptom.** Nine unit tests failed. Any request that logged raised
`AttributeError: 'PrintLogger' object has no attribute 'name'` from deep inside structlog.

**Root cause.** Incompatible structlog configuration: `structlog.stdlib.add_logger_name`
reads `logger.name` and therefore requires a **standard-library** logger, but the factory was
`structlog.PrintLoggerFactory`, whose logger has no `name`. Mixing `structlog.stdlib.*`
processors with a non-stdlib factory is the mistake.

**Fix.** Moved to the canonical stdlib integration: `structlog.stdlib.LoggerFactory` with
`ProcessorFormatter.wrap_for_formatter` as the final processor, and one
`ProcessorFormatter` on a single stdlib handler. This is better than the minimum fix, because
it gives **one output path** — Uvicorn, Gunicorn, SQLAlchemy and Alembic records now render in
the same format and carry the same `trace_id` as Tracelet's own lines, which is what F15.AC2
actually asks for.

**Prevention.** `tests/unit/test_log_redaction.py::test_end_to_end_through_the_configured_logger`
exercises a real log call through the configured pipeline, so a processor/factory mismatch
fails a test rather than waiting for the first production error.

**Related:** ADR-0013, F15.AC2.

---

### E4 — Redaction could not reach an IP address inside a traceback

**Status:** Fixed. **Milestone:** M0. **Date:** 2026-09-26.

**Symptom.** Found while writing the redaction test, not by a failure. Key-based redaction
scrubs `{"ip": "..."}`, but a database connection error renders as free text —
`could not connect to 49.37.128.5:5432` — under the key `exception`. No sensitive *key* is
involved, so nothing masked it.

**Root cause.** The control was designed around structured context only. A traceback or error
message is free text, and F12.AC13 ("a log line must never contain what the database refuses
to store in plaintext") applies to it just the same. Left alone, this would quietly undo part
of ADR-0007 the first time PostgreSQL rejected a connection.

**Fix.** A second, value-level pass over free-text fields (`event`, `exception`, `message`,
`detail`, `error`, `last_error`, `stack`) masking IP literals. The predicate is the stdlib's
`ipaddress.is_global`, which is false for loopback, private, link-local and the documentation
ranges — so container plumbing such as `127.0.0.1` and `172.18.0.3` is **kept**, because
masking it would cost real diagnostic value for no privacy gain.

**Prevention.** Four tests in `test_log_redaction.py` cover it: global addresses masked,
private and loopback kept, non-addresses unmangled, and the whole thing exercised end to end
through the configured logger.

**Honest limitation, recorded rather than hidden:** this is best-effort. It masks IP
*literals*. An address that reaches a log encoded some other way — reversed, hex, inside a
URL-escaped blob — would still pass. The structural control is not logging the value in the
first place; this is the safety net.

**A test-data note worth keeping.** The first version of the test used `203.0.113.47`, and it
failed: Python classifies the TEST-NET documentation ranges as **non-global**, so they are
deliberately not masked. Documentation addresses are the wrong fixture for testing address
redaction.

**Related:** ADR-0007, ADR-0013, F12.AC13.

---

### E5 — An empty environment variable is not an absent one

**Status:** Fixed. **Milestone:** M0. **Date:** 2026-09-26.

**Symptom.** Two unrelated-looking failures on the first `docker compose up`:

1. `api` in a restart loop — `ValidationError: telegram_owner_chat_id — Input should be a
   valid integer, unable to parse string as an integer [input_value='']`.
2. `caddy` in a restart loop — `parsing caddyfile tokens for 'email': wrong argument count
   or unexpected line ending`.

**Root cause — one cause, two symptoms.** `.env.example` deliberately ships every
milestone-gated key **present but blank**, and says leaving them blank is correct until the
milestone arrives. But a blank variable is *set to the empty string*, not absent:

- Pydantic saw `""` for `int | None` and refused to parse it. The process would not boot over
  a setting not needed until M6.
- Caddy's `{$VAR:default}` substitutes only when the variable is **unset**. Compose always
  set it, so `email` received no argument and Caddy rejected the entire config.

Notably `caddy validate` **passed during the image build**, because the variable was genuinely
unset there. The failure only appeared at runtime — a good reminder that validating a
templated config proves less than it appears to.

**Fix.**

- *Application:* one `model_validator(mode="before")` on `Settings` drops blank strings so
  field defaults apply. Fixed once, generally — the next optional `int` or `Path` setting
  would otherwise reintroduce it one milestone at a time.
- *Caddy:* **removed the `email` directive entirely.** A placeholder default would be worse
  than none, because Let's Encrypt would reject `admin@localhost` on a real domain and break
  certificate issuance rather than the config. The ACME contact address is optional, and
  Let's Encrypt discontinued expiry notification emails in 2025, so the knob bought nothing
  and could only break TLS. `TRACELET_ACME_EMAIL` was removed from `.env.example` with the
  reasoning recorded there.

**Prevention.** `tests/unit/test_config.py` asserts that blank and whitespace-only values for
every key `.env.example` ships empty behave as unset, that real values still parse, and that
the documented defaults are valid.

**Related:** F14.AC5, ADR-0012.

---

### E6 — `tl openapi` broken by Git Bash path rewriting and docker flag order

**Status:** Fixed. **Milestone:** M0. **Date:** 2026-09-26.

**Symptom.** Check 9 of `tl verify` failed with two errors at once:

```
exec: "-T": executable file not found in $PATH
ResolveError: ENOENT: no such file or directory '/work/web/d:/Software/Git/work/api/openapi.json'
```

**Root cause.** Two Windows-specific problems, plus one of my own making:

1. `-T` was appended **after** the service name, so docker treated it as the command to run.
   Docker flags must precede the service.
2. **Git Bash rewrote `/work/api/openapi.json` into a Windows path** before the argument
   reached docker. MSYS path conversion mangles anything shaped like an absolute Unix path,
   and the container never saw the path that was written.
3. The task redirected stdout straight into the committed `api/openapi.json`. The shell
   **truncates the target the instant the command starts**, so the failure left an empty file
   — which the drift check then correctly reported as 127 deleted lines. A confusing
   second-order symptom caused entirely by the redirect.

**Fix.**
- `API_RUN_T` places `-T` before the service name.
- **Relative paths only** inside container commands, resolved against the container working
  directory. MSYS leaves `../api/openapi.json` alone.
- Export to a temp file and `mv` into place only on success, so a failure can never truncate
  a committed artefact.

**Prevention.** The reasoning is commented at both call sites in `scripts/tl`, since "use a
relative path here" looks arbitrary without it. Check 9 of `tl verify` exercises the whole
path on every run.

---

### E7 — Generated artefacts differed by line ending, faking permanent contract drift

**Status:** Fixed. **Milestone:** M0. **Date:** 2026-09-26.

**Symptom.** After fixing E6, the drift check still failed — reporting **127 insertions and
127 deletions** on a 127-line file. Every line changed, with no content change.

**Root cause.** `api/openapi.json` had first been generated by Python on the Windows host,
where text-mode stdout translates `\n` to `\r\n`. It was then regenerated inside the
container with LF. Byte-for-byte the files differ on every line.

This would have been **permanent and one-sided**: the drift check would pass in CI (Linux)
and fail forever for a Windows developer, reporting an API contract mismatch that does not
exist. Precisely the kind of check people learn to ignore, which then stops catching the real
drift it exists for.

**Fix.** Both layers, because they protect different things:
- **`.gitattributes`** with `* text=auto eol=lf`, and the three generated artefacts pinned
  `text eol=lf linguist-generated=true`. Protects the repository. Applied to existing files
  with `git add --renormalize .`.
- **`sys.stdout.reconfigure(newline="\n")`** in the exporter. Protects anyone redirecting it
  to a file directly, outside git's control.

`sort_keys=True` was already in place and matters for the same reason: an unstable key order
would make the drift check fire at random.

**Prevention.** `.gitattributes` makes it structural rather than a habit, and check 9 now
passes from both a container and the host.

**Related:** F14.AC9.

---

### E8 — Enrollment links used `http://`, so the session cookie could never be stored

**Status:** Fixed. **Milestone:** M1. **Date:** 2026-09-27.

**Symptom.** Enrolment completed, the account activated, and the admin was not signed in. No
error anywhere: the API answered `204`, the browser accepted the response, and the next
request was anonymous.

**Root cause.** `Settings.public_base_url` chose `http` when `site_address` started with
`localhost`, on the reasonable-sounding theory that local development is plain HTTP. It is
not: Caddy terminates TLS on **every** path including localhost, through its internal CA. The
session cookie carries `Secure`, and a browser refuses to store a `Secure` cookie received
over `http` — silently, because refusing a cookie is not an error.

So the two halves were each individually defensible and jointly broken, which is the usual
shape of this class of bug.

**Fix.** `public_base_url` is **always** `https`. There is no deployment where an http link is
correct — the three documented modes (localhost internal CA, purchased domain, free
subdomain) all terminate TLS at Caddy (ADR-0012).

**Prevention.** A parametrised unit test asserts the scheme for five site addresses including
`localhost` and `127.0.0.1`. The test that previously asserted the **opposite** was found
still in the suite and replaced, which is worth recording: a test can encode a bug.

**Related:** F8.AC2, F8.AC15, ADR-0012.

---

### E9 — `EmailStr` pulled in two dependencies and the API crash-looped

**Status:** Fixed. **Milestone:** M1. **Date:** 2026-09-27.

**Symptom.** Every Gunicorn worker failed to boot: `ImportError: email-validator is not
installed, run pip install pydantic[email]`. The container restarted, forever.

**Root cause.** `pydantic.EmailStr` is not a self-contained type — it requires
`email-validator`, which requires `dnspython`. Two packages arrived as a side effect of
writing `EmailStr` in a request model.

**Fix.** Replaced with a constrained string in `auth/types.py`: `strip_whitespace`, `to_lower`
(the column is `citext`, so normalising on the way in keeps stored values and comparisons
consistent), a length bound, and a pattern rejecting what would actually cause a problem —
no `@`, no dot in the domain, whitespace.

Correct here for a reason beyond dependency count: this system sends **no email at all**.
Gate 1 declined an SMTP provider and recovery runs over Telegram (ADR-0008), so an admin
address is purely a **login identifier**. Paying two dependencies for RFC 5322 conformance on
a string nothing is ever delivered to does not pass ES5.

**Prevention.** The rejection is recorded in the dependency ledger, so the next person
reaching for `EmailStr` finds the reason rather than rediscovering it. Check 12 of `tl verify`
builds the production image, so an import error of this kind fails CI rather than a deploy.

**Related:** ES5, ADR-0008, docs/ARCHITECTURE.md §8.

---

### E10 — Multi-step auth state lived in process memory, so login was a coin flip

**Status:** Fixed by migration `0003`. **Milestone:** M1. **Date:** 2026-09-27.

**Symptom.** Sign-in failed roughly half the time, and failed *differently* each way: the
first attempt returned `401` with a valid code, the next returned `404` for a token that had
just been issued. Retrying eventually worked.

**Root cause.** Three short-lived tokens — the MFA challenge between password and code, the
confirm token issued at enrolment, and the Telegram chat-verification code — were held in a
module-level dict. `TRACELET_WEB_CONCURRENCY=2` means **two Gunicorn workers with separate
memory**, so whether the follow-up request found the token depended on which worker received
it. A 401 meant the right worker with a stale code; a 404 meant the wrong worker.

**Fix.** The `auth_challenges` table and `auth/challenges.py` (migration `0003`). One table
with a `kind` discriminator rather than three, because all three have the same shape and
lifecycle: single-use, short TTL, bound to one admin. The enrollment and reset tokens stay in
their own tables, where the lifetimes and delivery paths genuinely differ.

**Worth recording separately.** The original code carried a comment arguing this was
acceptable because "the admin simply retries". That comment is the actual defect: a
rationalised trade-off in a comment is how a bug survives review, because the next reader
sees a decision rather than a mistake. Any state that must survive from one HTTP request to
the next is shared state, and on this deployment shared means PostgreSQL — the same argument
ADR-0010 makes for rate-limit buckets.

**Prevention.** An integration test signs in three times consecutively, each with a fresh
TOTP step. A per-worker store fails it about half the time under the real deployment, and
deterministically under any future worker count above one.

**Related:** F8.AC1, ADR-0010, docs/DATA_MODEL.md §3.7.

---

### E11 — The commit ran after the response, so a failed transaction was reported as 200

**Status:** Fixed. **Milestone:** M1. **Date:** 2026-09-27.

**Symptom.** A request that violated a database constraint returned `200 OK` with a complete
response body, and nothing was written.

**Root cause.** The conventional FastAPI dependency —

```python
async def get_db():
    async with session_scope() as session:
        yield session
```

— commits in the dependency's **teardown**, and FastAPI runs teardown *after* the response has
been generated. A commit that fails there has no way to change the status code. The exception
was logged and swallowed, and the client was told the operation succeeded.

**This is what hid E12** for as long as it did: the enrolment path was failing at COMMIT and
reporting success, so the symptom was "the account did not activate" rather than "the
transaction was rejected".

**Fix.** `db/request_session.py`. Middleware owns the session, because middleware holds the
response object and can therefore replace it: it commits first, and returns a `500` Problem
Details response if the commit fails.

The rollback policy is deliberate and is the second thing this gets right:

* **2xx / 3xx** → commit.
* **4xx** → commit **as well**. The handler chose that outcome, and anything it wrote is part
  of the decision — a failed login must keep its audit row (F8.AC16), and that row is written
  by the same code that then raises `Unauthenticated`.
* **5xx or an unhandled exception** → roll back. Nobody chose that outcome.

**A consequence worth stating,** because it caused E14: with commit-on-4xx, a handler that
mutates and *then* rejects will commit the mutation. Such a handler must validate before
mutating, or roll back explicitly.

**Prevention.** An integration test mounts a route that mutates and returns success while
violating the deferred owner trigger, and asserts the response is a 5xx with nothing written.
It fails with a 200 against the dependency-teardown pattern.

**Related:** ES4, F8.AC16, NFR5.AC5, ADR-0013.

---

### E12 — The owner trigger asserted an invariant that is false during bootstrap

**Status:** Fixed in migration `0002` before merge. **Milestone:** M1. **Date:** 2026-09-27.

**Symptom.** Enrolment of the very first owner failed at COMMIT with `cannot remove the last
active owner` — an error about removing an owner, raised while creating one. Reported to the
client as `200` (E11).

**Root cause.** `assert_owner_exists` fired on **every** `admins` UPDATE and required that an
active owner exist afterwards. But the first owner is created `pending_enrollment`, and every
update on the way to activating them — setting a password, storing a TOTP secret, flipping
status — happens while **no active owner exists**. The global assertion is simply false during
bootstrap.

**Fix.** Reformulated to the narrower property that is actually required: *an operation may
not **remove** the last active owner.* Two constraint triggers with `WHEN` clauses, so the
check runs only when the row **was** an active owner and is about to stop being one:

```sql
WHEN (OLD.role = 'owner' AND OLD.status = 'active'
      AND (NEW.role <> 'owner' OR NEW.status <> 'active'))
```

A side benefit that matters on a 1 GB box: an ordinary update — a failed-login counter bump on
an owner — no longer runs a count query.

**Prevention.** The bootstrap-to-active path is exercised by every enrolment test in the
integration suite, and a test asserts both triggers exist and are `DEFERRABLE INITIALLY
DEFERRED` in the catalogue. Documentation is not enforcement.

**Related:** F8.AC13, F8.AC15, docs/DATA_MODEL.md §3.1.

---

### E13 — `INET` returns an `ipaddress` object, and comparing it to a string rejected every session

**Status:** Fixed. **Milestone:** M1. **Date:** 2026-09-27.

**Symptom.** Every authenticated request returned `401`. Signing in worked, the cookie was
set, and the next request was anonymous. `session_binding_mismatch` was in the log the whole
time, with `kind="ip_prefix"`.

**Root cause.** `sessions.resolve` compared `prefix_of(ip)` — a canonical **string** like
`203.0.113.0/24` — against `row.ip_prefix`, which SQLAlchemy hands back as an
`IPv4Interface` for an `INET` column. The two are never equal, so the binding check failed
for every session including the one just created.

**The interesting part is why `mypy --strict` did not catch it.** The model annotated the
column as `Mapped[str | None]`. The annotation was a lie, and a type checker cannot find a bug
that the types deny the existence of.

**Fix.** Compare via `str(...)`, and annotate the `INET` columns as
`IPv4Interface | IPv6Interface | None` in all three models that have one, so the declared type
matches what the driver actually returns.

**Prevention.** An integration test signs in and then calls an authenticated route — which
sounds too obvious to need writing down, and is exactly the test whose absence let this ship.

**Related:** F8.AC3, ES1.

---

### E14 — A 4xx raised after a mutation surfaced as 500 instead of 409

**Status:** Fixed. **Milestone:** M1. **Date:** 2026-09-27.

**Symptom.** Demoting the last owner returned `500 INTERNAL_ERROR` instead of `409
LAST_OWNER`, and the log showed the failure raised from the commit, outside any handler.

**Root cause.** Two correct decisions interacting. The request session commits on a 4xx (E11),
so a handler that applied the UPDATE and *then* raised `LastOwner` had its mutation committed
anyway — and the deferred constraint trigger fired at COMMIT time, in the middleware, where no
exception handler could map it to a status code.

**Fix.** Two changes, in this order:

1. **Validate before mutating.** `_count_other_active_owners` runs before the UPDATE, so the
   rejection happens with nothing written.
2. **`SET CONSTRAINTS ALL IMMEDIATE` after mutating**, so the deferred trigger evaluates
   inside the handler where an `IntegrityError` becomes a clean 409 rather than a commit-time
   500.

**The general rule, worth writing down once:** with commit-on-4xx, any handler that mutates
and then rejects must either validate first or roll back explicitly. That is the price of
keeping the audit row for a rejected request, and it is the right trade.

**Prevention.** Integration tests assert `409 LAST_OWNER` for both demote and disable. Delete
is a 422 for a reason recorded in `docs/API.md` §5.

**Related:** F8.AC13, E11, ADR-0013.

---

### E15 — Single-use tokens were spent before the password was validated

**Status:** Fixed. **Milestone:** M1. **Date:** 2026-09-27.

**Symptom.** Submitting a password that failed the policy on `/enroll` or `/reset/confirm`
returned the correct `422` — and then the link no longer worked. On the enrolment path the
invitee had to go back to an owner for a fresh invite; on the reset path, a 30-minute token
had to be requested and waited for again.

**Root cause.** Both handlers consumed the token first and validated the password afterwards.
Written in the order the reader thinks about it, rather than the order the failure modes
require.

**Fix.** `_peek_single_use_token` finds a live token **without** spending it; the password is
validated; then `_consume_single_use_token` spends it conditionally. The conditional UPDATE
still decides the concurrent case, so nothing about the race changed — only the ordering.

**Prevention.** Two integration tests submit a policy-violating password and then reuse the
same link successfully, one for enrolment and one for reset.

**Related:** F8.AC7, F8.AC15, F8.AC17.

---

### E16 — Two concurrent demotions could both pass, leaving no active owner

**Status:** Fixed. **Milestone:** M1. **Date:** 2026-09-27.

**Symptom.** None observed — found while writing the concurrency test the E14 fix implied,
which is the only reason it is in this file rather than in production.

**Root cause.** The E14 fix left a hole. Two transactions each demoting a *different* active
owner behave like this:

1. each `_count_other_active_owners` sees the other owner still active, because an uncommitted
   change in one transaction is invisible to the other under READ COMMITTED;
2. each applies its UPDATE;
3. each runs `SET CONSTRAINTS ALL IMMEDIATE`, which fires the deferred trigger **early** — in
   the same blind snapshot — and, per PostgreSQL semantics, *consumes the pending event* so
   nothing is re-checked at COMMIT;
4. both commit. Zero active owners, and nobody can administer the system.

The deferred trigger alone would have caught this: at COMMIT the trigger body runs with a
fresh snapshot, so the second transaction sees the first one's committed demotion and raises.
`SET CONSTRAINTS ALL IMMEDIATE`, added to turn a commit-time 500 into a clean 409, had
inadvertently disabled the very protection the deferral existed for. The documentation claimed
the trigger closed the race; it no longer did.

**Fix.** `_lock_active_owners` takes `SELECT ... FOR UPDATE` on every active owner row,
ordered by id, before either route reads or changes the owner set. The second transaction then
waits for the first to commit, and its next statement gets a snapshot in which the count is
finally truthful. Ordered by id so two transactions cannot take the same rows in opposite
orders and deadlock. At two admins this locks at most two rows, on a route that runs a handful
of times in the system's life.

Both mechanisms are kept: the lock plus the pre-check produce the clean 409, and the deferred
trigger remains the guarantee for anything that bypasses the route.

**Prevention.** A deterministic test holds `FOR UPDATE` on the owner rows from outside the
app, fires the PATCH, and asserts the handler **blocks** rather than deciding — it fails
immediately if the lock is removed. Two further tests race real demotions and deletions and
assert exactly one succeeds and exactly one active owner remains.

**Worth noting.** `SET CONSTRAINTS ALL IMMEDIATE` looked like a pure ergonomics improvement:
same checks, better error. It silently moved a check from a snapshot that could see concurrent
commits to one that could not. Changing *when* a constraint is evaluated changes *what* it can
see.

**Related:** F8.AC13, E11, E14, docs/DATA_MODEL.md §3.1.

---

### E17 — The sole owner could never reset their own authenticator

**Status:** Fixed. **Milestone:** M1. **Date:** 2026-09-27.

**Symptom.** `tracelet admin reset-totp --email <the only owner>` aborted with
`IntegrityError: cannot remove the last active owner / HINT: promote another admin to owner
first`. Found by the repository owner running the command this file had just recommended to
them, on a one-admin deployment.

**Root cause.** Two correct constraints with no legal state between them.

1. Re-enrolling TOTP means clearing `totp_enrolled_at`.
2. The CHECK `ck_admins_active_requires_password_and_totp` forbids an `active` account
   without it, so the status *must* leave `active`.
3. The owner trigger forbids the last active owner leaving `active` (F8.AC13).

So for the only owner, the operation is impossible — and that is the account most likely to
need it, on a path whose entire purpose is to prevent lockout (F8.AC8). Recovery codes and
`reset-password` still worked, but neither helps the specific case of a lost authenticator:
`reset-password` deliberately leaves the second factor alone.

**Fix.** Stop clearing in place when clearing is illegal. For the last active owner the
account stays `active`, the secret is left alone, and the enrollment link does the replacing:
`/enroll` writes a new password, a new secret and a new set of recovery codes, and
`/totp/confirm` re-confirms. The status never changes, so the trigger never fires. Every
other admin keeps the strict behaviour, because it is available and it is better — a
compromised authenticator stops working immediately rather than at re-enrolment.

The trade is stated to the operator rather than hidden: the existing authenticator keeps
working until the link is used, and if the device was *compromised* rather than lost, the
answer is to invite a second owner first. The audit row records which of the two behaviours
ran (`cleared_in_place`).

The same `SELECT ... FOR UPDATE` as the admins router guards the count, for the E16 reason.

**Prevention.** `tests/integration/test_cli_recovery.py` — fifteen tests over the break-glass
paths, three of which fail with this exact `IntegrityError` against the old code. The file
exists because none of the M1 suite covered the CLI: it was verified by hand on accounts that
were never the last owner, which is precisely the case that works.

**Worth noting.** Every individual constraint here was right, each was tested, and the
combination was unreachable. A test per constraint cannot find that; only exercising the
operation on the account that has to survive it can. The suite now uses the `exclusive_owner`
fixture for these, so "the only owner" is the default case rather than an edge case nobody
reaches.

**Related:** F8.AC4, F8.AC8, F8.AC13, E12, E16.

---

### E18 — A 202 with no body was reported to the operator as a malformed response

**Status:** Fixed. **Milestone:** M1. **Date:** 2026-09-27.

**Symptom.** Clicking "Send a verification code" showed *"The server returned something
this page does not understand."* The code arrived in Telegram anyway. The log showed
`telegram_sent` and `status=202`, so the server had done exactly what was asked.

**Root cause.** In the frontend client, endpoints with no response body are parsed by
`noContent()`, which returns `null`. The guard that decides whether a `null` means "the
parser rejected this" read:

```ts
if (parsed === null && response.status !== 204) { /* malformed */ }
```

Two endpoints answer **202**, not 204: `/auth/reset/request` and
`/auth/telegram/verify/start`. Both were therefore reported as faults after succeeding.

A status-code allow-list was the wrong instrument. The question is not "which statuses
have no body" — it is "did a body arrive, and could it be read".

**Fix.** Read the response as text first, so *no body* and *unreadable body* stay
distinguishable regardless of status:

* empty body → success, whatever the status;
* non-empty body that fails `JSON.parse` → malformed;
* parsed body the narrowing rejects → malformed.

**Prevention.** Both 202 endpoints now behave correctly by construction rather than by
being listed. The password-reset path had the same defect and nobody had reached it yet.

**Worth noting.** The failure was maximally misleading: the user-visible message blamed
the *server*, the server had succeeded, and the side effect (a Telegram message) had
already happened. A client that lies about a success is worse than one that fails
loudly, because the operator retries and re-triggers the side effect.

**Related:** ES4, F8.AC7.

---

### E19 — The Telegram bot token was written to the log in plaintext on every send

**Status:** Fixed. **Milestone:** M1. **Date:** 2026-09-27.

**Symptom.** Found while reading the log for an unrelated trace id:

```
HTTP Request: POST https://api.telegram.org/bot<TOKEN>/sendMessage "HTTP/1.1 200 OK"
```

The full bot token, in clear text, on every successful notification.

**Root cause.** Telegram carries the bot token in the URL **path**, so any library that
logs a request URL logs a credential. `httpx` does exactly that, at INFO, from its own
logger — which `configure_logging` routes through structlog along with everything else.

`notify/telegram.py` already had a `_redact` helper, and it was not enough: it only
touches messages *this* module raises or logs. httpx's line never passes through it.
The key-based redactor could not help either, because the token is mid-string under a
non-sensitive key (`event`).

**This is precisely the class of failure F12.AC3 exists to prevent**, and the token is
not an ordinary API key: Telegram is a password-recovery channel (RISKS R18), so it is
closer to a password than to a service credential.

**Fix.** Two layers, because they fail differently:

1. **Silence the logger.** `httpx` and `httpcore` are set to `WARNING`, the same way
   `sqlalchemy.engine` already was. Nothing in that line is worth keeping — our own
   middleware logs method, path, status and duration with a trace id, and
   `telegram.py` logs the outcome with the chat id.
2. **Mask the shape.** `_mask_bot_tokens` in the redactor rewrites `/bot<digits>:<...>`
   to `/bot<bot-token>` in free text, for a traceback, a future HTTP client, or any
   other library that reintroduces it. Matched on **shape**, not on the configured
   value, so it holds for a rotated token, a second bot, or one this process has never
   seen.

**Prevention.** Five unit tests in `test_log_redaction.py`, including one asserting both
loggers are at `WARNING` after `configure_logging` — silencing is the control, masking
is the backstop — and one asserting an ordinary `key:value` string outside a `/bot` URL
is not mangled.

**Worth noting.** The redaction suite had passed since M0 and was genuinely good: it
proved sensitive **keys** never survive, and that IP literals in free text are masked.
It could not catch this, because the credential belonged to a third-party library's log
line in a format nobody had thought about. "We redact secrets" is a claim about the
inputs you enumerated.

**Related:** F12.AC3, F12.AC13, RISKS R18, E4.

---

### E20 — A Telegram delivery failure surfaced as `500 Internal error`

**Status:** Fixed. **Milestone:** M1. **Date:** 2026-09-27.

**Symptom.** "Send a verification code" returned `500 INTERNAL_ERROR` with a trace id
and nothing else. The log showed `telegram_failed` after three attempts, then
`unhandled_exception`.

**Root cause.** `TelegramError` subclasses `RuntimeError`, not `TraceletError`. The
global handler therefore treated it as an *unexpected* failure — which is right for
anything it does not recognise, and means the detail is deliberately withheld from the
client. So the one person who could act on it was told the least useful thing possible,
while looking at the form field most likely to be at fault.

`settings.require()` raises a bare `RuntimeError` too, so an unconfigured deployment
produced the same opaque 500.

**Fix.** Translate at the service boundary, into the two cases that differ in what the
operator should do:

| Failure | Response |
|---|---|
| Telegram **rejects** it — wrong chat id, bot never messaged, bot blocked | `422`, with the error attached to the **`chat_id`** field |
| Telegram **unreachable**, or not configured | `503 DEPENDENCY_UNAVAILABLE`, saying the code was not sent and to retry |

`TelegramError` gained a `permanent` flag, set on a 4xx from Telegram, so the caller can
tell "this will never work" from "this might work in a minute". `request_password_reset`
already caught both and recorded `delivered: false`; only chat verification propagated,
because it is the one place where the admin is waiting and must be told.

**Prevention.** Three integration tests cover both branches and assert a failed send
leaves **no** half-finished challenge row — otherwise a code from an earlier attempt
could later be confirmed against a chat id the admin had since corrected.

**Related:** F8.AC7, ADR-0013, E21.

---

### E21 — Antivirus TLS interception broke every outbound HTTPS from a container

**Status:** Worked around; the underlying cause is environmental. **Milestone:** M1.
**Date:** 2026-09-27.

**Symptom.** Three failures that looked unrelated, all starting after the host hung and
was restarted:

* `docker compose build api` — `Could not find a version that satisfies the requirement
  setuptools>=75 (from versions: none)`, which reads as a network or index problem;
* Telegram delivery — `CERTIFICATE_VERIFY_FAILED: unable to get local issuer
  certificate`;
* `apk add` inside a throwaway `alpine` container — silent failure.

The browser was unaffected throughout, which is what made it confusing.

**Root cause.** Norton 360 performs TLS interception: it terminates HTTPS and re-signs
with a root of its own. Asking who signed the certificate the container is actually
served made it obvious in one line:

```
SUBJECT : CN=api.telegram.org
ISSUER  : CN=Norton Web/Mail Shield Root, OU=generated by Norton Antivirus for SSL/TLS scanning
```

Windows trusts that root, so browsers are fine. A container carries its own CA bundle
and does not, so **every** outbound HTTPS from a container fails.

**Fix.** `api/certs/` — empty in CI and in production, where the whole thing is a no-op
costing one cached layer. Anything dropped there is installed into the image's trust
store at build time. The certificate itself is git-ignored: a TLS-interception root is
specific to one machine, and committing one would make every other developer, CI and the
production box trust a CA that has nothing to do with them.

**The part worth remembering: `update-ca-certificates` is not sufficient.** Python's HTTP
clients do not read the system trust store. Both `pip` and `httpx` use `certifi`, so
installing the root system-wide fixes `apt` and leaves the build *and* Telegram failing
in a way that looks unrelated to the fix just applied. The image therefore does both:
updates the system store, appends to `certifi`'s bundle after each `pip install` (certifi
does not exist in the `base` stage — it arrives with the first install), and sets
`PIP_CERT`, `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE`.

**Prevention.** `api/certs/README.md` carries the diagnosis, the one-line command that
identifies interception, and the export procedure — because the symptoms name neither
certificates nor the antivirus, and the next person to hit this will be reading a `pip`
error about `setuptools`.

**Not the preferred fix.** Turning the interception off is better where that is an
option: it means every encrypted connection is decrypted and re-encrypted inside another
process, which is a real trade independent of Docker. This is recorded as a workaround
chosen deliberately, not as a resolution.

**Recurred 2026-10-07 (M7), for a container that does not build from `api/`.** After a
reboot the Cloudflare quick tunnel used for the owner's phone tests could not start: `failed
to request quick Tunnel ... x509: certificate signed by unknown authority`, the issuer again
"Norton Web/Mail Shield Root". The API image was unaffected (it has the root, above); the
throwaway `cloudflared` container did not. Worked around for that container only by mounting
`api/certs/local-tls-inspection.crt` read-only and pointing `SSL_CERT_DIR` at it. Nothing in
the repository changed. A container started by hand needs the same, or the scanning turned off.

**Related:** E19, E20.

---

### E22 — Stripping whitespace before deciding hex-vs-raw corrupted a binary key

**Status:** Fixed. **Milestone:** M1. **Date:** 2026-09-28.

**Symptom.** CI failed on `5 · pytest unit` for a commit that had just passed the same
check locally and passed it in the *other* CI run triggered by the same push:

```
EnvelopeError: Encryption key at .../raw_key is 31 bytes; AES-256 needs exactly 32.
```

**Root cause.** `_read_key_file` did `path.read_bytes().strip()` **before** deciding
whether the contents were hex or raw bytes. The stripping is there for a good reason —
`openssl rand -hex 32 > file` leaves a trailing newline — but it was applied to both
forms.

A raw key is 32 **uniformly random** bytes. Six of the 256 possible byte values are ASCII
whitespace (`0x09`–`0x0D`, `0x20`), so a random key begins or ends with one roughly
**4.6%** of the time. When it did, `strip()` silently removed a byte of key material and
the key was rejected as 31 bytes.

Two things made this hide well:

* **It is probabilistic**, so it passed locally, passed one CI run, and failed another
  for the same commit. That signature reads as CI flakiness, and the temptation is to
  re-run it.
* **The error message was confidently wrong.** "is 31 bytes; expected 32" points the
  reader at how the key was generated, not at the code that just shortened it.

**Not a test-only fault.** The module advertises raw-32-byte keys as a supported form,
and a production box generating one that way would fail to boot about one time in
twenty-two, with a message blaming the key file.

**Fix.** Strip only for the hex attempt, and fall back to the **unmodified** bytes:

```python
raw = path.read_bytes()
try:
    key = bytes.fromhex(raw.strip().decode("ascii"))
except (ValueError, UnicodeDecodeError):
    key = raw
```

**Prevention.** Six parametrised tests, one per whitespace byte, each wrapping a key in
that byte — deterministic where the original was a 4.6% coin flip. Plus a test asserting
a hex key still tolerates surrounding whitespace, so the fix cannot be "simplified" by
removing the strip entirely.

**Worth noting.** The test that caught this was the pre-existing
`test_a_raw_32_byte_key_file_is_accepted`, which generates a random key. It had been
passing since the suite was written, and was one unlucky draw away from reporting a real
defect at any point. A random input found a bug a fixed fixture never would — and then
made it look like infrastructure noise.

**Related:** F12.AC3, ADR-0007.

---

### E23 — Masking IP literals by syntax wiped Chrome's version from every Chrome visit

**Status:** Fixed before merge. **Milestone:** M2. **Date:** 2026-09-28.

**Symptom.** The first live capture stored the user agent as
`... Chrome/[ip] Mobile Safari/537.36`. Found by reading the stored row during the first
end-to-end smoke test, not by any test.

**Root cause.** `request_headers`, the user agent, the referer and UTM values are
visitor-controlled free text, so the capture path masked every IP literal in them to keep
a plaintext address out of the database (CLAUDE.md invariant 4). But Chrome's reduced user
agent reports its version as `131.0.0.0` — which *is* a syntactically valid IPv4 address.
The mask did exactly what it was written to do, on every Chrome visit.

Syntax cannot tell a version number from an address. That is the actual lesson: the rule
was not wrong about the input, it was asking a question the input cannot answer.

**Fix.** Three layers instead of one (`capture/signals.py`):

1. **The observed client address is masked everywhere, exactly.** That is what invariant 4
   is about, and matching one known string has no false positives. Bounded on both sides
   so `1.2.3.4` does not match inside `11.2.3.45`, while an address ending a sentence
   (`1.2.3.4.`) is still masked — a privacy mask should err towards masking.
2. **Address-bearing headers are dropped by name**, whatever they hold.
3. **Any other IP literal is masked** as defence in depth — except in version-bearing
   headers (`user-agent` and the `sec-ch-ua*` family), where only layer 1 applies.

**Prevention.** Unit tests assert `Chrome/131.0.0.0` survives in both the stored headers
and the `user_agent` column, that client-hint version lists survive, and that the observed
address is still masked *inside* a version-bearing header. An integration test sends the
visitor's address through six headers, the referer and a UTM value, then searches the
entire stored row as text.

**Related:** F3.AC1, F12.AC1, CLAUDE.md invariant 4.

---

### E24 — Pydantic checked the slug pattern before lowercasing it

**Status:** Fixed before merge. **Milestone:** M2. **Date:** 2026-09-28.

**Symptom.** `POST /api/v1/links` with `"slug": "T-UPPER-CASE"` returned `422
STRING_PATTERN_MISMATCH`. The capture path, meanwhile, accepts a slug in any case — so the
API refused to create a link in a form it would happily serve.

**Root cause.** `StringConstraints(to_lower=True, pattern=...)` reads as "lowercase, then
check". It is not: the pattern is validated against the original string. Nothing in the
declaration suggests an order.

**Fix.** Normalise in a `BeforeValidator`, then apply the pattern as its own constraint.

**Prevention.** Integration tests create a link with an upper-case slug and expect it
normalised, and refuse a duplicate that differs only in case. Written to test the
intended behaviour, and failed against the original declaration — which is how this was
found.

**Related:** F1.AC1.

---

### E25 — Two crawler needles would have misclassified, one of them a person

**Status:** Fixed before merge. **Milestone:** M2. **Date:** 2026-09-28.

**Symptom.** None observed — found while choosing real user agents for the tests.

**Root cause.** The link-preview fetcher list is matched as ordered substrings, first match
wins. Two mistakes:

* **Telegram's fetcher sends `TelegramBot (like TwitterBot)`.** `twitterbot` came earlier in
  the list, so it would have been named `twitter`. Harmless to classification, wrong in
  every report.
* **`pinterest/` matched Pinterest's own in-app browser** (`[Pinterest/iOS]`) — a *person*,
  who would have been classified `crawler`, excluded from analytics and never notified on.
  A needle naming the brand rather than the fetcher's token. `viber` had the same flaw.

**Fix.** Telegram listed before Twitter; `pinterest/` narrowed to the fetcher's
`pinterest/0.`; `viber` removed. The two rules are written next to the list: the more
specific fetcher first, and never match an app name alone.

**Prevention.** Tests assert each fetcher's name, and a parametrised "people are not
crawlers" test covers Pinterest's, LinkedIn's and WhatsApp's in-app browsers alongside the
Meta cases.

**Worth noting.** The asymmetry is what makes this class of bug dangerous. A crawler
mistaken for a person costs a spurious alert. A *person* mistaken for a crawler is silently
removed from every view, and nothing will ever report it missing.

**Related:** F2.AC8, F2.AC9, ADR-0004 decision 9.

---

### E26 — Caddy's access log held every visitor's address in plaintext

**Status:** Fixed. **Milestone:** M2 (the defect dates from M0). **Date:** 2026-09-28.

**Symptom.** Found while working through the M2 done-check "no plaintext IP anywhere in
schema, logs, or API responses". The application's own logs were clean; Caddy's were not:

```
"remote_ip":"172.18.0.1", "client_ip":"198.51.100.77",
"headers":{"X-Forwarded-For":["198.51.100.77"], ...}
```

In production `remote_ip` is the visitor's real public address, on every request, written
to stdout and retained by Docker.

**Root cause.** The Caddyfile has had `log { output stdout; format json }` since M0, and
Caddy's default access log records the full client address and every request header
verbatim. Every privacy control in this project -- the AES envelope, the 30-day TTL, the
HMAC, the header sanitiser, the prefix-only durable form -- sits in the application. The
edge had none, and nobody had looked at it, because "the database never stores a
plaintext IP" was true and felt like the whole promise. F12.AC13 says a log line must never
contain what the database refuses to store; the edge log was a log line.

**What was already safe.** Caddy 2.5+ redacts `Cookie`, `Authorization`,
`Proxy-Authorization` and `Set-Cookie` by default, confirmed by probing with dummy
credentials. Admin session tokens never reached the log.

**Fix.** A `format filter` on the site log:

* `remote_ip` and `client_ip` masked to `/24` and `/48` with `ip_mask` -- the same network
  prefix the database keeps as durable (RW-3), so the log stays useful for spotting abuse
  by network;
* **all request headers dropped.** The application stores the header set, sanitised, where
  the rules are enforced and tested; an unsanitised second copy at the edge would only be a
  second place to leak from;
* `X-Csrf-Token` dropped from response headers -- Caddy does not know it is a secret.

Verified against the running stack: a request carrying `X-Forwarded-For: 198.51.100.77`
now logs `client_ip: 198.51.100.0` and no headers, and the address appears nowhere in the
line.

**Prevention.** An integration test captures the application's log records with
`caplog` -- *before* the redaction processor runs -- and fails if the capture path so much
as passes the address to a logger. The Caddyfile carries the reasoning next to the
filter. The edge log itself has no automated test yet: it would need the full compose
stack in CI.

**Worth noting.** Docker's retained logs from before this change still hold the addresses
of every request made to the development stack -- here, test values only. On a deployed
box the same would be real visitors, which is why this had to be fixed before M9 and not
after.

**Related:** F12.AC13, CLAUDE.md invariant 4, RW-3, ADR-0012.

---

### E27 — Every reverse-DNS lookup from a container came back "no PTR", including 8.8.8.8

**Status:** Worked around for Spike A; guard owed by M3. **Milestone:** M3 (Spike A).
**Date:** 2026-09-29.

**Symptom.** The first Spike A run resolved 900 sampled Indian ISP addresses and found a
PTR record for **none** of them — 0 % for every ISP, IPv4 and IPv6. A result that clean is
more likely a broken instrument than a finding, so it was checked before being believed:
from inside the `api-tools` container, `gethostbyaddr("8.8.8.8")` failed with
`[Errno 4] No address associated with name`, while `nslookup 8.8.8.8` on the Windows host
returned `dns.google`.

**Root cause.** Docker Desktop's DNS proxy — what a container reaches through the compose
network's embedded resolver (`127.0.0.11`) and on the default bridge alike — answers
forward lookups but not `PTR` queries. The same image started with `--dns 8.8.8.8`
resolved both controls correctly. The failure is indistinguishable from "this address has
no PTR record": `gethostbyaddr` raises the same `OSError` for both.

**Fix.** Spike A was re-run with `--dns 8.8.8.8 --dns 1.1.1.1` and the first run discarded
(RISKS R3). No production code does reverse DNS yet, so nothing shipped was affected.

**Prevention — owed by M3, recorded here so it is not forgotten.** S6 runs its lookups
inside the `api` container, so the same fault would turn every visit's rDNS into a silent
"no PTR". S6 must (a) distinguish NXDOMAIN from SERVFAIL, timeout and resolver refusal,
recording the latter as an *absence with a reason* (F3.AC5) rather than as "no record", and
(b) run a canary PTR lookup against a known address and surface a failure on System Health
as "S6 resolver unavailable". Whether the production resolver on the GCP host answers PTR
is **unverified** until M9's deployment.

**Worth noting.** The spike's decision rule had been fixed in advance; the broken run
would have "passed" it as a catastrophic 0 % and been rationalised as the ISPs' fault.
Controls before conclusions.

**Related:** RISKS R3, F4.AC5 (S6), F3.AC5, ADR-0005.

---

### E28 — A strict state alone produced a strict *city* coordinate

**Status:** Fixed before commit. **Milestone:** M3. **Date:** 2026-09-29.

**Symptom.** Found while writing the engine's persistence: a visit whose strict output was
`IN / Karnataka / — / —` still carried `strict_lat, strict_lng` of central Bengaluru, and
`geopoint` with them.

**Root cause.** The consensus took the strict point from "the deepest strict level", and
the admin1 winners' candidates carry *city* coordinates — a database record for Karnataka
is really a record for some city in it. So the coordinate was one level more precise than
anything the engine had decided to believe.

**Why it matters.** `geopoint` is the geofence input (DATA_MODEL 5.1). M6 would have
treated a visitor known only to be in Karnataka as standing in Bengaluru and fired
"inside" alerts on it — exactly the "location we do not believe" CLAUDE.md invariant 5
forbids, and the one failure ADR-0005's strict set exists to prevent.

**Fix.** A strict point exists only when the strict city does
(`inference/consensus.py`). The advisory point is unchanged — a guess, shown as one.

**Prevention.** `test_a_strict_state_alone_yields_no_strict_point`.

**Related:** F4.AC10, F6.AC6, CLAUDE.md invariant 5, ADR-0005.

---

### E29 — Alembic's autogenerate would have proposed dropping `links` and `visits`

**Status:** Fixed. **Milestone:** M3 (the defect dates from M2). **Date:** 2026-09-29.

**Symptom.** Found while registering the M3 models: `alembic/env.py` imported the auth
models for their side effect of registering on `Base.metadata`, and nothing else. The
capture models of M2 were never registered.

**Root cause.** Autogenerate compares the database against the metadata it can see. Two
tables it cannot see look like tables that should not exist, so the next
`tl revision --autogenerate` would have emitted `op.drop_table("visits")` — on the
busiest table in the system, inside a file a reviewer skims. No such revision was ever
generated; migration 0004 was written by hand, which is why this stayed latent.

**Fix.** `env.py` registers the capture and inference models alongside auth.

**Prevention.** Worth a CI check that autogenerate against a migrated database produces
an empty diff; recorded here rather than built, because it needs the database service in
the lint job. Until then, **read every autogenerated revision for `drop_`**.

**Related:** CLAUDE.md §2 (schema changes ship with a migration), DATA_MODEL.

---

### E30 — MaxMind's middle-of-India point became the "registry address" of half the country

**Status:** Fixed before commit. **Milestone:** M3. **Date:** 2026-09-29.

**Symptom.** After GeoLite2 was installed, `asn_profiles` put the modal centroid of Airtel
(AS24560 and AS9498), Tata (AS4755) and Tikona (AS45528) all in **Chhindwara, Madhya
Pradesh**. Separately, the consensus trail could show a country-only candidate voting for
"Madhya Pradesh" as a state.

**Root cause.** When MaxMind knows only that an address is in India, its record carries
the country and **the country's centroid** as coordinates, and no city. Two pieces of
code trusted the coordinates. The profile builder counted every located record as a
placement, so the centroid became the most common "place" of large ISPs. And the
GeoNames placer named the point near a candidate's coordinates and **filled in an admin1
the source had never claimed**. The GeoNames city nearest that centroid is Chhindwara.

**Fix.** Profiles count only records that name a city. The placer renames only fields a
candidate already asserts, and never places a country-level candidate. After the fix:
Tikona 44 % on Delhi, Jio 40 % on Mumbai, Airtel broadband spread (8 %).

**Prevention.** `test_a_country_only_claim_is_never_given_a_state`,
`test_placing_renames_only_what_was_claimed`, `test_a_profile_ignores_records_without_a_city`.
The general lesson, for every source: **a coordinate is only as precise as the level the
record claims** — the same shape as E28.

**Related:** F4.AC12(a), B1, ADR-0005, DATA_MODEL 8.1.

---

### E31 — A failing unit test printed the MaxMind account ID and the start of the licence key

**Status:** Fixed. **Milestone:** M3. **Date:** 2026-09-29.

**Symptom.** Once the owner added vendor credentials to `.env`, a unit test that assumed
there were none failed, and pytest's assertion message printed the `Download` object —
including its `auth` tuple: the account ID in full and the first characters of the
licence key, before pytest truncated it. It reached local test output only; nothing was
committed, logged or sent anywhere.

**Root cause.** Two faults. `Download` was a plain dataclass, so its generated `repr`
contained the credentials — and for IP2Location and IPinfo the URL itself carries the
token, so any traceback, log line or assertion touching it would leak them. And the test
built `Settings()` from the environment, so its outcome depended on the developer's `.env`.

**Fix.** `url` and `auth` are `field(repr=False)`. The test pins the credentials to empty.

**Prevention.** `test_a_download_never_shows_its_credentials`. **Any object that holds a
secret gets a repr that does not show it** — `SecretStr` does this for settings; a
dataclass needs `repr=False`. The owner may choose to rotate the MaxMind licence key; the
exposure was local and partial.

**Related:** F12.AC13, CLAUDE.md invariant 4 (the same principle, for credentials).

---

### E32 — The `outerWidth` probe could never report the value it exists to catch

**Status:** Fixed before it was relied on. **Milestone:** M4 (the defect dates from M2).
**Date:** 2026-10-02.

**Symptom.** Found while writing the `client.zero_outer_width` rule: the capture page sent
`outerWidth: window.outerWidth || null`.

**Root cause.** In JavaScript `0 || null` is `null`. A window width of **0** — the headless
signal F5.AC3 names — was converted into "not reported" before it left the browser, so the
rule could never fire, and the stored value said the browser had declined to answer when it
had answered 0.

**Fix.** `typeof window.outerWidth === "number" ? window.outerWidth : null`.

**Prevention.** The general rule, for every probe: **a falsy value is still a value.** Never
`|| null` a measurement whose meaningful values include 0 or `false`.

**Related:** F5.AC3, F3.AC5.

---

### E33 — Unassessed network flags were stored as `false`

**Status:** Fixed before commit. **Milestone:** M4. **Date:** 2026-10-02.

**Symptom.** Found while documenting the flags: `is_tor` was `false` on every visit when
no exit list was installed, `is_datacenter` was `false` when no ASN was known, and
`is_proxy_suspected` was `false` on `server_only` visits, which have no fingerprint.

**Root cause.** The verdict carried booleans whose default was `false`, and persistence
wrote them as they were. Each `false` asserted a fact — "not a Tor exit" — that nothing
had checked. M2 chose nullable columns precisely to prevent this (DATA_MODEL 5.1).

**Fix.** Each flag is written only when its evidence existed, otherwise `NULL`.

**Prevention.** `test_a_server_only_visit_classifies_from_server_signals_alone` and
`test_an_enriched_browser_visit_is_human_with_identity` assert `NULL` vs assessed values.

**Related:** F3.AC5, F5.AC7, F5.AC9.

---

### E34 — A real visitor's history was a 404: `visitor_id` is 128 bits, not 256

**Status:** Fixed before commit. **Milestone:** M5. **Date:** 2026-10-02.

**Symptom.** In the browser, on visits made through the real capture path, every "view
history" link was refused, and the filter rejected a visitor ID copied from a visit. The
integration suite was green.

**Root cause.** The filter, the visitor endpoint and the page all assumed a full
HMAC-SHA256: 32 bytes, 64 hex characters. ADR-0006 truncates `visitor_id` to 128 bits
(`identity.DIGEST_BYTES = 16`). The tests made their visitor IDs by hand, as 32-byte
values, so they agreed with the wrong assumption instead of with the identity code.

**Fix.** The length comes from `identity.DIGEST_BYTES` everywhere it is checked; the page
and the API document 32 hex characters.

**Prevention.** The test fixtures use 16-byte IDs with a comment naming the constant.
The general lesson is that a fixture is an assertion about another module: where one
exists, derive it from that module's constant rather than restating it.

**Related:** ADR-0006, F9.AC12, F9.AC13.

---

### E35 — A year-long calendar always fell back to raw rows (7 s at the design load)

**Status:** Fixed before commit. **Milestone:** M5. **Date:** 2026-10-02.

**Symptom.** Measuring NFR2.AC4 at 90 k visits on one CPU, the 365-day calendar heatmap
reported `computed_from: raw` and took 6–10 s, though every day with data had been built.

**Root cause.** The rollups answer a window only if every day in it has a `rollup_state`
row, so a missing rollup is never read as "no visits" (ADR-0016). Days before the first
visit are never built -- there is nothing to build -- so any window reaching back past
the first visit failed the check. On a young deployment that is every year-long view.

**Fix.** A day before the oldest retained visit counts as built: `occurred_at` is the
server's receive time, so no visit can arrive for it later. Measured afterwards: p95
24 ms from rollups.

**Prevention.** The p95 measurement is recorded in ADR-0016 and is repeatable from the
M5 evidence. The general rule: "complete" for a derived table must include the days that
are complete because they are empty.

**Related:** ADR-0016, F9.AC6, NFR2.AC4.

---

### E36 — Integration tests fail while the dev stack is running

**Status:** Fixed in tooling. **Milestone:** M5. **Date:** 2026-10-02.

**Symptom.** Three analytics integration tests failed with counts off by one and an
emission rate of 0.5 instead of 0.67 -- but only while `docker compose up` was running.
With the stack down they passed.

**Root cause.** The suite shares the dev database (KICKOFF, handoff notes). A running `api`
container runs the scheduler: its inference job picked up a test's visit that the test had
deliberately left un-inferred, inferred and reclassified it, and changed what the test
counted, mid-test.

**Fix.** `./scripts/tl verify` stops a running `api` container for the integration step
and starts it again afterwards, saying so.

**Prevention.** Running the suite by hand with the stack up reproduces it; the comment in
`scripts/tl` names this entry. A separate test database would remove the class entirely,
and is worth doing if a third instance appears.

**Related:** ADR-0009 (the scheduler), ADR-0015.

---

### E37 — The map "loaded" fifteen tiles, all of them watermarks

**Status:** Fixed by ADR-0017. **Milestone:** M5. **Date:** 2026-10-02.

**Symptom.** Checking the worldwide state layer by screenshot, the Geography map showed a
grid of "API KEY REQUIRED" behind the shading. An earlier automated check of the same page
had passed: it counted `.leaflet-tile-loaded` and found fifteen.

**Root cause.** CARTO now requires an API key for its basemaps, and answers keyless requests
with `200 image/png` -- a perfectly loadable tile that is a watermark. The check measured
"the request succeeded", which was true, not "the map shows a map", which was not.

**Fix.** ADR-0017: no tiles. The map draws self-hosted outlines, and the CSP exception for
CARTO is gone.

**Prevention.** A visual claim is verified by looking at it: M5's map evidence is a
screenshot, not an element count. The general rule is that a third party's success status
is not evidence of the content you wanted from it.

**Related:** ADR-0003, ADR-0017, RISKS R14, R26.

### E38 — Every mobile-network visit had no city at all, not even a guess

**Status:** Fixed by ADR-0018 (engine m3.4). **Milestone:** M5. **Date:** 2026-10-02.

**Symptom.** The owner reported that every city on the dashboard read "Abstained". On the
dev set (160 visits from real Indian ISP addresses): strict city 0 of 160, which Spike A
predicted -- but advisory city only 66, and the split was exact: all 66 broadband visits
had one, none of the 94 mobile visits did, although every one of those 94 had city
candidates in `visit_candidates`.

**Root cause.** Rule (b) (mobile and CGNAT networks, F4.AC12(b)) built its block list and
applied it to the *shared* first consensus pass, which served as both the advisory result
and, when no hosting rule applied, the strict one. So "no strict city on a mobile network"
was implemented as "no city on a mobile network", contradicting ADR-0005's "advisory is
always the argmax". A unit test even asserted it ("discarded outright, advisory
included"), because SPEC F4.AC12(b) said "discarded outright" without saying from what.
Separately, the dashboard showed strict wherever any strict level existed, so a visit with
a strict country and an advisory city read "India".

**Fix.** Strict and advisory are separate walks: rule (b) blocks city depth in the strict
walk only. Advisory is re-walked with strict's levels fixed whenever they would disagree
(the hosting-plus-GPS case), so it always extends strict. The dashboard shows the best
guess everywhere (ADR-0018).

**Prevention.** A suppression rule's scope is stated as *strict* or *advisory*, never
"discarded"; F4.AC12(b) now says strict. A parametrised unit test asserts, per suppression
scenario, that every level any candidate named has an advisory value and that advisory
equals strict wherever strict emitted (DATA_MODEL 5.3 invariant 13). And coverage is
checked per network class, because an average over all visits hid a 0 % / 100 % split.

**Related:** ADR-0005, ADR-0018, SPEC section 11 row 14, RISKS R3.

---

### E39 — The state map opened on the whole world instead of fitting the visits

**Status:** Fixed. **Milestone:** M5. **Date:** 2026-10-02.

**Symptom.** With "State / province" made the Geography map's default shading, a fresh load
showed the whole world with India a small patch under its points, although switching the
shading away and back fitted India correctly.

**Root cause.** On first load the map draws twice in quick succession (development
StrictMode, then the query settling), each ending in an animated `fitBounds`. Logged: zoom 4
after the first fit, 2 (the initial world zoom) after the second, and 2 a second and a half
later. The second, animated fit never took effect -- consistent with Leaflet ignoring a view
change while a zoom animation is in progress; the exact interleaving was not traced further.

**Fix.** `fitBounds(..., { animate: false })`, so every fit lands immediately, plus
`invalidateSize()` first so the fit measures the panel's final size.

**Prevention.** A map view set from an effect that can re-run is never animated. The check
for "the map fits the data" is a fresh page load, not a toggle, because a toggle runs the
effect once.

**Related:** ADR-0017, ADR-0018, F9.AC5.

---

### E40 — Clicking a country or state drew a rectangle around it

**Status:** Fixed. **Milestone:** M5. **Date:** 2026-10-02.

**Symptom.** The owner reported a "weird boxy outline" on the Geography map after clicking
an area. It was a light rectangle the size of the area's bounding box.

**Root cause.** Leaflet makes each shaded area an interactive SVG `<path>`. A click focuses
it, and the browser's default focus style (`outline: auto`) on an SVG element is drawn
around its bounding box. `document.activeElement` was the path, with `outline: auto`, not
`:focus-visible`. The map had no hover or selection styling of its own, so the box was the
only feedback a click gave.

**Fix.** `.map path.leaflet-interactive:focus { outline: none; }` -- areas are not in the
tab order, and keyboard users keep the map's own focus ring and the tables. In its place:
hover brightens an area's border, a click selects it with a bold outline in the text colour
(readable in all three themes) until the sea is clicked or Escape is pressed, and visit
points moved to their own pane above the shapes so a selected area never covers them.
Tooltips also stopped saying "1 visits".

**Prevention.** Any clickable map shape gets explicit hover and selection styles, so the
browser's default is never the only feedback.

**Related:** ADR-0017, F9.AC5, NFR7.

---

### E41 — The first modal dialog opened in the top-left corner

**Status:** Fixed. **Milestone:** M5.5. **Date:** 2026-10-03.

**Symptom.** In the component gallery, a confirmation dialog rendered as a 400 × 206 box at
(0, 0) behind a blurred page instead of in the centre.

**Root cause.** A modal `<dialog>` is centred by the browser's own stylesheet (`margin: auto`
in the top layer). Tailwind's reset sets `margin: 0` on every element, which silently removed
it. Nothing else looked wrong, because only the top layer depends on that margin.

**Fix.** `.dialog:modal { margin: auto; }`, with the drawers setting their own edge position.

**Prevention.** A primitive that relies on a user-agent default restates it, because the CSS
reset removes defaults wholesale; the gallery opens every overlay as part of phase 1's checks.

**Related:** ADR-0019, DESIGN §5.5.

### E42 — Old element selectors out-ranked the new components

**Status:** Fixed. **Milestone:** M5.5. **Date:** 2026-10-03.

**Symptom.** Restyled inputs kept M1's padding and background, field labels were bold, every
icon button grew an accent border on hover, and Account section titles were uppercase.

**Root cause.** M1 styled elements directly (`input[type='text']`, `.field label`,
`button:hover`, a global `h2`). An element-plus-attribute selector has higher specificity than a
single class, so `input[type='text']` beat `.input` and `button:hover` reached buttons that were
never meant to look like M1 buttons.

**Fix.** The legacy rules are scoped to unclassed elements (`button:not([class])`,
`input:not([class])[type=…]`), the global heading rules and the duplicate `.field` rules are
gone, and the remaining M1 idioms are restyled onto the tokens.

**Prevention.** DESIGN UI-1: a primitive's class decides its look. No new element-level rule may
style a property a primitive also sets.

**Related:** DESIGN §5, UI-1, UI-2.

### E43 — A long link name made the whole page wider than a phone

**Status:** Fixed. **Milestone:** M5.5. **Date:** 2026-10-03.

**Symptom.** At 375 px the dashboard scrolled sideways and every card was cut off on the right;
the document was 496 px wide.

**Root cause.** A native `<select>` sizes itself to its longest option. The link filter's longest
option ("demo-ig — M5 demo: real ISP addresses…") made it 480 px, and because grid items default
to `min-width: auto`, every grid up to the page grew to fit it.

**Fix.** `min-width: 0` and `max-width: 100%` on the select and its wrappers, a shrinkable grid
track, and the page grid as `minmax(0, 1fr)`; the option text ellipses instead.

**Prevention.** The phone pass measures `document.documentElement.scrollWidth` against the
viewport and lists every element wider than it, rather than judging by eye.

**Related:** DESIGN §9.4, F9.AC17 (SPEC §11 row 15).

### E44 — Every page load reported a CSP violation, and the Chromium sweep said zero

**Status:** Fixed. **Milestone:** M5.5 (present since M5). **Date:** 2026-10-03.

**Symptom.** The Firefox and WebKit sweep (Playwright against the Caddy-served build) recorded a
`script-src` violation, blocked URI `eval`, once per page load in all three engines, Chromium
included. The M5.5 Chromium sweep had recorded **0 violations** on the same build.

**Root cause.** Two faults.
1. zod 4 compiles a fast parser for object schemas, and first probes whether it may by calling
   `new Function("")` (`util.allowsEval`). `script-src 'self'` blocks it, zod catches the throw
   and falls back to its normal parser. That is why nothing visibly broke, but the browser still
   reports the blocked eval. zod has done this since M5 introduced it (ADR-0003).
2. The Chromium sweep attached its `securitypolicyviolation` listener with a script run *after*
   the page loaded, then moved between pages with `history.pushState`. zod's probe runs once, on
   the first parse after a full load, before the listener existed, so it was never seen.

**Fix.** `z.config({ jitless: true })` where the schemas are defined (`web/src/api/schemas.ts`),
which skips the probe; the parser that runs is the one that ran anyway. A unit test asserts
the setting.

**Prevention.** A CSP check registers its listener **before any page script runs** (Playwright
`add_init_script`) and does a full load of every page, not a client-side navigation. It also
runs a positive control: an injected `<style>`, a `style` attribute and a third-party image
must all be reported, or the check is void. A library that probes for `eval` is configured not
to when it is added.

**Related:** F13.AC2, ADR-0019 "Spike results", RISKS R27.

### E45 — Showing a chart's data table pushed the chart over the next panel

**Status:** Fixed. **Milestone:** M5.5. **Date:** 2026-10-03.

**Symptom.** On Inference, pressing **Data** under "Confidence distribution" (11 columns) drew
the chart 836 px wide in a 518 px panel. The bars ran across the Accuracy panel beside it, and
the chart's own Data and CSV buttons slid under that panel and could not be clicked. The sweep
found it as a click intercepted in all three engines.

**Root cause.** `.chart` is a grid with no declared columns. Its implicit track is `auto`, and
the table's wrapper is a grid item with the default `min-width: auto`, so the track grew to the
table's minimum width. The canvas then resized to the track. `.dt-wrap` scrolls only if its
container is narrower than the table, and its container was no longer narrower.

**Fix.** `.chart { grid-template-columns: minmax(0, 1fr); }`: one track bounded by the panel, so
the table scrolls inside `.dt-wrap`.

**Prevention.** The same rule as E43, applied wherever a grid holds a table: give every grid an
explicit `minmax(0, …)` track. The QA sweep clicks every chart's Data toggle and fails on an
intercepted click.

**Related:** E43, NFR7.AC3, DESIGN §6.

### E46 — Charts announced a generated data dump, "… is NaN", instead of their name

**Status:** Fixed. **Milestone:** M5.5 (present since M5). **Date:** 2026-10-03.

**Symptom.** Each chart's `aria-label` was not the name the code gave it but ECharts' own
description, for example "This is a chart with type Sankey diagram. The data for DB-IP › is NaN…".
Every Sankey value read as NaN.

**Root cause.** Decal patterns need `aria: { enabled: true }`, and enabling `aria` also turns on
ECharts' automatic label, which writes `aria-label` onto the container element. That element is
the one React labels, so ECharts replaced the name after every render. Its describer does not
understand Sankey links, hence NaN.

**Fix.** `aria: { enabled: true, label: { enabled: false }, decal: { show: decals } }`. The chart is
named by our label, and the data is the table under it (NFR7.AC3). A unit test pins the option.

**Prevention.** A third-party component that may write attributes onto an element we own is
checked in the rendered DOM, not only in our JSX: the a11y sweep reads names from the live page.

**Related:** NFR7, ADR-0019 decision 7.

### E47 — Two pages were wider than the screen: a long ISP name, and a hidden column header

**Status:** Fixed. **Milestone:** M5.5. **Date:** 2026-10-03.

**Symptom.** The screenshot matrix's overflow check (`scrollWidth` against the viewport) flagged
Breakdowns at 390 px (245 px too wide) and Visits at 1024 px (34 px), in all three themes.

**Root cause.**
1. *Breakdowns.* A ranked list's label is `white-space: nowrap` with an ellipsis, but in an
   auto-layout table a nowrap cell sizes its column to the whole text. "Atria Convergence
   Technologies Pvt. Ltd. Broadband Internet Service Provider INDIA" widened the ISP table, and
   the counts and shares ran into each other.
2. *Visits.* The table scrolled correctly inside `.dt-wrap`, but its visually hidden "Actions"
   header (`.sr-only`, absolutely positioned) is positioned against an ancestor outside the
   scroller, because `.dt-wrap` was not positioned. So it was not clipped, and it stretched the
   document.

**Fix.** `.ranked { table-layout: fixed; }`, with the number columns' widths on the header cells,
so names truncate. `.dt-wrap { position: relative; }`, so absolute descendants are clipped by
the scroller.

**Prevention.** Every scroll container that can hold `.sr-only` content is positioned. The
matrix measures overflow on every page, at every width, in every theme, and lists the
offending element.

**Related:** E43, DESIGN §9.4, F9.AC17.

### E48 — In Countries mode the unvisited world was one grey mass

**Status:** Fixed. **Milestone:** M5.6 (present since M5). **Date:** 2026-10-06.

**Symptom.** Reported by the owner: on Geography with "Countries" chosen, no borders showed
between countries without visits; only the hovered country got an outline. States mode drew
them.

**Root cause.** Countries mode styled every country with `shade()`, whose outline is the
`--border` token at 0.5 px. On the dark themes `--border` sits a step away from `--map-land`, so
the outline vanished into the fill. States mode never showed it because it draws a separate
country-border layer on top in `--text-muted`.

**Fix.** The country layer takes the same outline as the state layer: `--text-muted`, 0.6 px,
0.8 opacity. Checked by screenshot in all three themes.

**Prevention.** A map layer's outline comes from a text-contrast token, never `--border`, which
is tuned to separate a card from its background, not one fill from an identical one. The QA
screenshots now include Countries mode, not only the default States view.

**Related:** E40, ADR-0017, DESIGN §6.6.

### E49 — The user menu opened off the bottom of the window

**Status:** Fixed. **Milestone:** M5.6 (present since M5.5). **Date:** 2026-10-06.

**Symptom.** Reported by the owner: the account menu at the foot of the sidebar opened downward,
so the theme choices, Keyboard shortcuts and Sign out were cut off by the bottom of the window.

**Root cause.** A Popover renders its content only once it is open (`{open && children()}`), but
`follow()` measured it in the `toggle` event, while it was still empty. An empty box fits below
the trigger, so it was placed there; the items then rendered and grew it out of the window.
`follow()` re-placed on scroll and resize only, never when the popover itself changed size.
The flip-above logic in `place()` was right; it was given a height of nearly zero.

**Fix.** `follow()` also watches the floating element with a `ResizeObserver` and re-places it
whenever its size changes. Every popover benefits: the user menu, filter editors, the period
picker and export. Checked in Chromium, Firefox and WebKit at window heights of 900, 700 and
560 px: the menu opens above its trigger, wholly inside the window, with all five items.

**Prevention.** Anything positioned from a measurement re-measures when what it measured
changes; a one-off measurement at open is only valid when the content is already there. A
browser check of a popover asserts its box lies inside the viewport, not only that it opened --
the M5.5 sweep counted this menu as opened and passed it.

**Related:** ADR-0019 (native popover), DESIGN §5.5.

### E50 — Once changed, the time zone could not be set back to India's

**Status:** Fixed before release. **Milestone:** M5.6. **Date:** 2026-10-06.

**Symptom.** The browser check of the new Preferences page changed the display time zone to
Asia/Tokyo and then could not select the original, Asia/Kolkata: it was not in the list.

**Root cause.** The list is the browser's own (`Intl.supportedValuesOf('timeZone')`), and
Chromium still names some zones by their pre-rename IANA names: "Asia/Calcutta", not
"Asia/Kolkata". The saved zone was only offered while it was the current value, so the
moment an admin moved away from India's zone they could not come back to its current name --
in a product whose audience is India-first.

**Fix.** Renamed zones are listed under their current names (Asia/Calcutta becomes
Asia/Kolkata, likewise Kathmandu, Yangon, Ho Chi Minh, Kyiv), and the saved and the reporting
zone are always offered.

**Prevention.** A list taken from the platform is normalised to the names the server and other
admins use. Browser checks change a setting *and change it back*: the round trip is what
caught this.

**Related:** DESIGN §10.8, F9.AC16.

### E51 — The audit log recorded every role change as "from" its new value

**Status:** Fixed. **Milestone:** M5.6 (present since M1). **Date:** 2026-10-06.

**Symptom.** Found while checking the new Team page against `audit_log`: promoting an analyst
was recorded as `admin.role_changed {"from": "owner", "to": "owner"}`. A status change had the
same fault (`{"from": "disabled", "to": "disabled"}`).

**Root cause.** `PATCH /admins/{id}` built the audit detail from `target.role` and
`target.status` *after* running `update(Admin)...`. An ORM-enabled `update()` in SQLAlchemy 2
synchronises the session by default, so the loaded `target` already carried the new values.
The M1 tests asserted only that an `admin.role_changed` row existed, never what it said.

**Fix.** The handler reads the role and status before the update and records those as
`from`. A new integration test promotes an analyst and disables them, then asserts both details
exactly; it fails on the old code with this symptom.

**Consequence.** `audit_log` is append-only, so rows written before this fix keep the wrong
`from`. Their `to`, actor, target and time are right; read `from` on older rows as unknown.

**Prevention.** An audit test asserts the row's *content*, not only that it exists. Any value
needed "before" a write is captured before the write, never re-read from an ORM object the
write may have synchronised.

**Related:** CLAUDE.md invariant 9, API §5, ERRORS E16.

### E52 — Test geofences outlived their tests and applied to every dev visit

**Status:** Fixed. **Milestone:** M6. **Date:** 2026-10-06.

**Symptom.** A new outbox test expected a strict-Maharashtra visit to be `outside` a Karnataka
geofence and got `undetermined`. The database held six active geofences named "Test fence",
with `link_ids` NULL -- applying to every link -- left by the first two runs of the geofence
invariant tests. Any visit inferred afterwards, in a test or by the dev server, would have been
evaluated against them; a polygon with no geopoint is undetermined, which outranks outside.

**Root cause.** The integration suite shares the dev database (E36). The invariant tests
inserted *valid* geofences to prove the shape CHECKs accept them, with the column defaults:
active, every link. Nothing removed them. A geofence differs from most test rows: it is not
looked up by id, it is applied to every later visit.

**Fix.** The rows were deleted. Every geofence a test makes is now named `itest ...`, inserted
inactive where the test does not need it active, and deleted by an autouse fixture. The suites
that infer visits narrow the job's loader to their own geofences, so an owner's real dev
geofence cannot change their results either.

**Prevention.** A test row that the system *applies* (geofences now; any future global rule)
is created inactive or scoped, named for cleanup, and removed after the test. Assertions about
a combined state are made where only the test's own inputs can reach the code under test.

**Related:** ERRORS E36, ADR-0020 decision 5.

---

### E53 — The delivery log crashed: `dict()` read a query result as a mapping

**Status:** Fixed before commit. **Milestone:** M6. **Date:** 2026-10-06.

**Symptom.** `GET /api/v1/health/outbox` returned 500, logged as `TypeError:
'ChunkedIteratorResult' object is not subscriptable`.

**Root cause.** The status counts were built as `dict(await db.execute(select(status,
count(*))...).tuples())`. `dict()` treats any argument with a `keys()` method as a mapping and
indexes it by those keys, and a SQLAlchemy `Result` -- tuples or not -- has `keys()` (the
column names). Ruff's C416 then suggests exactly this form as the "simpler" rewrite of a
comprehension.

**Fix.** An explicit loop over `.tuples()`, with a comment saying why it is not `dict(...)`.

**Prevention.** Never pass a `Result` to `dict()`; iterate it. The integration test that reads
the delivery log covers the endpoint, so a regression fails the suite rather than the page.

**Related:** API §10.

---

### E54 — zod's eval probe came back on every page once the header parsed a payload

**Status:** Fixed before commit. **Milestone:** M6. **Date:** 2026-10-06.

**Symptom.** The M6 Playwright walk reported `script-src` `eval` from the main chunk on every
page, in all three engines -- the violation E44 had removed.

**Root cause.** E44's fix, `z.config({ jitless: true })`, ran as a side effect of
`api/schemas.ts`. Until M6 only lazy pages parsed payloads, and every one of them imported
`schemas.ts` first. M6's header bell parses the outbox counts on first paint, from the main
chunk, with a schema from `api/geofences.ts` -- before any page had loaded `schemas.ts`. zod's
first object parse probed `new Function`, and the CSP reported it.

**Fix.** `api/zod.ts` configures zod and re-exports it; it is the only module that imports
`zod`. `schemas.ts`, `geofences.ts` and the test import `z` from it, so no schema can exist
before the configuration does.

**Prevention.** ESLint's `no-restricted-imports` forbids importing `zod` anywhere else (type
imports allowed). A configuration that must precede every use lives in the module every use
imports, never beside one of its users.

**Related:** ERRORS E44, DESIGN UI-3.

---

### E55 — Leaving the geofence editor threw `_leaflet_pos`

**Status:** Fixed before commit. **Milestone:** M6. **Date:** 2026-10-06.

**Symptom.** The M6 walk recorded a page error, `Cannot read properties of undefined (reading
'_leaflet_pos')`, when navigating away from the geofence editor.

**Root cause.** React runs a component's effect cleanups in declaration order. The map's own
effect, declared first, removed the map; the outline, shape and draw-tool effects then cleaned
up against the removed map, and Leaflet read the position of a pane that no longer existed.

**Fix.** Each later cleanup does nothing once `map.current` is no longer its map (removing the
map removed its layers). `fitBounds` is no longer animated, so no animation outlives the map.

**Prevention.** In a component that owns a Leaflet map, every effect cleanup other than the
map's own checks that the map it captured is still the current one.

**Related:** DESIGN §16.

---

### E56 — Alerts queued by the integration suite were delivered to the owner's Telegram

**Status:** Fixed. **Milestone:** M6. **Date:** 2026-10-06.

**Symptom.** The M6 delivery log showed 28 "Delivered" alerts for visits on "Integration
link". They had been sent to the owner's real Telegram chat at 11:57:43–11:58:12 UTC, seconds
after the dev API was restarted on the M6 code.

**Root cause.** The integration suite shares the dev database and pauses the API while it
runs (E36). Its inference tests now queue an alert for every human visit (F7.AC5), and nothing
removed those rows. When the API came back with the outbox worker, the rows were due, and it
delivered them -- through the real bot, to the real chat, because the dev server is configured
exactly like production. A second path was open too: a test visit left uninferred would be
inferred by the live job later and alert then.

**Fix.** An autouse fixture in the integration `conftest.py` deletes, after every test, the
outbox rows the test queued (by id high-water mark) and the visits it left uninferred on links
it created. A check afterwards found no undelivered outbox row and no uninferred visit on any
link. Nothing else was sent.

**Consequence.** The 28 messages cannot be recalled from Telegram; they carry test data only
(test links, test visitors, no real address).

**Prevention.** Anything a test leaves in the shared database that a *running* system acts on
-- queued messages, unprocessed visits, active geofences (E52) -- is removed by a fixture, not
by the test's good intentions. Before a live worker is started against a database tests have
used, its queue is inspected.

**Related:** ERRORS E36, E52; F7.AC5.

---

### E57 — A geofence circle was half hidden, or vanished, under the land

**Status:** Fixed. **Milestone:** M6. **Date:** 2026-10-06. Reported by the owner.

**Symptom.** A circle drawn across the coast showed only its sea half. A circle typed as a
centre and radius inland did not appear at all. Separately, the delivery log, filtered to a
status with no rows, showed only "No deliveries with this status" and no way back.

**Root cause.** Two for the circle. (1) Leaflet draws every vector layer in one pane, ordered
by when it was added. The country and state outlines are filled opaque, and that effect
redraws them -- on a region toggle, a theme change -- *after* the shape, so the land was painted
over it. Over the sea there was no fill, so that half showed. (2) The map moved to a shape
only when a new one appeared (keyed on "there is a circle"), so a circle whose centre was
typed elsewhere was redrawn off-screen and looked deleted. For the log: the Panel's empty
state replaces its whole body, and the status filter lived in the body.

**Fix.** Outlines and places get their own panes below the shapes' (z 350 and 380 under
Leaflet's 400), so the order is structural rather than temporal. The map moves to a shape
whenever it is out of view or under 40 px across, and typed values are applied after a
half-second pause, so the map does not chase each keystroke. The log's empty state is shown
only when nothing was ever queued; a filter with no rows answers inside the log, filter
still there, with "Show all deliveries". Each was checked in the browser.

**Prevention.** On a Leaflet map with more than one kind of layer, every kind gets a pane
with an explicit z-index. A filter is never inside the area its own empty state replaces.

**Related:** DESIGN §16, UI-7.

---

### E58 — State names stuck on the editor's map after hovering

**Status:** Fixed. **Milestone:** M6. **Date:** 2026-10-06. Reported by the owner.

**Symptom.** In the geofence editor, state names shown on hover ("Missouri", "Kentucky")
stayed on the map after the pointer had left them, several at once.

**Root cause.** The names were Leaflet `sticky` tooltips bound to each outline. The outlines
are rebuilt whenever the picked regions, the tool, the country or the theme change -- for
instance on the click that picks a state, with the pointer still over it. Removing a layer
whose tooltip is open leaves the tooltip element behind in the tooltip pane, owned by nothing,
and nothing ever closes it.

**Fix.** No hover tooltips on outlines at all. Country and state names are printed on the map
as permanent, non-interactive labels at the centre of each area's largest part, in one
collision pass with the city names (metros first, then areas, then other cities), and only
where the area has room for its name. The owner asked for exactly this. Checked in
Chromium, Firefox and WebKit: zero stray tooltips after sweeping the pointer across the
states.

**Prevention.** A tooltip on a layer that is rebuilt while it may be hovered is a leak. Names
that identify areas are drawn as labels; hover is for transient emphasis only (an outline).

**Related:** E57, DESIGN §16.

---

### E59 — Zooming the country map stuttered, worse with every zoom

**Status:** Fixed. **Milestone:** M6. **Date:** 2026-10-06. Reported by the owner.

**Symptom.** Zooming into a country in the geofence editor lagged while the city dots and
names appeared. Measured in Chromium, zooming India from its fitted view to the maximum: main
-thread long tasks of 99 ms and 84 ms, and after five steps **1,605 city-name elements in the
page for 1,110 cities**.

**Root cause.** Each city name was a permanent Leaflet tooltip on an SVG dot, and every zoom
cleared and rebuilt all of them -- every city in the country, on screen or not. The rebuild
did not remove every tooltip element (the count grew past the number of cities), and Leaflet
repositions every tooltip on every frame of a zoom animation, so the work grew with each zoom.
The collision test also compared each name against every name placed so far.

**Fix.** Dots, city names and area names are drawn on one canvas in the places pane: no DOM
element per name, so nothing to leak or reposition. Only what is on screen (plus a margin) is
drawn, at most once per animation frame while panning, and the canvas is hidden mid-zoom and
redrawn once at the end. Collisions use a uniform grid. After: no long task while zooming or
panning, no name elements in the page, and the same names drawn (31 countries, 18 Indian
states, all 8 metros at the fitted view) in Chromium, Firefox and WebKit.

**Prevention.** Anything drawn in the hundreds on a map is drawn on a canvas, never one DOM
element each. A layer that redraws on zoom draws only the viewport.

**Related:** E58, DESIGN §16.

---

### E60 — Every name on the editor map blinked out on each zoom step

**Status:** Fixed. **Milestone:** M6. **Date:** 2026-10-06. Reported by the owner.

**Symptom.** Zooming the geofence editor's map, all city and area names vanished for the
length of each zoom animation and reappeared at its end: a flicker on every step.

**Root cause.** E59's canvas was hidden on `zoomstart` and redrawn on `zoomend`, on the
reasoning that stretched text mid-zoom would look worse than none. A blink on every step was
worse.

**Fix.** The canvas now does what Leaflet's own canvas renderer does: on `zoomanim` (and
`zoom`) it is transformed from the view it was drawn for to the view being zoomed to, with
`leaflet-zoom-animated` so the transition runs with the map's; the redraw at the end resets
the transform and draws crisp. Sampled every animation frame in Chromium, Firefox and WebKit:
never hidden, scaled 1 to 2 zooming in and 1 to 0.5 zooming out, back to 1 with the names
redrawn; still no long task while zooming or panning.

**Prevention.** A layer that redraws after a zoom scales with the zoom in the meantime; it
never disappears.

**Related:** E59, DESIGN §16.

---

### E61 — A deleted circle came back half a second later

**Status:** Fixed. **Milestone:** M6. **Date:** 2026-10-06. Reported by the owner.

**Symptom.** The tool rail's "Delete shape" removed the shape, and it reappeared. Reproduced
for drawn and typed circles: after delete, the radius field still read the old value, and the
circle was back 1.5 s later.

**Root cause.** E57 made typed circle values apply 500 ms after the last change, whenever the
fields differed from the current circle. Deleting set the circle to none but left the fields
showing it -- and "fields hold a circle, there is none" counted as a difference, so the old
circle was applied again. Delete also switched straight into draw mode, whose hint lines made
the map look as if something was still there.

**Fix.** Only what the person typed is applied: a flag set by keystrokes, cleared whenever the
fields are refreshed from the shape. The fields also empty when the shape is deleted, and
delete returns the rail to Select. Checked in Chromium, Firefox and WebKit for a drawn circle,
a typed circle and a drawn polygon: gone at once and still gone 1.5 s later.

**Prevention.** A debounced "apply what is typed" acts on typing, never on a difference
between a field and its source -- a difference also arises when the source changes.

**Related:** E57.

---

### E62 — After saving a geofence the editor neither went back nor started a new one

**Status:** Fixed. **Milestone:** M6. **Date:** 2026-10-06. Reported by the owner.

**Symptom.** "Create geofence" left the editor open on the geofence just made, now headed
with its name and offering "Save": no way back to the list and no fresh form for the next
geofence, which is what the owner was trying to do (one geofence per test link).

**Root cause.** A design choice, not a crash: a create navigated to the new geofence's own
URL so it could be edited further. Separately, the editor reads a geofence under its own query
key, and a save refreshed only the list's key, so reopening it within the app could show the
pre-save values from the cache for up to a minute.

**Fix.** Create and Save return to the Geofences list with a confirmation; "New geofence"
there starts blank. A save replaces the cached geofence with the server's answer, and a delete
drops it. Checked in Chromium, Firefox and WebKit, including reopening without a reload.

**Prevention.** A form that creates returns to where the next one starts. A write updates
every query key that holds what it changed, not only the list's.

**Related:** DESIGN §16.

---

### E63 — Firefox reported a CSP violation on every capture page

**Status:** Fixed. **Milestone:** M6 (present since M2). **Date:** 2026-10-06.

**Symptom.** Testing ADR-0021's asking links in Firefox, every capture visit -- asking or
not -- logged `img-src` blocking `https://localhost/favicon.ico`.

**Root cause.** The capture page declares no icon, so Firefox requests `/favicon.ico` on its
own, and the page's CSP is `img-src 'none'`. Chromium and WebKit happen not to fetch it there,
which is why the M2 checks never saw it.

**Fix.** The page declares an empty inline icon, `<link rel="icon" href="data:,">`, and the
CSP allows `img-src data:`. Nothing loads from any origin; F2.AC12 holds. The unit test that
forbade every `<link>` now asserts the one allowed: the inline icon.

**Prevention.** Every server-rendered page declares its icon, so no browser fetches one the
CSP refuses.

**Related:** F2.AC12, F13.AC2.

---

### E64 — The Links page's "Asks for location" switch flipped back after it had saved

**Status:** Fixed before commit. **Milestone:** M6. **Date:** 2026-10-06.

**Symptom.** Toggling the switch showed an error and the switch returned to its old position,
yet the audit log showed the change saved; the next click then sent no change at all.

**Root cause.** The call declared that it read nothing from the response (`parse: () =>
null`). The API client reads a parser's `null` on a non-empty body as a malformed response, so
a successful save was reported as a failure and the optimistic switch was undone.

**Fix.** The call parses the saved link (`id`, `ask_location`) and the switch shows what the
server holds.

**Prevention.** A write's parser proves the response it gets; `noContent` is only for
endpoints that answer with no body.

**Related:** UI-13.

---

### E65 — A refused Create in the geofence editor looked like a dead button

**Status:** Fixed. **Milestone:** M6. **Date:** 2026-10-07.

**Symptom.** The pre-PR browser walk timed out in all three engines after clicking "Create
geofence" for a drawn circle: no request was sent and nothing on screen changed. The editor
now opens on the world map, where the walk's short drag made a 1,579 km circle.

**Root cause.** Not the refusal itself -- the radius limit is 50 m to 1,000 km, as the API
enforces -- but how it was shown. The reason was placed at the foot of the inspector, which
scrolls on its own, so at 1440 × 900 it sat below the panel's fold, far from the button at the
top right; and it was a plain paragraph, so nothing announced it. The same held for an API
error with no place on the map.

**Fix.** The reason is `role="alert"` (DESIGN §5.2, the field-error pattern), and the inspector
scrolls it into view when it appears, so it shows by the field at fault (UI-14).

**Prevention.** Checked in Chromium, Firefox and WebKit: the refused circle's reason is an
alert inside the visible part of the inspector, and a corrected radius then saves. The browser
walk now sizes its circle for the world view.

**Related:** UI-14, F6.AC1.

### E72 — CI saw a verified owner chat that no test in the run had set up for itself

**Status:** Fixed. **Milestone:** M6. **Date:** 2026-10-07.

**Symptom.** PR #8's CI failed one integration test twice running,
`test_outbox.py::test_quiet_hours_are_set_by_an_owner_and_audited`: the settings reported
`chat_verified: true` where the test expects `false`. The same suite passed locally.

**Root cause.** Test isolation, not product code. After each test the suite deletes the
`@example.test` admins -- except that the engine refuses to remove the last active owner, so
when no real owner exists the test owners survive. CI's database is fresh and has no real
owner; the developer's database has one, so locally every test admin was deleted and the leak
never showed. A `test_auth_flow` test verifies chat 424242 on its owner; that owner survived,
and `test_outbox` uses the same chat id as the configured owner chat, so it read as verified.

**Fix.** When test owners have to survive, the cleanup clears their Telegram chat and its
verification, so no test inherits another's verified chat.

**Prevention.** A cleanup that has to keep a row must also reset the state later tests read
from it. The integration suite behaves differently with and without a real owner in the
database; CI is the run without one, so a CI-only failure there points at isolation first.

**Related:** F7.AC8, F10.AC13.

---

### E66 — The restore check could not restore PostGIS's own table

**Status:** Fixed before commit. **Milestone:** M7. **Date:** 2026-10-07.

**Symptom.** The first restore-check test failed: `pg_restore: error: could not execute query:
ERROR: permission denied for table spatial_ref_sys`.

**Root cause.** `spatial_ref_sys` belongs to PostGIS and is marked as an extension
configuration table, so `pg_dump` includes its data. The scratch database already has that
table, filled, from its template; and `tracelet_maint` -- rightly not a superuser -- may not
write to an extension's table. Filtering out only the `EXTENSION` entries was not enough.

**Fix.** The restore list keeps table data only for the tables the backup counted, which are
exactly the database's own (extension-owned tables are left out of the counts too). A unit
test pins the filter on a sample listing; the integration test restores a real dump.

**Prevention.** The restore check runs against a real dump in CI (`test_backups.py`), so a
new extension's data, or a new grant, fails there and not in production.

**Related:** ADR-0022, F12.AC10.

---

### E67 — A purge failed at its first batch: an autocommit connection had "begun"

**Status:** Fixed before commit. **Milestone:** M7. **Date:** 2026-10-07.

**Symptom.** The manual purge's background task died with `InvalidRequestError: This
connection has already initialized a SQLAlchemy Transaction() ... can't call begin()`, and the
test only saw `last_purge` stay empty -- the test helper had gathered the task with
`return_exceptions=True` and swallowed it.

**Root cause.** The maintenance connection was opened in `AUTOCOMMIT` so the purge could
commit batch by batch, and the advisory lock was taken with a plain `execute`. SQLAlchemy
2.0 still *autobegins* a transaction object on that first statement, whatever the isolation
level, so the first batch's explicit `begin()` collided with it.

**Fix.** The connection uses ordinary transactions; the lock is taken inside one, because a
session advisory lock outlives the transaction that takes it. `AUTOCOMMIT` is opt-in, only for
`CREATE`/`DROP DATABASE`. The test helper now re-raises a failed task.

**Prevention.** Background jobs started by a request are awaited in their tests with
exceptions re-raised; "the result never appeared" is not an assertion.

**Related:** ADR-0022.

---

### E68 — System health's flow diagram and database table were cut off

**Status:** Fixed before commit. **Milestone:** M7. **Date:** 2026-10-07.

**Symptom.** In the screenshot matrix, the inference flow diagram's last three columns
(classification, geofence, alert) were clipped at 1440 px, and the geo-database table's Update
buttons at 1024 px. The page-overflow check passed: both scrolled inside their card instead.

**Root cause.** Seven fixed-minimum columns, and a seven-column table, in a content column of
about 860 px (650 at 1024).

**Fix.** The last three steps, one node each, share one column, and the columns wrap where
they do not fit. The table merges size and checksum into one "File" column and keeps names,
versions and identifiers on one line.

**Prevention.** A clipped child is not page overflow; the matrix screenshots are read, not only
checked for a horizontal scrollbar.

**Related:** DESIGN §16 M7, UI-10.

---

### E69 — The retention tests cleared the dev database's encrypted IP addresses

**Status:** Fixed. **Data lost in the dev database, not recoverable.** **Milestone:** M7.
**Date:** 2026-10-07.

**Symptom.** Checking System health after QA, the audit log showed a *scheduled* purge at
16:03 that had cleared **164** encrypted IP addresses -- every one on the dev data's `demo-ig`
link (real ISP visits, 2 September to 3 October). The 63 remaining were set to expire after 3
days instead of 30.

**Root cause.** Two retention tests change the IP period through the API (to 5 and to 3
days), which -- correctly -- re-dates every stored IP's expiry. The fixture then "restored"
the policy by rewriting the row in SQL, which re-dates nothing. The next test,
`test_the_nightly_purge_runs_once_a_night`, ran the real nightly purge in the shared
database, and every IP older than 5 days was due. The visits, their HMACs and network
prefixes are untouched, so no analytics changed; what is lost is revealing those visits' full
addresses. No backup predates it -- M7's first ran at 16:47.

**Fix.** The fixture sets and restores the policy through `change_policy`, so the expiries are
re-dated back. The 63 surviving expiries were re-dated by hand to the 30-day policy the same
afternoon, before the live 10-minute purge reached them.

**Prevention.** A test that changes shared state through a code path with side effects
restores it through the same path, never by rewriting the row. The suite shares the dev
database (ES3); a fixture's teardown is part of the test.

**Related:** F12.AC2, F12.AC7, E52, E56.

---

### E70 — The nightly backup ran again every 15 minutes after rotation

**Status:** Fixed. **Milestone:** M7. **Date:** 2026-10-07.

**Symptom.** Two scheduled backups on one evening, 15 minutes apart.

**Root cause.** "Has tonight's backup run?" counted only `ok` and `running` rows. A manual
backup later the same day made the scheduled one no longer the day's newest, so rotation
pruned its file and marked it `pruned` -- and the next 15-minute check found nothing done.

**Fix.** `nightly_due()`: a pruned backup completed, so it counts; unit-tested on every status.

**Prevention.** The rule is a pure function with its own tests; the integration test can be
skipped when the shared database already has a backup that hour, the unit test cannot.

**Related:** ADR-0022, F12.AC9.

---

### E71 — The Tor exit list read "Update available" right after it was updated

**Status:** Fixed before commit. **Milestone:** M7. **Date:** 2026-10-07.

**Symptom.** In the browser walk of the new geo-database states, `tor-exits` showed "Update
available" both before and after a manual update that succeeded.

**Root cause.** The release check compares the vendor's `Last-Modified` with the installed
copy's `released_at`. The Tor Project republishes the list every few minutes with a new date
and, mostly, the same bytes. The installer rightly keeps the serving copy when the bytes are
identical -- and recorded nothing, so the installed date stayed old and the check found a
"newer" release every time.

**Fix.** When a download is identical to the serving copy, its `released_at` takes the new
date: the serving copy is the latest release.

**Prevention.** `test_the_same_bytes_under_a_newer_date_take_the_date` in the installer tests.

**Related:** SPEC §11 row 24, F10.AC3.

---

Add entries here as bugs are found and fixed. Use the next available `E<n>` identifier and
the same structure: symptom, root cause, fix, **prevention**.

### Template

```markdown
## E1 — <one-line symptom as observed>

**Status:** Fixed in <commit or PR>. **Milestone:** M<n>. **Date:** YYYY-MM-DD.
**Symptom:** what was actually observed, including how it was noticed.
**Root cause:** the real cause, not the first plausible one. If the initial diagnosis was
wrong, say so — that is the most useful part of the entry.
**Fix:** what changed, and why this fix rather than the alternatives.
**Prevention:** the test, constraint, invariant or CI job that stops recurrence.
**Related:** F/AC-IDs, ADRs, RISKS entries.
```
