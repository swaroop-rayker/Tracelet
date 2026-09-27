# ADR-0012 — Deployment: three containers, Cloudflare edge, dual domain paths

**Status:** Accepted (Gate 1 and Gate 2, 2026-09-25)
**Deciders:** repository owner
**Relates to:** C4, C6, F13, F14, B4, NFR1, NFR2, NFR6

---

## Context

The brief fixed the target: Docker on a GCP e2-micro — 1 shared vCPU, 1 GB RAM, 2 GB swap,
30 GB disk — on the free tier only.

Two facts about that target shape everything:

1. **GCP free-tier e2-micro exists only in `us-west1`, `us-central1` and `us-east1`.** An
   Indian visitor is roughly 250 ms away. A cold TCP + TLS + request cycle is 3 to 4 round
   trips, so **time to first byte is 800 to 1000 ms before a single line of code runs.**
   No amount of server optimisation touches this.
2. **1 GB is the binding constraint.** Steady state must sit at or below 700 MB (NFR6.AC1).

At Gate 1 the owner was offered Oracle Cloud Always Free (4 ARM vCPU, 24 GB, Mumbai and
Hyderabad regions) as a strictly larger free alternative and **chose to keep GCP e2-micro
as specified**.

On the domain question, the owner asked for **both** the purchased-domain path and the free
dynamic-DNS subdomain path to be supported.

## Decision

### Three containers

| Service | `mem_limit` | Role |
|---|---|---|
| `caddy` | 64 M | Automatic TLS, HTTP/2, HSTS, nonce CSP, security headers, L1 limits, static SPA serving, `trusted_proxies` |
| `api` | 420 M | Gunicorn supervising 2 Uvicorn workers, plus the outbox worker and scheduler |
| `db` | 260 M | PostgreSQL 16 + PostGIS, tuned per ADR-0002 |

**Hard `mem_limit` on every container is not a formality.** It converts the failure mode
from "the kernel OOM-killer picks a victim, possibly PostgreSQL mid-write" into "one
container restarts and its healthcheck reports it". That distinction is the difference
between a blip and a corrupted afternoon.

The SPA is built at image-build time and served as static files. **No Node.js runtime in
production** (F14.AC4).

### Cloudflare free plan on the purchased-domain path

This was reframed at Gate 2 from a nice-to-have into the **single largest performance and
accuracy lever in the design**:

| What it gives | Why it matters here |
|---|---|
| **Edge TLS in Bangalore/Mumbai** | TTFB from roughly 1000 ms down to roughly 300 ms. Solves a problem the origin cannot |
| **`CF-Ray` colo code** | A metro-level geo signal (ADR-0005, S8) that is **server-side**, costs the visitor nothing, and **survives the Instagram webview** where every client-side signal may not |
| **`CF-IPCountry`** | A high-quality country voter, free tier included |
| **L0 DDoS absorption** | Most of the brief DDoS requirement, on a box that 1 vCPU makes trivially floodable |
| **Origin IP hidden** | Direct-to-origin attack becomes harder |

### Both domain paths supported, and explicitly not equivalent

One configuration variable selects the mode. The same image serves both (F13.AC5).

**The asymmetry must stay visible.** Cloudflare requires delegating the zone nameservers,
which is impossible for `duckdns.org` or `sslip.io` because you do not control those zones.
So the free-subdomain path forfeits, together: edge TLS, the `CF-Ray` colo signal,
`CF-IPCountry`, L0 DDoS absorption, origin hiding — **and it retains the B4 browser-trust
problem that buying a domain exists to solve.**

**The purchased-domain path is the supported path. The free-subdomain path is a documented
degraded mode.** Recorded here, in `KICKOFF.md` section 3, and in RISKS R8 so it cannot
quietly be treated as a like-for-like choice.

### Real client IP, safely

`CF-Connecting-IP` is trusted **only** when the peer address is in a verified Cloudflare
range; otherwise the peer address is used (F13.AC6). A forged header must never be
trusted — otherwise any visitor could choose their own apparent IP and defeat both rate
limiting and geolocation.

