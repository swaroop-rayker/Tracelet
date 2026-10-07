# RISKS — Tracelet

**Status:** live document. Update whenever a risk changes state or a spike reports.
Severity is `impact × likelihood` at the time of writing, reassessed after each spike.

| ID | Risk | Severity | Status |
|---|---|---|---|
| R1 | `CF-Ray` colo to metro mapping may be unstable for India | Medium | Open, spike in M3 |
| R2 | External geo APIs: undocumented limits, ToS, breakage | Medium | Open, mitigated by design |
| R3 | **rDNS city-code coverage may be too low to carry the accuracy plan** | **High** | **Spike A run 2026-09-29: 2.0 %. F4.AC13 amended (SPEC §11 row 9); M3 unblocked** |
| R4 | Geo-database update memory spike could OOM the box | Medium | Open, mitigated by design |
| R5 | `fetch(keepalive)` may not survive Instagram webview navigation | Medium | **Android: survives (Spike B, 2026-09-28). iOS: unmeasured — open** |
| R6 | 1 GB steady state under real load; swap thrash | Medium | Open, verified in M9 |
| R7 | Bot-detection ceiling without JA4 | **High** | **Accepted with a weaker substitute: header set (R19, SPEC §11 row 7)** |
| R8 | Safe Browsing may flag the site regardless | Medium | Accepted, no guaranteed remedy |
| R9 | 30–60 ground-truth labels give wide confidence intervals | Medium | Accepted, disclosed |
| R10 | Free-subdomain path is materially weaker than documented parity suggests | Medium | Accepted, owner-chosen |
| R11 | **VM loss loses everything since the last manual backup download** | **High** | **Accepted, owner-chosen** |
| R12 | Solo developer: no review, no bus factor | Medium | Accepted, mitigated by CI and docs |
| R13 | GeoLite2 licence terms and account continuity | Low | Open, monitor |
| R14 | CARTO basemap tile availability and usage policy | Low | **Materialised 2026-10-02 (CARTO now requires a key); closed by ADR-0017: no tiles** |
| R15 | Nominatim usage policy compliance | Low | Mitigated by design |
| R16 | GCP free-tier egress ceiling (1 GB/month) | Low | Open, monitor |
| R17 | Fingerprint instability inflates unique-visitor counts | Medium | Accepted, disclosed |
| R18 | Telegram becomes a security dependency, not just a notifier | Medium | Accepted, mitigated |
| R19 | **Header order and HTTP/2 detail are unobservable behind Caddy** | **High** | **Closed: header set + re-weighting built in M4 (SPEC §11 row 7)** |
| R20 | A first-visit location prompt cannot be answered inside the interstitial | Medium | **Revisited 2026-10-06 — per-link opt-in, 15 s ceiling (ADR-0021)** |
| R21 | **A JavaScript-executing Meta scanner passes the crawler gate** | **High** | **Closed in M4: classified `datacenter`, never `human` (test fixture)** |
| R22 | **Registry-artifact collapse to admin1 still emits the artifact's state** | **High** | **Closed 2026-09-29: collapse to country (SPEC §11 row 11)** |
| R23 | S10 latency triangulation cannot be built without third-party requests | Medium | **Closed 2026-09-29: S10 dropped (SPEC §11 row 12)** |
| R24 | Rule (a) cannot tell a regional ISP from a registry collapse | Medium | **Accepted for M3 — tune in M8 with ground truth** |
| R25 | **The raw analytics fallback is slow at the design load** | Medium | **Open — rollup path measured at p95 ≤ 70 ms; raw fallback up to 10 s on long windows** |
| R26 | Geofence drawing (M6) has no street-level basemap | Medium | **Closed 2026-10-06 — accepted: no basemap, region geofences (ADR-0020)** |
| R27 | The UI redesign (M5.5) regresses accessibility, the CSP or behaviour | Medium | **Mitigated 2026-10-03 — 0 CSP violations in Chromium, Firefox and WebKit (after E44), filters unchanged, a11y sweep clean, 72-image matrix; hand keyboard pass by the owner 2026-10-07, nothing failed** |

---

## R3 — rDNS city-code coverage · **HIGH** · blocks M3

**Risk.** The city-accuracy plan leans heavily on source S6: Indian residential IPs having
PTR records that encode a metro code (`blr`, `mum`, `hyd`, `maa`, `pnq`,
`abts-kk-static-*`). S6 is the highest-weighted non-database source and the primary
corroborator that allows registry-artifact suppression to *not* fire. **If coverage is low,
the F4.AC13 city targets are unreachable and suppression will collapse most visits to
admin1.**

**Why it matters more than it looks.** Suppression rule (a) requires corroboration from a
non-database source. The only three are S1 (consented GPS, unavailable most of the time),
S6 (rDNS) and S8 (Cloudflare colo, absent on the free-subdomain path). If S6 is thin, city
coverage depends almost entirely on S8 — and R10 then compounds it.

**Spike A — run during M0/M1, before M3 is written.**
Sample several hundred Indian residential IPs across Airtel, Jio, ACT, BSNL and Vi. Resolve
PTR. Measure: what fraction have a PTR at all, what fraction bear a recognisable metro
code, and per-ISP breakdown. Half a day.

