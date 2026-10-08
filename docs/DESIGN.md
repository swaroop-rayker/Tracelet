# DESIGN — Tracelet's design system, the M5.5 redesign plan, and the UI rules

**Status:** Accepted (2026-10-03, repository owner), with enhancements E4–E10. Part III binds
every milestone from M5.5 on (CLAUDE.md §3); the stack choices are ADR-0019.
**Owner of this doc:** the repository owner. Changes follow §17.
**Relates to:** ADR-0003 (frontend stack), ADR-0017 (map without tiles), ADR-0018 (best-guess
location), ADR-0019 (design system stack), F9.AC16–F9.AC18, F9.AC20, NFR7, RISKS R27.

---

## How to read this

| If you are… | Read |
|---|---|
| Building any UI, in any milestone | **Part III** (rules) and the §15 checklist, then the primitives you need in §5 |
| Implementing the redesign (M5.5) | Part II, in phase order (§11) |
| Adding a token, a primitive or a dependency | §4, §5 and §17 |
| Reviewing a UI pull request | §15 |

**The one-paragraph version.** Tracelet becomes a dense, quiet, technical analytics tool built
from one set of tokens and about twenty-five owned primitives. It has a collapsible sidebar, a
compact header with a ⌘K command palette, and a page template of title, period and filter
toolbar, then KPIs, the primary chart, secondary analytics and detail. There is one accent
colour, no shadows on cards, Inter, 4 px spacing, and a radius of 6 to 12 px. Every number is
tabular, every chart has a table, and every panel has its four states. **No functionality or
business logic changes:** the same endpoints, the same filters in the URL, the same data. Only
how it looks, reads and responds changes.

---

## 0. Principles

In priority order, from the owner's brief:

1. **Consistency.** One product, not a set of screens. A thing that looks the same behaves the
   same everywhere.
2. **Information hierarchy.** Every page reads in this order: title, period and primary action,
   KPI numbers, the main chart, secondary analytics, detail. Someone new should know what the
   page says within five seconds.
3. **Readability.** Contrast is tested, numbers are tabular, and a label always beats a legend.
4. **Density.** This is a tool for analysts and engineers. Whitespace separates groups; it does
   not pad them.
5. **Alignment.** One grid, one spacing scale, one radius scale.
6. **Interaction clarity.** Every interactive element has visible hover, active, focus,
   disabled and loading states.
7. **Decoration — last.** The product should look engineered, not decorated.

Three principles specific to Tracelet, because of what the product is:

- **Show uncertainty, never hide it.** A best-guess location carries its confidence (ADR-0018).
  A figure that cannot be computed is a dash with a reason, never a zero. The design gives
  uncertainty a consistent, quiet visual form (§7.3) so that it informs without shouting.
- **Provenance is part of the data.** Every analytics figure says what it was computed over
  (F9.AC20: stage mix, rollups or raw, refreshed time). The redesign makes this quieter, as a
  one-line footer, but never removes it.
- **A blank panel is a defect (B5).** Every data surface renders a readable loading, error,
  empty or data state. The redesign improves how each state looks, not whether it exists.

---

## 1. Current-state audit (what the redesign fixes)

Observed on the running dashboard on 2026-10-02 (M5 head 6a9a9a0):

| # | Problem | Where |
|---|---|---|
| A1 | **The hierarchy is inverted.** The filter form (period, link, search, automated, "more filters") sits *above* the page title and takes the first 240 px of every page | `Layout.tsx` renders `FilterBar` before `<Outlet/>` |
| A2 | **Cards inside cards.** KPI tiles sit in a "Summary" panel; seven tiles wrap 5 + 2 with a ragged second row | `OverviewPage.tsx` `SummaryPanel` |
| A3 | **Repeated noise.** "No previous data" appears on six tiles at once | `KpiTile` |
| A4 | **The KPI change colour ignores meaning.** "Automated share ▲" is coloured green, but more bot traffic is worse | `.kpi-change.up { color: var(--ok) }` |
| A5 | **The navigation has no structure.** Seven flat text links, no icons, no grouping, and theme and sign-out mixed into the nav; below 1024 px it collapses to a wrapping top bar | `Layout.tsx`, `index.css` §responsive |
| A6 | **No header, breadcrumb or global search** | — |
| A7 | **Hatched bars everywhere.** ECharts decal patterns are on for every series, including single-series bar charts where colour carries no meaning, which makes the charts look busy | `chartkit.ts` `aria.decal.show: true` |
| A8 | **Breakdown charts are canvas bars**, so labels are image text that cannot be selected and is cramped; the "Show as table" disclosure duplicates the content | `BreakdownsPage.tsx` |
| A9 | **"More filters" opens about 30 checkboxes in 7 fieldsets** — the whole filter vocabulary, all at once | `FilterBar.tsx` |
| A10 | **Loading is a sentence** ("Loading visits over time…"), so the layout jumps when data arrives | `ui.tsx` `Loading` |
| A11 | **Heading semantics.** Page titles are `h2`; there is no `h1`; section headings mix uppercase-muted `h2` with sentence-case `h3` | `index.css` `h2 {text-transform: uppercase}` |
| A12 | **Type and spacing drift.** System font stack, ad-hoc `rem` values, a 0.375–0.5 rem radius mix, and a `.card` with a 3 px left border used for every callout | `index.css` |
| A13 | **Controls are labelled stacks** (a label above every select), which is heavy for a toolbar | `.control` |
| A14 | **The visits timeline is a `<details>` list**, readable but not scannable as columns | `VisitsPage.tsx` |
| A15 | **The Account page is one long column of sections** with no navigation | `AccountPage.tsx` |

What is already right, and the redesign keeps: three tested themes (F9.AC16), the four-state
`Panel` (F9.AC18), the provenance line (F9.AC20), URL filter state (F9.AC13), native controls
(NFR7.AC2), a table behind every chart (NFR7.AC3), lazy routes, and no third-party request
(ADR-0017).

---

## 2. Non-negotiable constraints

The redesign and every later UI change must hold all of these. Each one is already a
requirement or an invariant; this list collects them where a designer will see them.

| Constraint | Source | What it means for design |
|---|---|---|
| **No functionality or business-logic change** | Owner, 2026-10-02 | The same endpoints, parameters, filters, data and permissions. Presentation only. Anything additive is listed in §12 and needs approval |
| **The URL is the filter state** | F9.AC13, `filters.ts` | Every filter control reads from and writes to the query string. A shared link reproduces the view |
| **Four states per data surface** | F9.AC18, B5 | Loading, error (with trace id), empty (with a reason), data |
| **Provenance stays visible** | F9.AC20 | The stage-mix line stays visible on every analytics surface (it may be condensed) |
| **WCAG AA in three themes; semi-dark is the default** | F9.AC16, NFR7.AC1 | Token changes pass `theme.test.ts`; new pairs get new tests |
| **Keyboard for everything** | NFR7.AC2 | Visible focus rings, logical tab order, Escape closes, no hover-only content |
| **Never colour alone** | NFR7.AC3 | Words, icons, position or pattern carry meaning; colour reinforces it |
| **CSP: `style-src 'self'`, `font-src 'self'`, `img-src 'self' data:`, no nonce for styles** | F13.AC2, Caddyfile | No `style="…"` in markup and no injected `<style>` tags. Fonts and icons are bundled. Dynamic geometry is set through CSS custom properties via CSSOM (§4.9) |
| **No third-party request from the dashboard** | ADR-0017 | No font CDN, icon CDN or analytics. Everything is served from the VM |
| **Static assets, no Node runtime** | F14.AC4, NFR6 | The design system ships as CSS and JS only |
| **Dependencies are justified** | ES5, CLAUDE.md §3 | Each one goes in the ARCHITECTURE §8 ledger with what was rejected |
| **Only the owner performs destructive or configuration actions** | CLAUDE.md invariant 9 | The UI makes this visible (§14 UI-17) |

---

# Part I — The design system

## 4. Foundations (tokens)

All values live as CSS custom properties in `web/src/index.css`, one block per theme, and are
exposed to Tailwind through `@theme inline` (ADR-0003). **Components never use a raw value**:
not a hex code, a pixel radius, an ad-hoc shadow or a font size outside the scale (§14 UI-2).

### 4.1 Colour

**The layer model.** Surfaces get lighter as they come forward, in dark themes and light
alike. In the light theme, cards are white on a near-white canvas. Borders do the separating;
shadows are reserved for floating layers (§4.5).

```
--bg          application canvas (behind everything)
--sidebar     the sidebar and the mobile drawer
--surface     cards and panels
--surface-2   raised or inset: hover rows, table headers, segmented-control track
--overlay     popovers, menus, dialogs, the command palette (with a shadow)
```

**Proposed values.** These are a starting point, checked on 2026-10-02 against every rule in
`theme.test.ts` plus the new rules in §8. All pass; the script is reproducible from this table.

| Token | Semi-dark (default) | Dark | Light |
|---|---|---|---|
| `--bg` | `#14161a` | `#0b0d10` | `#f7f8fa` |
| `--sidebar` | `#181b20` | `#101318` | `#f1f2f5` |
| `--surface` | `#1c1f25` | `#15191f` | `#ffffff` |
| `--surface-2` | `#22262d` | `#1b2027` | `#f4f5f7` |
| `--overlay` | `#272c34` | `#20262e` | `#ffffff` |
| `--border` | `#2b3038` | `#232831` | `#e3e6eb` |
| `--border-strong` (inputs, focus-adjacent) | `#3a404b` | `#313845` | `#cdd2da` |
| `--text` | `#e8eaee` | `#e6e8eb` | `#14171c` |
| `--text-muted` (secondary) | `#9da4b0` | `#959ca7` | `#5a6270` |
| `--text-subtle` (metadata) | `#8f97a3` | `#8a919c` | `#646c79` |
| `--accent` | `#7aa2ff` | `#6f9bff` | `#2f62e6` |
| `--accent-hover` | `#93b4ff` | `#8cafff` | `#2552c7` |
| `--on-accent` (text on a filled accent) | `#0b1020` | `#0a0f1c` | `#ffffff` |
| `--accent-bg` (selected rows, active nav) | `#202a3d` | `#18223a` | `#ebf0fe` |
| `--ok` / `--ok-bg` | `#4fc58c` / `#1d2b26` | `#45bd84` / `#14231d` | `#16794a` / `#e8f5ee` |
| `--warn` / `--warn-bg` | `#e2b04a` / `#2d2a1f` | `#d9a640` / `#25221a` | `#8a5a00` / `#fbf3e2` |
| `--error` / `--error-bg` | `#f2766b` / `#2f2124` | `#ef6a60` / `#271a1c` | `#c0372b` / `#fceceb` |

The tokens `--info` and `--info-bg` alias the accent pair.

Measured: text on canvas 15.0 / 15.9 / 16.9:1. Muted on surface 6.6 / 6.4 / 6.1:1. Accent on
surface 6.6 / 6.6 / 5.2:1. Every text token is at least 4.5:1 on all five layers, and every
status foreground is at least 4.5:1 on its own tinted background.

**Rules for colour use:**

- **One accent.** It marks the active navigation item, primary actions, selection, focus rings,
  links, and **the primary data series**. Nothing else is blue for decoration.
- **Status colours mean status.** Green means healthy, completed or improved; amber means
  degraded or needing attention; red means failed, blocked or worsened. They are never used
  because a chart needs another colour.
- **Neutral is the default data colour.** Secondary series, comparison lines and "Other" are
  neutral greys, not extra hues.

**Data-visualisation colours:**

| Token | Use | Semi-dark | Dark | Light |
|---|---|---|---|---|
| `--chart-1` | the primary series (= accent) | `#7aa2ff` | `#6f9bff` | `#2f62e6` |
| `--chart-2` | teal | `#3fc1b0` | `#36b5a5` | `#0f8a7c` |
| `--chart-3` | amber | `#e2b04a` | `#d9a640` | `#a86b00` |
| `--chart-4` | violet | `#b89cff` | `#ac8ff5` | `#7a4fd6` |
| `--chart-5` | rose | `#f07fa3` | `#e8739a` | `#c2416f` |
| `--chart-6` | neutral, for "Other", comparisons and the rest | `#8e98a8` | `#848ea0` | `#5f6b7d` |
| `--seq-1…5` | sequential ramp (choropleth, heatmap), low to high | `#3d5ea6 #4a6fc0 #5f86dd #7aa2ff #b3caff` | `#34549a #3f63b3 #5079d6 #6f9bff #aac3ff` | `#aac0f6 #7f9ff0 #5682e8 #2f62e6 #1a3d9c` |
| `--map-sea` / `--map-land` | Geography map (ADR-0017) | `#0c0e11` / `#272c34` | `#040506` / `#20252c` | `#cfd9e6` / `#fdfdfe` |

The palette drops from eight categorical colours to six. A chart never shows more than five
named series plus "Other" (§6.3).

**Classification has fixed colours,** used identically by badges and charts so that "bot" is
the same colour everywhere:

| Class | Colour | Why |
|---|---|---|
| human | `--chart-1` (accent) | the primary data |
| unknown | `--chart-6` (neutral) | an absence of a verdict |
| crawler | `--chart-2` (teal) | automated but declared |
| datacenter | `--chart-3` (amber) | suspicious network |
| bot | `--chart-5` (rose) | automated |
| spam, spoofed | `--error` | hostile; distinguished by label, never by colour alone |

