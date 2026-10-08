# Tracelet — Analytics and UI enhancement plan

**Status:** Proposed (2026-10-03). **Phase A approved 2026-10-06** and moved into DESIGN §12
E16–E22 and MILESTONES M5.6, which now govern it. **Phases B, C and D approved 2026-10-08**
(C without C2, see "Decisions needed"). Each is written into SPEC, DESIGN and MILESTONES
(M7.5, M7.6, M7.7) as its branch starts, and from then on those documents govern it, not this
plan.
**Author:** repository owner, with Claude
**Shared copy:** https://claude.ai/code/artifact/ce6a722c-e3b0-4386-9d3a-28e63a19e972

---

## Summary

Phase A, seven UI-only additions, comes before M6 and takes about 3 days. Thirteen deeper
features follow in three phases across M6 and M7. None of them collects anything new from
visitors: each uses what a visit already stores.

- **Phase A: before M6, UI only (7 items).** Uses existing endpoints, with no schema or
  API change. It answers "the web app looks plain".
- **Phase B: right after M6 (4 alert types).** Builds on M6's outbox and Telegram worker.
- **Phase C: M7 (5 analytics features).** Needs new queries, and for Sources, new rollup
  dimensions.
- **Phase D: M7 or later (4 workflow features).** Needs small new tables for admin-entered
  data.

**Status: a proposal, not a requirement.** Nothing in the repository changes until the
owner approves an item. On approval, a UI item becomes a DESIGN §12 entry (E16 onwards). A
server item gets a SPEC acceptance criterion and a §11 row, a MILESTONES scope line, and an
ADR where it adds a dependency or changes architecture (CLAUDE.md §2).

```
Phase A comes before M6; the deeper analytics ride with M7
(Shaded [==]: planned milestones. Plain: phases in this plan. Not to scale.)

┌──────────────────┐   ┌==================┐   ┌──────────────────┐   ┌=========================┐
│ Phase A: UI only │ → │ M6 (planned)     │ → │ Phase B: alerts  │ → │ M7 (planned)            │
│ 7 items, ~3.3 d  │   │ Geofences,       │   │ 4 types, ~3 d    │   │ ┌─────────────────────┐ │
│ before M6,       │   │ Telegram         │   │ on the M6 outbox │   │ │ Phase C: analytics  │ │
│ no API           │   │ ADR-0020 first   │   │                  │   │ │ 5 features, ~8.5 d  │ │
└──────────────────┘   └==================┘   └──────────────────┘   │ └─────────────────────┘ │
                                                                     │ ┌─────────────────────┐ │
                                                                     │ │ Phase D: workflow   │ │
                                                                     │ │ 4 features, ~4.5 d  │ │
                                                                     │ └─────────────────────┘ │
                                                                     └=========================┘
```

Only Phase A comes before M6. Phases B, C and D wait for the milestones whose machinery
they reuse.

## Phase A — before M6, UI only

Seven additions, about 3.3 days, with no API, schema or migration change. Each reuses an
endpoint shipped in M5, follows DESIGN Part III (UI-1 to UI-25), and passes the M5.5
Playwright sweep before merge: zero CSP violations in three engines, plus the screenshot
matrix.

| # | Addition | Data it uses (already shipped) | Done when | Effort |
| --- | --- | --- | --- | --- |
| A1 | **Sparklines in the KPI cards**: a 30-point trend under Visits, Human share and Location consent | `/analytics/timeseries` by day: `metric=visits`, `split_by=classification`, `metric=consented` | Same window as the KPI. The minimum and maximum are in the card's accessible name. Unique visitors and Enrichment get no sparkline, because no per-day series exists for them | 0.5 d |
| A2 | **State map card on Overview**: the Geography map at card size | `/analytics/geo` and the M5 outlines (ADR-0017) | No tiles. A click on a state applies its filter (E3). The card links to Geography | 0.5 d |
| A3 | **Live feed**: the newest visits arrive at the top, with a pulsing dot and "visitors in the last 30 min" | `GET /api/v1/visits`, polled every 15 s | Polling stops while the tab is hidden. Animation respects reduced motion. Load is one request per 15 s | 0.5 d |
| A4 | **Hour × weekday heatmap**: when links get opened, in IST | `/analytics/timeseries?bucket=hour`, folded into 7 × 24 in the browser | Cells in the reporting time zone, with Data and CSV like every chart. Over 31 days it shows an empty state with that reason (hourly windows are capped at 31 days) | 0.5 d |
| A5 | **Link detail page** at `/links/:slug`: KPIs, trend, funnel, app or browser, states and recent visits for one link | `GET /api/v1/links`, plus `link_id` on every analytics call | A "Links" sidebar entry and breadcrumb; four states on every panel. Creating and editing links stays in M7 (F10.AC6) | 1 d |
| A6 | **Daily volume fix**: zero-visit days drawn as empty cells | `/analytics/calendar` | The legend starts at 1 visit, so the year no longer looks active | 0.1 d |
| A7 | **Device and platform icons** in tables and lists | The Lucide registry (ADR-0019) | Generic icons only (phone, desktop, in-app window, mobile network, broadband). No brand logos | 0.25 d |

