# ADR-0003 — Frontend stack: React + TypeScript + Vite, ECharts, Leaflet

**Status:** Accepted (Gate 2, 2026-09-25)
**Deciders:** repository owner
**Relates to:** C2, F6.AC1, F9, NFR6, NFR7, ES1, ES5

---

## Context

The dashboard is not a CRUD form. It needs:

- **An interactive drawing canvas** — polygons and circles with vertex editing, drag and
  delete, over a map (F6.AC1).
- **Fourteen distinct chart types** including a calendar heatmap, a Sankey diagram, a
  choropleth, a funnel, confidence histograms and a timeline (F9.AC1–F9.AC12).
- **Charts that survive volume** — up to 90 k points in a scatter or timeline view.
- **Composable, URL-shareable filter state** across all of it (F9.AC13).
- **Live polling** on the System Health page (F10.AC1).
- **Three themes**, semi-dark by default, all meeting WCAG AA (F9.AC16, NFR7.AC1).
- **Zero runtime memory cost** on a 1 GB box (NFR6).
- **No API key** for map tiles, since Gate 1 declined additional service signups.

C2 leaves the choice to us.

## Decision

**React 19 + TypeScript (strict) + Vite**, built to static assets at Docker image build
time and served by Caddy. **No Node.js process in production** (F14.AC4).

| Concern | Choice |
|---|---|
| Server state, polling, deduplication | `@tanstack/react-query` |
| Routing with URL-encoded filter state | `react-router` |
| Styling and theming | `tailwindcss` with CSS custom properties per theme |
| Component primitives | shadcn/ui — **source copied into the repository, not a dependency** |
| Charts | **Apache ECharts**, wrapped in a roughly 30-line local hook |
| Maps and drawing | **Leaflet** + `@geoman-io/leaflet-geoman-free` |
| Basemap tiles | **CARTO** raster, dark and light variants, **no API key** |
| Runtime payload validation | `zod` |
| API client | Generated from OpenAPI by `openapi-typescript`, CI drift check |

## Alternatives considered

### Framework

| Option | Why rejected |
|---|---|
| **SvelteKit** | Smaller bundles and less boilerplate, genuinely appealing. Rejected on ecosystem depth for the *specific* libraries this project leans on: Geoman, ECharts and shadcn-equivalents all have thinner Svelte integration, so more glue code and fewer existing answers when something breaks. For a solo developer, "fewer answered questions" is a direct time cost. |
| **Jinja2 + HTMX + Alpine** | No build step and the smallest possible footprint, and it would have been the right answer for a forms-and-tables dashboard. Rejected because an interactive polygon-drawing canvas plus cross-filtered charts is precisely what HTMX is not for: you end up writing substantial imperative JavaScript anyway, but without types or a component model — losing ES1 in the process. |
| **Next.js** | SSR needs a Node runtime in production, roughly 80 MB, for a dashboard behind authentication where SSR provides no benefit. Directly conflicts with NFR6. |

### Charts

| Option | Why rejected |
|---|---|
| **Recharts** | Prettier defaults and a pleasant API, but no calendar heatmap and no Sankey — two required visualisations — and SVG rendering degrades badly past a few thousand points. Would have required a second charting library. |
| **Chart.js** | Canvas-based and fast, but narrower chart variety; Sankey and calendar heatmap need third-party plugins. |
| **visx** | Maximum control, far more assembly per chart. Wrong effort profile for fourteen charts built by one person. |
| **D3 directly** | Same objection, more so. |

ECharts wins because **one dependency covers every required chart type** and its canvas
renderer handles the volume. That is the whole argument: one library instead of two or
three, and no rendering ceiling.

### Maps

| Option | Why rejected |
|---|---|
| **MapLibre GL** | Vector tiles look considerably better and the drawing tooling is good. Rejected because free vector basemap styles effectively require an API key (MapTiler), and Gate 1 declined additional signups. |
| **Mapbox GL** | Paid, and requires a key. Fails C6. |
| **Google Maps** | Requires a key and a billing account. Fails C6. |

Leaflet plus CARTO raster tiles requires **no key at all**, and CARTO ships dark and
light basemaps that match the semi-dark default directly. The cost is raster rather than
vector: less crisp at high zoom, no tilt or rotation. Acceptable for drawing boundaries
and plotting points.

### Rejected add-ons

- **`echarts-for-react`** — a roughly 30-line `useEffect` hook does the job. Not worth a
  package (ES5).
- **A component library as a dependency** — shadcn/ui source is copied in, so it adds
  zero runtime weight and remains fully editable.

## Consequences

**Positive**

- **Zero production memory cost.** Static assets served by Caddy; no Node runtime.
- One charting dependency covering everything, with no volume ceiling.
- No map API key, so nothing to leak, rotate, or exceed a quota on.
- Generated API types plus zod give both compile-time contract safety and runtime payload
  safety. A backend schema change surfaces as a caught validation error rather than a
  `TypeError` inside a chart component.
- Tailwind custom properties make three themes a token swap rather than three stylesheets.

**Negative, and accepted**

- **React is more boilerplate** than SvelteKit for identical behaviour.
- **ECharts has a large bundle** (roughly 300 KB gzipped for the full build). Mitigated by
  importing only the required chart and component modules rather than the barrel export —
  a bundle-size check belongs in CI.
- **CARTO tile usage policy** applies. At two admins this is trivially within limits, but
  it is a third-party dependency for the basemap and is recorded as such.
- **Raster tiles** look less sharp than vector at high zoom.
- **Accessibility is manual work.** NFR7 is not free from any of these libraries; charts
  need explicit non-colour encodings and keyboard paths (NFR7.AC2, NFR7.AC3).

**Neutral**

- The generated client must never be hand-edited. This is a discipline requirement, and
  CI enforces it (F14.AC9).

## Revisit if

- Bundle size affects capture-page performance — it cannot, because the capture page is
  server-rendered Jinja2 with inline CSS and shares nothing with the SPA (ADR-0004). This
  separation is deliberate and is also part of the fix for B5.
- The basemap needs vector quality badly enough to justify a MapTiler key, which would
  reopen the C6 signup question.
