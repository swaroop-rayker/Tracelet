/**
 * Annotation helpers (F9.AC25): the notes request for a chart, and times typed in the
 * reporting zone. Kept apart from the components so fast refresh keeps working.
 */

import { useApi } from '@/api/query';
import { NOTES, annotationListSchema, type Annotation } from '@/api/workflow';
import { zonedInstant } from '@/filters';

const LOCAL = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})$/;

/** The notes request for a chart: its window and link, nothing else. */
export function notesParams(params: URLSearchParams): URLSearchParams {
  const out = new URLSearchParams();
  for (const key of ['from', 'to', 'link_id']) {
    const value = params.get(key);
    if (value !== null) out.set(key, value);
  }
  return out;
}

export function useNotes(params: URLSearchParams): readonly Annotation[] {
  const query = useApi(NOTES, notesParams(params), annotationListSchema);
  return query.data?.items ?? [];
}

/** `YYYY-MM-DD HH:MM` in `zone` as that instant's ISO string, or null if it is not a time. */
export function zonedToIso(local: string, zone: string): string | null {
  const m = LOCAL.exec(local.trim());
  if (m === null) return null;
  const [, year, month, day, hour, minute] = m.map(Number) as [
    number,
    number,
    number,
    number,
    number,
    number,
  ];
  if (month < 1 || month > 12 || day < 1 || day > 31 || hour > 23 || minute > 59) return null;
  const instant = new Date(zonedInstant({ year, month, day, hour, minute }, zone));
  return Number.isNaN(instant.getTime()) ? null : instant.toISOString();
}

/** An instant as `YYYY-MM-DD HH:MM` in `zone`. */
export function isoToZoned(iso: string, zone: string): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: zone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date(iso));
  const part = (type: string): string => parts.find((p) => p.type === type)?.value ?? '';
  return `${part('year')}-${part('month')}-${part('day')} ${part('hour')}:${part('minute')}`;
}