## Phase B — right after M6: more alert types

Four alert types reuse M6's outbox, worker, quiet hours and Alerts page. Together they take
about 3 days, and each needs a new F7 acceptance criterion (F7.AC10 onwards). All of them
fire for `classification='human'` only (invariant 6). Any place they name is the strict
location, or is worded as a best guess.

| # | Alert | Fires when | Exactly once, by | Kind | Effort |
| --- | --- | --- | --- | --- | --- |
| B1 | **Daily digest** | At a set time (default 09:00 IST): yesterday's visits, the human share, top states, top links, and any dead-lettered alerts | `dedup_key` `digest:{date}` | Scheduled job; held by quiet hours | 1 d |
| B2 | **Volume spike** | A link's human visits in the last 60 min exceed both an absolute floor and k × its median for that hour over the last 7 days. The owner sets the floor and k | `spike:{link}:{hour}` | Scheduled job (every 5 min, advisory lock) | 1 d |
| B3 | **First visit from a new place** | The first human visit to a link from a strict country or strict state not seen before for that link | `newplace:{link}:{region key}` | In the inference transaction, like F7.AC5 | 0.5 d |
| B4 | **Returning visitor** | A visitor id seen on a link more than N days ago comes back (N set by the owner, default 7) | `return:{link}:{visitor}:{date}` | In the inference transaction | 0.5 d |

B3 and B4 are per visit, so they commit atomically with the visit. B1 and B2 summarise many
visits, so they run as scheduled jobs: two workers cannot both send, because the
`dedup_key` unique constraint stops the second insert. Each type gets its own on/off switch
on the Alerts page (owner only, audited).

## Phase C — M7: deeper analytics

Five features, about 8.5 days, with Sources first: its data is already captured on every
visit, and no chart shows it yet. Every query runs on the rollups or on indexed raw rows,
with no numpy or pandas (CLAUDE.md §3).

| # | Feature | What it shows | Server work | Effort |
| --- | --- | --- | --- | --- |
| C1 | **Sources page** | Referrer site, UTM source, medium and campaign, and the in-app browser. Visits that arrive with no referrer (common in the Instagram webview) are counted as "None", not hidden | Four new rollup dimensions (`referrer_host`, `utm_source`, `utm_medium`, `utm_campaign`) and matching filter keys. Only the referrer's host is kept, never its path or query (those can carry personal data). DATA_MODEL §9, a migration that rebuilds days still in raw retention, and API §8 | 2 d |
| C2 | **Automatic insights** | Sentences on Overview with their numbers ("Karnataka: 42 visits, up from 14 the week before"), and markers on unusual days in Visits over time | `/analytics/insights`: week-over-week movers with a noise floor (at least 20 visits and a 2× change), and unusual days by rolling median and MAD (robust z above 3.5), computed in plain Python on rollup rows. Insights state observed changes, never causes | 2 d |
| C3 | **New vs returning, and cohorts** | The new vs returning trend, a weekly cohort grid (first-visit week × weeks after), and the time until a visitor returns | A first-seen query per visitor and link over the existing `(visitor_id, occurred_at)` index. It is limited to raw retention, and says so | 2 d |
| C4 | **Mobile networks by state** | Jio, Airtel, Vi and BSNL share per state, and mobile vs broadband per state | A carrier-family table that maps ASNs (AS55836 Jio, AS24560 and AS45609 Airtel, AS55410 and AS38266 Vi, AS9829 BSNL), crossed with the best-guess state, labelled as such (ADR-0018) | 1.5 d |
| C5 | **Capture quality by platform** | The enriched vs server-only share per app and over time, which shows the in-app-browser loss (RISKS R5) | The funnel stages grouped by `app_medium`. It sits on M7's health pages | 1 d |

## Phase D — M7 or later: workflow

Four features, about 4.5 days, that help the owner explain and reuse what the charts show.
Each stores admin-entered data only, never anything about visitors.

