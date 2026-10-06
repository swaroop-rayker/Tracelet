/**
 * ECharts, imported per module and wrapped in one component (ADR-0003: no
 * `echarts-for-react`, and never the barrel export).
 *
 * Three accessibility rules are enforced here rather than per chart (NFR7):
 *
 * - **Not by colour alone** (NFR7.AC3): the `aria` component's decal patterns are on,
 *   so every series also differs by texture, and each chart is required to supply its
 *   data as a table, shown under a keyboard-reachable disclosure.
 * - **A text alternative**: the canvas has `role="img"` and a label.
 * - **Readable tooltips under the CSP**: the stylesheet policy forbids inline
 *   `style=""`, which ECharts' default tooltip markup uses, so tooltips are plain text
 *   built by `tooltipText` and escaped.
 *
 * The chart reads its colours from the active theme and redraws when it changes.
 */

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { BarChart, HeatmapChart, LineChart, SankeyChart } from 'echarts/charts';
import {
  AriaComponent,
  CalendarComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  VisualMapComponent,
} from 'echarts/components';
import { init, use as registerModules, type EChartsType } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import { chartTheme, tableToCsv, type DataTable, type OptionBuilder } from '@/components/chartkit';
import { Button, cssVars } from '@/components/ui';
import { palette } from '@/theme';

registerModules([
  BarChart,
  HeatmapChart,
  LineChart,
  SankeyChart,
  AriaComponent,
  CalendarComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  VisualMapComponent,
  CanvasRenderer,
]);

export function EChart({
  option,
  label,
  table,
  decals,
  height = 280,
}: {
  readonly option: OptionBuilder;
  /** What the chart shows, for screen readers. */
  readonly label: string;
  /** The same data as a table: required, because no chart may rely on colour alone. */
  readonly table: DataTable;
  /** Texture patterns where colour separates series (DESIGN 6.4); from the builder. */
  readonly decals: boolean;
  readonly height?: number;
}): React.JSX.Element {
  const [showTable, setShowTable] = useState(false);
  const container = useRef<HTMLDivElement | null>(null);
  const chart = useRef<EChartsType | null>(null);
  const [themeTick, setThemeTick] = useState(0);

  useEffect(() => {
    const element = container.current;
    if (element === null) return undefined;
    const instance = init(element, undefined, { renderer: 'canvas' });
    chart.current = instance;
    const observer = new ResizeObserver(() => {
      instance.resize();
    });
    observer.observe(element);
    const onTheme = (): void => {
      setThemeTick((n) => n + 1);
    };
    window.addEventListener('tracelet:theme', onTheme);
    return () => {
      window.removeEventListener('tracelet:theme', onTheme);
      observer.disconnect();
      instance.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    const p = palette();
    const theme = chartTheme(p, { decals });
    const own = option(p);
    // The tooltip is merged one level deep, so a chart's formatter keeps the themed look.
    const tooltip = {
      ...(theme.tooltip as object),
      ...((own.tooltip as object | undefined) ?? {}),
    };
    chart.current?.setOption({ ...theme, ...own, tooltip }, { notMerge: true });
  }, [option, decals, themeTick]);

  return (
    <div className="chart">
      <div
        ref={container}
        className="chart__canvas"
        role="img"
        aria-label={label}
        style={cssVars({ '--chart-h': `${String(height)}px` })}
      />
      <div className="chart__tools">
        <Button
          variant="ghost"
          size="sm"
          icon="Table"
          aria-pressed={showTable}
          onClick={() => {
            setShowTable((v) => !v);
          }}
        >
          Data
        </Button>
        <Button
          variant="ghost"
          size="sm"
          icon="Download"
          onClick={() => {
            downloadCsv(table, label);
          }}
        >
          CSV
        </Button>
      </div>
      {/* Always present for screen readers; shown on request for everyone (NFR7.AC3). */}
      <div className={showTable ? undefined : 'sr-only'}>
        <TableView table={table} caption={label} />
      </div>
    </div>
  );
}

/** The chart's own figures as a CSV file, generated in the browser (DESIGN 12 E9). */
function downloadCsv(table: DataTable, label: string): void {
  const blob = new Blob([`\ufeff${tableToCsv(table)}`], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')}.csv`;
  link.click();
  window.setTimeout(() => {
    URL.revokeObjectURL(url);
  }, 0);
}

export function TableView({
  table,
  caption,
}: {
  readonly table: DataTable;
  readonly caption?: ReactNode;
}): React.JSX.Element {
  return (
    <div className="dt-wrap">
      <table className="dt dt--compact">
        {caption !== undefined && <caption className="sr-only">{caption}</caption>}
        <thead>
          <tr>
            {table.columns.map((c) => (
              <th key={c} scope="col">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td key={j} className={typeof cell === 'number' ? 'num' : undefined}>
                  {typeof cell === 'number' ? cell.toLocaleString() : cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
