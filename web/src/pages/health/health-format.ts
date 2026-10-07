/**
 * Wording and tones shared by the System health pages (DESIGN §7, §16 M7).
 */

import type { LimitValue, Severity } from '@/api/system';
import type { Tone } from '@/components/ui';

export const OWNER_ONLY = 'Only the owner can change this.';
export const POLL_MS = 15_000;

const UNITS = ['B', 'KB', 'MB', 'GB', 'TB'] as const;

/** 127 MB, 4.5 GB: two significant figures past the first unit that fits. */
export function bytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  let n = value;
  let unit = 0;
  while (n >= 1024 && unit < UNITS.length - 1) {
    n /= 1024;
    unit += 1;
  }
  const digits = unit === 0 || n >= 100 ? 0 : 1;
  return `${n.toFixed(digits)} ${UNITS[unit] ?? 'B'}`;
}

/** 3 d 4 h, 2 h 10 min, 45 s. */
export function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const d = Math.floor(s / 86_400);
  const h = Math.floor((s % 86_400) / 3_600);
  const m = Math.floor((s % 3_600) / 60);
  if (d > 0) return `${String(d)} d ${String(h)} h`;
  if (h > 0) return `${String(h)} h ${String(m)} min`;
  if (m > 0) return `${String(m)} min`;
  return `${String(s)} s`;
}

export function severityTone(severity: Severity): Tone {
  if (severity === 'critical') return 'error';
  if (severity === 'warning') return 'warn';
  return 'info';
}

export function stateTone(state: string): 'ok' | 'warn' | 'error' {
  if (state === 'critical') return 'error';
  return state === 'warn' ? 'warn' : 'ok';
}

const PERIODS: readonly (readonly [number, string])[] = [
  [86_400, 'a day'],
  [3_600, 'an hour'],
  [900, '15 minutes'],
  [60, 'a minute'],
  [1, 'a second'],
];

/** "30 a minute, bursts of 10". */
export function limitInWords(value: LimitValue): string {
  const n = value.per_period.toLocaleString('en-IN');
  const named = PERIODS.find(([seconds]) => seconds === value.period_seconds);
  const rate = named ? `${n} ${named[1]}` : `${n} every ${String(value.period_seconds)} seconds`;
  return `${rate}, bursts of ${value.burst.toLocaleString('en-IN')}`;
}

export const SOURCE_STATUS: Readonly<
  Record<string, { readonly label: string; readonly tone?: 'ok' | 'warn' | 'info' }>
> = {
  fired: { label: 'Fired', tone: 'ok' },
  suppressed: { label: 'Suppressed', tone: 'warn' },
  disabled: { label: 'Off' },
  unavailable: { label: 'Unavailable' },
  empty: { label: 'No answer' },
  silent: { label: 'Not recorded' },
};

/** A reason code from the server, as words: `database_not_installed` → "database not installed". */
export function words(code: string | null | undefined): string {
  return code ? code.replace(/[_:]+/g, ' ').trim() : '';
}
