# ADR-0027 — The M9 load test uses k6, in a container, never on the production box

**Status:** Accepted (2026-10-10).
**Deciders:** repository owner
**Relates to:** SPEC NFR1, NFR2, NFR6, F14.AC2, F11.AC2-AC3, F11.AC9; ADR-0010 (rate limiting,
shedding), ADR-0012 (the e2-micro); RISKS R6; CLAUDE.md invariants 1 and 6

---

## Context

NFR1.AC4 and F14.AC2 require a load test on the real e2-micro: 10 concurrent capture
requests sustained, 500 a day, 2 admins working the dashboard at once, with NFR2's p95
targets and NFR6's 700 MB measured while it runs. Nothing in the repository can generate
that load, so this is a new tool (CLAUDE.md §3).

Three things constrain it:

1. **It must not run on the 1 GB box.** A load generator there would be measuring itself.
2. **The capture limits are per network** (30 a minute and 300 an hour per /24, F11.AC2). A
   test from one address is rate-limited after its burst, and a rate-limited visit takes the
   cheap path (F11.AC3), so the test would measure the wrong thing.
3. **The test must not make the product misbehave.** It must not alert anyone and must not
   leave its visits in the owner's analytics.

## Decision

1. **k6** (`grafana/k6`, pinned by version), run as a one-off container from the owner's PC
   by `./scripts/tl loadtest <url>`, behind the `tools` profile. Never on the VM, never in
   CI's default path. Its scripts are JavaScript in `loadtest/`, versioned with the code.
2. **Scenarios:**
   - **capture:** 10 virtual users looping on a dedicated test link for 15 minutes, a cold
     visit each time (no cookie);
   - **dashboard:** 2 virtual users signed in as a test owner, cycling the analytics,
     geography and health endpoints;
   - **headroom (NFR1.AC5):** capture at 30 concurrent for 3 minutes; the pass condition is
     degradation (slower, or shed with the visitor still redirected), never an error.
3. **Limits for the window, raised and restored by the owner** from System Health (F11.AC9).
   Both changes are audited (CLAUDE.md invariant 9). A final short scenario at the default
   limits checks that the limiter engages and every redirect still lands.
4. **Thresholds are the specification:** k6 fails on any non-redirect response, and on a
   capture p95 above NFR2.AC2's direct-path figure. The server-side figures (NFR2.AC1's
   50 ms, AC4's 300 ms) come from the application's own timing logs for the window, because
   the client-side number includes the network.
5. **Memory is sampled on the VM during the run:** `docker stats` per container and the
   host's figures, for NFR6.AC1 and the shedding threshold (R6).
6. **No side effects:** the test link has no alert rule, and k6's user agent classifies as a
   bot (invariant 6). After the run the owner deletes the link and its visits (an audited
   owner action). Results go in `docs/LOADTEST.md` with the commit, the date and the raw
   summaries.

## Alternatives considered

| Option | Why not |
|---|---|
| **Locust** | Python, so the tests would read like the codebase. It needs a Python environment on the host or its own image, and its default UI wants a browser. k6 is one static binary in a container with built-in thresholds. |
| **`hey` / `wrk` / `oha`** | Hammer one URL. They cannot sign in an admin, carry a session, or express the mixed scenario NFR1 describes. |
| **A Python script with `httpx`** | No new dependency. It would mean writing percentiles and thresholds by hand, and it would be the least-tested code in the repository on the day it matters. |
| **Run it from a GCP Cloud Shell** | Free, and its network is close to the VM, so it would measure the server rather than India's network. Kept as a fallback if the PC's TLS inspection skews the numbers (E21); the server-side figures do not depend on where the client is. |
| **Lower the limits' key to /32 for the test** | Changes production code for a test. Raising the limits is already an owner action. |

## Consequences

- One tools-only image in the dependency ledger, with zero production memory cost.
- The results are honest about where they came from: the client is in India, behind TLS
  inspection, so client-side latency is an upper bound; the server's own timings are the
  NFR2.AC1 figure.
- The run leaves an audit trail (the limit changes, the link's deletion), which is the
  intended evidence.
