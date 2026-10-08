/**
 * Compare (F9.AC27, DESIGN §12 E35, §16 M7.7): two links over one period, or one scope over two
 * periods, side by side.
 *
 * Mostly UI: each side is the existing analytics requests with its own `link_id` or window, so
 * a figure here means exactly what it means on Overview. Everything is in the URL (UI-9):
 * `cmp_mode`, `cmp_a`, `cmp_b` (two links) and `cmp_from`, `cmp_to` (period B, local dates;
 * by default the period before A). Differences are words and numbers, never colour alone.
 */

import { useMemo } from 'react';
import { useSearchParams } from 'react-router';
import { useApi } from '@/api/query';
import {
  breakdownSchema,
  linkChoicesSchema,
  summarySchema,
  timeSeriesSchema,
  type Breakdown,
  type Kpi,
} from '@/api/schemas';
import { compareChart, rankedRows } from '@/charts';
import { EChart } from '@/components/EChart';
import { Panel } from '@/components/Panel';
import { PageHeader } from '@/components/shell/PageHeader';
import {
  DataTable,
  EmptyState,
  Field,
  RankedList,
  SegmentedControl,
  Select,
} from '@/components/ui';
import { apiParams, parseFilters, resolveWindow, withParams, type Filters } from '@/filters';
import { count, pct } from '@/format';
import { useSession } from '@/session';

type Mode = 'links' | 'periods';

const KPIS: readonly { readonly key: string; readonly label: string }[] = [
  { key: 'visits', label: 'Visits' },
  { key: 'unique_visitors', label: 'Unique visitors' },
  { key: 'human_share', label: 'Human share' },
  { key: 'consent_grant_rate', label: 'Location consent' },
  { key: 'enrichment_completion_rate', label: 'Enrichment completed' },
];

const DATE = /^\d{4}-\d{2}-\d{2}$/;

interface Side {
  readonly name: string;
  readonly params: URLSearchParams | null;
}

/** The two sides' request parameters, or null where a side is not chosen yet. */
function sides(
  filters: Filters,
  zone: string,
  mode: Mode,
  linkNames: Map<string, string>,
): [Side, Side] {
  const s = filters.scalars;
  if (mode === 'links') {
    // The global link filter does not apply: each side is its own link.
    const base = apiParams(filters, zone);
    base.delete('link_id');
    const side = (id: string | undefined, fallback: string): Side => ({
      name: id === undefined ? fallback : (linkNames.get(id) ?? fallback),
      params: id === undefined ? null : withParams(base, { link_id: id }),
    });
    return [side(s.cmp_a, 'Link A'), side(s.cmp_b, 'Link B')];
  }
  const a = apiParams(filters, zone);
  const window = resolveWindow(filters, zone);
  let b: URLSearchParams;
  const from = s.cmp_from;
  const to = s.cmp_to;
  if (from !== undefined && to !== undefined && DATE.test(from) && DATE.test(to)) {
    b = apiParams({ ...filters, range: 'custom', from, to }, zone);
  } else {
    const start = Date.parse(window.from);
    const end = Date.parse(window.to);
    b = withParams(a, {
      from: new Date(start - (end - start)).toISOString(),
      to: new Date(start).toISOString(),
    });
  }
  return [
    { name: 'This period', params: a },
    {
      name: from !== undefined && to !== undefined ? `${from} to ${to}` : 'The period before',
      params: b,
    },
  ];
}

