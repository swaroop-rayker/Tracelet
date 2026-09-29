# ADR-0005 — Location inference: multi-candidate consensus with dual strict/advisory output

**Status:** Accepted (Gate 2, 2026-09-25)
**Deciders:** repository owner
**Relates to:** F4, B1, B2, SC3, RW-1, RW-6

---

## Context

The brief set a success criterion of "highest possible city accuracy, 100 percent
state/region and country accuracy" and reported B1: a Bangalore visitor on Wi-Fi recorded
as Faridabad or Noida.

**B1 is not a coding defect.** Free GeoIP databases map an entire ISP netblock to the
address in the Regional Internet Registry record, or to the ISP peering city. Indian
broadband netblocks — Airtel, Jio Fiber, ACT, BSNL — are largely registered centrally in
the Delhi NCR region, so a Bangalore subscriber inherits a Delhi-area coordinate. MaxMind
publishes India city-level accuracy within 50 km at roughly 50–60 percent. No amount of
application code makes a wrong database entry right.

Two further facts shaped the design:

- **100 percent state accuracy is not achievable** by anyone using free passive IP
  geolocation. Presenting a target that cannot be met would make SC3 unfalsifiable.
- **B2 established the owner tolerance**: a wrong city inside the correct state is
  acceptable; a wrong state is not. That is a statement about *which* errors matter, and it
  maps directly onto a hierarchical abstention model.

At Gate 1 the owner chose a **hybrid** of strict abstention and best-guess output, rather
than either alone.

## Decision

### 1. Eleven independent candidate producers

| | Source | Nature |
|---|---|---|
| S1 | Consented browser Geolocation API | Authoritative, confidence 0.99 |
| S2 | MaxMind GeoLite2-City | Offline database |
| S3 | IP2Location LITE DB11 | Offline database |
| S4 | IPinfo Lite | Offline database, country + ASN |
| S5 | DB-IP Lite | Offline database |
| S6 | **rDNS PTR city-code lexicon** | Highest-value free India signal |
| S7 | ASN and ISP organisation-name parsing | Classifies the connection |
| S8 | **`CF-Ray` edge colo** | Metro hint, server-side, **survives the WebView** |
| S9 | ipwho.is, ip-api.com | External, toggleable, cached by prefix |
| S10 | ~~Latency triangulation~~ | **Dropped in M3** — needs third-party requests (SPEC §11 row 12) |
| S11 | Browser timezone cross-check | **Rejects**, never proposes |

Each emits zero or more candidates and **every candidate is persisted** — winners,
losers and suppressed alike — to `visit_candidates`. That table *is* the derivation trail
the brief asked for, and it is what makes per-source accuracy computable (F4.AC17).

### 2. Weighted consensus

`effective_weight = source_prior × evidence_quality × agreement_bonus × tz_penalty`,
grouped by `admin1` and then by `city`. Independent agreement raises confidence; S11
contradiction lowers it.

### 3. Three suppression rules — the substance of the B1 fix

**(a) Registry-artifact suppression.** Each ASN modal centroid is precomputed across the
offline databases, along with `modal_share` — the fraction of that ASN entries sitting at
that one point. A high share is the signature of a database collapsing a whole ISP onto
its registration address. If the winning city equals that centroid **and** no non-database
source (S1, S6, S8) corroborates it, the city collapses to the **country** — amended in M3
(SPEC section 11 row 11): the registry record that placed the city placed the state too,
so stopping at `admin1` emitted B1's wrong state as a fact.

**(b) Mobile and CGNAT ASNs.** City candidates are discarded outright; `admin1` is the
deepest emittable level. Carrier gateways are geographically meaningless at city scale.
This is the brief requirement "Mobile Carrier Gateway Adjustments".

**(c) Hosting, VPN and Tor ASNs.** All strict levels abstain. The address describes
infrastructure, not a person; emitting a city would be fabrication.

### 4. Dual output

| | Behaviour | Used for |
|---|---|---|
| **Strict** | `NULL` with a recorded `abstain_reason` below threshold | Geofencing, Telegram alerts, exports — anything **acted on** |
| **Advisory** | Always the argmax, with 0..1 confidence | Displayed greyed-out — what you **learn from** |

Plus `agreement_score`, `conflict_score`, and `inference_version` on every visit.

