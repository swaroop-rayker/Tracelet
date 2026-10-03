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
    // `label` off: ECharts would overwrite the canvas's own aria-label with a generated data
    // dump ("… is NaN" on a Sankey). The name is ours; the data is the table (ERRORS E46).
    aria: { enabled: true, label: { enabled: false }, decal: { show: decals } },
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

/** `color` (a `#rrggbb` token value) at `alpha`, for gradients and translucent fills. */
export function withAlpha(color: string, alpha: number): string {
  const hex = color.trim().replace(/^#/, '');
  if (!/^[0-9a-f]{6}$/i.test(hex)) return color;
  const n = Number.parseInt(hex, 16);
  return `rgba(${String((n >> 16) & 255)}, ${String((n >> 8) & 255)}, ${String(n & 255)}, ${String(alpha)})`;
}

/** The primary series' area: accent fading to transparent -- the one gradient (DESIGN §6.2). */
export function primaryArea(p: Palette): Record<string, unknown> {
  return {
    color: {
      type: 'linear',
      x: 0,
      y: 0,
      x2: 0,
      y2: 1,
      colorStops: [
        { offset: 0, color: withAlpha(p.accent, 0.18) },
        { offset: 1, color: withAlpha(p.accent, 0) },
      ],
    },
  };
}

/** A data table as CSV, quoted where needed, for the per-chart download (DESIGN §12 E9). */
export function tableToCsv(table: DataTable): string {
  const cell = (value: string | number): string => {
    const text = String(value);
    // A leading = + - @ would run as a formula in a spreadsheet (as the server's export guards).
    const safe = /^[=+\-@]/.test(text) ? `'${text}` : text;
    return /[",\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
  };
  return [table.columns, ...table.rows].map((row) => row.map(cell).join(',')).join('\n');
}

export interface DataTable {
  readonly columns: readonly string[];
  readonly rows: readonly (readonly (string | number)[])[];
}
