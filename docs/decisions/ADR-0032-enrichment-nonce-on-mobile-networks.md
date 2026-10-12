# ADR-0032 — The enrichment nonce on mobile networks: the same carrier, not only the same /24

**Status:** Accepted (2026-10-11). The owner approved fixing it in M9, before any code.
**Deciders:** repository owner
**Amends:** SPEC F2.AC6 (SPEC §11 row 38); `capture/nonce.py`'s prefix binding
**Relates to:** ADR-0004 (server-authoritative capture), ADR-0031 (mobile visitor identity),
F2.AC7, F11.AC4, RISKS R5, R17

---

## Context

F2.AC6 makes the enrichment request carry a nonce that is a single-use HMAC over the visit,
an expiry 60 s ahead and **the visitor's /24 (or /48)**. The verifier recomputes the MAC from
the prefix the POST arrives from, so a nonce carried to another network fails. The nonce
module recorded one known edge -- a dual-stack browser switching address families -- and
expected it to be rare, because a browser reuses the page's connection for a request fired
within a second.

Production on 2026-10-11 showed the edge is the common case on mobile data. Of the owner's
eight visits on one Android phone over a carrier network, **six lost their enrichment**: the
POST arrived two to four seconds after the page, well inside the minute, first use, and was
refused as `mac_mismatch` (logged at 16:06:07 and 16:06:38 UTC). The carrier's CGNAT had sent
the follow-up request from a different /24 -- the same pool behaviour ADR-0031 records for
identity: six requests, five prefixes. On Wi-Fi every visit enriched.

A refused enrichment costs the visit its client fingerprint, timezone, screen and consented
GPS; the visit waits for the sweeper (F2.AC7, about 100 s) before it is inferred and alerted;
and ADR-0031, which keys a mobile visitor on the carrier only when the visit is enriched,
cannot help it.

## Decision

**Keep the prefix binding. When it fails, accept the nonce if it is genuine for its visit
and the request comes from the same mobile carrier.**

1. The POST's own prefix is tried first, exactly as now.
2. On `mac_mismatch` only, the visit the token names is loaded, and the MAC is recomputed with
   **that visit's own stored prefix** -- the one it was minted for. A token that fails this is
   forged or tampered with, and refused as before. Expiry is then checked as before.
3. The token is accepted only if the POST's address and the visit's prefix resolve to **the
   same ASN, and that ASN is a mobile or CGNAT carrier** in the curated classification
   (`asn_classes.json`, the list `connection_class` uses). Anything else -- a broadband
   network, two different carriers, no ASN database installed -- is refused as before, now
   logged as `network_mismatch`.
4. Single use (the conditional `UPDATE`) and the 60 s lifetime are unchanged.
5. An accepted re-bound enrichment adds a signal, `capture.enrichment_other_prefix` with the
   carrier's ASN, so the derivation shows it happened.

The honeypot token keeps the strict binding: a late hit from the same network is the evidence
it wants, and nothing degrades if a carrier's pool moves.

## Alternatives considered

| Option | Why rejected |
|---|---|
| Drop the network binding entirely | A nonce leaked from a page could then enrich (that is, plant client facts on) a visit from anywhere for 60 s |
| A wider prefix (/16) for everyone | Still fails across a carrier's pools, and loosens the binding for broadband where it works |
| Put the prefix in the token | Tells whoever holds it which network it was minted for, which the module avoids on purpose |
| Raise the TTL or retry from the page | The failure is the network, not the time; a retry would leave from yet another address |
| Keep the strict binding | The owner declined: most mobile visits would stay unenriched |

## Consequences

**Positive:** mobile visits enrich again -- fingerprint, timezone, screen, consented GPS --
alert within seconds rather than after the sweeper, and become carrier-keyed visitors under
ADR-0031.

**Negative, accepted:**
- **A stolen nonce can be used from anywhere on the same carrier within its 60 s, once**,
  instead of only from the same /24. What it buys is the ability to send one visit's client
  claims, which are attacker-controlled by construction anyway (the payload is a claim, never
  trusted, ADR-0004).
- An ASN lookup on the refused path: an mmap read, microseconds, only after a mismatch.
- Depends on the ASN databases. Without them the strict binding applies, as before.
