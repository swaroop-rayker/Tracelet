# KICKOFF — Tracelet

**Status:** approved at Gate 3, 2026-09-25. Implementation may begin.
**Owner:** solo developer.
**This file is the "start here" page.** It records what was decided, what you must
obtain before writing code, and what M0 looks like.

---

## 1. One-paragraph description

Tracelet is a self-hosted visitor-intelligence platform. A public tracking link
(`/r/{slug}`) — placeable as a standalone page or inside a social bio on Instagram,
LinkedIn or Reddit — records permitted visitor telemetry server-side, enriches it
best-effort from the browser, classifies the visitor as human or automated, infers
location from up to eleven independent signals with explicit confidence and
abstention, evaluates admin-drawn geofences, alerts the owner over Telegram, and
redirects to an admin-configured destination. A protected admin dashboard provides
analytics, visualisation, geofence drawing, and system operations. It runs entirely
on free-tier infrastructure: one GCP e2-micro, 1 vCPU, 1 GB RAM, 2 GB swap, 30 GB disk.

---

## 2. Decisions locked at Gate 2

| Area | Decision | ADR |
|---|---|---|
| Backend | FastAPI + Pydantic v2 + SQLAlchemy 2.0 (async) + Alembic | ADR-0001 |
| Database | PostgreSQL 16 + PostGIS | ADR-0002 |
| Frontend | React 19 + TypeScript strict + Vite, static-built, Caddy-served | ADR-0003 |
| Charts / maps | Apache ECharts, Leaflet + Geoman; self-hosted Natural Earth outlines, no tiles | ADR-0003, ADR-0017 |
| Capture model | Server-authoritative write; client enrichment additive; 90 s sweeper | ADR-0004 |
| Location | Multi-candidate consensus, dual strict/advisory output, 3 suppression rules | ADR-0005 |
| Identity | HMAC pseudonymous `visitor_id` (stable pepper) + rotating `session_fp` | ADR-0006 |
| IP at rest | AES-256-GCM, key outside the DB, 30-day TTL purge | ADR-0007 |
| Auth | Opaque server-side sessions, `__Host-` cookie, mandatory TOTP | ADR-0008 |
| Background work | Transactional outbox + in-process asyncio worker. **No Celery, no ARQ** | ADR-0009 |
| Rate limiting | Layered: Cloudflare, Caddy, Postgres-backed GCRA, bounded pool | ADR-0010 |
| Bot detection | Rules-based weighted scoring. **Explicitly not ML** | ADR-0011 |
| Deploy | 3 containers, hard `mem_limit`s, domain-agnostic config | ADR-0012 |
| Errors | RFC 9457 Problem Details + ULID `trace_id` + structlog. **No Prometheus** | ADR-0013 |
| Cache / queue store | **Postgres only. No Redis / Valkey** | ADR-0009, ADR-0010 |
| Backups | Nightly `pg_dump`, 7d + 4w rotation, monthly automated restore-verify | ADR-0014 |
| Edge | Cloudflare free plan on the purchased-domain path | ADR-0012 |

---

## 3. What you must obtain before M1

All free. Nothing here needs a credit card.

| # | Item | Where | Needed by | Notes |
|---|---|---|---|---|
| 1 | **Telegram bot token + chat ID** | `@BotFather` on Telegram | M1 | Mandatory. Also the password-recovery channel — there is no email provider in this design |
| 2 | **MaxMind account + licence key** | maxmind.com GeoLite2 signup | M3 | GeoLite2-City + GeoLite2-ASN. MaxMind will email you about the account |
| 3 | **IP2Location LITE account** | lite.ip2location.com | M3 | DB11 (city + lat/lng) |
| 4 | **IPinfo Lite token** | ipinfo.io lite | M3 | Country + ASN voter |
| 5 | **DB-IP Lite** | db-ip.com lite | M3 | No signup. CC-BY — **attribution required** on the privacy/about page |
| 6 | **GeoNames `cities15000.txt` + `admin1CodesASCII.txt`** | download.geonames.org | M3 | No signup. CC-BY 4.0 — attribution required |
| 7 | **A domain name** (approx. 1–12 USD/yr) | any registrar | M9 | See the warning below |
| 8 | **Cloudflare free account** | cloudflare.com | M9 | Requires delegating the domain nameservers |

### Warning about the domain — read before M9

The brief required free-tier-only, and at Gate 1 you asked for both the
purchased-domain path and the free-subdomain path. Both are built and config-switched.
**They are not equivalent.** The free-subdomain path (DuckDNS, sslip.io) cannot sit
behind Cloudflare, because Cloudflare requires nameserver delegation of the zone and
you do not control `duckdns.org`. Choosing it forfeits, all at once:

- **Edge TLS** — capture TTFB from India goes from roughly 300 ms to 800–1000 ms
- **The `CF-Ray` colo signal** — a metro-level geo hint that works even inside the
  Instagram webview, at zero cost to the visitor
- **`CF-IPCountry`** — a high-quality country voter
- **Layer-0 DDoS absorption** and origin-IP hiding
- and it retains the browser "unsafe site" reputation problem (B4) that the purchased
  domain exists to solve

The purchased-domain path is the supported path. The free-subdomain path is a
documented degraded mode. See ADR-0012 and `docs/RISKS.md` R8.

---

## 4. Quickstart — fresh clone to running, 5 commands

