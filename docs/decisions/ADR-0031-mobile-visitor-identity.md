# ADR-0031 — Mobile visitor identity: the carrier, not the /24, and one tap is one visitor

**Status:** Accepted (2026-10-11). The owner chose to fix it in M9 and approved this design as
written, before any code.
**Deciders:** repository owner
**Amends:** ADR-0006 (the network component of `visitor_id` and `session_fp`)
**Relates to:** F7.AC2, F7.AC13, F9.AC22, F5.AC11, F12.AC6, CLAUDE.md invariants 6 and 10,
RISKS R17, ERRORS E87's SC1 walk

---

## Context

ADR-0006 derives `visitor_id = HMAC(pepper_stable, canonical_fp ‖ ip_prefix)`: the
fingerprint plus the /24 (IPv4) or /48 (IPv6). Everything that asks "have we seen this person
before?" reads it: one alert per link and visitor per day (F7.AC2), the returning-visitor
note (F7.AC13), new and returning visitors (F9.AC22), impossible travel (F5.AC11).

M9's real phone visits on production (2026-10-11, one Android phone, one Chrome, link
`phn-test`) showed it failing on a mobile network:

- **Six mobile requests in three hours came from five different /24 prefixes.** The carrier
  (CGNAT) hands out an address from a large pool per connection, so the /24 is an artefact
  of the pool, not a property of the person. Six requests, six `visitor_id`s, **six
  alerts** where F7.AC2 intends one (plus at most its upgrade).
- **One tap made two visits, with two identities.** Chrome abandoned its first request
  (Caddy logged 499) and re-sent it 0.5 s later; once, a second request came 42 s after the
  first. The server records every request it receives (F2.AC2, invariant 2), so each became
  a visit. One was enriched, the other never ran the page and became `server_only`, and the
  two canonical forms differ (`server_part` alone vs `server_part + client_part`), so even
  on the same /24 the HMACs differed.

On Wi-Fi the /24 is stable, and the same phone was recognised correctly.

## Decision (proposed)

**1. On a mobile network, an enriched visit's identity uses the carrier, not the /24.** When
the visit's `connection_class` is `mobile` and its ASN is known, the network component of
`visitor_id` and `session_fp` is `asn:<number>` instead of the prefix. Everything else keeps
the prefix: broadband, business, datacenter, VPN and Tor visits, a visit with no ASN, and
every **server-only** visit (point 3). The client fingerprint -- screen and pixel ratio,
colour depth, timezone, languages, cores, memory, GPU, canvas, WebGL and font hashes --
carries the distinction between people; the carrier keeps two identical phones on two
networks apart.

**2. A server-only visit that is the same tap takes its sibling's identity.** A `server_only`
visit on a mobile network inherits the `visitor_id` and `session_fp` of an **enriched** visit
on the **same link** that occurred **within 60 seconds** of it, from the **same ASN**, with the
**same browser family and major version and the same operating system**. That is the
re-sent request, or the visitor coming back to the tab. Nothing else is inherited: its own
classification, location and stage stay its own. Effect: F7.AC2's existing key already
treats it as the same visitor, so no second alert, and analytics count one visitor.
Recorded on the visit as a signal, `identity.same_tap` naming the sibling, so the
derivation stays explainable.

**3. Without such a sibling, a server-only mobile visit keeps the /24.** Its canonical form
is only browser family and version, OS, first language and the header set: shared by most
people on the same carrier with the same browser. Keying it by carrier would merge
strangers and silence their alerts.

**4. Versioned, not rewritten.** Carrier-keyed identifiers use new HMAC labels,
`visitor.v2` and `session.v2`; prefix-keyed ones keep `v1`. `classifier_version` moves on to
`m9.1` (invariant 10). Existing visits keep their identifiers; a mobile visitor seen before
reads as new once. Nothing is recomputed: production
holds a handful of real visits.

**Unchanged:** `fingerprint_id` (it has no network part, ADR-0006), the three peppers and
their lifetimes, F7.AC2's rules themselves (only who counts as the same visitor changes),
and every identity on a non-mobile connection.

## Alternatives considered

| Option | Why rejected |
|---|---|
| Carrier for **every** mobile visit, server-only too | A server-only fingerprint is browser and language only; on one carrier it would merge thousands of strangers into one visitor and suppress their alerts |
| Drop the network part for enriched visits altogether | Loses the one separator for identical phones on different networks; the carrier costs nothing |
| A wider prefix (/16) for mobile | Carrier pools span many /16s; it would still mint new visitors, just less often, and the width would be a guess |
| An alert-only window: no second alert for the same link, carrier and browser within N minutes | A link shared in a reel brings many people on the same carrier with the same app build within minutes; it would silence real alerts, and analytics would still double-count |
| A first-party cookie | ADR-0006's reasons still hold: a 700 ms page, consent, and trivially cleared |
| Leave it (accept one alert per mobile request) | The owner declined (2026-10-11) |

## Consequences

**Positive:** a phone on mobile data is one visitor across its connections: one alert a day
per link (and the upgrade), returning-visitor notes and figures that work on mobile, and a
re-sent request no longer alerts twice.

**Negative, accepted -- and the owner should weigh the first:**
- **Two people with the same phone model, the same browser version, the same language,
  timezone and screen, on the same carrier, visiting the same link the same day, become one
  visitor.** Identical hardware and software produce identical canvas and WebGL hashes. The
  second person gets no alert that day (unless the upgrade or confirmation rule applies) and
  is counted as returning. Under v1 they also had to share a /24, which on a mobile pool was
  rare. Popular models make this real at volume; at the owner's volume, a duplicate alert is
  the commoner failure today.
- **A different person's unenriched visit within 60 seconds on the same carrier and
  browser** is folded into the earlier visitor (point 2): their alert is not sent. Bounded
  to server-only visits and to one minute.
- **A one-time discontinuity:** v1 and v2 identifiers never match.
- Mobile depends on the ASN being resolved. A visit with no ASN keeps the /24.
- "Mobile" is the curated ASN classification `connection_class` already uses (S7,
  `asn_classes.json`). **JioFiber shares Reliance Jio's ASN 55836**, which that list treats as
  mobile, so JioFiber home visitors are keyed by the carrier too; their enriched fingerprint
  still separates households.
- **As built:** carrier-keyed identifiers use new HMAC labels (`visitor.v2`, `session.v2`);
  prefix-keyed ones keep `v1`, so a Wi-Fi or broadband visitor's history is unbroken.

## Revisit if

- Ground-truth or owner reports show missed alerts from distinct people on one carrier.
- A carrier's address plan makes the /24 stable again (then point 1 costs collisions for
  nothing).
