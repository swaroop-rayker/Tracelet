# ADR-0011 — Bot and spoof detection: weighted rules, explicitly not machine learning

**Status:** Accepted (Gate 2, 2026-09-25)
**Deciders:** repository owner
**Relates to:** F5, F3.AC4, F9.AC11, ADR-0004

---

## Context

The brief required detection of bots, crawlers, spam, malware and spoofing, and named
specific techniques: headless browser detection, GPU and OS cross-checking, iOS memory
protection, a stealth honeypot trap, device fingerprint collision for proxy detection, and
agreement and conflict scoring.

It also required, separately and repeatedly, that the **source** of every derivation be
recorded and displayed. That requirement constrains the detection approach more than it
first appears: a verdict that cannot be explained cannot satisfy it.

Three further facts:

- **There is no labelled training data.** Ground truth for this system will be 30 to 60
  owner-labelled visits (F4.AC15), which is for location accuracy and is nowhere near a
  training set for classification.
- **Every classification feeds the notification decision** (F7.AC1). A false `bot` verdict
  means a real visitor generates no alert — a silent failure the owner would never notice.
- **Enrichment data is attacker-controlled** (ADR-0004). A client can claim any screen
  size, GPU, timezone or capability.

## Decision

**A weighted rules engine. Explicitly not machine learning, and not a paid service.**

### Structure

```
server-side rules (always run)  +  client-side rules (when enriched)
                    ↓
        cross-checks: claim vs. observation
                    ↓
        fingerprint collision analysis
                    ↓
        honeypot
                    ↓
  classification + bot_score + spoof_score + fired signals[]
  + agreement_score + conflict_score + classifier_version
```

### The insight that organises the whole engine

**The strongest signals are not individual properties — they are contradictions between
what a client *claims* and what the network *shows*.**

A client can lie about any single value. It cannot easily make its lies consistent with
each other and with observations it does not control. Hence the cross-check family:

| Cross-check | Why it works |
|---|---|
| WebGL renderer vs claimed OS | An Apple GPU under a Windows UA is not a configuration that exists |
| `navigator.deviceMemory` present + iOS UA | **Safari on iOS never exposes it.** Its presence is proof of a lie — the brief "iOS Memory Protection" |
| UA-CH platform vs UA string | Two sources for the same fact; automation often updates only one |
| Header **order** vs claimed client | Real browsers have stable, characteristic header ordering that HTTP libraries do not reproduce |
| Browser timezone vs inferred country | A cheap proxy and VPN signal |
| Screen dimensions vs device class | 1920x1080 under a mobile UA |
| Impossible travel for a `visitor_id` | Two continents in five minutes |

### Fingerprint collision, in both directions

Same `fingerprint_id` across ≥N distinct ASNs in a window → `is_proxy_suspected`.
The **inverse is explicitly not a proxy**: many distinct fingerprints behind one prefix is
NAT or a carrier gateway. Getting that direction wrong would misclassify every visitor
behind a mobile carrier, which in India is most of them.

This is also why `fingerprint_id` excludes the IP prefix from its derivation (ADR-0006) —
the detection is meaningless if the identifier changes with the network.

### Output: fully explainable

Every fired rule is stored with its weight and evidence in `visits.signals` (JSONB, GIN
indexed). The dashboard shows the complete list per visit (F5.AC2), and ranks firing
frequency across all visits (F9.AC11) so a noisy rule is visible.

Thresholds are versioned configuration; every visit carries `classifier_version` so a
tuning change does not silently invalidate historical comparisons.

### Scope clarification on "malware"

Tracelet cannot inspect a visitor device, so device-level malware detection is not
possible. The requirement is scoped to **malicious-automation signals** — known-malicious
ASN and UA lists, scanner and exploit-probe patterns on the capture path — surfaced under
`classification='spam'` with the specific signals attached. Logged as amendment 2 in
`SPEC.md` section 11 rather than silently reinterpreted.

## Alternatives considered

| Option | Why rejected |
|---|---|
| **A trained classifier** | The instinctive modern choice. Three disqualifying problems: **(1) no labelled data** — and labels would need to come from the rules engine anyway, so the model would learn to imitate the thing it replaces; **(2) unexplainable verdicts**, which directly breaks the brief requirement to show how each value was derived; **(3) silent poisoning risk** — a bad model update would misclassify traffic for weeks before anyone noticed, and every affected visit would be permanently mislabelled in the database. For a solo-operated system with no monitoring stack, unexplainable and silent is the worst combination available. |
| **A commercial bot-detection service** | Substantially better than anything here, and how real products solve this. Fails C6, free tier only. |
| **JA3/JA4 TLS fingerprinting** | **The single strongest signal available** — it fingerprints the TLS library, which automation cannot easily disguise. Requires access to the TLS handshake, which Caddy does not expose without a plugin, and terminating TLS elsewhere adds infrastructure. **Deferred out of v1** (SPEC amendment 5, RISKS R7), with header-order and HTTP/2 fingerprinting substituted as the closest available approximation. |
| **UA string matching alone** | What most naive implementations do. Trivially spoofed; useful only as one input among many, which is how it is used here. |
| **CAPTCHA** | Effective and unacceptable: it would add friction to a 700 ms redirect for every visitor, and solving or bypassing bot-detection challenges is outside the scope of what this system does to people. |