**Decision rule, set in advance so the result is not rationalised:**

| Coverage | Action |
|---|---|
| Above 50 % | Proceed as specified |
| 30–50 % | Proceed, but lower the F4.AC13 city coverage target to match, via a SPEC section 11 amendment |
| **Below 30 %** | **Amend F4.AC13 before building M3.** Shift city expectation onto S8 + consented GPS, and be explicit that non-consented non-Cloudflare visits will usually abstain at city |

**Result (2026-09-29): 2.0 % — below the 30 % line. Decision rule outcome: amend F4.AC13
before building M3.**

*Method.* Script and raw results: `api/spikes/spike_a_rdns.py`, `api/spikes/spike_a_result_2026-09-29.json`. Announced prefixes for each ISP fetched from RIPEstat on the day (Airtel AS24560
+ AS45609, Jio AS55836, ACT AS24309 + AS18209, BSNL AS9829, Vi AS38266 + AS45271 +
AS55410). Per ISP, 150 random IPv4 addresses — at most one per /24, /24s drawn uniformly
over the announced space — and 30 random IPv6 addresses. Seed 20260929. PTR resolved
through Google and Cloudflare public DNS. A city-code lexicon of ~150 Indian IATA codes,
city names and common abbreviations, and a separate telecom-circle/state lexicon, were
**written into the script before the first lookup**. The first run was discarded: it
reported 0 % everywhere because Docker Desktop's resolver does not answer PTR at all
(ERRORS.md E27), which a control lookup of 8.8.8.8 exposed.

| ISP (IPv4, n = 150 each) | Has a PTR | City code | Circle/state only |
|---|---|---|---|
| Airtel | 34.7 % | 0.7 % | 30.7 % (`north`, `tn`, `kk`, `ap`, `mp`) |
| Jio | 3.3 % — all Akamai cache nodes, not subscribers | 0 % | 0 % |
| ACT | 99.3 % — encode the address only (`49.204.83.225.actcorp.in`) | 0 % | 0 % |
| BSNL | 15.3 % | 9.3 % — mostly MTNL `triband-del-` / `-mum-` | 0 % |
| Vi | 0 % | 0 % | 0 % |
| **Pooled** | **30.5 %** | **2.0 %** | 6.1 % |

IPv6: **0 PTR records in 150** lookups, every ISP.

*Would a better lexicon change it?* No. The only unmatched place-like tokens were three
BSNL exchange-area codes (`ktm`, `gya`, `mld`); counting them moves pooled coverage from
2.0 % to about 2.4 %.

*Bias, stated.* Uniform over announced address space is not uniform over visitors. The
visitor mix for an India-primary audience is dominated by Jio and Vi mobile — the two ISPs
at 0 % — and increasingly by IPv6, also 0 %. A visitor-weighted figure would be **lower**,
not higher. Airtel's circle codes are real **admin1-level** evidence for about a third of
Airtel fixed-line addresses, but `north` is a zone spanning several states.

**What it means.** S6 cannot be the corroborator that keeps suppression rule F4.AC12(a)
from firing. Combined with SPEC section 11 row 8 (consented location will be rare) and
R10 (no S8 on the free-subdomain path), a non-consented visit will usually have **no
non-database corroboration for city at all**. The F4.AC13 city target of ≥ 50 % strict
coverage is unreachable as designed. **Decision, 2026-09-29 (repository owner):** keep city strict
precision ≥ 95 %, drop the coverage floor until M8 sets one from ground truth, and report
coverage per path. SPEC section 11 row 9.

---

## R5 — Instagram webview enrichment survival · MEDIUM (was HIGH) · Android closed, iOS open

**Risk.** ADR-0004 uses `fetch(url, {keepalive: true})` rather than `sendBeacon` on the
assumption of better survival across navigation inside in-app WebViews. **If it does not
survive, `stage='server_only'` becomes the primary path for social traffic, not a
fallback** — which means no client signals at all for the largest visitor segment: no GPU,
no screen, no fingerprint, no consented geolocation, no headless detection.

**This does not break the system** — that is precisely what ADR-0004 was designed for. But
it changes what the data looks like, and it changes which milestones matter.

**Spike B — needs a real phone and a real Instagram bio link.** Cannot be answered from a
desktop emulator; the WebView behaviour is what is under test. Test both iOS and Android
Instagram, and ideally LinkedIn and Reddit for comparison.

**If it fails:**
1. Extend the interstitial for detected webviews (still within the 1500 ms cap) to give the
   request more time to complete *before* navigation.
2. Try a synchronous-ish variant: fire enrichment on `visibilitychange` as well as inline.
3. Accept `server_only` dominance and **lean harder on S8 `CF-Ray` colo** — the one
   metro-level signal that is entirely client-independent. This makes the purchased-domain
   path effectively mandatory for useful social-traffic geolocation, which escalates R10.
4. Record the observed enrichment rate per `webview_host` so F9.AC20 stage-mix reporting is
   honest about it.

