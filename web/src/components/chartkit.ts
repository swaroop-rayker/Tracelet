/**
 * Chart helpers shared by every ECharts option builder: theme-aware base options, axis
 * styling, and tooltip text that needs no inline styles (the stylesheet CSP forbids
 * them). Pure functions, kept apart from the component so the component file exports
 * only components.
 */

import type { EChartsCoreOption } from 'echarts/core';
import type { Palette } from '@/theme';

export type OptionBuilder = (p: Palette) => EChartsCoreOption;

/** Options every chart shares: theme colours, decals, text styling, HTML-free tooltips. */
export function baseOption(p: Palette): EChartsCoreOption {
  return {
    color: [...p.series],
    backgroundColor: 'transparent',
    textStyle: { color: p.text, fontFamily: 'inherit' },
    aria: { enabled: true, decal: { show: true } },
    animationDuration: 250,
  };
}

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
