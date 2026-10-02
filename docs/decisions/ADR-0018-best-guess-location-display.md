# ADR-0018 — The dashboard shows the best-guess location; strict is what gets acted on

**Status:** Accepted (2026-10-02)
**Deciders:** repository owner (directed), recorded before the code per CLAUDE.md §2
**Amends:** ADR-0005 §4 (dual output: advisory was "displayed greyed-out"), ADR-0016
(rollup location keys were strict), CLAUDE.md invariant 5
**Relates to:** F4.AC10, F4.AC12(b), F9.AC4, F9.AC5, F9.AC13, SPEC §11 row 14, ERRORS E38

---

## Context

ADR-0005 split every location level into **strict** (threshold-gated, abstains with a
reason) and **advisory** (the argmax, with a confidence). The dashboard was built strict-
first: breakdowns, the map, filters and the visits list all used strict values, and showed
an abstention as "Abstained".

Measured on the dev database on 2026-10-02 (160 visits from real Indian ISP addresses, the
Spike A set, through the real pipeline):

| | Count |
|---|---|
| Strict city | **0 of 160** |
| Strict state | 39 of 160 |
| Advisory city | 66 of 160 -- **every broadband visit, and no mobile one** |

Spike A had already predicted the first line (RISKS R3: rDNS city coverage 2.0 %), and SPEC
§11 row 9 accepted it. The result is a dashboard on which every city reads "Abstained" --
the owner's verdict: bad UX; it has to show the city the engine thinks is most likely.

The third line was a bug (ERRORS E38): rule (b), the mobile-carrier rule, removed city
candidates from the *advisory* pass too, so ADR-0005's "advisory is always the argmax"
was false for every Jio, Airtel-mobile and Vi visitor -- most of India's traffic.

## Decision

1. **Advisory is the argmax over every locating candidate.** The three suppression rules
   (F4.AC12) decide what may be *acted on* and gate strict only. Rule (b) still stops a
   mobile network from yielding a strict city; the advisory city is now shown.
2. **Advisory extends strict.** Wherever strict emitted a value, advisory is that value.
   Before this, a hosting-ASN visit with consented GPS could show a strict GPS city beside
   an advisory city from the VPN exit's databases; now the advisory pass is re-run with the
   strict levels fixed whenever the two would disagree. So a best guess never contradicts a
   statement, and "best guess" is one consistent location per visit.
3. **The dashboard shows the best guess at every level**, as the primary location -- not
   greyed out, not "Abstained". It carries its confidence, and is marked *confirmed* where
   strict emitted the same value:
   - visits list, visit detail and visitor view: "Hyderabad, Telangana, India · 55 %";
   - breakdowns (country, state, city) count each visit at its best-guess location;
   - the map shades by best-guess country and state, and plots points at consented GPS or
     the best-guess **city** (a state-level guess is not drawn as a dot);
   - location filters match the best guess, so a filter selects exactly what a chart
     counted.
   A visit that **no source** could place at a level is "Unknown" there -- still counted,
   never dropped.
4. **Strict keeps every job where acting on a guess would do harm:** geofence evaluation
   (`geopoint` stays strict-city-only, DATA_MODEL 5.3 invariant 11), Telegram wording
   ("confirmed" versus "best guess", F7.AC4), and accuracy measurement (`emission_rate`,
   the source-flow chart, `strict_*_count`, F4.AC13 targets). Strict output is unchanged
   by this decision.
5. `ENGINE_REVISION` becomes **m3.4**. Visits stamped m3.3 and earlier may lack an advisory
   city on a mobile network; the dashboard falls back to strict for a visit with no
   advisory value at all.

## Alternatives considered

| Option | Why not |
|---|---|
| **Lower the strict city threshold** | Makes strict wrong more often to make it present more often. Strict feeds geofencing, where a guess produces a false alert (ADR-0005's reason for rejecting "always emit a best guess"), and the ≥ 95 % city precision target (F4.AC13) would fail |
| **Show the guess only in the visit detail** | What was built. The owner judged the result -- "Abstained" on nearly every row and chart -- unusable |
| **Fill strict city from advisory when confidence is "close"** | A second threshold with no ground truth to set it (that is M8), and it blurs the one line the design depends on: strict is what may be acted on |
| **Count both side by side in every breakdown** | Doubles every location chart for a distinction the visit detail already shows per level |

## Consequences

**Positive**

- Every visit with a candidate shows a city: on the dev set, 160 of 160 (was 0 strict, 66
  advisory).
- The mobile-network bug (E38) is fixed at its cause, in the engine, not papered over in
  the UI.
- Geofencing and alerts are untouched: nothing acts on a guess.

**Negative, and accepted**

- **Charts now show places the engine does not stand behind.** A city breakdown is a
  distribution of best guesses -- right roughly 70 % of the time by F4.AC13's advisory
  target (unmeasured until M8). Each label carries its confidence where space allows, and
  the Breakdowns and Geography pages say what they count.
- **The ISP-head-office city returns to the charts (B1).** Rule (a) still stops it being
  *stated*, but a Faridabad registry guess is now counted under Faridabad. Its confidence is
  low and the visit detail says why it was not confirmed. This is the cost ADR-0005 named
  when it declined "always emit a best guess"; the owner accepts it for display only.
- Rollup days built before migration 0008 used strict keys. 0008 forgets every day that
  still has raw visits, so they are rebuilt under this rule; a day already past raw
  retention keeps its strict keys (DATA_MODEL section 9).

## Revisit if

- M8's ground truth shows advisory city accuracy far below 70 % -- a chart of guesses that
  are mostly wrong is worse than "Unknown".
- A strict-only view is wanted back: a per-page "confirmed only" toggle is a filter on
  `confidence_*`, not a new model.