### Setup in five commands

`git clone` → `cp .env.example .env` → `make bootstrap` → `make up` → `make verify`
(F14.AC3, SC2).

## Alternatives considered

| Option | Why rejected |
|---|---|
| **Oracle Cloud Always Free (4 ARM vCPU / 24 GB, Mumbai)** | Technically superior on every axis that matters here: 24x the memory, and a region that removes the latency floor entirely rather than masking it with a CDN. Offered at Gate 1 and **declined by the owner in favour of the stated constraint.** Genuine downsides that make the decision defensible either way: ARM64 requires multi-arch image builds, Always-Free ARM capacity is notoriously hard to provision in popular regions, and idle accounts can be reclaimed. |
| **A single container running everything** | Smallest footprint. Rejected: one process crash takes down all three roles, memory limits become unenforceable per role, and PostgreSQL in a container with an application supervisor is an operational trap. |
| **Managed PostgreSQL** | Would free roughly 210 MB and remove backup responsibility. No genuinely free tier with adequate storage. Fails C6. |
| **Kubernetes (k3s)** | Roughly 300 MB of control plane on a 1 GB box to orchestrate three containers that never scale. Complexity with negative return. |
| **Nginx or Traefik instead of Caddy** | Both fine. Caddy chosen for automatic certificate management with no extra component — on a solo-operated system, certificate renewal failing silently at month three is a realistic outage, and Caddy removes that class of problem. |
| **Serverless (Cloud Run / Lambda)** | Cold starts on a latency-critical redirect, plus a stateful PostgreSQL requirement that free serverless tiers do not serve. |
| **A CDN other than Cloudflare** | None offer a free tier with edge TLS in India **plus** a usable request-geography header. The `CF-Ray` colo signal is specifically what makes Cloudflare the right choice rather than a generic one. |

## Consequences

**Positive**

- Fits the stated constraint, with roughly 379 MB of headroom (ARCHITECTURE section 6).
- Per-container memory limits make failures isolated and legible.
- With Cloudflare, the latency floor is mitigated and two geo signals appear for free —
  one of which (`CF-Ray`) is the only metro-level signal that reliably survives the
  Instagram webview.
- Automatic TLS removes a whole class of silent outage.
- The same image deploys to both domain modes, so the choice is deployment-time.

**Negative, and accepted**

- **1 GB has no room for error.** Every dependency, container and in-process cache needs its
  RSS stated before merge (CLAUDE.md section 5). This is a permanent tax on every change.
- **1 shared vCPU means CPU steal is real.** Sustained load can be throttled by the
  hypervisor in ways that do not show up in local testing on a Ryzen 7.
- **Swap is a cliff, not a cushion.** 2 GB of swap on a network-attached disk means that
  once swapping begins, latency degrades severely. L2 request shedding must engage before
  the kernel starts swapping (F15.AC6).
- **Cloudflare is a third party in the request path** on the purchased-domain path,
  terminating TLS. That is a real trust delegation and should be a conscious one.
- **The free-subdomain path is materially weaker** — no L0 DDoS on a 1 vCPU box, no colo
  signal so lower city coverage, and the unresolved B4 problem.
- **GCP free-tier egress is 1 GB/month from North America.** Adequate for 500 small visits
  a day plus dashboard use, and worth monitoring. Map tiles come from CARTO directly to the
  browser, so they do not count against it.
- **A single VM is a single point of failure**, with no failover. Accepted for v1; recovery
  depends on the backup path, which is manual off-VM (ADR-0014, RISKS R11).

## Revisit if

- The M9 load test breaches NFR1 or NFR2 on this hardware — the first response is reducing
  to one Uvicorn worker (roughly 110 MB freed), then raising PostgreSQL `shared_buffers`,
  **before** reconsidering the host.
- Sustained memory headroom falls below roughly 150 MB.
- Egress approaches the free-tier limit.
- The owner reconsiders Oracle Always Free — at which point the ARM64 multi-arch build is
  the main piece of work, and every other decision in this ADR set carries over unchanged.
