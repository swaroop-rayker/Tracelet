# ADR-0019 — A token-based design system with owned primitives, native overlays, Inter and Lucide

**Status:** Accepted (2026-10-03, repository owner, with docs/DESIGN.md). The Phase 0 spike
results are recorded below; if one fails, Decision 3's fallback applies.
**Deciders:** repository owner
**Amends:** ADR-0003 ("Component primitives: shadcn/ui — source copied"); ARCHITECTURE §5
("decal patterns on every series")
**Relates to:** docs/DESIGN.md, F9.AC16–F9.AC18, NFR7, F13.AC2 (CSP), ES5, RISKS R27

---

## Context

The owner asked for the dashboard to be redesigned as a premium, dense, technical analytics
product before M6, with **no change to functionality or business logic**, and for the result to
be a reusable design system that later milestones must follow (docs/DESIGN.md).

Four facts shape how it can be built:

1. **The CSP is `style-src 'self'` with no style nonce** (Caddyfile, F13.AC2). Inline `style="…"`
   in markup and injected `<style>` elements are blocked. Setting properties through CSSOM
   (`element.style.x = …`, `style.setProperty`) is not markup, and is expected to be allowed;
   Phase 0 verifies it.
2. **`font-src 'self'` and no third-party requests** (ADR-0017). A web font must be bundled.
3. **ADR-0003 chose shadcn/ui** (copied Radix-based components). M5 never used it: every
   control is native. shadcn's overlays depend on Radix, whose dialog scroll-lock
   (`react-remove-scroll`) is, to our understanding, implemented by injecting a `<style>`
   element, which this CSP would block. That is unverified here; it would be the first thing
   checked if Radix were reconsidered.
4. **NFR7.AC3** (never colour alone) is implemented today by turning ECharts decal patterns on
   for every series. That makes single-series charts hatched and busy (DESIGN §1 A7).

## Decision

1. **A token-based design system in plain CSS custom properties** (three themes, five surface
   layers, the DESIGN §4 scales), mapped into Tailwind with `@theme inline`. This keeps ADR-0003's
   Tailwind choice and the semi-dark default (F9.AC16).
2. **Owned primitives, no component kit.** About twenty-five components in
   `web/src/components/ui/`, written for this product (DESIGN §5). This **replaces ADR-0003's
   shadcn/ui row**: we keep shadcn's idea (source in the repo, variants, Tailwind) without
   Radix.
3. **Native overlays first.**
   - `<dialog>` with `showModal()` for dialogs, drawers and the command palette, which gives a
     focus trap, inert background and Escape for free. Background scroll is locked with CSS
     (`body:has(dialog[open]) { overflow: hidden }`), not an injected style.
   - The `popover` attribute for menus, popovers and tooltips (top layer, light dismiss).
   - A positioning helper of about 50 lines that sets CSS custom properties through CSSOM.
   - Native `<select>`, `<details>` and checkboxes, restyled.

   **Fallback, if the Phase 0 spike fails in a supported browser:** individual Radix primitives
   (Popover, DropdownMenu, Tooltip only, never Dialog), each added to the ledger after a CSP
   check.
4. **Inter Variable, self-hosted** via `@fontsource-variable/inter` (SIL OFL 1.1), Latin and
   Latin-Extended subsets only, `font-display: swap`. Code uses the system monospace stack. No
   second web font.
5. **Icons from `lucide-react`** (ISC), imported per icon through one registry module, so only
   the icons the product names are bundled (DESIGN §4.8).
6. **One chart theme function**, `chartTheme(palette)`, applied by every ECharts option builder
   (DESIGN §6.2).
7. **The decal policy (NFR7.AC3) is refined:** decals stay on wherever colour distinguishes
   series (split or stacked charts, the funnel if coloured), and are off where colour carries no
   meaning (single-series bars and lines, the calendar heatmap, and the sankey, whose labels name
   every flow). Every chart keeps its data table. *A single-series chart conveys nothing by
   colour, so there is nothing for a pattern to repeat.*
8. **Breakdowns and signal rankings become HTML ranked lists** (a semantic table with inline
   meter bars) instead of canvas bar charts. They are crisper, selectable text, accessible
   without a duplicate table, and cheaper.
9. **A development-only component gallery** at `/__design`, a lazy route behind
   `import.meta.env.DEV`, absent from production builds.

## Alternatives considered

| Option | Why not |
|---|---|
| **shadcn/ui with Radix** (ADR-0003 as written) | Radix Dialog's scroll lock is believed to inject `<style>`, which the CSP would block (unverified). It means about seven new packages (Radix parts, `class-variance-authority`, `clsx`, `tailwind-merge`). Native elements now cover dialogs and popovers |
| **MUI, Mantine or Chakra** | Runtime CSS-in-JS injects style tags (blocked by the CSP); heavy; a recognisable look the brief rules out ("generic dashboard") |
| **Headless UI or React Aria Components** | Good accessibility, but large for the handful of overlays needed. React Aria stays the fallback if native popover positioning proves inadequate |
| **System fonts only** | Zero bytes, but metrics differ per OS (Segoe vs SF), so density and alignment cannot be tuned once; tabular figures vary |
| **Geist or IBM Plex** | Both fine. Inter is the brief's preference and has the most complete tabular and contextual features |
| **A hand-drawn SVG sprite** | No dependency, but drawing and maintaining 35+ consistent icons is design work with no product value; Lucide is ISC and tree-shakes |
| **Heroicons, Phosphor or Tabler** | Comparable. Lucide has the widest set in one consistent 1.5 px style and per-icon ESM imports |
| **Keep decals everywhere** | It satisfies NFR7.AC3 literally, but texture on a single series is noise, and the brief's "restrained, engineered" direction is lost |
| **Recharts or visx for the ranked lists** | A second chart library; a styled table does the job better |

