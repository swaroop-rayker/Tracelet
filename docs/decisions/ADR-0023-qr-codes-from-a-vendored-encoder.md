# ADR-0023 — QR codes come from a vendored encoder, drawn in the browser

**Status:** Accepted (2026-10-08). The owner chose the vendored file.
**Deciders:** repository owner
**Relates to:** SPEC F1.AC12, §11 row 29; DESIGN §12 E36, UI-4, UI-24; ARCHITECTURE §8;
CLAUDE.md §3 (no dependency without justification), invariants 3 and 7

---

## Context

The link builder (ENHANCEMENTS-PLAN D4, F1.AC12) shows a link's share URL -- `/r/{slug}` with
optional `utm_*` tags -- as a QR code to print or put on a slide. A QR code needs an encoder:
Reed–Solomon error correction, version and mask selection, and the module layout. The
plan said this needs a small library, an ADR and a ledger entry with its gzipped size.

Three constraints shape the choice:

- **No third-party request** from the dashboard (F13.AC2, the CSP): the code must ship in the
  bundle, and the image must be drawn in the browser, not fetched from a QR service.
- **No new runtime dependency without a reason** (CLAUDE.md §3), and a small bundle (UI-24:
  at most +10 KB gzipped per lazy route).
- **The encoder must be right.** A QR code that scans to the wrong URL is worse than none, and
  "wrong" is invisible to whoever prints it.

## Decision

**Vendor Nayuki's QR Code generator (TypeScript), unchanged, at a pinned commit.**

- Source: `github.com/nayuki/QR-Code-generator`, `typescript-javascript/qrcodegen.ts`, commit
  `3c6d0b3cefb4e049dc337e82237c9644399716a8` (2026-08-31), 41 022 bytes, SHA-256
  `1dc03fb5a10e0e2318ea162755bbdb9977ca6ce52cff959e9c9b6deafdccda9c`. MIT licence; the
  notice is kept at the head of the file.
- Kept at `web/src/vendor/qrcodegen.ts` **verbatim** except for two additions, both marked: a
  `// @ts-nocheck` header with its reason (upstream is strict TypeScript but predates our
  `noUncheckedIndexedAccess`), and a final `export default qrcodegen;` so a module can import
  it. `src/vendor` is excluded from eslint and prettier: it is reviewed, not restyled.
- **Reviewed before it was committed:** pure computation -- no network, DOM, storage, timers,
  `eval` or dynamic code.
- Used only through `web/src/qr.ts`, which types its two entry points (`encodeText`,
  `getModule`) and is unit-tested: the version and size for known inputs, the three finder
  patterns, and that the same text always gives the same matrix.
- Rendered as an `<img>` of a black-on-white SVG (a `data:` URI, which `img-src` already
  allows), downloadable as `.svg` through a `Blob`. Loaded in its own lazy chunk, only when a
  link's page shows the builder.

**Size:** see the ARCHITECTURE §8 ledger row, measured from the production build.

## Alternatives considered

| Option | Why not |
|---|---|
| **Write the encoder in plain TypeScript** | No third-party code, but Reed–Solomon, the 40 version tables, masking and penalty scoring to write and prove: about 1.5 days, for a result less tested than an encoder used for a decade |
| **`uqr` from npm** (an ES-module port of Nayuki's) | Smaller audience; an npm install brings a package-manager supply-chain surface for 40 KB of code we can read once |
| **`qrcode-generator` from npm** | Long-standing and fine, but not the implementation the owner chose, and the same npm surface |
| **`qrcode` (soldair) from npm** | Larger, with dependencies, and server-side features we do not need |
| **A QR image service** | A third-party request carrying the share URL: forbidden by F13.AC2 and the CSP |

## Consequences

- One more file to keep current by hand. It is a stable, finished library; an update is a
  deliberate copy at a new pinned commit, recorded here.
- No `package.json` change and no lockfile change.
- The link builder adds no endpoint and stores nothing; the destination still comes only from
  the `links` row (invariant 3), and the builder adds only `utm_*` keys (invariant 7).
