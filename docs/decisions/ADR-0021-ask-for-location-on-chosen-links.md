# ADR-0021 — Ask for location on chosen links

**Status:** Accepted (2026-10-06). The owner decided the scope, the wait and the wording.
**Deciders:** repository owner
**Amends:** F1 (new F1.AC11), F2.AC5, F4.AC1 and F4.AC2 (SPEC §11 row 19); RISKS R20's
decision of 2026-09-29; DATA_MODEL §4.1
**Relates to:** ADR-0004 (server-authoritative capture), ADR-0020 (geofences act on strict
fields), CLAUDE.md invariants 1 and 2, RISKS R20, Spike B

---

## Context

The capture page has never asked for location. Spike B measured real phones in 2026-09
(Instagram's in-app browser, Android Chrome, desktop Chrome): a permission prompt cannot be
answered inside the 0.7-1.5 s interstitial (F2.AC5); it vanishes under the visitor. The owner
chose then to read only a permission the visitor had already granted (SPEC §11 row 8), and
recorded the consequence: consented location would be rare.

M6 made that consequence concrete. A geofence decides only on a stated location (ADR-0020).
Without a GPS fix, nearly all mobile traffic is `undetermined`, and the owner's own phone test
of a geofence alert could not pass: the page never asked, so the phone never shared.

## Decision

1. **Per link, opt-in** *(owner)*. Links gain `ask_location boolean`, default `false`. Every
   link that leaves it off behaves exactly as today: no prompt, the 0.7-1.5 s redirect.
2. **A link that asks shows plain consent text** *(owner)*, on the interstitial, before the
   browser's own prompt: *"This link would like your location, to tell its owner roughly
   where you opened it. You can say no -- you'll go on to {destination host} either way."*
   With "Continue without sharing" and the privacy link. The browser prompt is requested as
   the page loads.
3. **The redirect waits for the answer, at most 15 s** *(owner)*. It leaves the moment the
   visitor allows (once the fix arrives) or denies, or presses either Continue control; at
   15 s it leaves regardless. The enrichment is sent before each of those exits.
4. **The redirect still cannot fail** (invariant 1). The 15 s timer is set before anything
   else runs and fires whatever the script does; the "Continue" links are real `href`s; the
   `<noscript>` refresh is unchanged (no script, no prompt, immediate). The capture itself
   happened on the server before the page was sent (invariant 2).
5. **What is recorded** (F4.AC2): allowed and a fix arrived: `granted`, coordinates
   authoritative as before; the browser said no: `denied`; the visitor pressed "Continue
   without sharing": `denied`, because they said no to *this* request; no answer by the time
   the page left: `unavailable` with the reason `timeout`; no geolocation API, or an in-app
   browser that refuses it: as today.

## Alternatives considered

| Option | Why not |
|---|---|
| **Every link asks** | Every visitor would face a consent screen and up to 15 s of waiting, including in Instagram's webview, where prompts are often blocked and the visitor only waits. The owner chose to decide per link |
| **Keep never asking** (row 8) | Correct and quiet, but consented location stays rare, and geofence alerts on mobile traffic stay `undetermined` (ADR-0020) |
| **Ask without holding the redirect** | Spike B's measurement: the prompt vanishes, unanswered, every time |
| **Hold with no ceiling** | A visitor who ignores the prompt would never arrive. Invariant 1 needs a timer that fires regardless |
| **Only the browser prompt, no text** | The brief asks to "preserve privacy"; a visible reason is what turns the prompt into informed consent |

## Consequences

**Positive**
- Where a link asks and the visitor agrees, location is GPS-grade and strict, so geofences
  and the high-priority alert work on mobile traffic (ADR-0020).
- Nothing changes for any link that does not opt in.

**Negative, and accepted**
- A visitor on an asking link sees a consent screen and may wait up to 15 s. The owner
  chooses where that is acceptable.
- Instagram's and other in-app browsers often block the prompt; those visitors wait until the
  browser reports the refusal or until they press Continue. Spike B's data says how common.
- `interstitial_ms` no longer bounds every link: F2.AC5's 1.5 s cap applies to links that do
  not ask.

## Revisit if

- In-app browsers turn out to hold asking visitors for the full 15 s routinely: shorten the
  ceiling for detected webviews, or skip the request there.
- A browser starts refusing prompts that are not triggered by a user gesture: request on the
  "Share my location" press instead of on load.