## Consequences

**Positive**

- One visual language, enforced by tokens, tests and a gallery, not by discipline.
- No new runtime style injection; the CSP stays as strict as it is.
- Four dependencies at most (the font, the icons, and possibly two Radix parts if the spike
  fails), each small, licensed permissively, and in the ledger.
- Bundle impact is budgeted: at most +40 KB gzipped initial JS and CSS (DESIGN §11).
  Measured at the end of M5.5: +15.5 KB JS and +5.1 KB CSS; the font is a separate 48 KB file.

**Negative, and accepted**

- **Owned overlays are our code to maintain.** Popover positioning, focus return and typeahead
  are written and tested here. This is mitigated by using native `dialog` and `popover` for the
  hard parts and by the Phase 0 spike.
- **The `popover` attribute needs a current browser** (Chrome 114+, Firefox 125+, Safari 17+).
  The dashboard has two admins on evergreen browsers; this is documented, not polyfilled.
- **Inter adds 48 KB** (the Latin face, measured in phase 0) to the first load, cached
  thereafter; Latin-Extended (85 KB) loads only if a page shows one of its characters.

## Phase 0 spike (before any primitive is built)

1. A Popover menu, a Dialog and a CSSOM-positioned element on the Caddy-served build: zero
   `securitypolicyviolation` events in Chromium, Firefox and WebKit.
2. A React `style={{'--w': '40%'}}` custom property: confirm no violation in the same three
   engines.
3. Inter subset size and render check in all three themes.

The results are recorded here, under a "Spike results" heading, before any primitive that
depends on them is built.

## Spike results (2026-10-03)

Run in **Chromium** (the Claude desktop browser pane) against a static page served with the
dashboard's exact CSP header (Caddyfile line 122). A `securitypolicyviolation` listener recorded
every report.

| Check | Result |
|---|---|
| `popover` attribute: `showPopover()` opens, `:popover-open` matches | **Pass**, no violation |
| `<dialog>.showModal()` opens; `body:has(dialog[open]) { overflow: hidden }` locks scroll | **Pass**, no violation |
| CSSOM custom property, `style.setProperty('--w', '40%')` (what React does for `style={{'--w': …}}`), read by a stylesheet rule `width: var(--w)` | **Pass**: the property applied, no violation |
| CSSOM direct property, `style.transform = …` (what Leaflet, ECharts and React do) | **Pass**, no violation |
| Control: `setAttribute('style', …)` | **Blocked** and reported (`style-src-attr`) — the listener works |
| Control: injected `<style>` element | **Blocked** and reported (`style-src-elem`) — the listener works |

**Firefox and WebKit, added 2026-10-03 (M5.5 QA).** The browser pane has only Chromium, so the
owner chose Playwright in a container (`mcr.microsoft.com/playwright/python` v1.63, not a
project dependency). It shares Caddy's network namespace, so `https://localhost` is the real edge
with the real header, not a stand-in. The listener is registered **before any page script runs**
(an init script), every page gets a full load, and each engine must also report three positive
controls (an injected `<style>`, a `style` attribute, a third-party image), or its run does not
count. Covered: the sign-in, enrolment and privacy pages, then all eleven signed-in routes in all
three themes, every popup and filter editor, the palette, help, icon tooltips, chart hovers,
every Data toggle, a CSV download, row expansion, the visit drawer, the map (hover, click,
keyboard pan and zoom) and the phone navigation drawer.

| Engine | Dashboard violations | Controls reported |
|---|---|---|
| Chromium 153 | **0** | 3 of 3 |
| Firefox 155 | **0** | 3 of 3 |
| WebKit 26.6 (Safari's engine; not Safari itself) | **0** | 3 of 3 |

The decisions above hold in all three engines. Two corrections came out of this run:

- **The first Chromium result was wrong.** Every page load reported a blocked `eval` in all
  three engines, from zod's JIT probe. The earlier sweep missed it because its listener was
  attached after load (ERRORS E44). Fixed with `z.config({ jitless: true })`; the zeros above
  are after the fix.
- Firefox also reports `/favicon.ico` blocked on `/privacy`. That page is rendered by the API
  under its own policy (`img-src 'none'`), and the request is the browser's automatic favicon
  fetch, not the dashboard's. It is noted for M9's capture-page hardening.

In WebKit, a Playwright screenshot itself triggers `style-src-elem`: Playwright injects a
`<style>` to hide the caret. The script detects this separately and does not count it.

**Font:** Inter Variable's Latin face is 48 KB (woff2) and Latin-Extended 85 KB. Only the faces
the page's text needs are downloaded (`unicode-range`); dashboard copy is Latin.
