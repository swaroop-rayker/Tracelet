/**
 * Option builders for every chart in F9.AC1–F9.AC12.
 *
 * Each returns the ECharts option (as a function of the theme palette) **and** the same
 * figures as a table, because no chart may convey its meaning by colour alone
 * (NFR7.AC3). Pure functions of API data: no fetching, no state.
 */

import type { EChartsCoreOption } from 'echarts/core';
import {
  categoryAxis,
  primaryArea,
  tooltipText,
  valueAxis,
  type DataTable,
  type OptionBuilder,
} from '@/components/chartkit';
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
  /**
   * Whether colour tells series apart, so texture patterns are drawn as well (DESIGN 6.4,
   * NFR7.AC3). False where colour carries no meaning: one series, a heatmap, a labelled sankey.
   */
  readonly decals: boolean;
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
      grid: { left: 8, right: 8, top: stacked ? 40 : 12, bottom: 4, containLabel: true },
      legend: stacked
        ? {
            top: 0,
            right: 0,
            icon: 'roundRect',
            itemWidth: 10,
            itemHeight: 10,
            textStyle: { color: p.muted },
          }
        : undefined,
      tooltip: { trigger: 'axis', formatter: axisTooltip },
      xAxis: { type: 'category', data: labels, boundaryGap: stacked, ...categoryAxis(p) },
      yAxis: { type: 'value', minInterval: 1, ...valueAxis(p) },
      series: data.series.map((s) =>
        stacked
          ? { name: s.label, type: 'bar', stack: 'total', data: s.values, barMaxWidth: 28 }
          : {
              name: s.label,
              type: 'line',
              data: s.values,
              smooth: 0.25,
              showSymbol: false,
              symbolSize: 6,
              lineStyle: { width: 1.75 },
              areaStyle: primaryArea(p),
              emphasis: { focus: 'none' },
            },
      ),
    }),
    table: {
      columns: ['Bucket', ...data.series.map((s) => s.label)],
      rows: labels.map((l, i) => [l, ...data.series.map((s) => s.values[i] ?? 0)]),
    },
    decals: stacked,
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
        itemWidth: 10,
        itemHeight: 10,
        textStyle: { color: p.subtle, fontSize: 11 },
      },
      calendar: {
        range: [first, last],
        top: 20,
        left: 32,
        right: 8,
        cellSize: ['auto', 13],
        itemStyle: { color: p.surface2, borderColor: p.surface, borderWidth: 2 },
        splitLine: { show: false },
        yearLabel: { show: false },
        dayLabel: {
          color: p.subtle,
          firstDay: 1,
          fontSize: 11,
          nameMap: ['', 'Mon', '', 'Wed', '', 'Fri', ''],
        },
        monthLabel: { color: p.subtle, fontSize: 11 },
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
    decals: false,
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
    grid: { left: 8, right: 56, top: 4, bottom: 4, containLabel: true },
    tooltip: { trigger: 'axis', axisPointer: { type: 'none' }, formatter: axisTooltip },
    xAxis: {
      type: 'value',
      minInterval: 1,
      ...valueAxis(p),
      splitLine: { show: false },
      axisLabel: { show: false },
    },
    yAxis: {
      type: 'category',
      inverse: true,
      data: [...labels],
      ...categoryAxis(p),
      axisLine: { show: false },
      axisLabel: { color: p.text, fontSize: 12, width: 180, overflow: 'truncate' },
    },
    series: [
      {
        name,
        type: 'bar',
        data: [...values],
        barMaxWidth: 20,
        itemStyle: {
          borderRadius: [0, 4, 4, 0],
          ...(color === undefined ? {} : { color: p.series[color] }),
        },
        label: { show: true, position: 'right', color: p.muted, fontSize: 12 },
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
    decals: false,
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
    decals: false,
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
      0,
    ),
    table: {
      columns: ['Stage', 'Count', 'Of requests'],
      rows: data.steps.map((s) => [
        STEP_LABEL[s.step] ?? s.step,
        s.count === null ? `Not measured (${s.reason ?? 'unknown'})` : s.count,
        s.count === null ? '—' : pct(first ? s.count / first : null, 1),
      ]),
    },
    decals: false,
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
      grid: { left: 8, right: 8, top: 40, bottom: 28, containLabel: true },
      legend: {
        top: 0,
        right: 0,
        icon: 'roundRect',
        itemWidth: 10,
        itemHeight: 10,
        textStyle: { color: p.muted },
      },
      tooltip: { trigger: 'axis', formatter: axisTooltip },
      xAxis: {
        type: 'category',
        data: BIN_LABELS,
        name: 'Confidence',
        nameLocation: 'middle',
        nameGap: 28,
        nameTextStyle: { color: p.subtle, fontSize: 11 },
        ...categoryAxis(p),
      },
      yAxis: { type: 'value', minInterval: 1, ...valueAxis(p) },
      series: data.levels.map((l) => ({
        name: label(l.level),
        type: 'bar',
        data: l.bins,
        barMaxWidth: 14,
        itemStyle: { borderRadius: [3, 3, 0, 0] },
      })),
    }),
    table: {
      columns: ['Level', ...BIN_LABELS, 'Unscored'],
      rows: data.levels.map((l) => [label(l.level), ...l.bins, l.unscored]),
    },
    decals: true,
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
          nodeGap: 12,
          nodeWidth: 10,
          emphasis: { focus: 'adjacency' },
          label: { color: p.text, fontSize: 12 },
          itemStyle: { borderWidth: 0 },
          lineStyle: { color: 'source', opacity: 0.28, curveness: 0.5 },
          data: [
            ...data.sources.map((s) => ({ name: sourceNode(s), itemStyle: { color: p.accent } })),
            ...data.levels.map((l) => ({
              name: levelNode(l),
              itemStyle: { color: l === 'none' ? p.subtle : (p.series[5] ?? p.muted) },
            })),
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
    decals: false,
  };
}