### 4.2 Typography

**Inter Variable**, self-hosted from the bundle (`font-src 'self'`). It ships the Latin and
Latin-Extended subsets only, with `font-display: swap`. The fallback stack is
`system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`. Inter's `tnum`
(tabular numerals) is on for every number, and `cv11` (single-storey a) is optional, decided in
Phase 0.

Code and identifiers use the system monospace stack: `ui-monospace, "Cascadia Code", "SF
Mono", Menlo, Consolas, monospace`. No second web font.

| Role | Size / line height | Weight | Tracking | Token |
|---|---|---|---|---|
| Page title (`h1`) | 24 / 32 | 600 | −0.02em | `--text-title` |
| KPI metric | 28 / 34 | 600 | −0.02em | `--text-metric` |
| Section / card title (`h2`/`h3`) | 15 / 22 | 600 | −0.01em | `--text-section` |
| Body | 14 / 20 | 400 | 0 | `--text-body` |
| Secondary | 13 / 18 | 400 | 0 | `--text-secondary` |
| Metadata, table header, badge | 12 / 16 | 500 | 0 (+0.02em for caps-free labels) | `--text-meta` |
| Micro (axis ticks, kbd) | 11 / 14 | 500 | 0 | `--text-micro` |

Only three weights: 400, 500 and 600. No uppercase labels except `kbd` keys. Page titles stay at
24 px on every page; a dashboard is not a landing page.

### 4.3 Spacing

A 4 px base, used only in these steps:

```
--space-1  4px    --space-5 20px    --space-9  40px
--space-2  8px    --space-6 24px    --space-10 48px
--space-3 12px    --space-7 28px
--space-4 16px    --space-8 32px
```

| Context | Value |
|---|---|
| Card padding | 20 px (16 px below 768 px) |
| Gap between cards in a grid | 16 px |
| Gap between page sections | 24 px |
| Page gutter | 32 px desktop, 24 px tablet, 16 px mobile |
| Control-to-control in a toolbar | 8 px |
| Label to value (KPI) | 4 px |

### 4.4 Radius

| Token | Value | Used by |
|---|---|---|
| `--radius-sm` | 6 px | badges, kbd, chips, checkboxes, small icon buttons |
| `--radius-md` | 8 px | buttons, inputs, selects, menu items, tooltips |
| `--radius-lg` | 12 px | cards, panels, the map frame, popovers |
| `--radius-xl` | 14 px | dialogs, drawers, the command palette |
| `--radius-full` | 9999 px | **only** avatars and status dots |

Nothing is a pill except the avatar and the status dot.

### 4.5 Elevation

The default is **no shadow**. Cards are separated by surface, border and spacing.

| Token | Value (dark themes / light) | Only for |
|---|---|---|
| `--shadow-popover` | `0 8px 24px -6px rgb(0 0 0 / .5)` / `0 8px 24px -8px rgb(16 24 40 / .16)` | menus, popovers, tooltips, the command palette |
| `--shadow-dialog` | `0 24px 64px -12px rgb(0 0 0 / .6)` / `0 24px 48px -12px rgb(16 24 40 / .2)` | dialogs, drawers |
| backdrop | `--bg` at 60 % plus `backdrop-filter: blur(2px)` | behind dialogs only |

### 4.6 Motion

| Token | Value | Use |
|---|---|---|
| `--dur-fast` | 120 ms | hover, press, colour changes |
| `--dur-base` | 180 ms | menus, popovers, tooltips, the sidebar collapse |
| `--dur-slow` | 240 ms | dialogs, drawers |
| `--ease-out` | `cubic-bezier(0.2, 0, 0, 1)` | entering |
| `--ease-in` | `cubic-bezier(0.4, 0, 1, 1)` | leaving |

Charts animate on first data only (250 ms, already the ECharts setting), not on every refresh.
`@media (prefers-reduced-motion: reduce)` sets every duration to 0 and stops the skeleton
shimmer. Nothing bounces, slides in from off-screen or animates a page transition.

### 4.7 Layout and breakpoints

| Token | Value |
|---|---|
| `--sidebar-w` / `--sidebar-w-collapsed` | 240 px / 68 px |
| `--header-h` | 56 px |
| `--content-max` | 1600 px, centred |
| `--control-h` / `--control-h-sm` | 36 px / 28 px |
| `--row-h` / `--row-h-compact` | 48 px / 40 px |

| Name | Width | Shell | Grids |
|---|---|---|---|
| `wide` | ≥ 1600 | sidebar open | up to 3 columns for card grids |
| `desktop` | 1280–1599 | sidebar open (user can collapse) | 2 columns |
| `laptop` | 1024–1279 | sidebar **collapsed** by default | 2 columns |
| `tablet` | 768–1023 | collapsed rail (68 px) | 1–2 columns; KPI row 2 × 2 |
| `mobile` | < 768 | no sidebar: header menu button opens a **drawer** | 1 column; KPI 2 × 2; tables become row cards or scroll horizontally |

The z-index scale is: content 0, sticky headers 10, sidebar 20, header 30, popover and tooltip
40, the drawer backdrop 50, dialog 60, toast 70.

### 4.8 Iconography

