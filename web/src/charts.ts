/**
 * Option builders for every chart in F9.AC1–F9.AC12.
 *
 * Each returns the ECharts option (as a function of the theme palette) **and** the same
 * figures as a table, because no chart may convey its meaning by colour alone
 * (NFR7.AC3). Pure functions of API data: no fetching, no state.
 */

import type { EChartsCoreOption } from 'echarts/core';
import { axisStyle, tooltipText, type DataTable, type OptionBuilder } from '@/components/chartkit';
import type {
  Breakdown,
  Calendar,
  Confidence,
  Funnel,
  Signals,
  SourceFlow,
  TimeSeries,
} from '@/api/schemas';
import { count, dimensionValue, label, pct } from '@/format';
import type { Palette } from '@/theme';

export interface Chart {
  readonly option: OptionBuilder;
  readonly table: DataTable;
}

interface AxisParam {
  readonly axisValueLabel?: string;
  readonly seriesName?: string;
  readonly value?: unknown;
  readonly name?: string;
  readonly data?: unknown;
}

function axisTooltip(params: unknown): string {
  const list = (Array.isArray(params) ? params : [params]) as AxisParam[];
  const title = list[0]?.axisValueLabel ?? list[0]?.name ?? '';
  return tooltipText(
    title,
    list.map(
      (p) =>
        `${p.seriesName ?? ''}: ${typeof p.value === 'number' ? p.value.toLocaleString() : String(p.value)}`,
    ),
  );
}

