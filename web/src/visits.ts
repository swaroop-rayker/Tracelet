/** Shared reading of a visit summary. */

import type { VisitSummary } from '@/api/schemas';
import { countryName } from '@/format';

/**
 * Where a visit was, as text. Strict when the engine stated it; otherwise the advisory
 * guess, labelled as one (CLAUDE.md invariant 5: never present a guess as a fact).
 */
export function placeOf(visit: VisitSummary): { readonly text: string; readonly strict: boolean } {
  const strict = visit.location.strict;
  const advisory = visit.location.advisory;
  const parts = (l: Record<string, string | null>): string[] =>
    [l.city, l.admin1, l.country_code ? countryName(l.country_code) : null].filter(
      (x): x is string => x !== null && x !== undefined,
    );
  const s = parts(strict);
  if (s.length > 0) return { text: s.join(', '), strict: true };
  const a = parts(advisory);
  return { text: a.length > 0 ? `${a.join(', ')} (guess)` : 'Location unknown', strict: false };
}