[`lucide-react`](https://lucide.dev), imported per icon (ADR-0019). Icons use a 1.5 px stroke
at **16 px** in controls and tables and **18 px** in the navigation, with `currentColor` only.

- **A closed registry.** `web/src/components/icons.ts` re-exports the about 30 icons the
  product uses under product names (`Icon.Visits`, `Icon.Geography`, …), so a page cannot pull
  in an arbitrary icon, and swapping a glyph is a one-line change.
- **An icon is never alone.** It either sits beside a visible label, or it is an `IconButton`
  with an `aria-label` and a tooltip carrying the same words.
- Icons go in navigation, buttons, state markers (✓ ⚠ ✕) and empty states (one, muted, 20 px).
  They never decorate headings or KPI cards.

### 4.9 Implementation notes

- Tokens are declared once per theme block in `index.css`. Tailwind maps them, so both
  `bg-surface` and `var(--surface)` work.
- **Dynamic geometry under the CSP.** A bar width or skeleton width is passed as a custom
  property, `style={{ '--w': '42%' }}`, which React applies through CSSOM
  (`style.setProperty`), and the CSS reads `width: var(--w)`. Phase 0 must verify that this
  produces **no CSP violation report** in Chrome, Firefox and Safari before any primitive relies
  on it. If it does, the fallback is a fixed set of width classes (`w-pct-0 … w-pct-100` in
  steps of 2).
- Charts and the map cannot read CSS, so `palette()` (`theme.ts`) is extended to every new
  token they use, and a theme switch still re-renders them.

---

## 5. Components (the primitives)

Every primitive lives in `web/src/components/ui/` (one file each), is exported from
`components/ui/index.ts`, appears in the development gallery (§11 Phase 1), and has a unit
test for its states. **Pages compose primitives; pages do not style elements.**

Each entry gives anatomy, variants, states and accessibility.

### 5.1 Actions

**Button**

- Variants: `primary` (filled accent, `--on-accent` text, **at most one per view**), `secondary`
  (surface fill with a border), `ghost` (no border until hover), `danger` (an error-coloured
  outline that fills only in a confirmation dialog).
- Sizes: `md` is 36 px (default) and `sm` is 28 px (in-card actions). A leading icon is
  optional; a trailing chevron goes on menu buttons.
- States: hover (`--surface-2` or `--accent-hover`, `--dur-fast`); active (one step darker);
  focus-visible (2 px accent ring, 2 px offset); disabled (40 % opacity text and border,
  `cursor: not-allowed`, still focusable with `aria-disabled` when it has a tooltip
  explaining why); busy (spinner replaces the leading icon, label becomes the
  present-participle verb, `aria-busy`).
- Rule: the visible text is a verb, such as "Export CSV" or "Send test message". Never "OK" or
  "Submit".

**IconButton.** A 36 × 36 hit area with a 16 px glyph, `ghost` by default, `--radius-md`. It
requires an `aria-label`, and a tooltip appears after 500 ms with the same text. It is used in
the header, card overflow (`•••`), copy-to-clipboard and closing dialogs.

**Link.** Accent colour, underlined on hover, with an external-link icon when it leaves the app.

### 5.2 Inputs

**Input and Textarea.** 36 px high, `--surface` fill (`--bg` inside cards in the light theme),
1 px `--border-strong`, `--radius-md`, 12 px horizontal padding. Placeholders are muted.
- States: hover (border `--text-subtle`), focus (border `--accent` plus a 3 px ring at 25 %
  accent), invalid (`aria-invalid`, border `--error`, message below with `role="alert"` and
  `aria-describedby`, the current `Field` behaviour kept), disabled.
- The label sits above for forms, and is visually hidden but present for toolbar controls.

**Select.** A native `<select>` styled with `appearance: none` and a chevron (an inline `data:`
SVG, allowed by `img-src data:`). Keyboard and screen-reader behaviour come from the browser
(NFR7.AC2). A custom listbox is used only where a native select cannot work (multi-select
filters, the command palette).

**SegmentedControl.** For two to four mutually exclusive views, such as Day | Hour, States |
Countries and High | Normal | Silent. It is a `radiogroup` of buttons on a `--surface-2` track,
and the selected item is `--surface` with a 1 px border. It never stands in for navigation.

**Checkbox and Switch.** A native checkbox restyled to 16 px with `--radius-sm`. A Switch
(`role="switch"`) is for settings that apply immediately, such as an inference source toggle (M7)
or a geofence's active flag (M6). A checkbox is for choices inside a form or filter.

**SearchInput.** An Input with a leading search icon and an optional trailing `kbd` hint. It
clears with Escape and an ✕ button.

**DateRangePicker (period).** A button showing the active preset ("Last 30 days") opens a
popover listing the presets, which are the existing `PRESETS` unchanged. A "Custom range…" item
reveals two native date inputs. It writes the same URL keys as today.

### 5.3 Filters

**FilterToolbar** replaces the filter form (fixing A1, A9 and A13):

```
[◷ Last 30 days ▾]  [⛓ All links ▾]  [＋ Filter]   Device: mobile, tablet ✕   Country: IN ✕   Clear all      [⧉]
                                                   ─────────── FilterChips ───────────                     copy link
```

- **Always visible:** the period, the link, "＋ Filter", the active filter chips, "Clear all"
  (shown when any filter is active) and copy-link (an IconButton with a "Copied" tooltip
  confirmation).
- **"＋ Filter"** opens a Menu listing every F9.AC13 dimension: Classification, Automated
  traffic, Device class, Connection, Consent, Country, State, City, ASN, Geofence, Visitor,
  Minimum confidence, GPS, Proxy, Webview, Stage. Choosing one opens its **FilterPopover**:
  checkboxes for the enumerations, an input for the text values (applied on Enter, as today), or
  a segmented Any | Yes | No.
- **FilterChip:** "Name: value[, value…]". Clicking it reopens its popover; ✕ removes it. It is
  12 px text, 28 px high, `--radius-sm`, on a `--surface-2` fill. More than two values collapse to
  "Device: 3 selected".
- "Include automated traffic" becomes the chip **"Automated: included ✕"**, shown only when on,
  because excluding is the default.
- The search text box moves to the command palette and to a SearchInput on the Visits page,
  where it applies; it still writes the same `search` URL key.

The behaviour is unchanged: `parseFilters` and `serializeFilters` stay the single source, and
every control still writes the URL.

### 5.4 Data display

**Card.** A `--surface` fill, 1 px `--border`, `--radius-lg`, 20 px padding, no shadow. Cards
never nest; inside a card, group content with spacing and hairline dividers.

**Panel** (the existing component, restyled). It is a Card with:

```
┌──────────────────────────────────────────────────────────────┐
│ Title                                     [controls]  [•••]  │  header: 15/600 title, optional 13 px muted description
│ One line saying what this shows and how to read it.          │
│                                                              │
│  body: loading skeleton | error | empty | data               │
│                                                              │
│ 144 requests · 79% enriched · rollups · 10:25 PM        ⓘ    │  footer: 12 px subtle provenance (F9.AC20); ⓘ explains it
└──────────────────────────────────────────────────────────────┘
```

The four states stay mandatory and tested (`Panel.test.tsx`). **Loading** becomes a skeleton
shaped like the body: a `kind` prop takes `chart`, `table`, `list`, `kpi`, `map` or `text`, keeps
`aria-busy` and an `sr-only` "Loading …" label, and the visible text label is dropped. **Error**
uses the compact Alert (5.6) with the trace id and a copy button. **Empty** uses EmptyState
(5.6).

**Stat (KPI card)**

```
┌─────────────────────────┐
│ Visits             ⓘ    │  12/500 muted label; ⓘ = the definition (tooltip)
│ 128,421                 │  28/600 tabular
│ ↑ 12.4%  vs prev. 30 d  │  12 px; arrow + number + words; colour by goodness, not direction
└─────────────────────────┘
```

- **Goodness-aware colour (fixes A4).** Each KPI declares `better: 'up' | 'down' | 'neutral'`.
  Human share up is green; Automated share up is red; Visits is `neutral` (accent-coloured
  arrow, no judgement). The arrow and the words carry the meaning; colour reinforces it
  (NFR7.AC3).
- **No previous data:** show "—" in muted text, once, with the reason in the ⓘ tooltip. Never
  repeat a sentence across six cards (A3).
- **Not computable** (for example, geofence hit rate before M6): the value is "—" and the delta
  line is the reason in muted 12 px.
- Large values use compact notation in the card ("128.4K"), with the exact value in the `title`
  attribute and in the screen-reader text.

**DataTable**

- The header row is 40 px, on `--surface-2`, 12/500 muted, sentence case, with sort affordance
  only where the API sorts.
- Body rows are 48 px (40 px in the `compact` variant, used for derivation and chart-data
  tables), separated by a 1 px `--border` hairline. There are **no vertical borders**.
- Numbers are right-aligned and tabular; identifiers are monospace and truncated in the middle
  (`01J9…X4TQ`) with a copy button on hover.
- Hover tints the row `--surface-2`. A selected row gets `--accent-bg` and a 2 px accent bar on
  its inline start.
- An expandable row has a chevron in the last column and opens a detail region inside the
  table, keeping the existing `<details>` semantics or `aria-expanded`.
- **Responsive.** Below 768 px, a table either scrolls horizontally with a sticky first column,
  or (for the Visits list) becomes stacked row cards. Each table declares which.
- Empty and loading are as for Panel.

**RankedList (bar list) — replaces breakdown bar charts (fixes A7, A8)**

```
Country                                   Visits     Share
India                     ████████████████  144      90.0%
United States             ██                  9       5.6%
Unknown                   █                   7       4.4%
```

This is a semantic `<table>`: label, an inline meter (`--chart-1` at 100 % for the top row,
others proportional, drawn as a CSS bar sized by a custom property), count and percentage.
"Other" and "Unknown" are always last and neutral-coloured. Because it *is* a table, it
satisfies NFR7.AC3 without a duplicate "Show as table". Rows can be clicked to apply the filter
(a UI shortcut that writes the same URL key; §12 E3).

**Badge**

```
● Human     ● Bot     ● Delivered     ● Dead-lettered
```

11–12 px, 500 weight, 3 × 8 px padding, `--radius-sm`, a `--surface-2` fill and a 6 px dot in
the semantic colour. Text is `--text`, never the colour itself, so contrast is independent of
hue. A `tone` variant (`ok`/`warn`/`error`/`info`) uses the tinted `--*-bg` with
`--ok`/`--warn`/`--error` text for status states (M6 deliveries, M7 health). Badges mark state
only; they are not decoration and not navigation.

**KeyValue (definition list).** A two-column `dl`: 12 px muted keys in a 160 px column and
14 px values. On mobile it stacks. It is used for visit summaries, settings read-outs and
inspectors.

**Legend.** Inline: a swatch (10 px square, `--radius-sm`) and a label, 12 px. For the
choropleth it is a stepped scale bar with numeric ranges as text.

**Kbd.** 11 px mono, 1 px border, `--radius-sm`, `--surface-2`.

**Timestamp.** It renders relative ("3 min ago") with the absolute time in the reporting
timezone in a tooltip and `<time dateTime>`. In tables of many rows it renders absolute
"09:58" under a day group header (the existing timeline grouping).

**Identifier.** Monospace, middle-truncated, with a copy button on hover or focus. Used for
visit ids, `visitor_id`, trace ids and ASNs.

**Sparkline** *(M5.6, §12 E16)*. A KPI's recent shape: plain SVG, a 1.5 px `--accent` line
with no axes, 28 px tall under a Stat, 56 × 14 px in the StatStrip. A missing value breaks the
line. Decorative (`aria-hidden`); a sentence beside it says the period, the low and the high.

**Glyph** *(M5.6, §12 E22)*. A 14 px `--text-subtle` icon before a device, connection or app
value in a table cell or ranked row. Decorative: the words beside it carry the meaning, and an
unmapped value gets the neutral "unknown" glyph, never a guess.

### 5.5 Overlays

**Tooltip.** For short, non-essential help only. It shows on hover after 500 ms and on focus
immediately, has an `--overlay` fill, 12 px text, a 240 px max width, `--radius-md` and
`--shadow-popover`, and disappears on Escape. It never holds the only copy of important
information; KPI definitions also live in the help sheet.

**Popover and Menu.** A native `popover` attribute with a small positioning helper (ADR-0019).
- Menus have 32 px items and a leading icon only when all items have one; a destructive item is
  last, separated by a divider and coloured `--error`.
- Keyboard: arrow keys move, typing jumps to an item, Enter selects, Escape closes and returns
  focus to the trigger.

**Dialog.** A native `<dialog>` with `showModal()`, which gives a focus trap, inert background
and Escape for free.

```
┌──────────────────────────────────────┐
│ Title                           [✕]  │  header 56 px
├──────────────────────────────────────┤
│ body (max 560 px wide; scrolls)      │
├──────────────────────────────────────┤
│                 [Cancel] [Primary]   │  footer: actions right-aligned; primary last
└──────────────────────────────────────┘
```

Sizes are `sm` (400), `md` (560) and `lg` (800). The backdrop is §4.5. A **confirmation dialog**
for destructive actions states the consequence in a sentence, names the object, and uses a
`danger` primary button labelled with the verb ("Delete geofence"). When the action cannot be
undone, the user types the object's name to confirm.

**Drawer (sheet).** A `<dialog>` docked right, 480 px wide (100 % on mobile). It is used for
inspectors (the geofence editor in M6) and, if approved, for visit details opened from a list
(§12 E5).

**CommandPalette (⌘K / Ctrl K).** A `md` dialog with a SearchInput and a grouped listbox:

- **Go to:** every page in the navigation.
- **Find:** "Search visits for '…'", which opens Visits with `search=…`; "Open visit `<id>`"
  when the query looks like a visit id; "Open visitor `<id>`" for a 32-hex `visitor_id`.
- **Actions:** switch theme, copy link to this view, toggle automated traffic, clear filters,
  sign out.

It uses only existing routes, URL keys and endpoints (§12 E1). Arrow keys move, Enter runs and
Escape closes; it is fully usable without a mouse.

### 5.6 Feedback

**Toast** *(M5.6, §12 E26)*. A short confirmation after an action ("Theme saved"), bottom-right,
`--overlay` surface, gone after 4 s or on Escape. Rendered into one polite live region, so a
screen reader hears it without losing its place. Never the only feedback: the row it came from
changes too. Never for errors, which stay where the action was.

**Skeleton.** Blocks in `--surface-2` with a 1.2 s shimmer (off under reduced motion), shaped
like the final content: KPI cards, axis and plot area, table rows (6 rows at row height), list
bars and the map frame. A full-page spinner is never used for dashboard data.

**EmptyState**

```
            (20 px muted icon)
        No visits in this period
   Try a longer period, or include automated traffic.
              [Show last 90 days]
```

A title and a one-line reason (always present; B5) are required; one action is optional. It is
centred inside the panel body at the panel's normal height, so the layout does not jump. No
illustrations.

**Alert (inline)** replaces the `.card` with a 3 px left border. It has a tone icon, a title, a
body and an optional action; it is tinted `--*-bg` with a 1 px tone border at 30 %, and keeps
`role="alert"` for errors and `role="status"` otherwise. Error alerts keep the trace id with a
copy button (F15.AC2).

**Banner (system-level).** A full-width strip under the header for state that affects every
page: degraded mode (M7, F15.AC6), stale rollups, or an expiring session. It is persistent while
the condition holds and dismissible only if the condition is informational.

**Toast.** Used sparingly, only to confirm an action whose result is not otherwise visible
("Link copied", "Test message sent"). It appears bottom-right, lasts 4 s, uses `role="status"`
and has no actions. Errors are never toast-only; they appear inline where the action was.

### 5.7 Navigation

**Sidebar.** See §9.1. Items are 32 px high with a 12 px horizontal padding, an 18 px icon and
a 14/500 label. Hover gives `--surface-2`. **Active** gives `--accent-bg`, 600 text in `--text`,
and a 2 px accent bar on the inline start (fixing A5 without a coloured pill). Group labels are
12/500 `--text-subtle` in sentence case. Collapsed, the sidebar shows icons only, each with a
tooltip giving the label, and the active bar remains.

**Breadcrumb.** 13 px, muted ancestors, `--text` for the current page, with "/" separators.
It appears in the header.

**Tabs.** An underline style: a 2 px accent underline on the active tab, 14/500, 40 px high. It
switches views *within* one object (Account sections on mobile, a geofence's Shape | Settings
in M6), never between pages. The selected tab is reflected in the URL (`?tab=`).

**Pagination / load more.** Keep the existing cursor "Load more", as a full-width `secondary`
button at the end of the list, with the count loaded so far ("Showing 50 of 1,240").

---

## 6. Data visualisation

### 6.1 Anatomy of a chart card

```
Visits over time                                  [Day | Hour] [Split: None ▾]  [•••]
128,421 total · ↑ 12% vs previous                       ← one headline figure, optional
┌──────────────────────────────────────────────────────────────────────────────┐
│                         plot (no frame)                                      │
└──────────────────────────────────────────────────────────────────────────────┘
Sep 3      Sep 10      Sep 17      Sep 24      Oct 1
144 requests · 79% enriched · rollups · 10:25 PM
```

*As built:* two quiet tools under the plot, **Data** (toggles the data table, `aria-pressed`)
and **CSV** (§12 E9, generated in the browser, formula-safe), instead of a `•••` menu in the card
header: `EChart` cannot reach its card's header, and two labelled buttons are easier to find than
a menu. The table is always present for screen readers, so NFR7.AC3 holds.

### 6.2 ECharts theme

One function, `chartTheme(p: Palette)` in `chartkit.ts`, replaces the scattered `axisStyle`:

| Element | Rule |
|---|---|
| Font | `Inter`, 11 px ticks, 12 px labels, `--text-subtle` |
| Grid | horizontal split lines only, 1 px `--border` dashed `[3, 3]`; no vertical lines; no axis line on the value axis; category axis line `--border` |
| Lines | 1.75 px, `smooth: 0.25` (gentle, not wavy); symbols hidden until hover (4 px dot, `--surface` ring) |
| Area | the primary series only: 0 → 12 % opacity of `--chart-1` vertical gradient (the one gradient allowed) |
| Bars | `--radius-sm` top corners, max width 28 px, 4 px category gap |
| Tooltip | `className: 'chart-tooltip'` styled in CSS (`--overlay`, `--shadow-popover`, 12 px); content from the existing escaped `tooltipText`; axis pointer is a 1 px `--border-strong` line, not a shadow block |
| Legend | top-right, 12 px, only when there are 2+ series; clicking toggles a series |
| Animation | 250 ms on first render; `animationDurationUpdate: 0` on filter changes |
| Empty | never an empty axis; the Panel's EmptyState instead |

### 6.3 Series and colour policy

- **Single series:** `--chart-1`. A previous-period comparison is `--chart-6`, dashed.
- **Split by a dimension:** the top 5 values in `--chart-1…5` order and the rest as "Other" in
  `--chart-6`. By classification, use the fixed mapping in §4.1.
- **Never a rainbow.** If a split would need more than six colours, it shows the top five plus
  Other.

### 6.4 Decals (texture patterns), refining NFR7.AC3

Today decals are on for every chart (A7). The rule becomes: **decals on wherever colour
distinguishes series** (stacked or split charts, and the funnel if coloured by stage), and **off
where colour carries no meaning** (single-series bars and lines, the calendar heatmap whose
values are in the tooltip and table, and the sankey whose node labels name every flow). Every
chart keeps its data table. This is a reading of NFR7.AC3 ("no chart conveys meaning by colour
alone"): a single-series chart conveys nothing by colour. It is recorded in ADR-0019 for owner
approval.

### 6.5 Specific charts

| Chart | Today | Redesign |
|---|---|---|
| Visits over time (F9.AC3) | ECharts line | the **primary chart**: 320 px, area on the primary series, headline total |
| Daily volume (F9.AC6) | calendar heatmap | same, `--seq` ramp, 12 px cells, month labels only, today outlined |
| Stage funnel (F9.AC8) | bars | horizontal stepped bars with the drop-off percentage between steps as text |
| Breakdowns (F9.AC4) | horizontal bar charts | **RankedList** (5.4) |
| Source flow (F9.AC10) | sankey | same; nodes `--surface-2` with `--border`, links at 35 % of the source colour |
| Confidence histogram | bars | single series, decals off, deciles labelled "0–10 %" |
| Signals (F5) | bars | **RankedList** with a category badge per row |
| Geography (F9.AC5) | Leaflet | §10.6 |

### 6.6 The map (Leaflet)

- The frame is a Card with `--radius-lg`, the map at 560 px desktop, 420 px tablet and 320 px
  mobile.
- Zoom controls become two stacked `IconButton`s, top-left, `--overlay` with `--shadow-popover`.
  Attribution is 11 px muted at the bottom-right, on `--overlay` at 80 %.
- The legend is overlaid bottom-left inside the map on `--overlay`.
- Interactions (as built in M5): hover brightens a border; a click selects with a 3 px `--text`
  outline; the sea or Escape clears it. Points sit in their own pane above the shapes, as
  `--chart-2` at 85 % with a 1 px `--surface` ring, sized by √count.
- The tooltip is the same `chart-tooltip` style.

---

## 7. Content and microcopy

### 7.1 Voice

Plain, specific and calm. Sentence case everywhere ("Visits over time", not "Visits Over
Time"). Talk about what the data is, not what the system did ("No visits in this period", not
"Query returned 0 rows").

### 7.2 Numbers, dates and units

| Kind | Format |
|---|---|
| Counts | `128,421` in tables; `128.4K` in KPI cards, with the exact value in the `title` attribute |
| Shares | `90.0%` (1 dp under 10 %, else 0 or 1 dp consistently per surface) |
| Deltas of shares | `+1.2 pts` (percentage points, never "%" of a percentage) |
| Deltas of counts | `+12%` |
| Durations | `4m 32s`, `1h 05m` |
| Times | `09:58` within a day group; `2 Oct, 09:58` otherwise; always in the reporting timezone (`/auth/me`), named once in the period picker ("Asia/Kolkata") |
| Coordinates | `12.9716, 77.5946` (4 dp) |
| Unknown or not applicable | `—` with a reason in a tooltip or the sr-only text, never `0` and never blank |

### 7.3 Showing uncertainty (ADR-0018)

- A best-guess place shows its confidence as muted text after it: "Mumbai, Maharashtra, India ·
  50%".
- A **confirmed** (strict) level shows a small ✓ in `--ok` with the tooltip "Confirmed — passed
  the strict threshold".
- Confidence never uses a progress bar in lists, which would be too heavy. The visit detail may
  show a 4-step confidence meter beside the per-level table.
- "Abstained" appears only on the Inference page, where it is the technical term. Everywhere
  else it is "Unknown".

### 7.4 Glossary

The help sheet (§9.2) defines: best guess, confirmed, enriched, server-only, rate-limited,
rollups or raw rows, automated traffic, stage mix, visitor, and confidence. Panels link terms to
it through ⓘ tooltips instead of repeating the definitions inline.

### 7.5 Errors

What happened, why if known, and what to do, plus the trace id with a copy button. For example:
"Couldn't load visits over time. The server took too long to answer. Try again, or narrow the
period. Trace 01J9…X4TQ ⧉".

---

## 8. Accessibility (NFR7 made operational)

| Rule | Check |
|---|---|
| Text at least 4.5:1, large text and UI parts at least 3:1, in all three themes, on all five layers | `theme.test.ts`, extended to `--sidebar`, `--overlay`, `--text-subtle`, `--on-accent`, and each `--ok/--warn/--error/--accent` on its `-bg` tint (all verified for the §4.1 values) |
| A visible focus ring on every focusable element: 2 px `--accent`, 2 px offset; never `outline: none` without a replacement | review, plus a source scan in vitest for `outline: none` outside an allow-list |
| Keyboard: every control reachable; menus with arrow keys; dialogs trap focus and restore it; Escape closes the topmost layer | the Phase 5 keyboard walkthrough (§11) |
| One `h1` per page (the page title), then `h2` cards and `h3` within | fixes A11 |
| Landmarks: `nav` (sidebar), `header`, `main`, `aside` (inspectors); a skip link (kept) | review |
| Target size at least 24 × 24 CSS px (WCAG 2.2 AA); controls are 28–36 px | tokens |
| Motion respects `prefers-reduced-motion` | CSS |
| Charts keep `role="img"` with a label, plus a data table | `EChart` (kept) |
| Status changes announced: copy confirmations, saved settings, filter result counts | `role="status"` live region in the shell |

---

# Part II — The redesign plan (milestone M5.5)

## 9. The application shell

### 9.1 Sidebar: information architecture

The brief's example items (Sessions, Events, Integrations) do not exist in Tracelet. This is
Tracelet's own structure, with slots reserved for the coming milestones, which appear only when
they ship:

```
┌──────────────────────────┐
│ ◈ Tracelet          [«]  │  product mark + collapse toggle
├──────────────────────────┤
│ Analytics                │  group label (12/500, subtle)
│  ▦  Overview             │  /
│  ≣  Visits               │  /visits
│  ◍  Geography            │  /geography
│  ▥  Breakdowns           │  /breakdowns
│ Engine                   │
│  ⌬  Inference            │  /inference
│  ⛉  Detection            │  /detection
│ Configure                │  (group appears with M6)
│  ⬡  Geofences       M6   │
│  ◔  Alerts          M6   │  Telegram settings, quiet hours, delivery log
│  ♡  System health   M7   │  (incl. links, backups, retention, admins per F10)
│                          │
│                          │
├──────────────────────────┤
│  ⚙  Account & security   │  /account
│  (UC) UI Check   ▾       │  user menu: role, theme, keyboard shortcuts, sign out
└──────────────────────────┘
```

- The sidebar uses `--sidebar` with a 1 px `--border` on its inline end.
- The collapse state is a per-device convenience stored in `localStorage` (wrapped in
  try/catch), not a server preference. It defaults to collapsed from 1024 to 1279 px.
- Navigation links keep carrying the current query string to filtered pages (the existing
  `Layout` behaviour).
- **The theme selector and sign-out move into the user menu** (fixing A5). Theme stays
  persisted through `PATCH /auth/me/preferences`, unchanged.

### 9.2 Header (56 px)

```
[≡] Analytics / Geography                      [⌕ Search or jump to…   Ctrl K]   [?]
```

- **Left:** a menu button (mobile only, opens the drawer), then the breadcrumb.
- **Right:** the command palette trigger (a 280 px SearchInput look-alike that opens §5.5;
  `Ctrl K` / `⌘K` shown by platform) and help `?`.
- **Who is signed in lives in the sidebar's user menu** (§9.1), not repeated as a header
  avatar: one place for theme, shortcuts and sign-out. *(As built in M5.5 phase 2; the first
  draft of this section put an avatar menu here too.)*
- **Help** opens a sheet with keyboard shortcuts (§12 E2) and the glossary (§7.4). There are no
  outbound links (no third-party requests), and no version line: the API exposes no version
  endpoint, and adding one would be an API change outside M5.5.
- **Notifications: deliberately absent** until a real feed exists. A bell with nothing behind it
  is a dead control (UI-12). Reserved for M6 (delivery failures) and M7 (health alerts).
- The header has a translucent `--bg` background, a 1 px bottom border, and is sticky. It never
  holds page actions.

### 9.3 Page template

```
<h1>Geography</h1>                                          [◷ Last 30 days ▾] [⤓ Export ▾]
Where visits came from, at their best-guess location.            ↑ period + page actions (secondary)
[⛓ All links ▾] [＋ Filter]  Country: IN ✕  Automated: included ✕   Clear all            [⧉]
──────────────────────────────────────────────────────────────────────────────────────────
KPI row (if any)
Primary card
Secondary grid
Detail
```

- The title is 24/600 and the description 14 px muted, one line.
- **The period lives with the title** (the brief's "primary action / date range" in position
  2). Link, "＋ Filter" and chips form the FilterToolbar row beneath. Both write the same URL as
  today.
- Pages that are about one object (visit detail, visitor, account) show a breadcrumb back-link
  and no filter toolbar (the existing `showsFilters` rule).
- Content is constrained to `--content-max` with the §4.3 gutters.

### 9.4 Responsive recomposition

| Area | ≥ 1280 | 1024–1279 | 768–1023 | < 768 |
|---|---|---|---|---|
| Sidebar | 240 px open | 68 px rail (expandable) | 68 px rail | hidden; drawer from ≡ |
| Header search | 280 px field | 200 px field | icon button | icon button |
| Period and page actions | beside the title | beside the title | under the title | under the title; Export goes into `•••` |
| Filter chips | one row, wraps | wraps | collapses to "Filters (3)" opening a sheet | the same sheet |
| KPI row | 4 across | 4 across | 2 × 2 | 2 × 2 |
| Card grids | 2–3 columns | 2 | 1–2 | 1 |
| Tables | full | full | horizontal scroll with a sticky first column | row cards (Visits) or scroll |
| Map | 560 px | 480 | 420 | 320, full-bleed in its card |

This extends F9.AC17 ("responsive to 1280 px and usable at tablet width") down to phones. It is
proposed as SPEC §11 row 15 (§13).

---

## 10. Page by page

The data, endpoints and controls of every page are **unchanged**; this section is layout,
components and copy.

### 10.1 Overview

```
Overview                                                  [◷ Last 30 days ▾]
Visits to your links: how many, who, and how complete the picture is.
[⛓ All links ▾] [＋ Filter]                                                   [⧉]

┌ Visits ────────┐ ┌ Unique visitors ┐ ┌ Human share ──┐ ┌ Enrichment completed ┐
│ 144            │ │ 144              │ │ 90.0%          │ │ 79.2%                │
│ —  no prior    │ │ —                │ │ —              │ │ —                    │
│ ╱╲_╱‾╲__╱  E16 │ │                  │ │ ‾‾╲_‾‾‾   E16 │ │                      │
└────────────────┘ └──────────────────┘ └────────────────┘ └──────────────────────┘
Automated share 10.0% · Location consent 0.0% ▁▁▁ · Geofence hit rate — (M6)  ← StatStrip

┌ Visits over time ─────────────────────────────── [Day | Hour] [Split: None ▾] [•••] ┐
│ primary chart, 320 px                                                              │
└ provenance ────────────────────────────────────────────────────────────────────────┘
┌ Top states ────────────────────────────┐ ┌ ● Live · updated 4 s ago ──── E18 ─────┐
│ RankedList, 5 rows (E4)                 │ │ 3 visitors in the last 30 min          │
└────────────────────────────────────────┘ │ newest six, new rows fade in            │
                                            └────────────────────────────────────────┘
┌ Where visits came from ─────── E17 ────┐ ┌ When links are opened ──────── E19 ────┐
│ state map, 280 px, no points            │ │ 7 × 24 heatmap, reporting time zone    │
└────────────────────────────────────────┘ └────────────────────────────────────────┘
┌ Daily volume · last 365 days ──────────┐ ┌ Stage funnel ──────────────────────────┐
└────────────────────────────────────────┘ └────────────────────────────────────────┘
```

- The seven KPIs: **four primary cards** (Visits, Unique visitors, Human share, Enrichment
  completed) and a **StatStrip** for the other three (Automated share, Location consent rate,
  Geofence hit rate). This fixes A2 and A3; all seven stay visible.
- Each KPI has a definition in its ⓘ tooltip. The "previous period" comparison is the existing
  `change` and `previous` from `/analytics/summary`.
- One provenance line for the KPI row sits under the StatStrip, not inside every card.
- Optional additions using existing endpoints (approval, §12 E4): "Top states" (RankedList, 5
  rows) and "Recent visits" (DataTable, 6 rows).
- **M5.6 (approved 2026-10-06, §12 E16–E22):** sparklines on Visits, Human share and Location
  consent (E16); "Recent visits" becomes the **live feed** (E18); a row with the **state map
  card** (E17) and the **hour × weekday heatmap** (E19). The map card is loaded lazily, so
  Leaflet stays out of the Overview chunk (UI-24).

### 10.2 Visits (timeline, F9.AC1)

```
Visits                                                     [◷ Last 30 days ▾] [⤓ Export ▾]
Every visit, newest first. Open one for its full derivation.
[⛓ All links ▾] [＋ Filter] [⌕ ISP, city, browser, link…]          Group by [Day ▾]   [⧉]

┌──────────────────────────────────────────────────────────────────────────────────────┐
│ Time    Class      Location                         Device              Network        │ ← sticky header
│ Friday, 2 October · 3                                                                  │ ← day group row
│ 09:58   ● Human    Naini Tal, Uttarakhand, India 20%  Chrome 131 · Android  NIB (AS9829) › │
│ ▼ expanded: KeyValue grid (When, Link, Stage, Location, Scores, Device, Network,       │
│   Visitor → history)                                     [Open full derivation →]      │
└──────────────────────────────────────────────────────────────────────────────────────┘
                         [ Load more — showing 50 of 1,240 ]
```

- Export (CSV / NDJSON) becomes a menu button in the page header. It keeps the same links and
  the same filter-honouring behaviour (F9.AC15).
- On mobile, each row becomes a two-line card: time, badge and location, then device and
  network.

### 10.3 Visit detail (F9.AC14)

- **Header:** breadcrumb "Visits / 01J9…X4TQ ⧉", then `h1` "Visit at 09:58, 2 Oct" with a class
  badge.
- **Summary strip:** four KeyValue cells (Location with confidence, Device, Network,
  Classification with scores).
- **Sections, not tabs:** Location per level, Derivation (every candidate), Classification
  signals, and Raw device and request fields (collapsed, as today). There is a sticky "On this
  page" index on the right at
  ≥ 1280 px. Sections beat tabs here because an investigator wants Ctrl-F across everything and a
  printable page.
- The derivation table uses the `compact` DataTable, with accepted or suppressed shown as a badge
  plus the reason text.

### 10.4 Visitor (F9.AC12)

The header shows the Identifier and "N visits · first seen · last seen". A compact visits
DataTable is followed by a "Changes between visits" list: each drift as a row with the changed
fields as neutral badges and the distance in km.

### 10.5 Breakdowns (F9.AC4)

A grid of RankedList cards (2 columns, 3 at ≥ 1600 px): Country, State, City, ASN, ISP, Device
class, Browser, App or browser, OS, Screen, Connection, Classification. Each shows the top 10
plus Other and Unknown, with a provenance footer. The page description keeps the ADR-0018
sentence about best-guess counting.

### 10.6 Geography (F9.AC5)

```
Geography                                                  [◷ Last 30 days ▾]
Where visits came from, at their best-guess location.
[toolbar]
┌ Where visits came from ─────────── [States | Countries] [Clusters: ~25 km ▾] [•••] ┐
│                                                                                    │
│                          map, 560 px, legend overlay                               │
│                                                                                    │
└ provenance · "N visits not on the map: no country" ────────────────────────────────┘
┌ Countries (RankedList) ──────────────┐ ┌ States and provinces (RankedList) ────────┐
└──────────────────────────────────────┘ └───────────────────────────────────────────┘
```

The "Counted but not drawn" note becomes an inline Alert (`info`) under the map, collapsed to
one line with "Show 3".

### 10.7 Inference and Detection

- **Inference:** the source-flow sankey as the primary card (400 px), then the confidence
  distribution and accuracy side by side. Accuracy shows a compact EmptyState ("Accuracy needs
  ground-truth labels — arrives in M8") instead of a table of nulls; the existing reason codes
  drive the text.
- **Detection:** "Rules that fire most" as a RankedList with category badges; supporting charts
  as cards.

### 10.8 Settings (was "Account and security"; rebuilt in M5.6, §12 E23–E28)

Six pages under `/settings`; `/account` redirects to `/settings/profile`. At ≥ 1024 px a left
sub-navigation inside the page; below it a select at the top. Each page is cards of **setting
rows**: a label and one line of explanation on the left, the state (Badge) or the action
(Button) on the right, at most one primary button per card. Forms open in an `sm` Dialog, so
the page stays a calm summary. Plan: `docs/plans/SETTINGS-REDESIGN-PLAN.md`.

```
┌───────────────┬──────────────────────────────────────────────────────────────────┐
│ Profile       │ Security                                                          │
│ Preferences   │ Password, two-factor, recovery codes and Telegram                 │
│▐Security▌     │ ┌──────────────────────────────────────────────────────────────┐ │
│ Sessions      │ │ Password                                [Change password…]   │ │
│ Team (owner)  │ │ At least 12 characters. Changing it signs out other sessions.│ │
│ System        │ ├──────────────────────────────────────────────────────────────┤ │
│               │ │ Two-factor authentication                        ( Enabled ) │ │
│               │ ├──────────────────────────────────────────────────────────────┤ │
│               │ │ Recovery codes  7 of 10 · ■■■■■■■□□□     [Regenerate…] danger │ │
│               │ ├──────────────────────────────────────────────────────────────┤ │
│               │ │ Telegram recovery chat ( Verified )         [Change chat…]   │ │
│               │ └──────────────────────────────────────────────────────────────┘ │
└───────────────┴──────────────────────────────────────────────────────────────────┘
```

| Page | Rows | Calls (all existing) |
|---|---|---|
| Profile | Initials, name, email; role and status as Badges; this session's expiry, relative | `GET /auth/me` |
| Preferences | Theme as a SegmentedControl; display time zone as a Select of the browser's IANA zones; the reporting zone, read-only; System Health's refresh interval as a SegmentedControl, 5 / 15 / 30 / 60 s (F10.AC1, M7) | `PATCH /auth/me/preferences` |
| Security | Password (Dialog: show/hide, a live 12-character checklist); two-factor status; recovery codes (count, 10-step meter, Regenerate with confirmation); Telegram (two-step Dialog) | `/auth/password`, `/auth/totp/regenerate-codes`, `/auth/telegram/verify/*` |
| Sessions | A DataTable: this device, IP prefix, started, last seen, expires; Revoke per row; Sign out all other sessions | `/auth/sessions`, one `DELETE` per session |
| Team (owner) | A DataTable of admins; Invite; per row a menu: change role, disable or enable, new setup link, delete | `/admins`, `/admins/{id}/enrollment-token` |
| System | Ready or not, one row per check, Re-check | `GET /readyz` |

- **Destructive actions confirm first (UI-16):** regenerating codes, revoking a session,
  signing out others, disabling or deleting an admin. Deleting an admin requires typing their
  email.
- **Shown once:** new recovery codes and a setup link open in a locked Dialog (no Escape, no
  backdrop, no ×) with Copy and Download; Done is enabled only once "I have saved these" is
  ticked (owner decision, 2026-10-06).
- **The server stays the judge.** Client checks are hints; refusals (`409 LAST_OWNER`, the
  self-delete `422`, a weak password) show where they apply. Team is hidden from analysts, a
  pure configuration page (UI-17); every route still refuses them.
- Success shows in the row and as a Toast; errors stay in the Dialog with their trace id.

### 10.9 Sign-in pages (login, enrol, recovery, reset)

These are centred on `--bg`: the product mark, then a 400 px Card (`--radius-xl`, 32 px
padding), then muted footer links. They use the same Input, Button and Alert. The one-time
token and TOTP flows are unchanged, and the TOTP input gets `inputmode="numeric"`,
`autocomplete="one-time-code"` and a monospace display.

*M5.6 (§12 E28):* a step indicator ("Step 1 of 2"; enrolment "1 Password · 2 Authenticator ·
3 Codes", short enough for one line on the 400 px card), show/hide on every password field, the live 12-character checklist on
enrolment and reset, and the TOTP secret shown in groups of four (Copy still copies the raw
secret). Steps, order and every message are unchanged, the enumeration-resistant ones above
all (F8.AC10). No QR code (owner decision, 2026-10-06).

### 10.10 Not found and errors

A compact page inside the shell: "Page not found" plus a link back to Overview. A top-level
error boundary renders an Alert with the trace id when one exists. No illustrations.

### 10.11 Links and link detail (M5.6, §12 E20)

`/links` lists every tracking link; `/links/:slug` is one link's dashboard. *M7 (F10.AC6,
SPEC §11 row 25) adds the writes to `/links`:* **[＋ New link]** in the header; a **•••
menu** per row with Edit…, Make default, Archive, and **Delete permanently…**; archived links
(Show archived) keep only Delete permanently…. **New** and **Edit** share one Dialog: label,
slug (with the capture URL it gives), destination, active, interstitial (ms), asks for
location, and the three alert priorities. **Delete permanently** is a danger Dialog that first
loads the preview -- "Deletes demo-ig, 164 visits and their 812 location candidates, 37 days
of figures; geofence *Campus* will be switched off" -- and is enabled only when the slug is
typed (UI-16). Every write is the owner's; an analyst sees the actions disabled with the
reason (UI-17).

```
Links                                                              [ ] Show archived
Every tracking link and where it sends visitors. Counts are all-time.
┌───────────────────────────────────────────────────────────────────────────────────┐
│ Link                     Destination              Status            Visits  Created│
│ M5 demo  demo-ig         instagram.com/…          ● Active  Default    160  3 d ago│
└───────────────────────────────────────────────────────────────────────────────────┘

Links › demo-ig                                          [◷ Last 30 days ▾]
M5 demo · https://…/r/demo-ig → https://instagram.com/…     ● Active  Default
[＋ Filter]                         ← the toolbar without the link selector: the page is the link
┌ KPI cards and strip, as Overview ┐
┌ Visits over time ┐
┌ App or browser ─────────┐ ┌ Top states ─────────────┐
┌ Live feed ──────────────┐ ┌ Stage funnel ───────────┐
```

- The index has no filter toolbar: its counts are the API's all-time `visit_count`.
- The detail page pins `link_id` and keeps every other filter and the period. A slug that
  matches no link is the not-found state, not an empty dashboard.

---

## 11. Implementation phases (M5.5, one PR)

One branch, `feat/m5.5-design-system`, stacked on M5. Commits follow the phases. **No API,
schema or endpoint changes.** `./scripts/tl verify` stays green after every phase.

| Phase | Deliverables | Exit criteria |
|---|---|---|
| **0 — Foundations** | ADR-0019 accepted; tokens from §4 in `index.css` for all three themes; Inter Variable self-hosted (ledger entry); `lucide-react` plus the icon registry (ledger entry); `chartTheme()`; `theme.test.ts` extended (§8); **CSP spike**: native `popover` + positioning helper, `<dialog>`, and the custom-property-via-CSSOM approach, each checked in Chrome, Firefox and Safari (or Edge, Firefox and WebKit) for zero CSP violation reports | all contrast tests pass; the spike's findings are recorded in ADR-0019; bundle measured (baseline vs. foundations) |
| **1 — Primitives** | everything in §5, in `components/ui/`; a **development-only gallery** at `/__design` (lazy route behind `import.meta.env.DEV`, so it is absent from the production bundle) showing every primitive in every state and theme | each primitive has a state test; the gallery renders with no console errors; keyboard checks pass for Menu, Dialog, Popover and CommandPalette |
| **2 — Shell** | Sidebar (collapse, groups, active state), Header (breadcrumb, palette trigger, help, user menu), PageHeader, FilterToolbar with chips and popovers, the mobile drawer, the CommandPalette, keyboard shortcuts | every route renders inside the new shell; filters round-trip through the URL exactly as before (`filters.test.ts` unchanged and green); no layout shift on navigation |
| **3 — Data display** | Panel restyle with skeleton kinds; Stat and StatStrip; DataTable; RankedList; Badge; EmptyState; Alert; chart theme applied to every ECharts builder; decal policy (§6.4); map controls and legend | `Panel.test.tsx` covers all four states with the new skeletons; `charts.test.ts` still passes (tables unchanged) |
| **4 — Pages** | §10.1–10.10, in the order Overview → Visits → Visit detail → Visitor → Geography → Breakdowns → Inference → Detection → Account → sign-in pages → 404 | each page matches its wireframe; every data surface shows four states; the screenshot matrix (below) is captured |
| **5 — QA and docs** | accessibility walkthrough; CSP check on every page; responsive pass; performance budget; docs (this file's status to Accepted, ARCHITECTURE §8 ledger, MILESTONES ticked, ERRORS for any bug found) | the §15 checklist is complete for the PR as a whole |

**Verification artefacts attached to the PR:**

- **Screenshot matrix:** 3 themes × 3 widths (1440, 1024, 390) × the 8 main pages = 72 images,
  plus the gallery in each theme. *(As built: taken with Playwright, because the browser pane
  crops and stalls. Every image also checks `scrollWidth` against the viewport (E47).)*
- **CSP:** zero `securitypolicyviolation` events across every page in every theme, in Chromium,
  Firefox and WebKit, on the Caddy-served build, not only the Vite dev server. The listener must
  be registered **before page scripts run**, every page gets a full load, and positive controls
  must be reported, or the check does not count. *(Corrected in M5.5: a listener attached
  after load missed a violation on every page (E44).)*
- **Keyboard script:** for every page, Tab through everything, open and close every menu,
  dialog and the palette, and apply and remove a filter, all without a mouse.
- **Bundle table:** initial JS and CSS gzipped before and after, and per lazy route. Budget:
  **at most +40 KB gzipped initial JS and CSS**, **at most +10 KB per route**. The web font is
  budgeted apart: one cached Latin face of 48 KB, loaded with `font-display: swap`. *(Corrected
  while building M5.5: the first draft counted the font inside the 40 KB, which the font alone
  exceeds. Measured: +15.5 KB JS and +5.1 KB CSS gzipped; every page route ≤ 3.8 KB.)*
- `./scripts/tl verify` passes all 13 checks.

**Size:** L (one to two weeks at solo pace, per MILESTONES' size key).

---

## 12. Enhancements and recommendations

These are my additions beyond the brief, each marked with what it needs. "UI-only" means it
uses only existing routes, URL keys and endpoints; nothing new on the server.

| # | Enhancement | Needs | Recommendation |
|---|---|---|---|
| E1 | **Command palette** (⌘K): navigation, visit and visitor id jump, search, theme, actions | UI-only | **Do in M5.5**: the brief asks for search, and this is search done properly |
| E2 | **Keyboard shortcuts:** `g o/v/g/b/i/d` go-to, `/` focus search, `?` help, `[` collapse sidebar, `Esc` close | UI-only | **Do in M5.5** |
| E3 | **Click to filter:** clicking a RankedList row, a map area or a chart bar applies that filter (writes the same URL key) | UI-only | **Do in M5.5**; it turns charts into navigation |
| E4 | **Overview additions:** "Top states" and "Recent visits" cards | UI-only (existing endpoints) | **Approved 2026-10-03** |
| E5 | **Visit detail in a drawer** from the Visits list, URL-addressable (`/visits/:id` still loads the full page directly) | UI-only, changes navigation feel | **Approved 2026-10-03** |
| E6 | **Previous-period overlay** on "Visits over time" (dashed neutral line), from a second call to the same endpoint with the shifted window | UI-only, one extra request | **Approved 2026-10-03** |
| E7 | **Freshness indicator:** "Live · updated 10:25" pill in the page header from `meta.refreshed_at`, amber when older than 15 minutes | UI-only | **Approved 2026-10-03** |
| E8 | **Glossary tooltips** (ⓘ) on jargon: stage mix, best guess, confirmed, enrichment | UI-only | **Approved 2026-10-03** |
| E9 | **Per-chart CSV** of the chart's own data table, generated client-side | UI-only | **Approved 2026-10-03** |
| E10 | **Recovery codes as a downloadable .txt** | UI-only (a `Blob` download, no server change) | **Approved 2026-10-03** |
| E11 | **Relative timestamps** with absolute-time tooltips | UI-only | **Do in M5.5** |
| E12 | **Saved views** (named filter sets): Save view in the filter bar, a Saved views group in the sidebar and the command palette, rename and delete in Settings › Preferences (§16 M7.7) | **Needs an API and a table** -- SPEC F9.AC26 | **Approved 2026-10-08 (M7.7)** |
| E13 | **Density toggle** (comfortable or compact rows) | UI-only, `localStorage` | Defer until someone asks |
| E14 | **Print stylesheet** for visit detail (evidence for a report) | UI-only | Cheap; do in Phase 5 if time allows |
| E15 | **Visual regression tests** (Playwright screenshots in CI) | a dev dependency plus browsers in `web-tools` (hundreds of MB) | Defer to M9; screenshots are manual in M5.5 |
| E16 | **Sparklines** on Visits, Human share and Location consent: inline SVG, no chart library; the low and high are spoken | UI-only (`/timeseries`) | **Approved 2026-10-06 (M5.6)** |
| E17 | **State map card** on Overview: the Geography map at card size, a click filters to the state; lazily loaded | UI-only (`/geo`) | **Approved 2026-10-06 (M5.6)** |
| E18 | **Live feed**: "Recent visits" polls every 15 s while the tab is visible, says how live it is (UI-18), counts visitors in the last 30 minutes, and fades new rows in (none under reduced motion) | UI-only (`/visits`) | **Approved 2026-10-06 (M5.6)** |
| E19 | **Hour × weekday heatmap**: the hourly series folded into 7 × 24 in the reporting time zone; windows over 31 days explain why it is empty | UI-only (`/timeseries?bucket=hour`) | **Approved 2026-10-06 (M5.6)** |
| E20 | **Links index and link detail pages** (§10.11), read-only | UI-only (`/links`, `link_id` on every call) | **Approved 2026-10-06 (M5.6)** |
| E21 | **Daily volume:** days with no visits are empty cells, and the scale starts at 1 | UI-only | **Approved 2026-10-06 (M5.6)** |
| E22 | **Device and network icons** beside device, connection and app values in tables and ranked lists; generic glyphs only, never a brand logo | UI-only (Lucide registry) | **Approved 2026-10-06 (M5.6)** |
| E23 | **Settings area**: six pages under `/settings` with a sub-navigation (§10.8); `/account` redirects | UI-only | **Approved 2026-10-06 (M5.6)** |
| E24 | **Confirmations** before regenerating codes, revoking sessions, and disabling or deleting an admin (typed email to delete) | UI-only | **Approved 2026-10-06 (M5.6)** |
| E25 | **Shown-once dialog** for new codes and setup links: locked until "I have saved these" is ticked | UI-only | **Approved 2026-10-06 (M5.6)** |
| E26 | **Toast** primitive in a polite live region (§5.6) | UI-only | **Approved 2026-10-06 (M5.6)** |
| E27 | **Team management** for the owner: invite, change role, disable or enable, new setup link, delete | UI on existing `/admins` routes | **Approved 2026-10-06 (M5.6)** |
| E28 | **Sign-in polish**: step indicators, show/hide, password checklist, grouped secret; no QR code | UI-only | **Approved 2026-10-06 (M5.6)** |
| E34 | **Annotations**: markers on Visits over time and New and returning; a Notes panel on Overview to add, edit and delete; Add note from the chart's actions (§16 M7.7) | API (`/annotations`), SPEC F9.AC25 | **Approved 2026-10-08 (M7.7)** |
| E35 | **Compare page** (`/compare`, `g c`): two links or two periods side by side (§16 M7.7) | UI-only: the existing endpoints twice, SPEC F9.AC27 | **Approved 2026-10-08 (M7.7)** |
| E36 | **Link builder** on a link's page: UTM fields, the share URL to copy, and its QR code to download as SVG (§16 M7.7) | UI-only; a vendored encoder (ADR-0023), SPEC F1.AC12 | **Approved 2026-10-08 (M7.7)** |
| E30 | **Sources page** (`/sources`, `g s`): ranked lists of referrer site, UTM source, medium and campaign, and in-app browser, with **None** as a row; a row filters (E3) (§16 M7.6) | API (four `/breakdown` dimensions, four filter keys), SPEC F9.AC21 | **Approved 2026-10-08 (M7.6)** |
| E31 | **Returning page** (`/returning`, `g r`): new vs returning per day, the weekly cohort grid, time to return; says from which day it can know (§16 M7.6) | API (`/analytics/returning`), SPEC F9.AC22 | **Approved 2026-10-08 (M7.6)** |
| E32 | **Mobile networks by state** on Geography: a table of the busiest best-guess states, carrier shares and mobile vs broadband, each state with its confidence (§16 M7.6) | API (`/analytics/carriers`), SPEC F9.AC23 | **Approved 2026-10-08 (M7.6)** |
| E33 | **Capture quality** page under System health (`/health/capture`): stages by app, and the enriched share per day (§16 M7.6) | API (`/analytics/capture-quality`), SPEC F9.AC24 | **Approved 2026-10-08 (M7.6)** |
| E29 | **Alert types card** on Alerts: four switches with their settings (digest time; spike floor and *k*; returning days), owner-only; and a "What" column in the delivery log naming each kind (§16 M7.5) | API (`alert_types` on `/notifications/settings`), SPEC F7.AC10–F7.AC15 | **Approved 2026-10-08 (M7.5)** |

---

## 13. Requirement and document changes (applied 2026-10-03)

| Change | Where |
|---|---|
| F9.AC17 extended: usable at 390 px (drawer navigation, single column, readable tables) | SPEC §11 **row 15** (moves M6's geofence amendment to row 16) |
| Design system decision (font, icons, native overlays instead of a component kit, chart theme, decal policy) | **ADR-0019** (moves M6's geofence ADR to **ADR-0020**) |
| New milestone **M5.5 — Design system and UI polish** before M6 | MILESTONES |
| UI rules made binding | CLAUDE.md §2 table and §3 (one line each, pointing here) |
| New risk R27 | RISKS |
| `@fontsource-variable/inter`, `lucide-react` | ARCHITECTURE §8 ledger, at Phase 0 |

---

# Part III — Rules for every milestone from M5.5 on

## 14. The rules

Binding once this document is accepted. A pull request that breaks one either fixes it or
records an exception in its description with the reason, and the owner approves it.

**Building blocks**

- **UI-1. Compose primitives.** New UI is built from `components/ui/`. A new visual pattern is
  added as a primitive first (§17), never styled inline in a page.
- **UI-2. Tokens only.** No hex, `rgb()`, pixel radius, shadow or font size in components or in
  Tailwind arbitrary values (`bg-[#…]`, `rounded-[7px]`). A vitest source scan enforces it for
  colours.
- **UI-3. No inline styles in markup** (`style="…"` or injected `<style>`), because of the CSP.
  Dynamic geometry goes through CSS custom properties (§4.9).
- **UI-4. No new UI dependency** without an ADR or ledger entry and a CSP check. Component kits
  that inject runtime styles (CSS-in-JS) are rejected outright.
- **UI-5. Icons come from the registry** (§4.8), and are never alone without a label or
  `aria-label` and tooltip.

**Pages and states**

- **UI-6. Every page uses the page template** (§9.3): `h1`, a one-line description, the period
  and page actions, then the toolbar, then content in hierarchy order.
- **UI-7. Every data surface is a `Panel` with four states** (F9.AC18), with skeletons shaped
  like the content and an empty state that gives a reason.
- **UI-8. Provenance on every analytics figure** (F9.AC20): the footer line, never removed.
- **UI-9. The URL holds view state** that a colleague would need to reproduce what you see:
  filters, period, tab, selected object. `localStorage` holds only per-device conveniences
  (sidebar collapse, density).
- **UI-10. Three themes, three widths.** Every new screen is checked in semi-dark, dark and light
  at 1440, 1024 and 390 px.

**Behaviour**

- **UI-11. Keyboard first.** Every control is reachable and operable by keyboard. New overlays
  use the Dialog, Popover and Menu primitives, so focus handling is inherited, not
  re-implemented.
- **UI-12. No dead controls.** Never ship a button, icon or menu item that does nothing, is
  "coming soon", or leads to a fake page. A reserved slot (like Notifications) stays absent
  until it works.
- **UI-13. Optimistic UI only for reversible, idempotent actions** (toggle a switch, rename).
  Everything else shows the busy state on the button that started it and its result inline.
- **UI-14. Errors appear where the action was,** with the trace id, never only as a toast.
- **UI-15. Long-running or bulk actions follow preview → confirm → progress → result.** Example:
  a retention purge shows the dry-run counts, then a confirmation dialog quoting the counts,
  then progress, then the outcome. This is mandatory for F12 retention, backups and geo-db
  updates.
- **UI-16. Destructive actions** use a `danger` button and a confirmation dialog that names the
  object and the consequence. Typed confirmation is required when the action cannot be undone.
- **UI-17. Role-aware UI (invariant 9).** Owner-only actions are **visible but disabled** for
  analysts, with a tooltip reading "Only the owner can …", so analysts know the capability
  exists and whom to ask. The server is still the enforcement point. Owner-only *pages* that are
  pure configuration may hide from analysts entirely, and the hide is noted in the page's doc
  comment.
- **UI-18. Live data says how live it is.** Polling surfaces (System Health, the outbox) show
  "updated Ns ago", pause when the tab is hidden, and never re-animate charts on each poll.

**Data and content**

- **UI-19. Charts follow §6:** one accent for the primary series, at most five plus Other, the
  fixed classification colours, the decal policy, and a data table for every chart.
- **UI-20. Uncertainty follows §7.3,** and "unknown" is "—" with a reason, never zero.
- **UI-21. Copy follows §7:** sentence case, verbs on buttons, numbers per §7.2, times in the
  reporting timezone.
- **UI-22. Numbers are tabular** (`tnum`) wherever they line up.

**Process**

- **UI-23. Design before code for a new screen.** A new page or a significant new surface gets
  an ASCII or annotated wireframe in this document (Part II's style) or in the milestone's
  section, before implementation.
- **UI-24. Budgets hold:** at most +10 KB gzipped per new lazy route, and no new web font.
- **UI-25. Screenshots in the PR** for every changed screen: three themes at 1440, plus 390 px
  in the default theme.

## 15. Definition of Done — the UI checklist for every PR

```
[ ] Built from components/ui primitives; no new one-off styling (UI-1)
[ ] No raw colours, radii, shadows or font sizes; tokens only (UI-2); no inline styles (UI-3)
[ ] Page template followed: h1, description, period/actions, toolbar, hierarchy (UI-6)
[ ] Every data surface: loading skeleton, error with trace id, empty with reason, data (UI-7)
[ ] Provenance footer on analytics figures (UI-8)
[ ] View state in the URL; shared link reproduces the view (UI-9)
[ ] Keyboard: everything reachable, Escape closes, focus restored, ring visible (UI-11, §8)
[ ] Contrast tests pass; any new token pair has a test (§8)
[ ] Checked in semi-dark, dark, light × 1440, 1024, 390 px; screenshots attached (UI-10, UI-25)
[ ] No CSP violations in the console on the Caddy-served build (UI-3, UI-4)
[ ] Owner-only actions disabled-with-reason for analysts (UI-17); destructive = confirm (UI-16)
[ ] No dead controls (UI-12); copy per §7 (UI-21)
[ ] Bundle delta stated; within budget (UI-24)
[ ] ./scripts/tl verify green
```

## 16. Guidance for the coming milestones

### M6 — Geofencing and Telegram notifications

**Geofences page** (`/geofences`, sidebar "Configure"):

```
Geofences                                                       [＋ New geofence]   ← the one primary action
Boundaries that decide which visits raise a high-priority alert.
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ Name            Shape          Scope        Priority  Alert        Active   Matches 7d │
│ Karnataka       ▣ Region (1)   All links    10        ● High       [on]     42       › │
│ Office campus   ⬠ Polygon      demo-ig      20        ● High       [on]     3        › │
│ Bengaluru 20km  ◯ Circle       All links    5         ● Normal     [off]    —        › │
└──────────────────────────────────────────────────────────────────────────────────────┘
[⤓ Export GeoJSON]  [⤒ Import GeoJSON]   [⌖ Test a coordinate]
```

The **editor** is a full-width map (the M5 outlines, ADR-0017; no tiles) with a left **tool
rail** and a right **inspector drawer**:

- Tool rail (IconButtons with tooltips; one active at a time): Select, **Pick regions** (click
  states or countries to toggle; the ADR-0018/M6 region-code geofence), Draw polygon, Draw
  circle, Edit vertices, Delete shape.
- Region picking: selected regions take the selection outline from M5 plus an `--accent-bg`
  fill. The inspector lists them as removable chips, with a **searchable list of every country
  and state**, so the about 13 % of divisions with no outline (measured 2026-10-02) can still be
  chosen.
- Inspector fields: Name, Description, Priority (number input), Alert priority (SegmentedControl
  High | Normal | Silent), Links (a multi-select; "All links" by default), Active (Switch).
  Validation errors from the API, such as a self-intersecting ring (F6.AC4), appear **on the
  map** (the offending edge outlined in `--error`) **and** as an inline Alert naming the rule.
- Save is `primary`, Cancel is `ghost`, Delete is a `danger` item in `•••` with a confirmation
  (UI-16). All of them are owner-only (UI-17).
- The coordinate test tool is a Popover with a lat, lng input that returns the matching
  geofences as a list with inside, outside or undetermined Badges; `undetermined` explains
  itself (F6.AC6).

**Asking for location (ADR-0021).** The Links list gains an "Asks for location" column: an
owner-only switch per link, optimistic and reversible (UI-13), read as Yes/No by analysts
(UI-17). The capture page of a link that asks shows plain consent text, "Continue without
sharing", and the privacy link, and waits up to 15 s for the browser's answer.

**Alerts page** (Telegram, F7):

- Setting rows (§10.8): Telegram chat (verified badge), Quiet hours (two time inputs plus the
  timezone), and "Send test message" (`secondary`; the result shows inline: "Delivered 09:58" or
  an error with the trace id).
- **Delivery log:** a DataTable with time, visit (Identifier), geofence, priority Badge, status
  Badge (`ok` Delivered, `warn` Retrying (3/8), `error` Dead-lettered) and a row action "Retry"
  (owner only) for dead letters.
- The notification bell in the header **appears with M6**, showing only dead-lettered
  deliveries. A count badge appears only when there are some.

**As built in M6 (2026-10-06).**

- **UI-24 exception, approved by the owner on 2026-10-06.** The editor's lazy chunk carries
  Leaflet-Geoman: 74 KB gzipped JavaScript and 6.5 KB CSS, against a budget of +10 KB per route.
  It loads only when an owner or analyst opens a geofence; no other page and no visitor ever
  downloads it. The alternative, own drawing tools on Leaflet (ADR-0020's fallback), was
  declined in favour of Geoman's tested vertex editing, drag and removal.
- **Routes and navigation.** A "Configure" sidebar group: Geofences (`/geofences`,
  `/geofences/new`, `/geofences/:id`; `g f`) and Alerts (`/alerts`; `g n`).
- **The editor** follows the wireframe, with these decisions: Geoman's own toolbar is hidden
  and the tool rail drives it; what the map draws is the editor's draft, so a drawn, typed or
  imported shape is one thing; zoom stops at 9, where the ~1 km outlines are still within a few
  pixels. **Two views** (owner decisions 2026-10-06): the world -- every country, named, no
  cities -- where a click opens a country; and one country alone, the rest sea, with its
  states and union territories named and its cities. "Country shown on the map" (World or a
  country) and "Back to the world" sit above the map, so they are there for every shape.
  A region geofence opens on its own country; "Pick all of India" picks the country key;
  names are printed on the map, never hover tooltips (E58), and a hovered area only brightens
  its outline. Fits use quarter zoom steps, so a country fills the map; the searchable list
  covers every country and division, outlined or not; a
  shape is sent on save only if it was changed, so an imported polygon keeps its holes. Create
  and Save return to the list (E62). Each list row also has a `•••` menu (owner request,
  2026-10-06): Edit (Open for an analyst) and "Delete permanently…" -- a hard delete, the row
  removed and an audit row written -- behind the geofence's name typed (UI-16), disabled with
  "Owner only" for analysts (UI-17). The editor's own `•••` offers the same delete.
- **Cities and towns** of the chosen country, for orientation only (owner decision
  2026-10-06): from GeoNames through `GET /api/v1/geofences/places`, so names are the engine's.
  Population bands -- metro 4M+, tier 1 1M+, tier 2 300k+, tier 3 50k+ -- appear as the map
  zooms in (metros always; tiers 1, 2 and 3 from zoom 5, 6 and 7) and are named a zoom later.
  Names never overlap, in one pass: metro names, then area names (largest area first,
  centred, nudged a line up or down if needed, never over a city's dot, and only where the
  area has room), then the other cities; a city's name goes right of its dot, else left. Dots are not interactive, so drawing and region picking pass
  through them. Layer order is fixed by panes: outlines, then places, then shapes (E57).
  Dots and every name are drawn on one canvas, only for the visible map, so zooming and
  panning stay smooth with a country's thousand-odd places (E59); the canvas lets clicks
  through to the map, and scales with a zoom animation rather than disappearing (E60).
- **Alerts** shows whether Telegram is configured and verified, never the token or chat id;
  quiet hours are a switch, two `HH:MM` fields and a time zone; the delivery log polls every
  15 s with its freshness shown, and its status filter and page are in the URL (`?status=dead`),
  which is where the bell links.
- **Analysts** can open every M6 screen. Writes are visible but unavailable, with the reason:
  a new `Button` prop, `disabledReason`, renders `aria-disabled` (still focusable) with the
  reason as its tooltip (UI-17). It is in the gallery and has a primitive test.

### M7 — System health and operations

- **The health overview** reuses the KPI pattern: CPU, RAM, swap and disk as Stat cards with a
  threshold state. Values over threshold get a `warn` or `error` Badge **and** words ("Disk 91 %
  — above the 85 % threshold"). Temperature on GCP is "—" with the reason "Not exposed by the
  VM" (RW-5).
- **Degradation** uses the global Banner (§5.6) while F15.AC6 holds, naming what is degraded
  and what still works.
- **Geo databases:** a DataTable with version, age, and a staleness Badge. "Update" follows
  preview → confirm → progress (UI-15), and shows the memory-capped subprocess's progress as
  steps (download, validate, swap).
- **Inference sources:** setting rows with Switches ("Applies to visits inferred from now on —
  no restart"). The flow diagram is a card using the sankey style, with an optional visit picker
  (an Identifier input) to overlay what fired.
- **Retention:** a form, then "Preview" (the dry-run counts in a DataTable), then Confirm (a
  typed confirmation quoting the counts), then progress, then the result (UI-15, UI-16).
- **Backups:** a DataTable with status Badges and the actions Download and Verify now, plus the
  last automated restore-verify result as a Stat.
- **Outbox:** a DataTable with status filters as a SegmentedControl, and "updated Ns ago"
  (UI-18).
- **Links** (F10.AC6) and **admins:** DataTables with a Dialog for create and edit forms.

**As designed for the build (2026-10-07, UI-23).** One area, **System health**, in the
sidebar's Configure group (`/health`, icon `Health`, `g h`), laid out like Settings (§10.8):
a sub-navigation on the left at ≥ 1024 px, a select above the content below it. Five pages,
because each answers one question; the polling ones say how fresh they are (UI-18).

```
System health                                                  ● Live · updated 10:25
The server, its data and its safety nets.
┌ nav ─────────────┐ ┌──────────────────────────────────────────────────────────────────┐
│ ♡ Overview       │ │ ⚠ The last backup failed — "pg_dump exited 1: …"     (Callout)    │
│ ⛁ Geo databases  │ │   Still works: earlier backups are on disk.          [Backups ›] │
│ ⌬ Inference      │ │ ┌ CPU ──────┐ ┌ Memory ───┐ ┌ Swap ─────┐ ┌ Disk ─────────────┐  │
│ ⛃ Data           │ │ │ 12 %      │ │ 61 %      │ │ 3 %       │ │ 91 % ● warn       │  │
│ ⇅ Rate limits    │ │ │ 16 CPUs   │ │ 4.5/7.4 GB│ │ 0.1/2 GB  │ │ above the 85 % …  │  │
└──────────────────┘ │ └───────────┘ └───────────┘ └───────────┘ └───────────────────┘  │
                     │ Load 0.4 · 0.6 · 0.8   Uptime 3 d 4 h   Database 127 MB          │
                     │ Temperature — "This host exposes no temperature sensor…"          │
                     │ Readiness ✓ database ✓ PostGIS ✓ migrations (head 0012)           │
                     │ Also from here: Links ›  Team ›  Alerts and the delivery log ›     │
                     └──────────────────────────────────────────────────────────────────┘
```

- **Overview** (`/health`): every degradation condition as a Callout (critical `error`,
  warning `warn`, notice `info`), each with what still works and a link to where it is fixed;
  "Nothing is degraded" when empty. Then the host Stats with threshold Badges **and** words,
  the line of smaller facts, readiness (taken over from Settings › System, which now points
  here), and the F10.AC6 links. Polls at the admin's own interval, 15 s unless changed in
  Settings › Preferences (F10.AC1). `scope: container` shows a `warn` Callout:
  "These figures are the container's — the host's /proc is not mounted."
- **Geo databases** (`/health/databases`): a DataTable — name and what it feeds, version
  and age, **state**, file, **Auto-update** Switch, and Update. *Amended for SPEC §11 row 24:*

```
Database          Version          State                          File        Auto
dbip-city-lite    2026-10          ● Up to date                   121 MB …    [on]   [Update]
geolite2-city     20261006T…       ● Update available · 9 Oct     61 MB …     [on]   [Update]
ipinfo-lite       20261006T…       ● Updating 42 % · downloading  23 MB …     [on]   [Updating…]
ip2location-…     20260929T…       ● Update failed · checksum …   220 MB …    [off]  [Update]
geolite2-asn      —                ● Unable to update · no key    —           [on]   [Update ⓘ]
[⟳ Check for updates]   Last checked 10:20
```

  State Badges: up to date `ok`; update available `info` with the release date; updating
  `info` with the percent and the step, **as a number, never a bar**; update failed `error`
  and unable to update `warn`, each with the reason inline (the installed copy keeps
  serving); not installed `error`. A "Stale" Badge is added when the copy is older than its
  threshold. **Update**: confirm → the vendor is asked first → "Already up to date —
  nothing downloaded", or "Updating n %" polling every 2 s → the result (UI-15). The same
  dialog offers **Download again**, for a damaged copy (SPEC §11 row 26).
  The Switch saves at once (reversible, UI-13), owner only.
- **Inference** (`/health/inference`): the **source switches** as SettingRows (label, code,
  family; "Applies to visits inferred from now on — no restart"); saving creates a settings
  version (one Save for all, the diff named in the confirmation). Below, the **flow diagram**:

```
 Capture → Sources                → Suppression          → Consensus      → Classification → Geofence → Alert
           ┌ The visitor's device ┐ ┌ Registry artifact ┐   ┌ Country ≥ 0.60 ┐
           │ S1 Device location ● │ │ Mobile network    │   │ Admin1  ≥ 0.75 │
           ├ Registry databases ──┤ │ Hosting network   │   │ Admin2  ≥ 0.75 │
           │ S2 GeoLite2      ●   │ │ Time-zone mismatch│   │ City    ≥ 0.80 │
           │ S3 IP2Location   ○ off │ Outvoted          │   └────────────────┘
           │ …                    │ │ Below threshold   │
           └──────────────────────┘ └───────────────────┘
 [Visit ID ____________ Show]   Showing 01a1123c… (inferred under m3.3+s7)
```

  Columns of nodes in the sankey's node style (§6: `--surface-2`, `--border`), the stage
  names above them; no lines — the columns are the order. *As built:* classification, geofence
  and alert hold one node each and share the last column, so five columns fit at 1440; where
  they do not fit (1024, 390) they wrap and read left to right, then down. Off sources are dimmed and say
  "off". With a visit, each source node gains its outcome (fired `ok`, suppressed `warn` with
  the rule, unavailable/empty neutral with the reason), each rule that fired is outlined in
  `--accent`, each level shows strict / advisory / why it abstained, and the right-hand
  stages show classification, geofence state and the alert. The visit is in the URL
  (`?visit=`, UI-9), and a visit's page links here with it.
- **Data** (`/health/data`): **Retention** — three number Fields (visits, encrypted IP,
  audit log, in days), rollups "kept forever", delivered alerts "30 days"; Save; then
  **Preview purge** → the exact counts in a DataTable → **Purge…** opens a typed confirmation
  quoting the counts ("Type PURGE to delete 1 204 visits, …") → progress → the result,
  which is the last purge line (UI-15, UI-16). **Backups** — a Stat row (last backup, last
  restore check, last download, with the R11 notice when overdue), **Back up now**, and a
  DataTable: started, kind, status Badge, size, tables/rows, checksum (Identifier), restore
  check Badge, and Download · Verify now per row.
- **Rate limits** (`/health/limits`): a DataTable grouped by capture / admin / outbound —
  label, the limit in words ("30 a minute, bursts of 10"), default, an "edited" Badge; **Edit**
  opens a Dialog with three number Fields and "Back to default"; an outbound row states its
  third party's ceiling. "Applies within 30 seconds, no restart."
- **The global banner** (§5.6): every page polls `/health/degradation` every 60 s and shows
  one Banner for the most severe critical or warning condition ("+2 more"), linking to System
  health. Notices stay on the Overview. Analysts see it too; it is information.
- **Analysts** see every page; every write (Save, Update, Purge, Back up, Verify, Edit,
  Download) is disabled with "Only the owner can …" (UI-17).

### M7.5 — Alert types (SPEC F7.AC10–F7.AC15, §12 E29)

One new Card on Alerts, between Quiet hours and the Delivery log, and one new column in the
log. No new page and no new primitive: Switch, Field, SettingRow, Card, Button.

```
┌ Alert types ──────────────────────────────────────────────────────────────┐
│ More kinds of message. Each is off until you switch it on, and counts      │
│ people only, never bots.                                                   │
│                                                                            │
│ [■] Daily digest                                         At [09:00]        │
│     Yesterday's visits, human share, top states (best guess)   Asia/Kolkata│
│     and links, and any dead letters. Held by quiet hours.                  │
│ ────────────────────────────────────────────────────────────────────────── │
│ [ ] Volume spike                   At least [10] visits in 60 minutes      │
│     A link far busier than usual for this time of day.  and over [3] × the │
│                                                         7-day usual        │
│ ────────────────────────────────────────────────────────────────────────── │
│ [ ] First visit from a new place                                           │
│     A country or state confirmed for the first time on a link. Added to    │
│     the visit's alert; sent alone only if that alert already went today.   │
│ ────────────────────────────────────────────────────────────────────────── │
│ [ ] Returning visitor                    Away more than [7] days           │
│     Someone back on a link after a while. Added to the visit's alert,      │
│     as above. Only visits still kept (retention) are remembered.           │
│                                                                            │
│                                                       [ Save alert types ] │
└────────────────────────────────────────────────────────────────────────────┘

Delivery log:  Queued · What · Visit · Link · Geofence · Priority · Status · ⋯
               "What" = Visit alert | Daily digest | Volume spike | New place | Returning | Test
```

- One form, one Save (like Quiet hours): the Save is disabled until something changed, and
  validation is inline on each Field (`HH:MM`; whole numbers; *k* 1.5–20).
- At 390 px each row stacks: switch and title, the description, then its Fields.
- Analysts see the switches and Fields disabled and the Save disabled with "Only the owner
  can change alerts." (UI-17).
- A digest row in the log has no visit and no link: "—" in both, as a test message has.
- Below 768 px the log drops its Geofence column (as the live feed drops Device): "What" is
  worth more on a phone, and the visit's own page names the geofence.

### M7.6 — Sources, returning visitors, carriers, capture quality (SPEC F9.AC21–F9.AC24, §12 E30–E33)

Two new analytics pages, one new panel and one new System health page. No new primitive:
Panel, RankedList, DataTable, EChart (line and bar), StatStrip, and the existing filter bar.

**Sources** (`/sources`, Analytics, after Breakdowns; `g s`):

```
Sources                                                    [period ▾] [filters]
Where visits came from: the site that linked here, the campaign tags on the
link, and the app it was opened in. No referrer is common in apps: it is None.

┌ Referrer site ────────────────┐ ┌ In-app browser ───────────────┐
│ None              ████████ 61 │ │ instagram         ███████  48 │
│ l.instagram.com   ███      22 │ │ browser           ████     30 │
│ google.com        █         5 │ │ whatsapp          █         6 │
└ provenance ───────────────────┘ └ provenance ───────────────────┘
┌ UTM source ──────┐ ┌ UTM medium ──────┐ ┌ UTM campaign ────┐
│ None   ███████ 70│ │ None   ███████ 70│ │ None   ███████ 70│
│ ig     ██      18│ │ social ██      18│ │ diwali ██      18│
└──────────────────┘ └──────────────────┘ └──────────────────┘
```

- The five panels sit in one `grid-3` (two columns below 1600 px, one on phones), referrer
  site first.
- A row applies its filter (E3): `referrer_host` or `utm_*`. The in-app browser list does not
  filter (as on Breakdowns). **None** is shown, last but counted, and does not filter (there
  is no "absent" filter).
- Each panel's empty state: "No visits in this period with these filters."

**Returning** (`/returning`, Analytics, after Sources; `g r`):

```
Returning visitors                                         [period ▾] [filters]
Who came back to a link. Known from 1 Sep (the oldest visit kept); 12 visits
without a visitor id are not counted.

┌ New and returning, per day ───────────────────────────── Data · CSV ┐
│ stacked bars: New (accent) · Returning (neutral)                      │
└────────────────────────────────────────────────────────────────────────┘
┌ Weekly cohorts ─────────────────────┐ ┌ Time to come back ────────────┐
│ First week  People  +1   +2   +3 …  │ │ Under an hour      ██      4  │
│ 1 Sep       40      25%  10%  5%    │ │ An hour to a day   ████   11  │
│ 8 Sep       32      19%  6%   —     │ │ A day to a week    ███     8  │
│ (cells shaded by share; — = not yet)│ │ A week to a month  █       2  │
└─────────────────────────────────────┘ └────────────────────────────────┘
```

- The cohort grid is a DataTable whose cells carry a share and a sequential tint (§6.3, via a
  CSS custom property, UI-3); the number is always printed, so colour is never the only cue.
- "Known from" comes from `since`; it is the page's provenance, not a footnote (UI-8).

**Mobile networks by state** (Geography, under the map):

```
┌ Mobile networks by state (best guess) ───────────────────────────────────┐
│ State           Visits  Jio   Airtel  Vi   BSNL  Other  Mobile  Broadband │
│ Karnataka · 71%    42   45%   30%     10%  2%    13%    80%     20%       │
└────────────────────────────────────────────────────────────────────────────┘
```

- The state's confidence beside its name (§7.3); the title says best guess. Shares, with the
  count in each cell's accessible name. At 390 px the table scrolls inside its card.

**Capture quality** (`/health/capture`, the sixth System health page):

```
┌ By app ─────────────────────────────────────────────────────────────────┐
│ App        Captured  Enriched  Server only  Pending  Consented           │
│ instagram     48       30 (63%)   18           0        2               │
│ browser       30       29 (97%)    1           0        5               │
└──────────────────────────────────────────────────────────────────────────┘
┌ Enriched share per day ─────────────────────────────── Data · CSV ┐
│ one line per busiest app (≤ 5), 0–100 %                              │
└──────────────────────────────────────────────────────────────────────┘
```

- It reads the filters' period only (System health has no filter bar): the last 30 days.

### M7.7 — Annotations, saved views, compare, link builder (SPEC F9.AC25–F9.AC27, F1.AC12, §12 E12, E34–E36)

No new primitive: Dialog, Field, Select, Button, DataTable, Card, the filter bar and the
command palette. The QR code is an `<img>` of an SVG (`img-src data:` already allows it), so
it is black on white in every theme, as a scanner needs.

**Annotations.** Overview's Visits over time gains **Add note** in its actions; a link's page
the same. A note is a vertical dashed marker at its bucket with a small flag, its text in the
tooltip and a "Notes" column in the chart's data table (colour is never the only cue).

```
┌ Notes ──────────────────────────────────────────────────── [＋ Add note] ┐
│ When              Note                      Link          By              │
│ 6 Oct, 19:30      Posted the reel           demo-ig       Swaroop    ✎ 🗑 │
│ 2 Oct, 09:00      Diwali campaign starts    All links     QA Analyst  ✎ 🗑 │
└───────────────────────────────────────────────────────────────────────────┘
Add note (Dialog): When [2026-10-06 19:30] · Note [______] (200) · Link [All links ▾] [Add]
```

- Edit is shown on your own notes only; Delete on your own, and an owner's on all (UI-17:
  someone else's are disabled with "Only its author or an owner can delete this").
  Deleting is a danger confirm naming the note (UI-16; it is audited, not typed).

**Saved views.** The filter bar gets **Save view** (bookmark icon) beside the copy-link button:
a Dialog asks a name and saves the current page and its query. The sidebar shows a **Saved
views** group under Analytics (collapsed past 8, "Show all"), each a plain link; the command
palette lists them under "Saved views". Settings › Preferences gets a **Saved views** card: a
DataTable of name, page and Rename / Delete. A view whose link no longer exists still opens:
the page explains the missing link, as an unknown slug does.

**Compare** (`/compare`, Analytics; `g c`):

```
Compare                                   (•) Two links  ( ) Two periods   [period ▾]
A [demo-ig ▾]                 vs          B [bio-link ▾]
┌ Summary ────────────────────────────────────────────────────────────────┐
│              A          B          Difference                            │
│ Visits       412        168        A +245 (+146 %)                       │
│ Human share  71 %       64 %       A +7 pp                               │
└──────────────────────────────────────────────────────────────────────────┘
┌ Visits over time: A solid, B dashed (two series, one chart) ──────────────┐
┌ Top states · A ───────────┐ ┌ Top states · B ───────────┐
┌ Referrer sites · A ───────┐ ┌ Referrer sites · B ───────┐
```

- Two periods: A is the filter bar's period, B a date range (default: the period before).
  The chart aligns them by day number ("Day 1 … Day 30"), and says so.
- Differences are words and numbers, never colour alone; the larger side is named.
- Everything is in the URL: `mode`, `a`, `b`, `b_from`, `b_to` (UI-9).

**Link builder** (a link's page, a Card after the panels: the figures come first):

```
┌ Share this link ───────────────────────────────────────────────────────┐
│ Source [instagram]  Medium [social]  Campaign [diwali]  (Term, Content)│
│ https://tracelet.example/r/demo-ig?utm_source=instagram&utm_…  [Copy] │
│ ┌──────────┐                                                          │
│ │ QR code  │  Scans to exactly the URL above.   [Download SVG]        │
│ └──────────┘                                                          │
└────────────────────────────────────────────────────────────────────────┘
```

- The encoder is loaded only when this card renders (its own lazy chunk, UI-24).
- Empty fields add nothing; values are URL-encoded; no other key can be added (invariant 7).

### M8 — Accuracy hardening and ground truth

- **Labelling queue:** one visit at a time in a focused layout. The derivation is on the left;
  the label form (country, state and city pickers, "Skip", "Can't tell") is on the right.
  Keyboard: `j`/`k` next and previous, `Enter` save, `s` skip. Progress reads "12 of 40
  labelled".
- **Accuracy dashboards** use the Inference page's cards. Precision and coverage are shown as
  Stats with the target as text ("≥ 95 % · target met ✓").

### M9 — Production hardening

- Error pages (404, 500 with trace id), an offline banner, the session-expired dialog (sign in
  again without losing the URL).
- Performance: route-level budgets checked; the font preloaded; lazy routes kept.
- Optionally, visual regression in CI (§12 E15).

## 17. Changing the design system

1. **Propose** the token, primitive or pattern in this document (a short section with anatomy,
   states, accessibility and a wireframe), in the same PR as the code.
2. **A new dependency or an architectural shift** (a component library, a new chart engine, a
   web font) needs an ADR first (CLAUDE.md §2).
3. **Tokens:** add the value to all three themes, add contrast tests for any new text or UI
   pair, and expose it via `@theme inline` and, if canvas-drawn, via `palette()`.
4. **Primitives:** add the component, its state test, and its gallery entry.
5. **Never fork a primitive** inside a page to "just tweak" it. Extend the primitive with a
   variant, or propose a new one.

---

## Appendix A — Primitive inventory and migration map

| Today | Becomes |
|---|---|
| `.card` + `Callout` (3 px left border) | `Alert` |
| `Loading` (a sentence) | `Skeleton` kinds inside `Panel` |
| `Empty` (dashed box) | `EmptyState` |
| `.panel` | `Panel` (restyled; same API plus `kind`) |
| `.kpi` tiles inside a Summary panel | `Stat` row plus `StatStrip`; no wrapping panel |
| `.badge` (outline) | `Badge` (dot) |
| `button`, `button.primary`, `button.link`, `a.button` | `Button` variants, `Link` |
| `.control` (label over select) | toolbar controls with visually hidden labels; form `Field` keeps visible labels |
| `FilterBar` form + "More filters" | `PageHeader` period + `FilterToolbar` + `FilterChip` + `FilterPopover` |
| ECharts horizontal bars (breakdowns, signals) | `RankedList` |
| `TableView` / `table.data` | `DataTable` (`compact` for chart-data tables) |
| `<details>` visit rows | `DataTable` expandable rows (same semantics) |
| `Secret`, `.codes` | `Identifier`, `CodeGrid` (copy-all) |
| sidebar `<p class="brand">` + flat links | `Sidebar` with groups, icons, collapse; `UserMenu` |
| — | `Header`, `Breadcrumb`, `CommandPalette`, `HelpSheet`, `Dialog`, `Drawer`, `Popover`, `Menu`, `Tooltip`, `Tabs`, `SegmentedControl`, `Switch`, `Kbd`, `Timestamp`, `Banner`, `Toast` |

## Appendix B — Icon registry (initial)

`Overview` LayoutDashboard · `Visits` Rows3 · `Geography` Globe2 · `Breakdowns` BarChart3 ·
`Inference` Workflow · `Detection` ShieldAlert · `Geofences` Hexagon · `Alerts` BellRing ·
`Health` Activity · `Account` Settings · `Search` Search · `Help` CircleHelp · `Collapse`
PanelLeftClose · `Expand` PanelLeftOpen · `Menu` Menu · `Close` X · `Chevron` ChevronDown ·
`More` MoreHorizontal · `Filter` ListFilter · `Add` Plus · `Copy` Copy · `Check` Check ·
`Download` Download · `Upload` Upload · `External` ArrowUpRight · `Info` Info · `Warn`
TriangleAlert · `Error` CircleX · `Up` ArrowUp · `Down` ArrowDown · `Calendar` CalendarDays
· `Link` Link2 · `Theme` SunMoon · `SignOut` LogOut · `Locate` Crosshair

The lucide names are confirmed against the installed version in Phase 0.

Added in M7.6: `Sources` Share2 · `Returning` Repeat2 · `Capture` Funnel.

Added in M7.7: `Compare` GitCompareArrows · `Note` StickyNote · `SaveView` Bookmark · `Edit` Pencil · `QrCode` QrCode.