**Result — run 2026-09-28/29 inside M2, against the real capture page through a temporary
Cloudflare quick tunnel. Android only.**

| Client | Stage | Enrichment arrived after |
|---|---|---|
| Instagram in-app browser, Samsung SM-S721B, Android 16 (referrer `l.instagram.com`) | **enriched** | 2.4 s |
| Chrome 153, same phone | **enriched** | 3.8 s |
| Chrome 154, Windows desktop | **enriched** | 1.1 s |
| Chrome, same phone, **JavaScript disabled** | `server_only` (swept) | — |
| 4 × `facebookexternalhit` link previews | `crawler`, then `server_only` | — |

**`fetch(keepalive)` survives navigation in the Android Instagram webview.** The
enrichment was received ~1.7 s *after* the 700 ms redirect had fired, so it completed in
flight after the page was gone — the property under test. The full client payload arrived:
screen, DPR, GPU, cores, memory, languages, timezone. The JavaScript-disabled visit was
recorded and redirected by the `<noscript>` refresh (visitor-confirmed; the destination is
off-site, so the server cannot observe the landing).

**Not measured: iOS.** No iPhone was available. iOS Instagram uses WKWebView, whose
keepalive behaviour differs from Android's Chromium WebView, so the Android result does not
transfer. **This risk stays open for iOS**, now at Medium: the failure mode is known and
harmless (`server_only`), and the stage mix per `webview_host` (point 4 above) will show
the iOS rate from real traffic once deployed. None of the "if it fails" responses is
needed for Android.

A single run on one device is evidence, not a rate. F9.AC20's stage-mix reporting is where
the rate gets measured.

**Also found:** a JavaScript-executing Meta scanner passes the crawler gate — R21.

---

## R11 — VM loss loses everything since the last manual download · **HIGH** · accepted

**Risk.** Gate 1 declined a cloud-storage account, so there is no automated off-VM backup
destination. Off-VM copies are manual downloads from the dashboard (F12.AC11, ADR-0014).
A VM loss — accidental deletion, account suspension, disk failure, region incident —
destroys everything since the owner last remembered to download.

**Compounding factor, which is the part that is easy to miss.** The IP encryption key and
the three HMAC peppers are **deliberately not in the backup** (ADR-0007, ADR-0014). If they
are not separately preserved out-of-band, then even a successful restore leaves every
`ip_enc` value permanently unreadable and breaks all historical `visitor_id` linkage.
**A backup without those secrets is a partial backup.**

**Mitigations in place:** dashboard reminder when the last download exceeds a configurable
age; a one-time M9 restore drill onto a fresh VM that explicitly includes the out-of-band
secrets; backups are safe to store anywhere because IP values are ciphertext.

**Retirement path.** Adding a free-tier object-storage target (Cloudflare R2, Backblaze B2)
is a small integration and **the single highest-value change available in this document.**
It needs one owner decision to reverse.

---

## R1 — `CF-Ray` colo to metro mapping stability

Cloudflare routes by network topology, not geography. An Indian visitor may be served from
BLR, BOM, or occasionally further, and assignment can shift with peering changes. So S8 is
a **regional** hint, not a city fix.

**Mitigation.** Treat colo as an `admin1`-level candidate with a metro *bias*, never as a
city assertion. Its main job is **corroboration** — allowing suppression rule (a) to stand
down — not primary city derivation. Weight and mapping live in versioned config.

**Spike, during M3.** Collect colo values from the owner devices across ISPs and over time,
and measure assignment stability. **Result:** _not yet run._

---

## R2 — External geo APIs: undocumented limits, ToS, breakage

ipwho.is and ip-api.com are used without keys. Limits are undocumented or informal, terms
can change, and endpoints can disappear. Anything relied upon that we do not pay for and
have no agreement with is, by definition, unreliable.

**Mitigation, already in the design:** per-source toggles (F4.AC7); cache by /24 prefix so
repeat visitors cost nothing; hard timeouts; circuit breakers; a source failure degrades
silently to the remaining sources; outbound budgets (F11.AC7) so a traffic spike cannot
trigger a ban. **Consequence:** accuracy must never depend on any single external source,
which the consensus design already enforces.

**Checked in M3 (2026-09-29), and one service dropped.** ip-api.com's free endpoint is
**HTTP only** — its docs: "256-bit SSL encryption is not available for this free API" —
and licensed for non-commercial use only. It would send a visitor's network address in
plaintext and fails F4.AC5's own "HTTPS", so S9 does not use it. **ipwho.is** is HTTPS,
needs no key, allows commercial use, and publishes its limit: 1 000 requests a day per
client address. The budget is set at 900/day; with the /24 cache and ~500 visits a day it
is not approached. The lookup is made for the prefix's *network address*, not the
visitor's, so the third party learns the network only. Nominatim's policy was checked
too: 1 req/s absolute, **4/min for anything on a schedule**, identifying User-Agent,
caching, ODbL attribution — the 4/min budget governs, the User-Agent is sent on every
request, and the privacy page carries the attribution.

---

## R4 — Geo-database update memory spike

