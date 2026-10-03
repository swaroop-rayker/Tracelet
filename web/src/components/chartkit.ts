/**
 * Chart helpers shared by every ECharts option builder: theme-aware base options, axis
 * styling, and tooltip text that needs no inline styles (the stylesheet CSP forbids
 * them). Pure functions, kept apart from the component so the component file exports
 * only components.
 */

import type { EChartsCoreOption } from 'echarts/core';
import type { Palette } from '@/theme';

export type OptionBuilder = (p: Palette) => EChartsCoreOption;

/** How a chart uses colour, which decides whether texture patterns are drawn (DESIGN §6.4). */
export interface ChartThemeOptions {
  /**
   * True wherever colour distinguishes series (split or stacked charts). A single-series
   * chart conveys nothing by colour, so patterns would only add noise (NFR7.AC3, ADR-0019).
   */
  readonly decals: boolean;
}

/**
 * The one chart theme (DESIGN §6.2): theme colours, Inter, a quiet tooltip styled by the
 * stylesheet (`.chart-tooltip`), first-render animation only.
 */
export function chartTheme(p: Palette, { decals }: ChartThemeOptions): EChartsCoreOption {
  return {
    color: [...p.series],
    backgroundColor: 'transparent',
    textStyle: { color: p.muted, fontFamily: 'inherit', fontSize: 12 },
    aria: { enabled: true, decal: { show: decals } },
    animationDuration: 250,
    animationDurationUpdate: 0,
    tooltip: {
      className: 'chart-tooltip',
      backgroundColor: p.overlay,
      borderColor: p.border,
      borderWidth: 1,
      padding: [8, 10],
      textStyle: { color: p.text, fontSize: 12, fontFamily: 'inherit' },
      axisPointer: { type: 'line', lineStyle: { color: p.borderStrong, width: 1 } },
    },
  };
}

/** Options every chart shares. Until each builder states its decal policy, decals stay on. */
export function baseOption(p: Palette): EChartsCoreOption {
  return chartTheme(p, { decals: true });
}

/** The value axis: dashed horizontal grid lines only, no axis line, subtle ticks. */
export function valueAxis(p: Palette): Record<string, unknown> {
  return {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: p.subtle, fontSize: 11 },
    splitLine: { lineStyle: { color: p.border, type: [3, 3] } },
  };
}

/** The category axis: a hairline base, no grid lines. */
export function categoryAxis(p: Palette): Record<string, unknown> {
  return {
    axisLine: { lineStyle: { color: p.border } },
    axisTick: { show: false },
    axisLabel: { color: p.subtle, fontSize: 11 },
    splitLine: { show: false },
  };
}

/** The pre-M5.5 axis style. Builders move to `valueAxis`/`categoryAxis` in M5.5 Phase 3. */
export function axisStyle(p: Palette): Record<string, unknown> {
  return {
    axisLine: { lineStyle: { color: p.border } },
    axisLabel: { color: p.muted },
    splitLine: { lineStyle: { color: p.border, opacity: 0.5 } },
  };
}

const ESCAPES: Readonly<Record<string, string>> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
};

export function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => ESCAPES[c] ?? c);
}

/** Tooltip markup with no inline styles: a bold title, then one line per entry. */
export function tooltipText(title: string, lines: readonly string[]): string {
  return [`<strong>${escapeHtml(title)}</strong>`, ...lines.map(escapeHtml)].join('<br/>');
}

export interface DataTable {
  readonly columns: readonly string[];
  readonly rows: readonly (readonly (string | number)[])[];
}