```bash
git clone <repo> tracelet && cd tracelet
cp .env.example .env
TRACELET_BOOTSTRAP_EMAIL=you@example.com ./scripts/tl bootstrap
./scripts/tl up
./scripts/tl verify
```

`make <task>` does the same thing where `make` exists; `./scripts/tl <task>` works
everywhere, including Git Bash on a stock Windows host where `make` is absent. Both call
the same implementation — `docs/ARCHITECTURE.md` §9.1.

**Step 2 needs editing before step 3:** the database passwords, the three HMAC peppers,
the session secret, the domain mode, and the Telegram bot token. Every variable is
documented in `.env.example`. You also need the AES-256 key that encrypts the TOTP secret
and, from M2, visitor IPs:

```bash
mkdir -p secrets && openssl rand -hex 32 > secrets/ip_key
```

Keep a copy of that file somewhere outside the machine. It is deliberately **not** in the
database backup, so restoring onto a fresh VM without it leaves every encrypted value
permanently unreadable (ADR-0007).

**Step 3 prints a one-time enrollment URL.** Open it, set a password, add the shown base32
secret to an authenticator app, type a code — and then **save the 10 recovery codes**,
which are displayed exactly once. There is no seeded default password anywhere in this
system, by design (ADR-0008, F8.AC15).

Two things worth doing immediately afterwards, from the dashboard:

- **Verify your Telegram chat.** Until you do, the reset-over-Telegram path is not armed
  and your only recovery routes are the codes and the server CLI (ADR-0008).
- **Check the recovery codes are somewhere you will actually find them.** With no codes and
  no Telegram, only `docker compose run --rm cli tracelet admin reset-password` can recover
  the account — which needs shell access to the box (F8.AC8).

---

## 5. M0 — the first milestone

**Goal:** an empty but fully-wired skeleton that proves the toolchain, not the product.

Scope:
- Repo layout, `docker-compose.yml` with 3 services and hard `mem_limit`s
- PostgreSQL 16 + PostGIS container, Alembic baseline migration, `postgis` extension
- FastAPI app with `/healthz`, `/readyz`, and the RFC 9457 error handler wired
- `structlog` JSON logging with ULID `trace_id` propagation and PII redaction
- Vite + React + TS strict skeleton, built in a multi-stage image, served by Caddy
- Caddy with automatic HTTPS, HSTS, CSP, security headers, `trusted_proxies` toggle
- `Makefile` with the seven targets in `CLAUDE.md` section 6
- GitHub Actions: `ruff`, `mypy --strict`, `pytest` (unit + integration against a
  **real** Postgres service container), `eslint`, `tsc --noEmit`, `prettier`,
  OpenAPI-to-TS drift check, `docker build`, commitlint, and `guard-private-docs`
- `.env.example` with every variable documented

**Done when:** `make verify` is green on a fresh clone in 5 commands, `/healthz`
returns 200 through Caddy over HTTPS, and CI is green on the M0 PR.

**Do not** build any product feature in M0. Its only job is to make every later
milestone cheap.

---

## 6. Two spikes to run during M0/M1 — before building against them

Both are tracked in `docs/RISKS.md`. They are here because each one can invalidate a
milestone you would otherwise have already written.

### Spike A — rDNS coverage (R3). Highest priority.

The city-accuracy plan leans on Indian residential IPs having PTR records that encode
a metro code (`blr`, `mum`, `hyd`, `maa`, `pnq`, `abts-kk-static-*`). **If real-world
coverage is below roughly 30 percent, the F4 city targets in `docs/SPEC.md` must be
revised before M3 is built.** Method: sample a few hundred Indian residential IPs
across Airtel, Jio, ACT, BSNL and Vi; resolve PTR; measure the fraction bearing a
recognisable code. Half a day. Record the result in `docs/RISKS.md` R3 and, if needed,
open a SPEC amendment.

### Spike B — Instagram webview enrichment (R5).

Does `fetch(url, {keepalive: true})` survive navigation inside the Instagram in-app
browser? This decides whether the 90 s sweeper is a rare fallback or the primary path
for social traffic. Needs a real phone and a real Instagram bio link; it cannot be
answered from a desktop emulator.

**Run 2026-09-28, inside M2: survives on Android; iOS unmeasured.** Results in RISKS R5.

---

## 7. Honest expectations

- **Country accuracy of 99.5 percent or better is realistic. 100 percent state
  accuracy is not achievable** by anyone using free passive geolocation, which is why
  F4 is specified as precision-with-abstention plus an advisory best guess. See
  ADR-0005 and SPEC section 3, RW-1.
- **Safe Browsing may still flag the site** even on a clean purchased domain. A review
  request is the only remedy; there is no guarantee. RISKS R8.
- **The IP-encryption key lives on the same VM** as the database, because free tier
  means no KMS. This defends a stolen dump, a leaked backup, and SQL injection. It does
  **not** defend a root compromise of the VM. ADR-0007.
- **30 to 60 ground-truth labels give wide confidence intervals.** Any accuracy figure
  Tracelet reports about itself must be read with that in mind. RISKS R9.

---

## 8. Reading order for a new contributor

1. This file
2. `CLAUDE.md` — the rules
3. `docs/SPEC.md` section 4 — what the features are
4. `docs/ARCHITECTURE.md` — how it fits together
5. `docs/decisions/` — why, in order
6. `docs/MILESTONES.md` — what to do next

If you are the repository owner, `docs/private/01-SYSTEM-WALKTHROUGH.md` traces a
single visitor request through every component and is the fastest way to build a
mental model.
