/**
 * Every chart builder returns its figures as a table too (NFR7.AC3: no chart conveys
 * meaning by colour alone), and says what it could not measure in words.
 */

import { describe, expect, it } from 'vitest';
import type { Breakdown, Funnel, Meta, SourceFlow } from '@/api/schemas';
import { filterForBreakdown } from '@/components/shell/filterDefs';
import { funnelChart, rankedRows, sourceFlowChart } from '@/charts';
import { parseFilters, serializeFilters } from '@/filters';

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
