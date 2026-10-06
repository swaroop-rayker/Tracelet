/**
 * The only way the dashboard imports zod (ESLint enforces it): configured here, once, before
 * any schema can parse anything.
 *
 * zod's fast path probes for `new Function` on the first object parse. `script-src 'self'`
 * blocks it, the throw is caught, and the browser still reports a CSP violation on every page
 * load (docs/ERRORS.md E44). Jitless skips the probe; the slower path is what ran anyway.
 *
 * The configuration used to live in `schemas.ts`, which was enough while only lazy pages
 * parsed. M6's header bell parses on first paint, from the main chunk, before any page had
 * loaded `schemas.ts` -- and the probe came back on every page (ERRORS E54).
 */

import { z } from 'zod';

z.config({ jitless: true });

export { z };