### 5. Measurable targets replacing the unachievable one

| Level | Strict | Advisory |
|---|---|---|
| Country | ≥99.5 % accuracy, ~100 % coverage | same |
| Admin1 | ≥99 % **precision** at ≥85 % coverage | ≥92 % accuracy |
| City, no consent | ≥95 % precision; coverage measured, floor set in M8 (amended — see below) | ≥70 % accuracy |
| City, consented | ~100 % | — |

> **Amended 2026-09-29 (SPEC section 11 row 9).** The city row originally read "≥95 %
> precision at ≥50 % coverage". Spike A measured rDNS city-code coverage at 2.0 % (RISKS R3),
> so the corroboration that coverage depended on does not exist for most visitors. Precision
> is kept — this decision's premise is never being confidently wrong — and the coverage
> floor waits for ground-truth data in M8.

Verified in CI against an owner-labelled ground-truth set (F4.AC15, F14.AC12).

### 6. Thresholds are versioned configuration

Stored in `inference_settings`, editable from the dashboard, every version retained for
rollback. Retuning requires no deployment, and old `inference_version` stamps stay
interpretable.

## Alternatives considered

| Option | Why rejected |
|---|---|
| **Always emit a best guess with a confidence score** | Offered at Gate 1 as option 2. Maximum coverage, and you would see Faridabad-instead-of-Bangalore rows again — merely labelled low-confidence. Unsafe for geofence alerting, where acting on a guess produces a false notification. |
| **Strict abstention only** | Offered as option 1. Never confidently wrong, but discards the engine reasoning: you could not see *that* it thought Faridabad, nor *why* suppression was correct. That information is what makes tuning possible. |
| **Single winning source by precedence** | Simple and explainable. Throws away the strongest available signal — **agreement between independent sources** — which is exactly what distinguishes a reliable inference from a lucky one. |
| **A trained model over candidate features** | Attractive in principle. Requires labelled training data we do not have (30–60 labels is nowhere near enough), and would make every verdict unexplainable, breaking the brief requirement to show how each value was derived. |
| **A paid geolocation API** | Would substantially improve accuracy and is the honest answer to "how do commercial products do this". Fails C6, free tier only. |

The hybrid was chosen because the two halves answer different questions: strict answers
*what may I act on*, advisory answers *what did the engine think*. Collapsing them loses
one or the other.

## Consequences

**Positive**

- B1 is addressed at its actual cause, with a reason recorded on every suppressed
  candidate rather than a silent correction.
- B2 tolerance is encoded directly: hierarchical collapse degrades city to state instead
  of guessing.
- SC3 becomes measurable and CI-enforceable rather than aspirational.
- Per-source accuracy is computable, so a consistently-wrong source can be down-weighted
  with evidence.
- Retuning is a configuration change with a rollback path.

**Negative, and accepted**

- **City coverage will be visibly incomplete.** Many visits will show
  `strict_city = null`. That is the design working, and the dashboard must present it as
  a deliberate abstention rather than missing data (F9.AC18).
- **Two field sets doubles the location columns** and adds UI complexity — the advisory
  values must be visually distinct or they will be misread as facts.
- **`visit_candidates` is the largest table**, roughly 720 k rows at retention. Budgeted
  in ARCHITECTURE section 7.
- **The whole plan leans on S6 having coverage.** RISKS R3 and Spike A exist for exactly
  this reason. If Indian residential PTR coverage is below roughly 30 percent, the city
  targets must be revised **before** M3 is built.
- **S9 sends the visitor IP to a third party.** Gate 1 approved this with per-source
  toggles, prefix caching and privacy-policy disclosure. It remains a genuine privacy
  cost and is disclosed as such.
- **S8 depends on Cloudflare.** On the free-subdomain path this source does not exist,
  which measurably lowers city coverage (ADR-0012).

## Revisit if

- Spike A reports low rDNS coverage — revise F4.AC13 targets before building M3.
- An external source proves consistently wrong for India; down-weight it with evidence
  from `visit_candidates` rather than by intuition.
- ~~Latency triangulation, once measured in M8, adds meaningful accuracy over S8.~~ S10 was
  dropped in M3 (SPEC section 11 row 12); revisit only with a design that needs no
  third-party request from the capture page.