A database update downloads, decompresses and validates roughly 150 MB against roughly
379 MB of headroom. **A failed update must never take down the capture endpoint.**

**Mitigation:** stream to disk, never into memory; validate in a **memory-capped
subprocess**; atomic symlink swap so a failure leaves the previous version serving; schedule
off-peak; hard `mem_limit` on the `api` container so worst case is one container restart
rather than an OOM-killed PostgreSQL. Tested explicitly in M3 with a deliberately corrupted
download.

---

## R6 — 1 GB steady state under real load

Budget says roughly 645 MB steady with roughly 379 MB headroom (ARCHITECTURE section 6).
That is a projection. Real-world drift, connection-pool growth, fragmentation and swap
behaviour are unmeasured until M9. **2 GB of swap on a network-attached disk is a cliff, not
a cushion** — once swapping starts, latency degrades sharply.

**Mitigation:** hard per-container limits; L2 request shedding must engage before the kernel
swaps (F15.AC6); Argon2 pinned; no numpy/scipy/pandas; every new dependency states its RSS
before merge. **Escalation order if breached:** reduce to one Uvicorn worker (roughly
110 MB), then lower `shared_buffers`, then revisit the host — **not** the database.

---

## R7 — Bot-detection ceiling without JA4

JA3/JA4 TLS fingerprinting is the strongest available bot signal because it fingerprints the
TLS library, which automation cannot easily disguise. Caddy does not expose the handshake
without a plugin, and terminating TLS elsewhere adds infrastructure. **Deferred out of v1**
(SPEC amendment 5, ADR-0011).

**Consequence, stated plainly:** a patched Chromium spoofing consistently across UA, UA-CH,
WebGL, screen, timezone and header order, from a residential proxy, **will be classified
`human`**. This engine raises cost; it does not stop a determined adversary.

**Substitute shipped:** header-order and HTTP/2 fingerprinting (F5.AC8) — cheap, no extra
infrastructure, and effective against unmodified HTTP libraries, which is the large majority
of real automation. **Revisit** if a Caddy JA4 plugin reaches acceptable maturity.

> **Update, 2026-09-28 (M2): the substitute is not available either.** Header order and
> HTTP/2 frame detail are both discarded by Go's HTTP server before any Caddy handler runs,
> so neither reaches the application. The mitigation this risk was accepted on does not
> exist. See R19.

---

## R8 — Safe Browsing may flag the site regardless

B4 has structural causes we fix (no open redirect, valid TLS, headers, no deceptive
patterns — F13.AC3, F13.AC8) and a reputational cause we cannot: a new domain has no
history, and a redirect service is an inherently suspicious category.

**There is no guaranteed remedy.** A review request is the only lever, and reviews can be
declined.

**Compounding:** the free-subdomain path (R10) starts from *inherited* reputation damage —
DuckDNS and sslip.io subdomains are widely flagged already, and some corporate and mobile
networks block them outright.

**Record the M9 outcome here whatever it is**, including a negative result, because a
negative result changes how the link can be used.

---

## R9 — Small ground-truth set, wide confidence intervals

F4.AC13 targets will be measured against 30 to 60 owner-labelled visits. A 95 %
precision figure over 40 labels has a confidence interval wide enough to contain 87 % and
99 %. **Any accuracy number Tracelet reports about itself is an estimate with a wide
interval**, and stating it as a point value would be misleading.

**Mitigation:** `accuracy_runs.label_count` is stored beside every metric, the API returns
it with every response, and the dashboard displays it next to every figure (F9.AC10,
API section 11). Labelling candidates are ranked by `conflict_score` so scarce labels are
spent where they are most informative.

---

## R10 — The free-subdomain path is weaker than "both supported" suggests

Gate 1 asked for both domain paths. They are built, config-switched, and **not
equivalent**. The free-subdomain path cannot sit behind Cloudflare, so it forfeits:

- Edge TLS — TTFB roughly 1000 ms instead of roughly 300 ms (NFR2)
- **The `CF-Ray` colo signal (S8)** — measurably lower city coverage, and this compounds R3
- `CF-IPCountry`
- L0 DDoS absorption, on a box that 1 vCPU makes trivially floodable
- Origin IP hiding
- and it retains the B4 reputation problem

**Accepted as an owner choice.** Documented in ADR-0012, KICKOFF section 3, and here so it
cannot quietly be treated as a like-for-like option.

---

## R12 — Solo developer

No code review, no second pair of eyes on a security decision, no bus factor.

**Mitigation:** CI is the reviewer that cannot be skipped — `mypy --strict`, lint, real-DB
integration tests, drift checks, route-name assertion, accuracy regression. Documentation is
written for a stranger, because in six months that is who the owner will be. ADRs record
*why*, which is the thing that is lost first. `docs/private/` carries the mental model.

---

## R13 — GeoLite2 licence and account continuity

MaxMind has tightened GeoLite2 access before: it now requires an account, a licence key,
and acceptance of terms, and access has changed on relatively short notice historically.