## Consequences

**Positive**

- Every verdict is explainable, satisfying the brief and making tuning empirical.
- No training data required; the engine works from the first visit.
- Thresholds are configuration, so tuning needs no deployment and is reversible.
- Cross-checks are hard to defeat without making a client internally consistent across
  properties the attacker does not fully control.
- Signal-frequency analytics make a misbehaving rule visible rather than mysterious.

**Negative, and accepted**

- **Manual tuning, indefinitely.** Rule weights need periodic revision as browsers and
  automation evolve. There is no self-improvement mechanism.
- **A determined, well-resourced adversary will pass.** A patched Chromium with consistent
  spoofing across UA, UA-CH, WebGL, screen, timezone and header order, from a residential
  proxy, will be classified `human`. Without JA4 there is no reliable defence against that,
  and the honest position is that this engine raises cost, it does not stop anyone. Stated
  plainly in `docs/private/04-ANTI-BOT-DEEP-DIVE.md`.
- **False positives are silent.** A misclassified human produces no notification and the
  owner never learns they missed a visit. Mitigations: conservative thresholds, the
  crawler view showing what was excluded, and signal-frequency monitoring — but the failure
  mode remains asymmetric and that should be understood.
- **Rules drift out of date.** Headless-detection signals in particular are a moving
  target; several classic ones have already been fixed upstream. They need periodic review,
  and a rule that never fires is as informative as one that always does.
- **Client-side rules do not run for `server_only` visits**, which may be the majority of
  social traffic (ADR-0004). Classification for those visits rests on server signals alone
  and is correspondingly weaker — which is why stage mix must accompany any classification
  statistic (F9.AC20).

## Revisit if

- JA4 becomes obtainable without new infrastructure — a Caddy plugin reaching acceptable
  maturity would justify reopening SPEC amendment 5. It would be the largest single
  improvement available to this engine.
- Signal-frequency analysis shows a rule firing on essentially all traffic or on none;
  either means it is no longer measuring what it was written to measure.
- Enough labelled classification data accumulates to make a model trainable **and** an
  explanation mechanism is available for it. Both conditions, not one.

---

## Amendment (M4, 2026-09-29) — as built

Recorded before the code, per CLAUDE.md section 2. The decision — weighted, explainable
rules — is unchanged; these are the places where building it showed the text above to be
wrong or incomplete.

1. **Header *set*, not header order.** The cross-check table's "header order vs claimed
   client" cannot be built behind Caddy (RISKS R19); SPEC section 11 row 7 replaced it with
   the header set: which headers a real browser of the claimed family always sends
   (`Sec-Fetch-*` for any modern browser, `Sec-CH-UA*` for Chromium) and whether their
   values agree with the UA string. Weight that header order would have carried moves to
   the UA-CH cross-checks, the headless probes, the honeypot and network reputation.
2. **Impossible travel is per `fingerprint_id`, not per `visitor_id`.** ADR-0006 derives
   `visitor_id` from the fingerprint *and the IP prefix*, so two visits sharing one are on
   the same network by construction and can never be far apart. The network-independent
   `fingerprint_id` is the identifier for which "two places at once" means anything.
3. **Where it runs.** Classification joins location inference in the ADR-0015 job, in the
   same write transaction, after inference — the tz-vs-country and datacenter rules need
   inference's output. It never runs in a request; a classifier failure is `unknown` with
   `classifier.engine_error`, and the redirect is untouched (F5.AC14).
4. **Weights and thresholds are a `classifier` section of the versioned
   `inference_settings`** (owner decision): one settings history, one audit trail, one
   rollback. `classifier_version` is `<classifier revision>+s<settings version>`.
5. **Datacenter covers Tor and hosting-ASN VPN egress.** The class list has no `tor` or
   `vpn`; the address describes infrastructure, as F4.AC12(c) already says for location,
   so the class is `datacenter` and `is_tor` / `is_vpn_suspected` say which. A real person
   behind a VPN is therefore not `human` — the conservative error for an alerting system
   (CLAUDE.md invariant 6).
6. **A `server_only` visit can be `human`**, on server evidence alone: a header set
   consistent with a real browser, a residential network, nothing fired. Requiring client
   evidence would make every visit whose enrichment the webview killed — the iOS in-app
   case, R5 — permanently `unknown`, and never notifiable.
7. **Precedence** when several classes are supported: `crawler` (a known preview fetcher)
   → `spam` (malicious-automation signals, F5.AC13) → `bot` (automation evidence at or
   over the bot threshold) → `datacenter` → `spoofed` → `human` → `unknown`.
