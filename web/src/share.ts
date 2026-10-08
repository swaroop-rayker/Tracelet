/**
 * The share URL a link builder makes (F1.AC12): the link's own capture URL with optional
 * campaign tags. Only the five `utm_*` keys are ever added -- never anything that could
 * choose the destination (invariant 3) or that a content blocker would match (invariant 7).
 */

export const UTM_KEYS = [
  'utm_source',
  'utm_medium',
  'utm_campaign',
  'utm_term',
  'utm_content',
] as const;
export type UtmKey = (typeof UTM_KEYS)[number];
export type UtmTags = Readonly<Partial<Record<UtmKey, string>>>;

/** `captureUrl` with each non-empty tag, trimmed and URL-encoded, in a fixed order. */
export function shareUrl(captureUrl: string, tags: UtmTags): string {
  const url = new URL(captureUrl);
  url.search = '';
  url.hash = '';
  for (const key of UTM_KEYS) {
    const value = tags[key]?.trim() ?? '';
    if (value !== '') url.searchParams.set(key, value);
  }
  return url.toString();
}