**Mitigation:** four independent database sources, so losing one degrades rather than
breaks. DB-IP Lite needs no signup at all and is the fallback baseline. Attribution
obligations (DB-IP Lite and GeoNames are CC-BY) are met on the privacy page — **a licence
breach is a real risk, not a formality** (F2.AC13).

---

## R19 — Header order and HTTP/2 detail are unobservable behind Caddy · **HIGH**

**Found in M2, by measurement.** A request was sent through Caddy with headers in the order
`Zzz-Last`, `Aaa-First`, `Mmm-Middle`. The application received `host`, `user-agent`, then
every other header **alphabetically**.

**Root cause.** Caddy is written in Go. Go's HTTP server parses request headers into a map
before any handler runs — the order is gone at that point — and the reverse proxy writes
them back out sorted. HTTP/2 SETTINGS, window updates and pseudo-header order, the other
half of the "HTTP/2 fingerprinting" signal, are consumed by the same server and never
exposed to a handler either. Neither is configuration; both are how the server works.

**What it breaks.** SPEC F5.AC8 as amended (section 11, row 5) substitutes header-order and
HTTP/2 fingerprinting for JA4, and **R7 was accepted on the strength of that substitute.**
Neither is implementable in this stack as designed. A hash computed in the application
would be identical for every client with the same header *set* — a value that looks
exactly like a fingerprint and carries none of the information — so M2 stores nothing in
`visits.header_order_hash` rather than store that.

**Options, for a decision before M4:**

1. **Accept and re-weight.** Drop the header-order signal; lean on UA/UA-CH consistency,
   client-hint cross-checks, headless probes, honeypots and network reputation. Honest,
   free, and the ceiling in R7 gets lower.
2. **Header *set* instead of order.** Which headers are present, and their values, survive
   Caddy intact and are already stored. Weaker than order — a careful client copies the
   set — but it catches unmodified HTTP libraries, which omit what browsers always send.
   Implementable in M4 at no infrastructure cost.
3. **Observe below Go's HTTP server.** A custom Caddy listener wrapper that records the raw
   bytes before parsing, or a TLS-terminating proxy that exposes JA4. Real engineering,
   another component, and memory on a 1 GB box.
4. **Cloudflare Bot Management** exposes JA3/JA4 — not on the free plan.

**Recommendation:** option 2, with option 1's re-weighting, and amend F5.AC8 to describe
it. That needs the owner's approval (CLAUDE.md section 2), so it is recorded here and not
applied.

**Decision, 2026-09-29 (repository owner):** option 2 with option 1's re-weighting. F5.AC8
and F3.AC1 amended (SPEC section 11 row 7). Implemented in M4; until then nothing reads
the header set as a signal. R7's ceiling is correspondingly lower.

---

## R20 — A first-visit location prompt cannot be answered inside the interstitial · MEDIUM

**Found in M2, while writing the capture script.** Two requirements pull against each
other:

* **F4.AC1** — the page requests geolocation permission.
* **F2.AC5** — a hard timer redirects at `interstitial_ms`, default 700 ms, **cap 1500 ms**.

On a first visit the browser shows a permission prompt, and the page navigates away 700 ms
later — before most people have read it, let alone answered. So consented GPS will almost
never be captured on a first visit, and the visitor briefly sees a prompt that vanishes
under them.

**What M2 does.** It asks, as F4.AC1 requires, and sends the enrichment shortly before the
redirect with whatever answer exists. An unanswered prompt is recorded as
`consent_state='unavailable'` with the reason `timeout` in `signals` — not as `denied`,
because nobody said no, and not as `not_asked`, because it was asked. Permission a visitor
granted on an earlier visit resolves in milliseconds and is captured normally.

**Options, for a decision after Spike B measures it:**

1. **Accept.** Consented location becomes a returning-visitor signal. Honest, no change.
2. **Only use permission already granted** — query the Permissions API and never show a
   prompt that cannot be answered. Better experience; F4.AC1 would need amending.
3. **Hold the redirect while a prompt is open** — violates F2.AC5, and holds the visitor
   on telemetry, which is what F2.AC5 exists to prevent.

**Recommendation:** decide with Spike B's real-phone data in hand. Option 3 is not
recommended.

**Data from Spike B (2026-09-28/29).** All three real browsers — Instagram webview,
Android Chrome, desktop Chrome — recorded the prompt as `timeout`. Not one visitor could
answer it. The prediction holds: with a 700 ms interstitial, first-visit consented location
is effectively never captured, and every such visitor sees a prompt vanish under them.

**Recommendation, updated with the data:** option 2 — query the Permissions API and ask
only where permission is already granted — which requires amending F4.AC1. Owner decision;
not applied.

**Decision, 2026-09-29 (repository owner):** option 2. F4.AC1 amended (SPEC section 11
row 8) and applied on the M3 branch: the capture page consults the Permissions API, reads a
position only where permission is already `granted`, records `denied` as `denied`, and
records an undecided permission as `consent_state='not_asked'`. It never prompts.
**Accepted consequence:** consented (S1) location will be rare, since nothing on this
origin ever asks.


