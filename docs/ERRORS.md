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

**Status:** Diagnosed, fix designed. Verified in **M2**. Confirm with **Spike B** first.
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

**Status:** Diagnosed, fix designed. Verified in **M2**.
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
