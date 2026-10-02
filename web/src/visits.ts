/** Shared reading of a visit summary. */

import type { VisitSummary } from '@/api/schemas';
import { countryName, pct } from '@/format';

export interface Place {
  /** City, state, country: the deepest the engine could place the visit. */
  readonly text: string;
  /** Confidence at the deepest level shown, 0..1, or null when nothing was placed. */
  readonly confidence: number | null;
  /** True when the engine *stated* the deepest level shown (strict), not only guessed it. */
  readonly confirmed: boolean;
}

const SHOWN = [
  ['city', 'city'],
  ['admin1', 'admin1'],
  ['country_code', 'country'],
] as const;

/**
 * Where a visit was, as text: the best guess at every level -- the highest-confidence
 * value the engine found (ADR-0018). It always agrees with strict wherever strict emitted,
 * so a confirmed level reads the same either way; an unconfirmed one carries its confidence.
 */
export function placeOf(visit: VisitSummary): Place {
  const { strict, advisory, confidence } = visit.location;
  // Advisory first; strict only as a fallback for a visit inferred before advisory was
  // always populated.
  const source = SHOWN.some(([key]) => advisory[key]) ? advisory : strict;
  const parts: string[] = [];
  let deepest: (typeof SHOWN)[number] | null = null;
  for (const entry of SHOWN) {
    const value = source[entry[0]];
    if (value === null || value === undefined || value === '') continue;
    parts.push(entry[0] === 'country_code' ? countryName(value) : value);
    deepest ??= entry;
  }
  if (deepest === null) return { text: 'Location unknown', confidence: null, confirmed: false };
  const [key, level] = deepest;
  return {
    text: parts.join(', '),
    confidence: confidence[level] ?? null,
    confirmed: Boolean(strict[key]),
  };
}

/** "Hyderabad, Telangana, India · 55%" -- the confidence only when it is a guess. */
export function placeLabel(place: Place): string {
  return place.confirmed || place.confidence === null
    ? place.text
    : `${place.text} · ${pct(place.confidence)}`;
}