export default function ComparePage(): React.JSX.Element {
  const { me } = useSession();
  const zone = me.reporting_tz;
  const [search, setSearch] = useSearchParams();
  const filters = parseFilters(search);
  const mode: Mode = filters.scalars.cmp_mode === 'periods' ? 'periods' : 'links';
  const links = useApi('/api/v1/links', null, linkChoicesSchema);
  const linkNames = useMemo(
    () => new Map((links.data ?? []).map((l) => [l.id, `${l.label} (${l.slug})`])),
    [links.data],
  );
  const [a, b] = sides(filters, zone, mode, linkNames);

  const set = (key: string, value: string | null): void => {
    const next = new URLSearchParams(search);
    if (value === null || value === '') next.delete(key);
    else next.set(key, value);
    setSearch(next, { replace: true });
  };
  const linkOptions = [
    { value: '', label: 'Choose a link' },
    ...(links.data ?? [])
      .filter((l) => l.archived_at === null)
      .map((l) => ({ value: l.id, label: `${l.label} (${l.slug})` })),
  ];

  return (
    <div className="page">
      <PageHeader
        title="Compare"
        description="Two links over one period, or one view over two periods, side by side."
        filters
        linkLocked={mode === 'links'}
      />
      <div className="compare__controls" role="group" aria-label="What to compare">
        <SegmentedControl
          label="Compare"
          value={mode}
          onChange={(v) => {
            set('cmp_mode', v === 'periods' ? 'periods' : null);
          }}
          options={[
            { value: 'links', label: 'Two links' },
            { value: 'periods', label: 'Two periods' },
          ]}
        />
        {mode === 'links' ? (
          <>
            <Select
              label="Link A"
              hideLabel={false}
              value={filters.scalars.cmp_a ?? ''}
              onChange={(v) => {
                set('cmp_a', v);
              }}
              options={linkOptions}
            />
            <Select
              label="Link B"
              hideLabel={false}
              value={filters.scalars.cmp_b ?? ''}
              onChange={(v) => {
                set('cmp_b', v);
              }}
              options={linkOptions}
            />
          </>
        ) : (
          <>
            <Field
              label="Period B from"
              value={filters.scalars.cmp_from ?? ''}
              onChange={(v) => {
                set('cmp_from', v);
              }}
              placeholder="YYYY-MM-DD"
              mono
              maxLength={10}
              hint="Empty: the period before."
            />
            <Field
              label="to"
              value={filters.scalars.cmp_to ?? ''}
              onChange={(v) => {
                set('cmp_to', v);
              }}
              placeholder="YYYY-MM-DD"
              mono
              maxLength={10}
            />
          </>
        )}
      </div>
      {a.params === null || b.params === null ? (
        <EmptyState
          title="Choose two links"
          reason="Pick link A and link B above. The period and filters apply to both."
        />
      ) : (
        <CompareBody
          a={{ ...a, params: a.params }}
          b={{ ...b, params: b.params }}
          byDay={mode === 'periods'}
          zone={zone}
        />
      )}
    </div>
  );
}

interface ReadySide {
  readonly name: string;
  readonly params: URLSearchParams;
}

