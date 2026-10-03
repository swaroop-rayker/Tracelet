/** Small helpers shared by the primitives. */

import type { CSSProperties } from 'react';

/** Join class names, dropping the falsy ones. */
export function cx(...parts: readonly (string | false | null | undefined)[]): string {
  return parts.filter((p): p is string => typeof p === 'string' && p !== '').join(' ');
}

/**
 * Custom properties for a style prop. React writes them through CSSOM
 * (`style.setProperty`), which the CSP allows; inline `style=""` markup it would block
 * (ADR-0019 spike). Only custom properties go through here, never raw declarations.
 */
export function cssVars(vars: Readonly<Record<`--${string}`, string>>): CSSProperties {
  return vars;
}

/** Copy text to the clipboard. Resolves false where the browser refuses. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/** `01J9TRACEXYZ…X4TQ` -- the start and end of a long identifier, middle elided. */
export function middleTruncate(value: string, keep = 6): string {
  return value.length <= keep * 2 + 1 ? value : `${value.slice(0, keep)}…${value.slice(-4)}`;
}

const RELATIVE = new Intl.RelativeTimeFormat('en', { numeric: 'auto' });

/** "3 min ago" for recent times, else the date (DESIGN §5.4 Timestamp). */
export function relativeTime(iso: string, now: number = Date.now()): string {
  const seconds = Math.round((new Date(iso).getTime() - now) / 1000);
  const abs = Math.abs(seconds);
  if (abs < 45) return 'just now';
  if (abs < 3600) return RELATIVE.format(Math.round(seconds / 60), 'minute');
  if (abs < 86400) return RELATIVE.format(Math.round(seconds / 3600), 'hour');
  if (abs < 86400 * 7) return RELATIVE.format(Math.round(seconds / 86400), 'day');
  return new Date(iso).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
}
