# ADR-0024 — Accuracy is measured by replaying consensus over stored candidates

**Status:** Accepted (2026-10-08). The owner chose replay over re-inference from ciphertext
IPs, and the real fixture as a CI secret.
**Deciders:** repository owner
**Relates to:** SPEC F4.AC13, F4.AC14, F4.AC15, F4.AC17, F9.AC10, F14.AC12, §11 row 30;
ADR-0005 (consensus), ADR-0007 (encrypted IPs), ADR-0015 (inference job), ADR-0018 (strict
and best guess); RISKS R9, R24; CLAUDE.md invariants 4, 5 and 10

---

## Context

M8 turns SC3 into a measured number. Three things need deciding before code:

1. **What is being measured.** A labelled visit was inferred once, under the settings version
   active at the time (`inference_version`, invariant 10). Tuning asks a different question:
   *what would the engine say now, or under a proposed version?* Answering it means running
   the engine again on old visits.
2. **What CI runs against.** F14.AC12 wants a CI job that fails when accuracy regresses. CI
   has no access to the production database, and the repository is **public**: a fixture of
   real labels is a list of the places the owner was and the networks they were on.
3. **The ADR-0007 payoff.** ADR-0007 kept the encrypted IP for 30 days so that a wrong result
   could be re-run during M8. A full re-run repeats every lookup: the offline databases
   (which have since been updated), a fresh PTR query (which may now answer differently) and
   the external APIs (which spend their quota). And it only reaches visits less than 30 days
   old.

Two facts about the engine shape the answer:

- `consensus.decide()` is **pure**: candidates, what is known about the network (`AsnInfo`)
  and the browser timezone go in, and the strict and advisory values come out (ADR-0005). No
  I/O happens inside it.
- Every candidate it saw is **stored** with its raw confidence, losers and suppressed ones
  included (`visit_candidates`, DATA_MODEL §5.4). That is the audit trail F4.AC11 already
  requires.

## Decision

**1. Accuracy is a replay.** For each labelled visit, the stored candidates are read back and
passed to `consensus.decide()` under a chosen `InferenceConfig`: the active version by
default, or any retained version, or a proposed one that is not saved. The result is compared
with the label. The network facts come from the visit (`asn`, `asn_org`, `is_tor`) and the
current `asn_profiles`, combined exactly as the engine combines them. The timezone countries
come from `tz_iana`. No IP is decrypted. No lookup is repeated. A label is truth set beside
the visit: it **never** changes the visit's stored inference.

- **What replay covers.** Every weight, threshold, suppression parameter, the family bonus and
  the timezone penalty: everything versioned in `inference_settings` (F4.AC14). This is the
  tuning surface.
- **What it does not cover.** A source that produced no candidate cannot be made to produce
  one. A lexicon change to S6 or S7 therefore needs the PTR or organisation name, not the
  candidates. The masked PTR (`visits.rdns_ptr`) keeps a city code's letters: only
  digit runs and hex-group runs are masked (`rdns.mask_ptr`, which over-masks on purpose). So
  lexicon expansion is measured against the masked PTRs and does not need the address.
- **Fidelity is tested.** An integration test infers a visit through the real engine, then
  replays it from the database under the same version, and asserts identical strict and
  advisory values at every level.

**2. Consented visits are scored twice.** As recorded, with GPS, they form the *consented*
population (F4.AC13: city accuracy ≈ 100 %). With the GPS candidate removed, they join the
*network-only* population, beside every non-consented visit. The network candidates of a
visitor who allowed location are the ones the engine would have used had they refused, so
every label measures the network path. This is what F4.AC13's non-consented targets are
about.

**3. Interval, not point.** Each proportion is reported with its count and a 95 % Wilson
score interval, computed in plain Python (no numpy, CLAUDE.md §5). Every figure carries its
`label_count` (RISKS R9).

**4. CI runs two fixtures.**

- **A committed synthetic fixture** (`api/tests/fixtures/accuracy/synthetic.json`): made-up
  candidate sets shaped like real ones. It always runs. A test proves the job **fails** when a
  threshold is deliberately regressed.
- **The real fixture is a GitHub Actions secret** (`ACCURACY_FIXTURE`, base64 of gzipped
  JSON), never a file in the repository. `tracelet accuracy export` builds it with an
  allow-list of fields:
  - included: the label's place names, connection kind, VPN flag and network family; the
    candidates' source, level, place names and raw confidence; the `AsnInfo` flags and modal
    fields; the timezone countries; consented or not; the path;
  - excluded: every id, timestamp, IP, prefix, HMAC, ASN number, organisation, PTR,
    coordinate and free-text note.

  The CI step decodes it into the runner's temp directory and runs the same check. It is
  skipped, with a notice, where the secret is absent, as on forks.
- Both run `tracelet accuracy check`, which needs no database. It scores under the settings
  embedded in the fixture (the box's active version at export), or under the code default
  when the fixture has none. It exits non-zero when a gated F4.AC13 target is missed.

**5. History in `accuracy_runs`.** A run is recorded by `tracelet accuracy run` on the box,
or by the owner from the dashboard. It stores the `inference_version` and
`classifier_version` it measured, the settings version, `label_count`, the full metrics and
whether every gated target passed. CI does not write to any persistent database. Its result
is the job's status and summary.

**6. Re-running inference from `ip_enc` is not built in M8.** Replay delivers what ADR-0007
kept the ciphertext for, which is validating a tuning change against old visits, without
decrypting anything, and for visits of any age. ADR-0007's 30-day purge is unchanged.

## Consequences

- Tuning is a loop with no deployment: propose a version, score it by replay, save it (the
  old one is retained, F4.AC14), and record a run.
- A label lives as long as its visit (180 days by default; it is deleted with the visit). The
  exported fixture is the durable copy of the ground-truth set, and `accuracy_runs` keeps the
  history after the labels are gone.
- The real-data CI gate is only as current as the last export. Re-exporting after a labelling
  session is a manual step (`tracelet accuracy export`, then `gh secret set`).
- Replay inherits `asn_profiles` as it is *now*. A profile rebuilt after a database update
  can change a replayed registry-artifact decision. This is the right behaviour for tuning,
  since it is what the engine would do today, but it means a replay is not a historical
  record. `accuracy_runs` and the stored visit are.

## Alternatives considered

| Option | Why not |
|---|---|
| **Full re-inference from `ip_enc`** | Repeats lookups whose answers have moved on (database updates, PTR changes, external quotas), so a difference would not isolate the tuning change. Decrypts addresses (audited, rate-limited) for every scoring pass. Reaches only the last 30 days. |
| **Keep labelled visits' `ip_enc` past 30 days** | A privacy change to ADR-0007 and F12, for a capability replay does not need. |
| **Score the stored decision, no replay** | Mixes every settings version the labels were inferred under, so a number cannot be attributed to one version, and a proposed version cannot be scored before it is saved. The stored decision is still shown beside each label. |
| **Commit the real fixture** | The repository is public: it would publish the cities and ISPs the owner tested from. |
| **Synthetic fixture only** | F14.AC12 asks for the ground-truth fixture: a gate on made-up data proves the machinery, not the accuracy. |
| **A dev-database export kept out of git and run only locally** | No CI gate at all. |