**Revisited 2026-10-06 (owner, ADR-0021, SPEC §11 row 19).** Option 3, holding the redirect,
is now adopted **per link and bounded**: a link with `ask_location` shows consent text and the
prompt, and waits for the answer for at most 15 s, with Continue always available. Links that
do not opt in keep option 2. What stays open: how long in-app browsers hold an asking visitor
before reporting a refusal. Measure it on the first real asking links.
---

## R21 — A JavaScript-executing Meta scanner passes the crawler gate · **HIGH**

**Found in Spike B, 2026-09-28.** Eight seconds after a link was shared, a client arrived
that **ran the capture script and enriched**, and was classified `unknown`:

* page request User-Agent `Dalvik/2.1.0 (Linux; U; Android 12; …) [FBAN/FB4A;…;FBLC/ar_EG…]`
  — the Android system HTTP library's string, not a WebView's, which a real in-app browser
  does not send for a page load;
* screen 2000×2000 at DPR 1, **52 CPU cores**, no WebGL renderer;
* timezone `America/Los_Angeles` against an `ar_EG` app locale;
* geolocation `denied` instantly, where every human visit timed out;
* referrer `https://www.facebook.com/`, in the same burst as four `facebookexternalhit`
  fetches.

This is almost certainly Meta's link-safety scanner rendering the page in a sandbox. M2's
crawler gate is a User-Agent needle list and does not match it; the page correctly served
it the same content as everyone else (no cloaking).

**Why it is High.** Under invariant 6, Telegram alerts fire only for `human`. Unless M4
classifies this client as `bot`, it would produce a "visitor" alert every time a link is
shared on a Meta surface — with a fabricated location from a US timezone — and count as a
visitor in analytics.

**Requirement on M4** (no M2 rule added, deliberately — a one-off needle is the wrong
layer): the classifier must mark this visit `bot`, as a regression fixture, from signals it
already has: a non-browser UA that executes script; UA-claimed OS vs. client-reported
hardware; timezone vs. locale; missing WebGL renderer; implausible core count; the
instant-denial pattern; and temporal clustering with a same-network link-preview burst.
Spike B's row is the first labelled example.


**Closed in M4 (2026-10-02).** The scanner comes from Meta's own network (AS32934), a
hosting ASN, so it classifies `datacenter` before any score is needed; its 2000x2000
"phone" screen and 52 cores would also fire `xcheck.screen_device_class` and
`xcheck.cores_device_class`. `test_the_meta_scanner_is_never_human` pins it.
---

## R22 — Registry-artifact collapse to admin1 still emits the artifact's state · **HIGH**

**Found in M3, while writing suppression rule (a).** F4.AC12(a) says an uncorroborated
city that sits on its ASN's registry centroid "collapses to admin1". B1 — the bug that
rule exists to fix — is a Bangalore visitor recorded as **Faridabad or Noida**. Faridabad
is in **Haryana**, Noida in **Uttar Pradesh**; the visitor is in **Karnataka**. The
databases that placed the city on the registry address placed the *state* there too, so
collapsing to admin1 turns "wrong city" into "strict, wrong state" — which B2 names as the
error that is not tolerable, and which CLAUDE.md invariant 5 forbids.

**Measured in the engine, not argued:** with the collapse at admin1, four databases
agreeing on Faridabad with Airtel's centroid there yielded strict `admin1 = Haryana`
(the test that showed it was replaced, after the decision, by
`test_the_artifact_state_is_voided_with_the_city`).

**What M3 does.** The collapse depth is versioned configuration,
`registry_artifact.collapse_to`, defaulting to the SPEC's `admin1`. Setting it to
`country` abstains on admin1 as well, with reason `registry_artifact`. Nothing else changes.

**Options, for the owner:**
1. **Collapse to country** when the artifact state is also uncorroborated. Recommended: it
   is the only setting under which B1's own example emits nothing false.
2. **Keep admin1** as written, accepting a strict wrong state for B1-shaped visits.
3. **Collapse to admin1 only when the artifact's admin1 differs from `modal_admin1`** —
   impossible by construction here, since the artifact city and state come from the same
   registry record; listed so it is not proposed later as a fix.

$1

**Decision, 2026-09-29 (repository owner):** option 1. F4.AC12(a) amended (SPEC section 11
row 11); the configuration knob is removed rather than kept, so no setting can reintroduce
a strict wrong state. Engine revision bumped to `m3.2`.

---

## R23 — S10 latency triangulation cannot be built without third-party requests · MEDIUM

**Found in M3.** Triangulating by latency means timing round trips from the visitor to
several endpoints in known places. This server is in one place, so the endpoints would be
other people's — and F2.AC12 says the capture page issues **no third-party request**, for
the reasons B6 records: content blockers kill them, and each one tells another company
that this visitor exists. Measuring one server's RTT alone yields a distance, not a
location, and TCP RTT through Cloudflare measures the edge, not the visitor.

**What M3 does.** S10 exists as a source, disabled by default as F4.AC8 requires; enabled,
it reports `unavailable` with reason `conflicts_with_f2_ac12` rather than pretending.

**Options, for the owner:**
1. **Drop S10** — amend F4.AC5/F4.AC8 and RW-6. S8 already carries most of its value
   (RW-6 says so), and Spike D can be retired.
