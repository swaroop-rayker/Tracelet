# ADR-0004 — Server-authoritative capture with additive client enrichment

**Status:** Accepted (Gate 2, 2026-09-25)
**Deciders:** repository owner
**Relates to:** F2, B3, B4, B5, B6, NFR2, NFR3

---

## Context

Four of the six reported bugs converge on this one decision, which is why it is recorded
separately rather than folded into ADR-0001.

**B3 — the Instagram webview captures nothing and just redirects.** Four distinct causes
stack up:

1. Instagram in-app WebView suppresses or ignores the Geolocation and Permissions APIs.
2. Instagram prefetches shared links with `facebookexternalhit` before a human ever taps
   them, so a naive implementation records the crawler and may treat the human as a repeat
   visitor.
3. `navigator.sendBeacon` is frequently dropped on page unload inside WebViews.
4. If the redirect is not gated on the client round-trip, **the redirect simply wins the
   race** and the client never reports anything.

**B4 — the browser marks the site unsafe.** Beyond domain reputation, Google Safe
Browsing classifies **open redirectors** as social-engineering infrastructure. An endpoint
whose job is "receive a request, 302 elsewhere" fits that signature.

**B5 — blank screen.** A capture page that depends on a JavaScript bundle has a failure
mode where nothing renders and nothing redirects.

**B6 — the browser refuses to provide data.** Investigation points not at bot detection
but at **content blockers**: EasyPrivacy and uBlock Origin match URL substrings such as
`track`, `collect`, `analytics`, `pixel`, `beacon` and `telemetry`, and will silently
abort the request.

Meanwhile NFR2.AC6 requires that enrichment never delay a redirect, and NFR3.AC2 requires
that no single dependency failure prevent one.

## Decision

**The HTTP request is the authoritative capture. Client-side collection is additive
enrichment that is always allowed to fail.**

Concretely:

1. **`GET /r/{slug}` returns 200 `text/html`, never a 3xx.** A 302 gives zero collection
   opportunity and matches the open-redirector signature.
2. **The `visits` row is committed before the response is generated**, carrying every
   server-derived signal: IP-derived values, ASN, ASN type, ISP, rDNS PTR, full header set
   and header order, UA, UA Client Hints, `Accept-Language`, HTTP version, TLS version,
   and `CF-Ray` colo plus `CF-IPCountry` when behind Cloudflare.
3. **The page requires no JavaScript.** Server-rendered Jinja2, inline critical CSS, no
   external assets, a visible notice, a real `Continue now` href, and a `<noscript>`
   meta-refresh fallback.
4. **Enrichment is a separate POST** to `/api/v1/s/{nonce}`, using
   `fetch(..., {keepalive: true})` rather than `sendBeacon`, authorised by a single-use
   HMAC nonce bound to the visit and IP prefix with a 60 s TTL.
5. **A hard client timer redirects at `interstitial_ms`** (default 700, cap 1500)
   regardless of enrichment state.
6. **A sweeper finalises unenriched visits at 90 s** with `stage='server_only'`, runs
   inference on server signals alone, and enqueues notification.
7. **The destination comes only from a `links` row** — never a query parameter, header, or
   path (F1.AC7).
8. **Public route names are deliberately boring:** `/r/`, `/api/v1/s/`, `/api/v1/hp/`.
   A CI test asserts no public path or query key contains a blocked substring.
9. **Link-preview fetchers are classified as `crawler`** and never notified on, so an
   Instagram prefetch cannot consume the human first-visit alert.

## Alternatives considered

| Option | Why rejected |
|---|---|
| **302 immediately, collect nothing client-side** | Fastest and most covert, and it was offered at Gate 1. Forfeits every client signal — GPU, screen, RAM, fingerprint, consented geolocation, headless detection — which removes roughly half the feature list (F3.AC2, F5.AC3–F5.AC5). Also matches the open-redirector pattern that causes B4. |
| **Client-first: hold the redirect until enrichment completes** | Gives the richest data, and is what produces B3. Inside a WebView the request may never complete, so the visitor waits, or the page appears broken. Directly violates NFR2.AC6. |
| **`sendBeacon` for enrichment** | The conventional choice, and specifically unreliable in the WebViews that matter most here. `fetch` with `keepalive: true` has better observed survival across navigation, though RISKS R5 records that this still needs confirming on a real device. |
| **Tracking pixel (`<img>` GET)** | Cache-friendly and simple, but pixel-shaped URLs are exactly what filter lists target (B6), and a GET cannot carry the enrichment payload. |
| **Client-side SPA for the capture page** | Reuses dashboard tooling. Rejected: it introduces the B5 blank-screen failure mode onto the visitor path, and adds bundle download time to a latency-critical page. |

## Consequences

**Positive**

- **B3 is fixed structurally, not heuristically.** Every visit is recorded regardless of
  what the WebView permits, because nothing about the record depends on the client.
- **B4 loses its structural cause.** There is no open redirect to classify.
- **B5 cannot occur on the capture path.** Zero-JS rendering with a `<noscript>` fallback
  means there is no state in which the visitor sees nothing.
- **B6 is addressed at the root.** Neutral route names plus no third-party requests means
  nothing for a filter list to match.
- NFR2.AC6 and NFR3.AC2 are satisfied by construction.
- Instagram prefetches become visible data rather than contamination.

**Negative, and accepted**

- **The visitor sees a roughly 700 ms interstitial.** This is the price of client signals,
  and Gate 1 chose it explicitly (RW-4). It also happens to be what makes the page
  compliant with a visible notice.
- **Two write paths per visit** — an insert then an update — so the finalisation logic must
  be idempotent and the nonce genuinely single-use, enforced by a conditional update on
  `enrichment_consumed_at`.
- **The sweeper is a required component, not an optional one.** If it stops, visits sit
  unfinalised and notifications never fire. It needs its own health check and a
  stuck-visit alert (`DATA_MODEL.md` partial index on `finalized_at IS NULL`).
- **Enrichment data is attacker-controlled.** A client can claim any screen size, GPU or
  timezone. This is why cross-checks and `spoof_score` exist (ADR-0011); nothing from the
  client is ever trusted for a security decision.
- **Analytics must state their stage mix** (F9.AC20), because a population that is 40
  percent `server_only` has systematically missing client fields. Reporting an average
  GPU distribution without that context would be misleading.

## Revisit if

- **Spike B (RISKS R5) shows `keepalive: true` does not survive Instagram navigation.**
  Then `server_only` becomes the dominant path for social traffic, and the response is to
  lean harder on server-side signals — particularly the `CF-Ray` colo (ADR-0005 S8), which
  is the one client-independent metro signal available — rather than to fight the WebView.
- Safe Browsing flags the domain despite there being no open redirect, which would indicate
  the cause is purely reputational (RISKS R8) and not addressable here.
