# CLAUDE.md — Tracelet

Operating rules for any AI assistant or human working in this repository.
Read this file first. It is short on purpose.

---

## 1. What Tracelet is

A visitor-intelligence platform with two surfaces:

1. **Public capture surface** — a tracking link (`/r/{slug}`) that records permitted
   visitor telemetry and then redirects to an admin-configured destination.
2. **Admin dashboard** — protected, admins only. Analytics, geofencing, system health.

Primary target audience for capture: **India-primary, globally functional.**
Deploy target: **a single GCP e2-micro (1 vCPU / 1 GB RAM / 2 GB swap / 30 GB disk).**
Budget: **free tier only.**

---

## 2. Doc maintenance rules (binding)

- **Docs are the source of truth. If code and docs disagree, stop and ask.**
- Any change to **schema, interfaces, or architecture** → update the relevant doc
  **in the same change**. Architectural changes need a **new ADR first**.
- After each milestone: tick `docs/MILESTONES.md`, update affected docs.
- **Never silently change a requirement. Propose the change and wait.**

### Which doc to touch

| Change | Update |
|---|---|
| Requirement added / altered / clarified | `docs/SPEC.md` (new F/AC-ID, or amend + log in §11) |
| DB schema, index, invariant | `docs/DATA_MODEL.md` + an Alembic migration in the same commit |
| Endpoint, payload, error shape | `docs/API.md` + regenerate the TS client |
| Component, data flow, cross-cutting concern | `docs/ARCHITECTURE.md` |
| Any architectural decision | **new** `docs/decisions/ADR-000N-<slug>.md`, before the code |
| Milestone completed | `docs/MILESTONES.md` checklist |
| New risk, or a spike result | `docs/RISKS.md` |
| Bug encountered & fixed | `docs/ERRORS.md` (symptom → root cause → fix → prevention) |
| UI token, primitive, pattern or screen layout | `docs/DESIGN.md` (and an ADR for a new UI dependency) |

### `docs/private/` is NOT source of truth

`docs/private/` is an uncommitted explainer track for the repository owner.
It is **git-ignored and must never be committed.** Because it is invisible to
version control it carries **no authority**: if it disagrees with a committed
doc, the committed doc wins and `docs/private/` gets corrected. Never cite it
as justification for a code change.

---

## 3. Engineering standards (binding)

- **Typed everywhere, strict mode.** `mypy --strict` on all Python;
  `tsc --noEmit` with `strict: true` on all TypeScript. No `Any`, no `any`,
  no `# type: ignore` without an inline reason comment.
- **Lint + format enforced in CI.** `ruff check` + `ruff format --check`;
  `eslint` + `prettier --check`. CI failing on style is not negotiable.
- **Tests:** unit tests for domain logic (inference, scoring, GCRA, crypto);
  integration tests for API + DB against a **real PostgreSQL + PostGIS instance.**
  **Never mock the database in integration tests.**
- **Errors:** typed exception hierarchy → one handler → RFC 9457 Problem Details
  JSON. Every response and every log line carries a ULID `trace_id`.
  Internal detail never reaches a client; it goes to the log under the same id.
- **No new dependency without justification.** Add it to the dependency ledger in
  `docs/ARCHITECTURE.md` §Dependency Ledger with a one-line reason, and state what
  you considered instead. Remember the box has 1 GB of RAM: reject anything that
  drags in `numpy`/`scipy`/`pandas` unless there is no alternative.
- **Conventional commits.** `feat:`, `fix:`, `docs:`, `refactor:`, `test:`,
  `chore:`, `perf:`, `build:`, `ci:`. Scope is the F-ID where one applies —
  e.g. `feat(F4): add rDNS city-code lexicon`.
- **One PR per milestone.** M0..M9, see `docs/MILESTONES.md`.
- **UI follows `docs/DESIGN.md` Part III** (rules UI-1…UI-25 and the §15 checklist):
  primitives and tokens only, no inline styles (CSP), four states per data surface,
  three themes × three widths. *Accepted 2026-10-03; binding from M5.5 on.*

---

## 4. Invariants you must not break

1. **The redirect must never fail.** If capture, inference, the DB, or Telegram is
   broken or rate-limited, the visitor is still redirected to the destination.
   Degrade the telemetry, never the visitor's journey.