2. **Allow a narrow exception to F2.AC12** for S10's probes when the flag is on — at the
   cost B6 describes.
3. **Keep it as a documented stub** until a design without third parties exists.

**Recommendation:** option 1. Not applied.

**Decision, 2026-09-29 (repository owner):** option 1. S10 dropped (SPEC section 11 row 12);
Spike D retired. The `latency` value stays in the `inference_source` enum, unused, because
removing a PostgreSQL enum value costs a table rewrite for nothing.

---

## R24 — Rule (a) cannot tell a regional ISP from a registry collapse · MEDIUM

**Measured in M3 on the installed databases.** `modal_share` is the fraction of an ASN's
address space the databases put on one point. A registry collapse produces a high share —
Tikona 44 % on Delhi, Jio 40 % on Mumbai. But so does an ISP that genuinely serves one
city: ACT's Hyderabad network (AS18209) sat at 31 % on Hyderabad with DB-IP alone, just
over the 0.30 threshold, and a Hyderabad visitor on it would have had a probably-correct
city withheld. From database data alone the two are indistinguishable.

**Why it is accepted for now.** The error is in the safe direction: a false collapse is an
*abstention* (coverage), never a wrong strict value (precision) — ADR-0005's premise.
With GeoLite2 added, AS18209 fell to 24 %, under the line. On the Cloudflare path an
independent colo (S8) corroborates the city and the rule does not fire.

**What would settle it.** Ground truth (F4.AC15, M8): per-ASN precision of the database
city for labelled visits, which tells a regional ISP (right) from a collapse (wrong)
directly. `registry_artifact.min_modal_share` is versioned configuration, so the
re-tune is a settings version, not a deployment.

---

## R25 — The raw analytics fallback is slow at the design load · MEDIUM

**Measured in M5 (2026-10-02).** 90 k visits over 180 days, API and database each capped
to one CPU, 20 requests per endpoint inside the compose network. **From rollups, every
endpoint's p95 is under 70 ms** -- NFR2.AC4 (300 ms) holds with room to spare. **From raw
rows** -- forced by any filter that is not a rollup dimension, such as ASN, city or
`has_gps` -- most endpoints stay under 220 ms, but three do not over a 30-day window or
longer: summary 4.3 s, source flow 0.9 s, and a 365-day calendar 10 s.

**Why.** Visit rows are wide (request headers, signals and probes as JSON; about one heap
page per row at this load) and the 197 MB table does not fit in 96 MB of shared buffers,
so a raw scan is bound by reading pages. Unique visitors -- the one figure always raw --
was fixed with a covering index (an index-only scan, 9 ms); the general case cannot be,
short of indexing most of the table.

**Why it is acceptable now.** The dashboard's default views are rollup-served. A raw
answer is correct, says `computed_from: raw` on the panel, and shows its loading state
while it runs (F9.AC18). Traffic today is a fraction of the design load.

**What would close it.** ADR-0016's revisit trigger has fired for these filters: add the
filters admins actually use to the rollups (ASN is the likely first), or narrow the raw
path's window for the slow endpoints. Decide from real usage in M9, not by guessing now.

---

## R26 — Geofence drawing has no street-level basemap · MEDIUM

**Found in M5 (2026-10-02).** ADR-0003 planned to draw geofences (F6.AC1) over CARTO tiles.
CARTO now requires a key (R14), and ADR-0017 replaced tiles with self-hosted country and
state outlines -- enough for a choropleth, not for drawing a boundary around a
neighbourhood, a campus or a building, which needs streets.

**Options for M6:** OpenStreetMap's tile server (no key; requires a Referer, so OSM learns
the dashboard's hostname; light style only), a provider key (reopens C6), or drawing against
outlines and coordinates only (honest, but hard to use). Decide before building the canvas,
with an ADR.

**Closed 2026-10-06, accepted (ADR-0020).** No basemap. The editor draws over the outlines,
with typed coordinates and GeoJSON import for precise shapes, and adds **region geofences**
(countries and states) matched on the strict country and state. Street-level drawing is not
offered. Reopen if a keyless, Referer-free basemap appears.

---

## R27 — The UI redesign regresses accessibility, the CSP or behaviour · MEDIUM

**Found 2026-10-02**, planning M5.5 (docs/DESIGN.md). A redesign that touches every screen can
quietly break three things the current UI gets right:

- **Accessibility (NFR7):** custom menus, popovers and a command palette replace native controls
  that were keyboard-correct for free.
- **The CSP (F13.AC2):** overlay libraries and style helpers commonly inject `<style>` or inline
  `style=""`, which `style-src 'self'` blocks. The page then *looks* fine in development and
  breaks behind Caddy.
- **Behaviour:** the filter bar's URL round-trip and the Panel's four states are easy to lose
  while restyling.

**Mitigations (DESIGN §11):** native `dialog` and `popover` first (ADR-0019); a Phase 0 CSP spike
in three engines before any primitive; contrast tests extended to every new token pair;
`filters.test.ts` and `Panel.test.tsx` must stay green unchanged in intent; a mouse-free
keyboard walkthrough; a CSP-violation listener over every page on the Caddy-served build; and a
72-image screenshot matrix in the PR.