| # | Feature | What it does | Server work | Effort |
| --- | --- | --- | --- | --- |
| D1 | **Annotations** | Notes pinned to a moment ("Posted the reel", optionally for one link), drawn as markers on every time chart | Table `annotations(at, text, link_id, created_by)`. Owner and analyst can add one. Deleting is owner only and writes `audit_log` (invariant 9) | 1 d |
| D2 | **Saved views** (DESIGN E12) | A named filter set, listed in the sidebar and the command palette | Table `saved_views(admin_id, name, query)`. The query is the same URL state filters already use | 1 d |
| D3 | **Compare mode** | Two links, or two periods, side by side, with the differences highlighted | Mostly UI: a second filter state in the URL, sent through the same endpoints | 1.5 d |
| D4 | **Link builder with QR code** | Builds the share URL `/r/{slug}` with UTM tags, and a downloadable QR code drawn in the browser | Belongs with M7's link management (F10.AC6). The destination still comes only from the `links` row (invariant 3). The QR code needs a small library, so it needs an ADR and a ledger entry with its gzipped size | 1 d |

## Guardrails

Every item above has to keep these, and its review checks them.

- **Privacy (SPEC OOS4 to OOS6).** No attempt to find out who a visitor is, no data from
  other sites, nothing new collected. The visitor id stays a pseudonymous HMAC, cohorts are
  aggregates, and only a referrer's host is stored.
- **Strict vs best guess (ADR-0018, invariant 5).** Alerts and geofences act on strict
  fields only. Charts may show the best guess, always labelled, with its confidence.
- **Humans only (invariant 6).** No alert type fires for a bot, crawler, datacenter, spam
  or spoofed visit.
- **CSP and no third parties (F13.AC2, ADR-0017).** No tiles, CDN, hosted fonts or inline
  styles. Every new screen passes the three-engine Playwright sweep with zero violations.
- **1 GB of RAM (CLAUDE.md §5).** No numpy, scipy or pandas, and no long-lived in-process
  cache. Queries run on rollups or on indexed rows, and each server item states its
  expected RSS before merge.
- **Boring public URLs (invariant 7).** Any new query key on `/r/{slug}` avoids track,
  collect, analytics, pixel, beacon and telemetry. The `utm_*` keys are fine.
- **UI rules (DESIGN Part III).** Primitives and tokens only, four states on every data
  surface, and three themes × three widths in the screenshot matrix.

## Decisions needed

The owner ticks what goes ahead; anything unticked stays a proposal.

- [x] **Phase A:** approve A1 to A7, or strike the ones not wanted — *all seven, 2026-10-06*
- [x] **Phase B:** pick the alert types, and confirm the defaults (digest at 09:00 IST,
      returning after 7 days) — *all four, 2026-10-08, with those defaults and a spike floor
      of 10 human visits in 60 minutes and k = 3; every type ships switched off. B3 and B4 add
      a line to the visit's own alert, and send a message of their own only when the visit's
      alert was already sent that day (SPEC §11 row 27). MILESTONES M7.5*
- [x] **Phase C:** confirm the order (C1 Sources first, then C2 Insights) — *C1, C3, C4 and
      C5, in that order, 2026-10-08; **C2 not approved**. For C1, stored referrers are cut to
      the host, existing rows included. MILESTONES M7.6*
- [x] **Phase D:** pick the features, and say whether a QR-code library is acceptable —
      *all four, 2026-10-08; the QR code uses a small library (Nayuki's QR Code generator)
      under ADR-0023. MILESTONES M7.7*
- [x] **Shipping B, C and D:** three branches and pull requests, B stacked on M7 while PR #9
      is open, C on B, D on C — *2026-10-08*
- [x] **Shipping Phase A:** as its own small PR (M5.6) before M6, or folded into PR #6 —
      *its own branch, `feat/m5.6-ui-enhancements`, stacked on M5.5*

What each approval writes into the repository, in the same change as the code:

| Approved | Documents updated |
| --- | --- |
| Phase A item | DESIGN §12 (E16 onwards), and MILESTONES (a scope line, or an M5.6 row) |
| Phase B alert | SPEC F7.AC10 onwards and §11 row 17, MILESTONES, and API §9 for its settings |
| Phase C feature | New SPEC F9 acceptance criteria, DATA_MODEL §9 and a migration for new dimensions, API §8, and MILESTONES M7 |
| Phase D feature | DATA_MODEL plus a migration for each new table, API, an ADR for the QR library (ADR-0023; 0021 and 0022 went to M6 and M7), and the ARCHITECTURE dependency ledger |
