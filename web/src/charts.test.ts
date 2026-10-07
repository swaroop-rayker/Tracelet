/**
 * Every chart builder returns its figures as a table too (NFR7.AC3: no chart conveys
 * meaning by colour alone), and says what it could not measure in words.
 */

import { describe, expect, it } from 'vitest';
import type { Breakdown, Calendar, Funnel, Meta, SourceFlow, TimeSeries } from '@/api/schemas';
import { chartTheme } from '@/components/chartkit';
import { filterForBreakdown } from '@/components/shell/filterDefs';
import {
  calendarChart,
  foldHourWeekday,
  funnelChart,
  hourWeekdayChart,
  rankedRows,
  sourceFlowChart,
} from '@/charts';
import { parseFilters, serializeFilters } from '@/filters';
import type { Palette } from '@/theme';
import { z } from '@/api/zod';

const META: Meta = {
  start: '2026-09-03T00:00:00+05:30',
  end: '2026-10-03T00:00:00+05:30',
  reporting_tz: 'Asia/Kolkata',
  computed_from: 'raw',
  refreshed_at: null,
  stage_mix: { total: 4, server: 0, enriched: 3, server_only: 1, rate_limited: 0 },
};

describe('chart tables', () => {
  it('a location breakdown names unplaced visits instead of dropping them', () => {
    const data: Breakdown = {
      meta: META,
      dimension: 'city',
      total: 4,
      rows: [{ key: 'IN|Karnataka|Bengaluru', count: 1, share: 0.25 }],
      unknown: 3,
      other: 0,
    };
    expect(rankedRows(data)).toEqual([
      { key: 'IN|Karnataka|Bengaluru', label: 'Karnataka, Bengaluru (IN)', count: 1, share: 0.25 },
      { key: '__unknown', label: 'Unknown', count: 3, share: 0.75, muted: true },
    ]);
  });

  it('Other comes before Unknown, both last and neutral', () => {
    const data: Breakdown = {
      meta: META,
      dimension: 'asn',
      total: 10,
      rows: [{ key: '55836', count: 6, share: 0.6 }],
      unknown: 1,
      other: 3,
    };
    expect(rankedRows(data).map((r) => [r.label, r.muted === true])).toEqual([
      ['AS55836', false],
      ['Other', true],
      ['Unknown', true],
    ]);
  });
});

describe('click to filter (DESIGN E3)', () => {
  const none = parseFilters(new URLSearchParams());

  it('a state sets its country too, with the same URL keys as the filter menu', () => {
    const next = filterForBreakdown('admin1', 'IN|Karnataka', none);
    expect(next === null ? '' : serializeFilters(next).toString()).toBe(
      'country_code=IN&admin1=Karnataka',
    );
  });

  it('an automated class also includes automated traffic, or it would show nothing', () => {
    const next = filterForBreakdown('classification', 'bot', none);
    expect(next === null ? '' : serializeFilters(next).toString()).toBe(
      'include_automated=true&classification=bot',
    );
  });

  it('a dimension with no filter key is not clickable', () => {
    expect(filterForBreakdown('browser', 'Chrome', none)).toBeNull();
  });

  it('the funnel says notified is not measured, rather than zero', () => {
    const data: Funnel = {
      meta: META,
      steps: [
        { step: 'requests', count: 4, reason: null },
        { step: 'notified', count: null, reason: 'notifications_not_built' },
      ],
    };
    const { table } = funnelChart(data);
    expect(table.rows[1]).toEqual(['Notified', 'Not measured (notifications_not_built)', '—']);
  });

  it('the source flow reads in words', () => {
    const data: SourceFlow = {
      meta: META,
      visits: 2,
      sources: ['rdns', 'none'],
      levels: ['city', 'none'],
      links: [
        { source: 'rdns', target: 'city', value: 1 },
        { source: 'none', target: 'none', value: 1 },
      ],
    };
    expect(sourceFlowChart(data).table.rows).toEqual([
      ['Reverse DNS', 'City', 1],
      ['None', 'Abstained', 1],
    ]);
  });
});

describe('CSP and accessibility settings that are easy to undo', () => {
  const p: Palette = {
    text: 'a',
    muted: 'b',
    subtle: 'c',
    border: 'd',
    borderStrong: 'e',
    surface: 'f',
    surface2: 'g',
    overlay: 'h',
    accent: 'i',
    accentBg: 'i2',
    ok: 'j',
    warn: 'k',
    error: 'l',
    series: ['m'],
    sequential: ['n'],
    mapLand: 'o',
  };

  it('zod never probes for eval, which the CSP reports on every page load (ERRORS E44)', () => {
    expect(z.config().jitless).toBe(true);
  });

  it('ECharts keeps decals but never overwrites the chart name (ERRORS E46)', () => {
    expect(chartTheme(p, { decals: true }).aria).toEqual({
      enabled: true,
      label: { enabled: false },
      decal: { show: true },
    });
  });
});

describe('M5.6 charts (DESIGN 12 E19, E21, E22)', () => {
  it('folds hourly buckets into weekday × hour in the reporting time zone', () => {
    const data: TimeSeries = {
      meta: META,
      bucket: 'hour',
      // 21:00 IST on Monday 5 October is 15:30 UTC; it must land on Monday, 21h, not 15h.
      buckets: ['2026-10-05T15:30:00Z', '2026-10-06T03:30:00Z', '2026-10-06T04:30:00Z'],
      series: [{ key: 'all', label: 'Visits', values: [3, 0, 2] }],
    };
    const cells = foldHourWeekday(data, 'Asia/Kolkata');
    expect(cells[0]?.[21]).toBe(3);
    expect(cells[1]?.[10]).toBe(2);
    expect(cells.flat().reduce((a, b) => a + b, 0)).toBe(5);
    const chart = hourWeekdayChart(data, 'Asia/Kolkata');
    expect(chart.table.columns).toHaveLength(25);
    expect(chart.table.rows[0]?.[0]).toBe('Mon');
  });

  it('leaves zero-visit days out of the calendar series but keeps them in the table', () => {
    const data: Calendar = {
      meta: META,
      days: [
        { day: '2026-10-01', count: 0 },
        { day: '2026-10-02', count: 4 },
      ],
    };
    const chart = calendarChart(data);
    const option = chart.option({
      text: 'a',
      muted: 'b',
      subtle: 'c',
      border: 'd',
      borderStrong: 'e',
      surface: 'f',
      surface2: 'g',
      overlay: 'h',
      accent: 'i',
      accentBg: 'i2',
      ok: 'j',
      warn: 'k',
      error: 'l',
      series: ['m'],
      sequential: ['n'],
      mapLand: 'o',
    }) as { series: { data: unknown[] }[]; visualMap: { min: number } };
    expect(option.series[0]?.data).toEqual([['2026-10-02', 4]]);
    expect(option.visualMap.min).toBe(1);
    expect(chart.table.rows).toHaveLength(2);
  });

  it('puts a glyph on device, connection and app rows, and none on states', () => {
    const device: Breakdown = {
      meta: META,
      dimension: 'device_class',
      total: 3,
      rows: [{ key: 'mobile', count: 3, share: 1 }],
      unknown: 0,
      other: 0,
    };
    expect(rankedRows(device)[0]?.icon).toBe('DeviceMobile');
    expect(
      rankedRows({
        ...device,
        dimension: 'admin1',
        rows: [{ key: 'IN|Goa', count: 3, share: 1 }],
      })[0]?.icon,
    ).toBeUndefined();
  });
});