function bucketLabel(iso: string, bucket: 'day' | 'hour', zone: string): string {
  const date = new Date(iso);
  return bucket === 'day'
    ? date.toLocaleDateString('en-IN', { timeZone: zone, month: 'short', day: 'numeric' })
    : date.toLocaleString('en-IN', {
        timeZone: zone,
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
}

// ---------------------------------------------------------------------------
// F9.AC3 -- time series
// ---------------------------------------------------------------------------

export function timeSeriesChart(data: TimeSeries, zone: string): Chart {
  const labels = data.buckets.map((b) => bucketLabel(b, data.bucket, zone));
  const stacked = data.series.length > 1;
  return {
    option: (p: Palette): EChartsCoreOption => ({
      grid: { left: 48, right: 16, top: stacked ? 36 : 16, bottom: 32 },
      legend: stacked ? { top: 0, textStyle: { color: p.text } } : undefined,
      tooltip: { trigger: 'axis', formatter: axisTooltip },
      xAxis: { type: 'category', data: labels, ...axisStyle(p) },
      yAxis: { type: 'value', minInterval: 1, ...axisStyle(p) },
      series: data.series.map((s) => ({
        name: s.label,
        type: stacked ? 'bar' : 'line',
        stack: stacked ? 'total' : undefined,
        data: s.values,
        showSymbol: labels.length <= 60,
        areaStyle: stacked ? undefined : { opacity: 0.12 },
      })),
    }),
    table: {
      columns: ['Bucket', ...data.series.map((s) => s.label)],
      rows: labels.map((l, i) => [l, ...data.series.map((s) => s.values[i] ?? 0)]),
    },
  };
}

// ---------------------------------------------------------------------------
// F9.AC6 -- calendar heatmap
// ---------------------------------------------------------------------------

export function calendarChart(data: Calendar): Chart {
  const max = Math.max(1, ...data.days.map((d) => d.count));
  const first = data.days[0]?.day ?? '';
  const last = data.days.at(-1)?.day ?? '';
  return {
    option: (p: Palette): EChartsCoreOption => ({
      tooltip: {
        formatter: (params: unknown) => {
          const value = (params as { value?: [string, number] }).value;
          return tooltipText(value?.[0] ?? '', [`${count(value?.[1] ?? 0)} visits`]);
        },
      },
      visualMap: {
        min: 0,
        max,
        type: 'piecewise',
        orient: 'horizontal',
        left: 'center',
        bottom: 0,
        splitNumber: 5,
        inRange: { color: [...p.sequential] },
        textStyle: { color: p.text },
      },
      calendar: {
        range: [first, last],
        top: 24,
        left: 36,
        right: 12,
        cellSize: ['auto', 14],
        itemStyle: { color: p.surface, borderColor: p.border },
        splitLine: { lineStyle: { color: p.border } },
        yearLabel: { show: false },
        dayLabel: { color: p.muted, firstDay: 1 },
        monthLabel: { color: p.muted },
      },
      series: [
        {
          type: 'heatmap',
          coordinateSystem: 'calendar',
          data: data.days.map((d) => [d.day, d.count]),
        },
      ],
    }),
    table: { columns: ['Day', 'Visits'], rows: data.days.map((d) => [d.day, d.count]) },
  };
}

// ---------------------------------------------------------------------------
// Horizontal bars: breakdowns (F9.AC4), signals (F9.AC11), funnel (F9.AC8)
// ---------------------------------------------------------------------------

function hbar(
  labels: readonly string[],
  values: readonly number[],
  name: string,
  color?: number,
): OptionBuilder {
  return (p: Palette) => ({
    grid: { left: 8, right: 48, top: 8, bottom: 8, containLabel: true },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: axisTooltip },
    xAxis: { type: 'value', minInterval: 1, ...axisStyle(p) },
    yAxis: {
      type: 'category',
      inverse: true,
      data: [...labels],
      ...axisStyle(p),
      axisLabel: { color: p.text, width: 180, overflow: 'truncate' },
    },
    series: [
      {
        name,
        type: 'bar',
        data: [...values],
        itemStyle: color === undefined ? undefined : { color: p.series[color] },
        label: { show: true, position: 'right', color: p.text },
      },
    ],
  });
}

export function breakdownChart(data: Breakdown): Chart {
  const rows = data.rows.map(
    (r) => [dimensionValue(data.dimension, r.key), r.count, pct(r.share, 1)] as const,
  );
  const extra: (readonly [string, number, string])[] = [];
  if (data.other > 0)
    extra.push(['Other', data.other, pct(data.total ? data.other / data.total : null, 1)]);
  if (data.unknown > 0) {
    extra.push([
      dimensionValue(data.dimension, ''),
      data.unknown,
      pct(data.total ? data.unknown / data.total : null, 1),
    ]);
  }
  const all = [...rows, ...extra];
  return {
    option: hbar(
      all.map((r) => r[0]),
      all.map((r) => r[1]),
      'Visits',
    ),
    table: { columns: ['Value', 'Visits', 'Share'], rows: all.map((r) => [...r]) },
  };
}

export function signalsChart(data: Signals): Chart {
  return {
    option: hbar(
      data.rows.map((r) => r.rule_id),
      data.rows.map((r) => r.count),
      'Visits on which the rule fired',
      3,
    ),
    table: {
      columns: ['Rule', 'Category', 'Visits', 'Share of visits'],
      rows: data.rows.map((r) => [r.rule_id, r.category, r.count, pct(r.share, 1)]),
    },
  };
}

const STEP_LABEL: Readonly<Record<string, string>> = {
  requests: 'Requests',
  captured: 'Captured server-side',
  enriched: 'Enriched by the page',
  consented: 'Location consented',
  notified: 'Notified',
};

export function funnelChart(data: Funnel): Chart {
  const measured = data.steps.filter((s) => s.count !== null);
  const first = data.steps[0]?.count ?? 0;
  return {
    option: hbar(
      measured.map((s) => STEP_LABEL[s.step] ?? s.step),
      measured.map((s) => s.count ?? 0),
      'Visits',
      1,
    ),
    table: {
      columns: ['Stage', 'Count', 'Of requests'],
      rows: data.steps.map((s) => [
        STEP_LABEL[s.step] ?? s.step,
        s.count === null ? `Not measured (${s.reason ?? 'unknown'})` : s.count,
        s.count === null ? '—' : pct(first ? s.count / first : null, 1),
      ]),
    },
  };
}

// ---------------------------------------------------------------------------
// F9.AC9 -- confidence histograms
// ---------------------------------------------------------------------------

const BIN_LABELS = Array.from(
  { length: 10 },
  (_, i) => `${(i / 10).toFixed(1)}–${((i + 1) / 10).toFixed(1)}`,
);

export function confidenceChart(data: Confidence): Chart {
  return {
    option: (p: Palette) => ({
      grid: { left: 48, right: 16, top: 36, bottom: 32 },
      legend: { top: 0, textStyle: { color: p.text } },
      tooltip: { trigger: 'axis', formatter: axisTooltip },
      xAxis: {
        type: 'category',
        data: BIN_LABELS,
        name: 'Confidence',
        nameLocation: 'middle',
        nameGap: 24,
        ...axisStyle(p),
      },
      yAxis: { type: 'value', minInterval: 1, ...axisStyle(p) },
      series: data.levels.map((l) => ({ name: label(l.level), type: 'bar', data: l.bins })),
    }),
    table: {
      columns: ['Level', ...BIN_LABELS, 'Unscored'],
      rows: data.levels.map((l) => [label(l.level), ...l.bins, l.unscored]),
    },
  };
}

// ---------------------------------------------------------------------------
// F9.AC7 -- source flow Sankey
// ---------------------------------------------------------------------------

export function sourceFlowChart(data: SourceFlow): Chart {
  // Sources and levels share names ("none"), so nodes are namespaced.
  const sourceNode = (s: string): string => `${label(s)} ›`;
  const levelNode = (l: string): string => (l === 'none' ? 'Abstained' : `Emitted: ${label(l)}`);
  return {
    option: (p: Palette) => ({
      tooltip: {
        trigger: 'item',
        formatter: (params: unknown) => {
          const item = params as {
            name?: string;
            value?: number;
            data?: { source?: string; target?: string };
          };
          const title =
            item.data?.source !== undefined
              ? `${item.data.source} ${item.data.target ?? ''}`
              : (item.name ?? '');
          return tooltipText(title, [`${count(item.value ?? 0)} visits`]);
        },
      },
      series: [
        {
          type: 'sankey',
          left: 8,
          right: 120,
          top: 8,
          bottom: 8,
          nodeGap: 10,
          emphasis: { focus: 'adjacency' },
          label: { color: p.text },
          lineStyle: { color: 'gradient', opacity: 0.35 },
          data: [
            ...data.sources.map((s) => ({ name: sourceNode(s) })),
            ...data.levels.map((l) => ({ name: levelNode(l) })),
          ],
          links: data.links.map((link) => ({
            source: sourceNode(link.source),
            target: levelNode(link.target),
            value: link.value,
          })),
        },
      ],
    }),
    table: {
      columns: ['Source', 'Level emitted', 'Visits'],
      rows: data.links.map((l) => [
        label(l.source),
        l.target === 'none' ? 'Abstained' : label(l.target),
        l.value,
      ]),
    },
  };
}
