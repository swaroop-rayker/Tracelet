/**
 * Every chart builder returns its figures as a table too (NFR7.AC3: no chart conveys
 * meaning by colour alone), and says what it could not measure in words.
 */

import { describe, expect, it } from 'vitest';
import type { Breakdown, Funnel, Meta, SourceFlow } from '@/api/schemas';
import { breakdownChart, funnelChart, sourceFlowChart } from '@/charts';

const META: Meta = {
  start: '2026-09-03T00:00:00+05:30',
  end: '2026-10-03T00:00:00+05:30',
  reporting_tz: 'Asia/Kolkata',
  computed_from: 'raw',
  refreshed_at: null,
  stage_mix: { total: 4, server: 0, enriched: 3, server_only: 1, rate_limited: 0 },
};

describe('chart tables', () => {
  it('a location breakdown names abstentions instead of dropping them', () => {
    const data: Breakdown = {
      meta: META,
      dimension: 'city',
      total: 4,
      rows: [{ key: 'IN|Karnataka|Bengaluru', count: 1, share: 0.25 }],
      unknown: 3,
      other: 0,
    };
    const { table } = breakdownChart(data);
    expect(table.rows).toEqual([
      ['Karnataka, Bengaluru (IN)', 1, '25.0%'],
      ['Abstained', 3, '75.0%'],
    ]);
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
