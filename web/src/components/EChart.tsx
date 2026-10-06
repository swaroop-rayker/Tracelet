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
import { baseOption, type DataTable, type OptionBuilder } from '@/components/chartkit';
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
  height = 280,
}: {
  readonly option: OptionBuilder;
  /** What the chart shows, for screen readers. */
  readonly label: string;
  /** The same data as a table: required, because no chart may rely on colour alone. */
  readonly table: DataTable;
  readonly height?: number;
}): React.JSX.Element {
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
    chart.current?.setOption({ ...baseOption(p), ...option(p) }, { notMerge: true });
  }, [option, themeTick]);

  return (
    <div className="chart">
      <div ref={container} role="img" aria-label={label} style={{ height }} />
      <details className="chart-table">
        <summary>Show as table</summary>
        <TableView table={table} caption={label} />
      </details>
    </div>
  );
}

export function TableView({
  table,
  caption,
}: {
  readonly table: DataTable;
  readonly caption?: ReactNode;
}): React.JSX.Element {
  return (
    <div className="table-wrap">
      <table className="data">
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
