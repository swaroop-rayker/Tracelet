/**
 * Data display (DESIGN §5.4): Stat, StatStrip, DataTable, RankedList.
 */

import type { ReactNode } from 'react';
import { Icon } from '@/components/icons';
import { InfoTip } from '@/components/ui/Tooltip';
import { cssVars, cx } from '@/components/ui/util';

/** A change against the previous period, judged by what is *better*, not by direction. */
export interface Delta {
  readonly direction: 'up' | 'down' | 'flat';
  /** "12%", "1.2 pts" -- without the arrow, which the component draws. */
  readonly text: string;
  /** Green for better, red for worse, accent for a neutral metric (DESIGN §5.4, A4). */
  readonly judgement: 'good' | 'bad' | 'neutral';
}

const ARROW = { up: Icon.Up, down: Icon.Down, flat: Icon.Flat } as const;
const SPOKEN = { up: 'up', down: 'down', flat: 'unchanged' } as const;

/** A KPI card: label, the number, and how it changed (DESIGN §5.4). */
export function Stat({
  label,
  value,
  exact,
  delta,
  comparison,
  note,
  hint,
}: {
  readonly label: string;
  /** As displayed: "128.4K", "90.0%", "—". */
  readonly value: string;
  /** The exact figure, when `value` is compact. */
  readonly exact?: string | undefined;
  readonly delta?: Delta | undefined;
  /** What the delta compares against: "vs previous 30 days". */
  readonly comparison?: string | undefined;
  /** In place of a delta: why there is none ("No earlier data", "Geofencing arrives in M6"). */
  readonly note?: ReactNode | undefined;
  /** The KPI's definition, behind an ⓘ (DESIGN §7.4). */
  readonly hint?: string | undefined;
}): React.JSX.Element {
  const Arrow = delta === undefined ? null : ARROW[delta.direction];
  return (
    <li className="stat">
      <span className="stat__label">
        {label}
        {hint !== undefined && <InfoTip term={label}>{hint}</InfoTip>}
      </span>
      <span className="stat__value" title={exact}>
        {value}
        {exact !== undefined && <span className="sr-only"> ({exact})</span>}
      </span>
      {delta !== undefined && Arrow !== null ? (
        <span className={`stat__delta stat__delta--${delta.judgement}`}>
          <Arrow size={13} strokeWidth={2} aria-hidden="true" />
          <span>
            <span className="sr-only">{SPOKEN[delta.direction]} </span>
            {delta.text}
          </span>
          {comparison !== undefined && <span className="t-meta">{comparison}</span>}
        </span>
      ) : (
        <span className="stat__delta">{note ?? '—'}</span>
      )}
    </li>
  );
}

/** A row of KPI cards: four across, two by two below 1024 px. */
export function Stats({
  label,
  children,
}: {
  readonly label: string;
  readonly children: ReactNode;
}): React.JSX.Element {
  return (
    <ul className="stats" aria-label={label}>
      {children}
    </ul>
  );
}

/** Secondary figures in one quiet line under the KPI cards (DESIGN §10.1). */
export function StatStrip({
  label,
  items,
}: {
  readonly label: string;
  readonly items: readonly {
    readonly key: string;
    readonly label: string;
    readonly value: string;
    readonly hint?: string | undefined;
  }[];
}): React.JSX.Element {
  return (
    <ul className="statstrip" aria-label={label}>
      {items.map((item) => (
        <li key={item.key}>
          <span>{item.label}</span>
          <strong>{item.value}</strong>
          {item.hint !== undefined && <span className="t-meta">{item.hint}</span>}
        </li>
      ))}
    </ul>
  );
}

export interface Column<T> {
  readonly key: string;
  readonly header: ReactNode;
  readonly render: (row: T) => ReactNode;
  readonly numeric?: boolean;
  readonly className?: string;
}

/** A table for real data: hairline rows, strong headers, numbers right-aligned (DESIGN §5.4). */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  caption,
  compact = false,
}: {
  readonly columns: readonly Column<T>[];
  readonly rows: readonly T[];
  readonly rowKey: (row: T, index: number) => string;
  /** Read by screen readers; visually hidden (the card title shows it). */
  readonly caption: string;
  readonly compact?: boolean;
}): React.JSX.Element {
  return (
    <div className="dt-wrap">
      <table className={cx('dt', compact && 'dt--compact')}>
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} scope="col" className={cx(c.numeric === true && 'num', c.className)}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={rowKey(row, i)}>
              {columns.map((c) => (
                <td key={c.key} className={cx(c.numeric === true && 'num', c.className)}>
                  {c.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export interface RankedRow {
  readonly key: string;
  readonly label: string;
  readonly count: number;
  /** Of the whole, 0..1; null when the whole is unknown. */
  readonly share: number | null;
  /** "Other" and "Unknown": neutral, always last. */
  readonly muted?: boolean;
}

/**
 * A ranked list with inline bars -- a semantic table, so it needs no separate data table to
 * be accessible (NFR7.AC3, ADR-0019). Bars are proportional to the top row. With `onSelect`,
 * each label is a button that applies the row as a filter (DESIGN §12 E3).
 */
export function RankedList({
  rows,
  caption,
  labelHeader,
  valueHeader = 'Visits',
  onSelect,
  selectHint,
}: {
  readonly rows: readonly RankedRow[];
  readonly caption: string;
  readonly labelHeader: string;
  readonly valueHeader?: string | undefined;
  readonly onSelect?: ((key: string) => void) | undefined;
  /** The verb for the button's accessible name: "Filter to". */
  readonly selectHint?: string | undefined;
}): React.JSX.Element {
  // Scale to the top *named* row: "Other" can outnumber every row and must not lead the eye.
  const named = rows.filter((r) => r.muted !== true);
  const top = Math.max(1, ...(named.length > 0 ? named : rows).map((r) => r.count));
  return (
    <table className="ranked">
      <caption className="sr-only">{caption}</caption>
      <thead>
        <tr>
          <th scope="col">{labelHeader}</th>
          <th scope="col" className="num">
            {valueHeader}
          </th>
          <th scope="col" className="num">
            Share
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const selectable = onSelect !== undefined && r.muted !== true;
          return (
            <tr key={r.key} className={cx(r.muted === true && 'ranked__row--muted')}>
              <td className="ranked__label">
                <span
                  className="ranked__bar"
                  style={cssVars({ '--w': `${Math.min(100, (r.count / top) * 100).toFixed(1)}%` })}
                  aria-hidden="true"
                />
                {selectable ? (
                  <button
                    type="button"
                    className="ranked__name ranked__select"
                    title={r.label}
                    aria-label={`${selectHint ?? 'Filter to'} ${r.label}`}
                    onClick={() => {
                      onSelect(r.key);
                    }}
                  >
                    {r.label}
                  </button>
                ) : (
                  <span className="ranked__name" title={r.label}>
                    {r.label}
                  </span>
                )}
              </td>
              <td className="num">{r.count.toLocaleString()}</td>
              <td className="num share">
                {r.share === null ? '—' : `${(r.share * 100).toFixed(1)}%`}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
