import { describe, expect, it } from 'vitest';
import {
  activeCount,
  apiParams,
  parseFilters,
  resolveWindow,
  serializeFilters,
  zonedInstant,
} from '@/filters';

const IST = 'Asia/Kolkata';
// 2026-10-02 12:00 in India.
const NOON_IST = Date.UTC(2026, 9, 2, 6, 30);

describe('the URL is the state (F9.AC13)', () => {
  it('round-trips every kind of filter', () => {
    const url =
      'range=custom&from=2026-09-01&to=2026-09-30&country_code=IN&classification=human' +
      '&classification=bot&has_gps=true&search=jio';
    const filters = parseFilters(new URLSearchParams(url));
    expect(filters.range).toBe('custom');
    expect(filters.lists.classification).toEqual(['human', 'bot']);
    expect(filters.scalars.country_code).toBe('IN');
    expect(activeCount(filters)).toBe(5);
    const sorted = (params: URLSearchParams): string[] => [...params.entries()].map(String).sort();
    expect(sorted(serializeFilters(filters))).toEqual(sorted(new URLSearchParams(url)));
  });

  it('defaults to the last 30 days and drops a malformed custom range', () => {
    expect(parseFilters(new URLSearchParams('')).range).toBe('30d');
    expect(parseFilters(new URLSearchParams('range=custom&from=yesterday')).range).toBe('30d');
    expect(parseFilters(new URLSearchParams('range=forever')).range).toBe('30d');
  });
});

describe('windows are cut in the reporting timezone (ADR-0016)', () => {
  it('finds Indian midnight in UTC', () => {
    const ms = zonedInstant({ year: 2026, month: 10, day: 2, hour: 0 }, IST);
    expect(new Date(ms).toISOString()).toBe('2026-10-01T18:30:00.000Z');
  });

  it('makes "last 7 days" seven whole local days ending tonight', () => {
    const window = resolveWindow(parseFilters(new URLSearchParams('range=7d')), IST, NOON_IST);
    expect(window.from).toBe('2026-09-25T18:30:00.000Z');
    expect(window.to).toBe('2026-10-02T18:30:00.000Z');
    expect(window.hourly).toBe(true);
  });

  it('makes "last 24 hours" end on the next local hour', () => {
    const window = resolveWindow(parseFilters(new URLSearchParams('range=24h')), IST, NOON_IST);
    expect(window.to).toBe('2026-10-02T07:30:00.000Z');
    expect(window.from).toBe('2026-10-01T07:30:00.000Z');
  });

  it('includes both ends of a custom range', () => {
    const filters = parseFilters(new URLSearchParams('range=custom&from=2026-09-01&to=2026-09-01'));
    const window = resolveWindow(filters, IST, NOON_IST);
    expect(window.from).toBe('2026-08-31T18:30:00.000Z');
    expect(window.to).toBe('2026-09-01T18:30:00.000Z');
  });

  it('turns a long range off hourly', () => {
    const filters = parseFilters(new URLSearchParams('range=90d'));
    expect(resolveWindow(filters, IST, NOON_IST).hourly).toBe(false);
  });

  it('sends the API instants, never the preset', () => {
    const params = apiParams(
      parseFilters(new URLSearchParams('range=7d&asn=55836')),
      IST,
      NOON_IST,
    );
    expect(params.get('range')).toBeNull();
    expect(params.get('asn')).toBe('55836');
    expect(params.get('from')).toBe('2026-09-25T18:30:00.000Z');
  });
});