**Measured 2026-10-03 (M5.5 phase 5).** The production build under Caddy's exact CSP header:
zero `securitypolicyviolation` events over every page in all three themes and every overlay,
the visit drawer, the chart tools and the map (Chromium). `filters.test.ts` unchanged and green;
every filter editor wrote the M5 URL keys in the browser. An automated sweep found one `h1` per
page, no skipped heading levels, and no unnamed control. Three real regressions were found and
fixed on the way (ERRORS E41–E43).

**Re-measured 2026-10-03 with Playwright, in Chromium, Firefox and WebKit, against Caddy
itself.** The listener was registered before page scripts ran, and positive controls proved it
worked (ADR-0019 "Spike results"). This run showed the Chromium zero above was **wrong**: zod's
`eval` probe was reported on every page load in all three engines, and the earlier listener had
been attached too late to see it (E44). After the fix: **0 violations in all three engines.**
The same run found three more regressions, all fixed: a chart's data table pushing the chart
over its neighbour (E45), ECharts overwriting every chart's accessible name (E46), and two pages
wider than the screen (E47). The 72-image matrix was captured with an overflow check on every
image.

**Hand keyboard pass, 2026-10-07.** The owner tabbed through every page, menu, dialog, drawer,
filter editor, popover and the command palette, mouse-free, and tried the shortcuts: focus
always visible, a sensible order, no trap, every control operable, focus returned on close.
Nothing failed. The last open item of M5.5 is closed; the risk stays monitored, because every
new overlay must keep UI-11.

---

## R14 · R15 · R16 · R17 · R18 — lower severity, monitored

**R14 — CARTO basemap.** Free raster tiles with no API key, subject to a usage policy. Two
admins is trivially within limits. If they change terms, the fallback is OSM raster tiles or
a MapTiler key, the latter reopening the C6 signup question.
**Materialised 2026-10-02:** every tile now reads "API KEY REQUIRED". Closed by ADR-0017 --
the analytics map draws self-hosted outlines and uses no tiles at all. What it leaves open
for M6 is R26.

**R15 — Nominatim usage policy.** Requires ≤1 request per second and a descriptive
User-Agent. Enforced by outbound rate limiting (F11.AC7), caching, and consented-visits-only
usage. Breaching it risks a block and is simply poor citizenship.

**R16 — GCP egress.** Free tier includes 1 GB/month from North America. 500 small visits a
day plus dashboard use fits comfortably. Since ADR-0017 the map's outlines come from the VM
(about 0.3 MB gzipped per first map view, browser-cached), which is negligible at two
admins. Monitored on the System Health page.

**R17 — Fingerprint instability.** A browser update or new monitor can mint a new
`visitor_id` for the same person, inflating unique-visitor counts and occasionally producing
a duplicate first-visit alert. Bucketing tolerance reduces this (ADR-0006); it cannot
eliminate it. Unique-visitor figures are presented as estimates.

**R18 — Telegram as a security dependency.** Because Gate 1 declined email, Telegram is both
the notification channel *and* a password-recovery channel (ADR-0008). Compromise of the
owner Telegram account enables a password reset. **Mitigation:** the reset flow does **not**
bypass TOTP, and recovery codes plus the CLI provide two independent paths that do not
involve Telegram at all.

**Live as of M1**, with three additions from building it:

- **A chat is only trusted once verified.** A six-digit code is sent to the proposed chat
  and must be typed back before any reset link will go there (F8.AC7). An unverified chat id
  means reset links arrive somewhere that may not be yours, and you would only discover it
  at the moment you needed it — so the verification is not a formality.
- **TOTP survives a reset**, and an integration test asserts it. If a Telegram compromise
  alone granted a session, Telegram would in effect be the only credential.
- **The bot token is a credential of the same weight as a password.** It lives only in
  `.env` (gitignored) and is redacted from every log line and exception message — Telegram
  puts it in the request path, so an httpx error would otherwise print it verbatim under a
  non-sensitive key where the log redactor cannot reach. Rotate it in @BotFather (`/revoke`,
  then `/token`) after any session in which it was handled in plaintext.

---

## Spike log

| Spike | Question | Blocks | Run by | Result |
|---|---|---|---|---|
| **A** | Indian residential rDNS metro-code coverage | M3 | M0/M1 | _pending_ |
| **B** | `fetch(keepalive)` survival in the Instagram webview | M2 | M0/M1 | _pending_ |
| C | `CF-Ray` colo assignment stability for India | — | M3 | _pending_ |
| D | Latency-triangulation accuracy contribution over S8 | RW-6 decision | M8 | **Retired 2026-09-29** — S10 dropped before it could be measured (SPEC §11 row 12, R23) |
| E | Load behaviour and swap pressure at NFR1 on real hardware | NFR1, NFR6 | M9 | _pending_ |

Record every result here, **including negative ones.** A spike that reports "this does not
work" has done its job and saved the milestone that would have assumed otherwise.