2. **The server-side capture is authoritative.** Client-side enrichment is additive
   and is always allowed to fail. Nothing user-visible may depend on client JS
   succeeding. (This is why the Instagram-webview bug is fixed by design — see
   `docs/ERRORS.md` B3.)
3. **No open redirect.** Destinations come only from `links.destination_url` rows
   set by an authenticated admin. Never from a query parameter, header, or path.
   Violating this re-triggers the Safe Browsing "unsafe site" warning (B4).
4. **Never persist a raw IP in plaintext.** `HMAC(ip)` + `/24`-or-`/48` prefix +
   ASN are durable; the full address is AES-256-GCM only, with a TTL.
5. **Never act on a location we do not believe.** Strict fields abstain with a
   recorded reason, and only strict fields drive geofencing, alerts' "confirmed"
   location and accuracy metrics. Advisory fields are the best guess -- the
   highest-confidence value at each level, always equal to strict where strict
   emitted -- and are what the dashboard shows, always with their confidence and an
   evidence trail. See ADR-0005 and ADR-0018.
6. **Never notify on a bot.** Telegram alerts fire only for `classification='human'`.
7. **Capture-path URLs stay boring.** Never use `track`, `collect`, `analytics`,
   `pixel`, `beacon`, or `telemetry` in a public path or query key — content
   blockers match those substrings and will silently kill the request (B6).
8. **Nothing in `docs/private/` is ever committed.**
9. **Only `owner` role performs destructive or configuration actions.**
   Every one of them writes an `audit_log` row.
10. **Every inference/classification result is stamped** with `inference_version`
    and `classifier_version`, so accuracy metrics stay comparable over time.

---

## 5. Memory discipline

The production box has 1 GB. Steady-state budget is in
`docs/ARCHITECTURE.md` §Memory Budget. Before merging anything that adds a
dependency, a container, or a long-lived in-process cache, state its expected
RSS. Specific landmines:

- Argon2id is pinned to `m=32MiB, t=3, p=1`. **Library defaults (64 MiB–1 GiB)
  will OOM the box on a single login.** Never take the default.
- GeoIP database updates stream to disk and validate in a memory-capped
  subprocess. Never load a database fully into memory to verify it.
- `.mmdb` files are `mmap`'d read-only, so they are page cache, not RSS. Keep it
  that way — do not read them into bytes.
- Every container carries a hard `mem_limit`. The failure mode must be "one
  container restarts", never "the kernel picks a victim".

---

## 6. Local commands

```bash
make bootstrap    # build images, run migrations, create the first owner admin
make up           # docker compose up -d
make verify       # lint + typecheck + unit + integration (the CI gate, locally)
make migrate      # alembic upgrade head
make fmt          # ruff format + prettier --write
make logs         # docker compose logs -f
make down         # docker compose down
```

**On Windows, use `./scripts/tl <task>` instead.** `make` is not present in Git
Bash on a stock Windows host, and the documented dev environment is Win11 +
Docker via WSL (SPEC C5). `scripts/tl` is the single implementation and the
`Makefile` only delegates to it, so the two cannot diverge:

```bash
./scripts/tl verify     # identical to `make verify`, runs anywhere
./scripts/tl help       # every task
```

Every task runs inside a container — no Python and no Node are required on the
host. Lint, typecheck and tests run in the profiled `api-tools` / `web-tools`
services, so the running `api` image stays production-lean.

Fresh clone to running, in 5 commands — see `docs/KICKOFF.md` §Quickstart.

---

## 7. Where to look first

| Question | File |
|---|---|
| What are we building, exactly? | `docs/SPEC.md` |
| How is it put together? | `docs/ARCHITECTURE.md` |
| Why is it put together that way? | `docs/decisions/ADR-*.md` |
| What are the tables? | `docs/DATA_MODEL.md` |
| What are the endpoints? | `docs/API.md` |
| What am I building next? | `docs/MILESTONES.md` |
| What could go wrong? | `docs/RISKS.md` |
| How should it look and behave? | `docs/DESIGN.md` |
| Has this bug happened before? | `docs/ERRORS.md` |
