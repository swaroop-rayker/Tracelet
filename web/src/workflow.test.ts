/**
 * M7.7 (F9.AC25, F1.AC12, ADR-0023): note times land in the right bucket, a share URL carries
 * only campaign tags, and the vendored encoder draws a well-formed QR code.
 */

import { describe, expect, it } from 'vitest';
import type { Meta, TimeSeries } from '@/api/schemas';
import { notesPerBucket, localKey, timeSeriesChart } from '@/charts';
import { isoToZoned, zonedToIso } from '@/notes';
import { encodeQr, qrSvg } from '@/qr';
import { shareUrl } from '@/share';

const IST = 'Asia/Kolkata';

describe('note times (F9.AC25)', () => {
  it('reads a time in the reporting zone, and writes it back the same', () => {
    expect(zonedToIso('2026-10-06 19:30', IST)).toBe('2026-10-06T14:00:00.000Z');
    expect(isoToZoned('2026-10-06T14:00:00.000Z', IST)).toBe('2026-10-06 19:30');
    expect(zonedToIso('6 Oct 7:30 pm', IST)).toBeNull();
    expect(zonedToIso('2026-13-01 10:00', IST)).toBeNull();
  });

  it('puts a note in its local day, which is not its UTC day', () => {
    // 20:00 UTC on the 5th is 01:30 IST on the 6th.
    const per = notesPerBucket(
      ['2026-10-05', '2026-10-06'],
      [{ at: '2026-10-05T20:00:00Z', text: 'Late post' }],
      (n) => localKey(n.at, 'day', IST),
    );
    expect(per).toEqual([[], ['Late post']]);
  });

  it('a chart with notes names them in its data table', () => {
    const meta: Meta = {
      start: '2026-10-05T00:00:00+05:30',
      end: '2026-10-07T00:00:00+05:30',
      reporting_tz: IST,
      computed_from: 'raw',
      refreshed_at: null,
      stage_mix: { total: 3, server: 0, enriched: 3, server_only: 0, rate_limited: 0 },
    };
    const data: TimeSeries = {
      meta,
      bucket: 'day',
      buckets: ['2026-10-05T00:00:00+05:30', '2026-10-06T00:00:00+05:30'],
      series: [{ key: 'all', label: 'Visits', values: [1, 2] }],
    };
    const chart = timeSeriesChart(data, IST, undefined, [
      { at: '2026-10-06T14:00:00Z', text: 'Posted the reel' },
    ]);
    expect(chart.table.columns.at(-1)).toBe('Notes');
    expect(chart.table.rows.map((r) => r.at(-1))).toEqual(['', 'Posted the reel']);
    expect(timeSeriesChart(data, IST).table.columns).not.toContain('Notes');
  });
});

describe('share URL (F1.AC12)', () => {
  it('adds only non-empty campaign tags, encoded, to the capture URL', () => {
    const url = shareUrl('https://tracelet.example/r/demo-ig', {
      utm_source: ' instagram ',
      utm_campaign: 'diwali sale & more',
      utm_term: '',
    });
    expect(url).toBe(
      'https://tracelet.example/r/demo-ig?utm_source=instagram&utm_campaign=diwali+sale+%26+more',
    );
    expect(shareUrl('https://tracelet.example/r/demo-ig', {})).toBe(
      'https://tracelet.example/r/demo-ig',
    );
  });

  it('never carries a key a content blocker matches (invariant 7)', () => {
    const url = new URL(
      shareUrl('https://t.example/r/x', {
        utm_source: 'a',
        utm_medium: 'b',
        utm_campaign: 'c',
        utm_term: 'd',
        utm_content: 'e',
      }),
    );
    for (const key of url.searchParams.keys()) {
      expect(key).toMatch(/^utm_/);
      expect(key).not.toMatch(/track|collect|analytics|pixel|beacon|telemetry/);
    }
  });
});

describe('QR code (ADR-0023)', () => {
  it('encodes a short URL as a small symbol with its three finder patterns', () => {
    const m = encodeQr('https://t.example/r/demo-ig');
    expect(m.size).toBe(m.version * 4 + 17);
    expect(m.dark).toHaveLength(m.size);
    const finder = (x0: number, y0: number): boolean[] =>
      [0, 1, 2, 3, 4, 5, 6].map((d) => m.dark[y0]?.[x0 + d] ?? false);
    // A finder's top edge is seven dark modules; its second row is dark, five light, dark.
    expect(finder(0, 0)).toEqual(Array(7).fill(true));
    expect(finder(m.size - 7, 0)).toEqual(Array(7).fill(true));
    expect(finder(0, m.size - 7)).toEqual(Array(7).fill(true));
    expect(finder(0, 1)).toEqual([true, false, false, false, false, false, true]);
    // The separator beside it is light.
    expect(m.dark[0]?.[7]).toBe(false);
  });

  it('is deterministic, and its SVG is black on white with a quiet zone', () => {
    const text = 'https://t.example/r/demo-ig?utm_source=instagram';
    expect(encodeQr(text)).toEqual(encodeQr(text));
    const m = encodeQr(text);
    const svg = qrSvg(m);
    expect(svg).toContain(`viewBox="0 0 ${String(m.size + 8)} ${String(m.size + 8)}"`);
    expect(svg).toContain('fill="white"');
    expect(svg).toContain('fill="black"');
    // The quiet zone: no module drawn in the first four columns.
    expect(svg).not.toMatch(/M[0-3],/);
  });
});
