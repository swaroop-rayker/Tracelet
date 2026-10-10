# ADR-0029 — Caddy serves the redirect when the app cannot

**Status:** Accepted (2026-10-10), after the owner asked how and when it affects visitors.
**Deciders:** repository owner
**Amends:** ADR-0012 (Caddy's role), ADR-0028 (the last stage of degradation)
**Relates to:** CLAUDE.md invariants 1 and 3; SPEC F1.AC4, F2.AC14, F13.AC2, F15.AC6, F15.AC7,
NFR3.AC2; ADR-0004; ERRORS E82

---

## Context

ADR-0028 made the redirect independent of the database and of the app's spare time: an
overloaded worker answers from memory. Its third re-test on the e2-micro (one worker, no swap
for the redirect path, memory now averaging 682 MB) still left **956 of 11 421 flood visitors
(8.4 %) past Caddy's 10 s**. The cause is no longer memory but CPU: at about 10 requests a
second on roughly 0.8 of a vCPU, the single event loop falls behind on *reading* requests,
and an answer from memory is only as fast as the loop that has to reach it. No code inside
the app can answer a request the app has not yet read, nor one that arrives while the app is
restarting, killed or wedged. Those are the cases where invariant 1 still fails.

Caddy is already in front of every request, is a Go server built for many connections at
little cost, and keeps running when the api does not.

## Decision

**When the api does not answer a capture within 3 s, or cannot be reached, Caddy answers it
itself with a page the api prepared in advance: a redirect to that link's stored
destination.**

1. **The api writes one small page per live link** into a volume shared with Caddy
   (`fallback`, read-write in the api, read-only in Caddy): `<slug>.html`, a `meta refresh`
   to the destination and a link to it, HTML-escaped; `_default.html` for the bare `/r/`
   (F1.AC3); `_unavailable.html`, the "temporarily unavailable" page, for anything else.
   Files are written atomically (a temporary file, then a rename). The set is reconciled
   with the database whenever the link cache is refreshed (ADR-0028, every 30 s) and at
   once when a link is created, edited, deactivated, archived or made the default, so a
   deactivated or archived link stops redirecting immediately (F1.AC4).
2. **Caddy's `/r` handler gets its own upstream timeout of 3 s** (the app's capture deadline
   is 1 s, so a working app always answers first), and **`handle_errors` for 502, 503 and
   504 on `/r` paths serves the matching page** with status 503, `Cache-Control: no-store`,
   and a CSP of `default-src 'none'; img-src data:; base-uri 'none'; form-action 'none';
   frame-ancestors 'none'`. The slug reaches the file name only through the same pattern
   the app enforces, `[a-z0-9-]{4,32}`, so no request can name another file.
3. **No open redirect** (invariant 3): every page holds a destination an authenticated admin
   stored; nothing from the request reaches a page's content.
4. **What is lost:** a visitor sent on by Caddy is not recorded, because Caddy has no
   database. Caddy's access log (addresses masked, ERRORS E26) still shows each such request
   and its 503, for an operator who goes looking; System Health does not count them, since
   the api cannot see what happened while it was not answering. This is the last stage of
   ADR-0028's degradation: full capture, unenriched, buffered, counted, and now **served by
   the edge**.

## Alternatives considered

| Option | Why not |
|---|---|
| **Accept the limit** | Offered with the figures; declined. Invariant 1 has no load clause. |
| **Pass the destinations to Caddy as configuration** (a `map` reloaded on every edit) | Reloading Caddy on every link edit, from the api, needs Caddy's admin API reachable from the api container: a privileged channel into the edge. Files in a read-only mount need nothing. |
| **Caddy redirects with a 302 from a header the app sets** | Requires the app to answer, which is the failure being covered. |
| **A second api process kept for redirects only** | Memory the box does not have (ADR-0012), and it would share the same starved CPU. |
| **A larger host** | ADR-0012's revisit condition; outside the free tier. |

## Consequences

- **The redirect survives a saturated, killed, restarting or wedged api.** It needs only
  Caddy, the files, and a link that existed at the last reconciliation.
- **A destination edit reaches the fallback page at once** (the editing request writes it);
  a link created while the api is down has no page until the api is back.
- **Visits served by the edge are not recorded.** The telemetry for the busiest moments is
  incomplete by design; the journey is not.
- **Disk:** one file of under 1 KB per link. **Memory:** none resident; Caddy serves files.
- **Every degradation drill in M9 that stops the api** (DB down is already covered by
  ADR-0028; api down, api wedged) now has a defined outcome: the visitor is sent on.
- A test of Caddy's configuration is needed: the live check and the stress test exercise it
  on the box, and an integration test checks the pages the api writes.