function CompareBody({
  a,
  b,
  byDay,
  zone,
}: {
  readonly a: ReadySide;
  readonly b: ReadySide;
  readonly byDay: boolean;
  readonly zone: string;
}): React.JSX.Element {
  const sumA = useApi('/api/v1/analytics/summary', a.params, summarySchema);
  const sumB = useApi('/api/v1/analytics/summary', b.params, summarySchema);
  const tsA = useApi(
    '/api/v1/analytics/timeseries',
    withParams(a.params, { bucket: 'day' }),
    timeSeriesSchema,
  );
  const tsB = useApi(
    '/api/v1/analytics/timeseries',
    withParams(b.params, { bucket: 'day' }),
    timeSeriesSchema,
  );
  const chart = useMemo(
    () =>
      tsA.data && tsB.data
        ? compareChart(tsA.data, tsB.data, { a: a.name, b: b.name }, byDay, zone)
        : null,
    [tsA.data, tsB.data, a.name, b.name, byDay, zone],
  );
  return (
    <>
      <Panel
        query={sumA}
        kind="table"
        title="Summary"
        description={`${a.name} against ${b.name}.`}
        isEmpty={() => false}
        empty={null}
        meta={(d) => d.meta}
      >
        {(left) =>
          sumB.data ? (
            <SummaryTable left={left.kpis} right={sumB.data.kpis} a={a.name} b={b.name} />
          ) : (
            <p className="t-meta m-0">Loading {b.name}…</p>
          )
        }
      </Panel>
      <Panel
        query={tsA}
        kind="chart"
        title="Visits over time"
        description={
          byDay
            ? `${a.name} solid, ${b.name} dashed, aligned by day number.`
            : `${a.name} solid, ${b.name} dashed.`
        }
        isEmpty={() => chart === null}
        empty="Loading both sides…"
        meta={(d) => d.meta}
      >
        {() =>
          chart && (
            <EChart
              option={chart.option}
              table={chart.table}
              decals={chart.decals}
              label={`Visits over time, ${a.name} and ${b.name}`}
              height={300}
            />
          )
        }
      </Panel>
      <div className="grid-3">
        <SideList side={a} dimension="admin1" title={`Top states · ${a.name}`} />
        <SideList side={b} dimension="admin1" title={`Top states · ${b.name}`} />
        <SideList side={a} dimension="referrer_host" title={`Referrer sites · ${a.name}`} />
        <SideList side={b} dimension="referrer_host" title={`Referrer sites · ${b.name}`} />
      </div>
    </>
  );
}

function difference(k: Kpi, other: Kpi | undefined, a: string, b: string): string {
  if (k.value === null || other?.value === null || other === undefined) return '—';
  const delta = k.value - other.value;
  if (delta === 0) return 'The same';
  const ahead = delta > 0 ? a : b;
  if (k.unit === 'ratio') return `${ahead} +${(Math.abs(delta) * 100).toFixed(1)} pp`;
  const base = delta > 0 ? other.value : k.value;
  const relative = base > 0 ? ` (+${pct(Math.abs(delta) / base, 0)})` : '';
  return `${ahead} +${count(Math.abs(delta))}${relative}`;
}

function shown(k: Kpi | undefined): string {
  if (k === undefined || k.value === null) return '—';
  return k.unit === 'ratio' ? pct(k.value, 0) : count(k.value);
}

function SummaryTable({
  left,
  right,
  a,
  b,
}: {
  readonly left: readonly Kpi[];
  readonly right: readonly Kpi[];
  readonly a: string;
  readonly b: string;
}): React.JSX.Element {
  const byKey = (list: readonly Kpi[], key: string): Kpi | undefined =>
    list.find((k) => k.key === key);
  return (
    <DataTable
      caption="Summary, side by side"
      rowKey={(r) => r.key}
      rows={KPIS}
      columns={[
        { key: 'figure', header: 'Figure', render: (r) => r.label },
        { key: 'a', header: a, numeric: true, render: (r) => shown(byKey(left, r.key)) },
        { key: 'b', header: b, numeric: true, render: (r) => shown(byKey(right, r.key)) },
        {
          key: 'diff',
          header: 'Difference',
          render: (r) => {
            const k = byKey(left, r.key);
            return k === undefined ? '—' : difference(k, byKey(right, r.key), a, b);
          },
        },
      ]}
    />
  );
}

function SideList({
  side,
  dimension,
  title,
}: {
  readonly side: ReadySide;
  readonly dimension: 'admin1' | 'referrer_host';
  readonly title: string;
}): React.JSX.Element {
  const query = useApi(
    '/api/v1/analytics/breakdown',
    withParams(side.params, { dimension, limit: '5' }),
    breakdownSchema,
  );
  return (
    <Panel
      query={query}
      kind="list"
      title={title}
      isEmpty={(d: Breakdown) => d.total === 0}
      empty="No visits on this side."
      meta={(d) => d.meta}
    >
      {(d) => (
        <RankedList
          caption={title}
          labelHeader={dimension === 'admin1' ? 'State' : 'Referrer site'}
          rows={rankedRows(d)}
        />
      )}
    </Panel>
  );
}
