# ADR-0006 — Visitor identity: HMAC pseudonymisation with a split pepper strategy

**Status:** Accepted (Gate 2, 2026-09-25)
**Deciders:** repository owner
**Relates to:** F5.AC7, F7.AC2, F9.AC12, F12.AC6, OOS4

---

## Context

The brief requires "HMAC-Based Visitor Hashing" and several features that depend on
recognising a returning visitor:

- Telegram alerts fire only for a **new** visitor (F7.AC2).
- A returning-visitor view shows one visitor history with location drift (F9.AC12).
- **Device fingerprint collision detection** flags a proxy when one fingerprint appears
  across many ASNs (F5.AC7).
- Impossible-travel detection needs a stable identity across visits (F5.AC11).

There is a direct tension here. Recognising returning visitors requires a **stable**
identifier. Privacy — and OOS3, OOS4 — argues for the **shortest** identifier lifetime
that still serves the purpose. A single identifier cannot be both.

A second tension: proxy detection needs to compare the *raw* fingerprint across visitors,
while visitor identity should not be reversible to the fingerprint components.

## Decision

**Three separate derived identifiers, each with a different lifetime and purpose.**

| Field | Derivation | Lifetime | Purpose |
|---|---|---|---|
| `visitor_id` | `HMAC-SHA256(pepper_stable, canonical_fp ‖ ip_prefix)`, truncated to 128 bits | Stable; pepper rotates at the 180-day retention boundary | New-vs-returning, visitor history, impossible travel |
| `session_fp` | `HMAC-SHA256(pepper_rotating, canonical_fp ‖ ip_prefix)` | Pepper rotates daily | Short-window correlation without long-term linkability |
| `fingerprint_id` | `HMAC-SHA256(pepper_fp, canonical_fp)` — **no IP component** | Stable | Fingerprint-collision proxy detection |

**Why `fingerprint_id` excludes the IP prefix:** proxy detection works by finding *the
same device* behind *different networks*. If the IP were in the hash, every network change
would produce a different value and the detection could never fire. This is the one
identifier that must be network-independent, and it is why three fields exist rather than
two.

**Canonical fingerprint components**, order-stable and normalised:

- Server-side: header order hash, `Accept-Language`, UA family and major version, UA-CH
  platform
- Client-side: screen dimensions and DPR, colour depth, timezone, language list,
  `hardwareConcurrency`, `deviceMemory`, GPU vendor and renderer, canvas, audio, font and
  WebGL hashes

**Stability tolerance.** Canvas and audio hashes are noisy across browser versions and on
some privacy-hardened configurations. The canonical form buckets continuous values
(rounding DPR, bucketing font counts) so a minor version bump does not mint a new visitor.
Exact-match hashing of raw values would inflate the unique-visitor count.

**Pepper handling.** All three peppers are 256-bit, read from the environment or a `0400`
file, never stored in the database, never logged, never returned by any API. Rotating a
pepper breaks historical linkage **by design** — that is a privacy feature, and it is why
rotation is scheduled to coincide with the retention boundary where the underlying rows
are being purged anyway.

**Non-reversibility.** HMAC with a secret pepper means an attacker holding the database
cannot enumerate candidate fingerprints to recover which device produced a row, because
they lack the pepper. A plain SHA-256 would be trivially brute-forceable over the small
space of plausible fingerprint values.

## Alternatives considered

| Option | Why rejected |
|---|---|
| **A first-party cookie as the visitor identifier** | Far more accurate for returning-visitor detection and the conventional approach. Rejected: the capture page redirects within roughly 700 ms, so a cookie provides little value on a single-visit flow; it requires consent handling in several jurisdictions; and it is trivially cleared. Fingerprint-derived identity needs no storage on the visitor device at all, which is the more privacy-respecting mechanism here despite the reputation of the word "fingerprint". |
| **Plain SHA-256 of the fingerprint** | No secret, so the hash is brute-forceable across the realistic fingerprint space. Offers no meaningful protection if the database leaks. |
| **Storing the raw fingerprint components** | Best for debugging and for retrospective re-bucketing. Rejected: it is a persistent, highly-identifying record with no stated purpose, which conflicts with OOS4 and with the data-minimisation posture of RW-3. |
| **One identifier for everything** | Simplest schema. Cannot satisfy both "recognise returning visitors" and "detect one device across many networks", and forces a single lifetime choice onto three different privacy profiles. |
| **Rotating the stable pepper daily** | Strongest privacy. Destroys F7.AC2, F9.AC12 and impossible-travel detection outright, since no visitor would ever appear as returning. |

## Consequences

**Positive**

- Returning-visitor detection, notification deduplication, and proxy detection all work,
  each from the identifier appropriate to it.
- No identifier is reversible without the pepper, so a database leak yields opaque values.
- Nothing is stored on the visitor device — no cookie, no local storage.
- `session_fp` gives a short-window correlation tool that does not accumulate long-term
  linkage.

**Negative, and accepted**

- **Fingerprint-derived identity is inherently imperfect.** A browser update, a new
  monitor, or a privacy extension can mint a new `visitor_id` for the same person, which
  inflates unique-visitor counts and can produce a duplicate "new visitor" alert. The
  bucketing tolerance reduces this but cannot eliminate it. Analytics should present
  unique-visitor figures as estimates.
- **The converse also happens:** two identical stock devices on the same network can
  collide into one `visitor_id`. At this volume the probability is low but non-zero, and
  it means "returning visitor" is a strong hint, not a certainty.
- **Three peppers are three secrets** to manage, back up out-of-band, and rotate.
  Losing `pepper_stable` silently breaks all historical visitor linkage — it must be in
  the operational runbook, and it is **not** in the database backup by design.
- **Rotating `pepper_stable` resets returning-visitor history.** Scheduling it at the
  retention boundary limits the surprise, but it must be documented and audit-logged.
- Fingerprinting has a poor public reputation. The privacy page must be explicit about
  what is derived, that it is non-reversible, and that no cookie is set (F2.AC13).

## Revisit if

- Unique-visitor counts diverge materially from ground truth during M8 labelling, which
  would indicate the bucketing tolerance needs adjusting.
- A first-party cookie becomes worthwhile because a use case emerges that genuinely needs
  cross-session accuracy — at which point the consent implications must be reassessed.

---

## Amendment (M4, 2026-09-29) — as built

1. **Header set, not header order**, in the canonical fingerprint's server-side part:
   header order never reaches the application (RISKS R19, SPEC section 11 row 7). The
   component is the sorted set of *names* of the browser-characteristic headers present.
2. **A `server_only` visit gets no `fingerprint_id`.** Its canonical form would be only
   the UA family, major version, language and platform — shared by every Chrome 131 user
   with `en-IN` — so the collision rule (F5.AC7) would read ordinary people as one device
   on many networks. `visitor_id` and `session_fp` are still derived, from the server part
   and the prefix, and are marked as server-only in `signals`.
3. **The daily rotation of `session_fp` is derived, not operated.** The day's key is
   `HMAC(pepper_rotating, UTC date)`; no one has to rotate an environment variable every
   midnight, and yesterday's values are unlinkable to today's once the day passes.
4. **A missing pepper degrades the identifier, never the visit**: the field is `NULL` with
   an `identity.pepper_missing` absence signal, as M2 already does for `ip_hmac`.
