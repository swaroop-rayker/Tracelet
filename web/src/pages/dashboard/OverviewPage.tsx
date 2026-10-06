/**
 * Overview (DESIGN §10.1): KPIs (F9.AC2), visits over time with the previous period (F9.AC3,
 * E6), the calendar heatmap (F9.AC6), the stage funnel (F9.AC8), and the top states and recent
 * visits (E4) -- all from existing endpoints.
 *
 * The seven KPIs: four primary cards and a strip for the other three, so all seven stay
 * visible without a ragged second row (audit A2, A3).
 */

import { useMemo, useState } from 'react';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router';
import { useApi } from '@/api/query';
import {
  breakdownSchema,
  calendarSchema,
  funnelSchema,
  summarySchema,
  timeSeriesSchema,
  visitPageSchema,
  type Kpi,
  type Summary,
} from '@/api/schemas';
import { calendarChart, funnelChart, rankedRows, timeSeriesChart } from '@/charts';
import { EChart } from '@/components/EChart';
import { MetaLine, Panel } from '@/components/Panel';
import { Freshness } from '@/components/shell/Freshness';
import { PageHeader } from '@/components/shell/PageHeader';
import { filterForBreakdown } from '@/components/shell/filterDefs';
import {
  Badge,
  DataTable,
  ErrorNotice,
  Loading,
  RankedList,
  SegmentedControl,
  Select,
  Stat,
  StatStrip,
  Stats,
  Switch,
  Timestamp,
  type Delta,
} from '@/components/ui';
import { parseFilters, resolveWindow, serializeFilters, withParams } from '@/filters';
import { label, pct } from '@/format';
import { useFilters } from '@/session';
import { placeLabel, placeOf } from '@/visits';

// ---------------------------------------------------------------------------
// KPIs
// ---------------------------------------------------------------------------

interface KpiSpec {
  readonly label: string;
  /** Which direction is better (DESIGN §5.4): judged, not merely pointed at. */
  readonly better: 'up' | 'down' | 'neutral';
  readonly hint: string;
}

const KPI: Readonly<Record<string, KpiSpec>> = {
  visits: {
    label: 'Visits',
    better: 'neutral',
    hint: 'Requests to your tracking links in the period, with the filters applied.',
  },
  unique_visitors: {
    label: 'Unique visitors',
    better: 'neutral',
    hint: 'Distinct visitors by browser fingerprint. An estimate: a new device is a new visitor.',
  },
  human_share: {
    label: 'Human share',
    better: 'up',
    hint: 'The share of visits classified as human.',
  },
  bot_share: {
    label: 'Automated share',
    better: 'down',
    hint: 'The share classified as bots, crawlers, datacenter clients, spam or spoofed.',
  },
  consent_grant_rate: {
    label: 'Location consent',
    better: 'up',
    hint: 'The share of visits whose browser had already granted location.',
  },
  geofence_hit_rate: {
    label: 'Geofence hit rate',
    better: 'neutral',
    hint: 'The share of located visits inside a geofence.',
  },
  enrichment_completion_rate: {
    label: 'Enrichment completed',
    better: 'up',
    hint: 'The share of requests the visitor’s browser enriched after the server captured them.',
  },
};

const PRIMARY = ['visits', 'unique_visitors', 'human_share', 'enrichment_completion_rate'];

const REASON: Readonly<Record<string, string>> = {
  no_geofence_evaluations: 'Geofencing arrives in M6',
  past_visit_retention: 'Past visit retention',
};

function compact(value: number): string {
  return value >= 10_000 ? `${(value / 1000).toFixed(1)}K` : value.toLocaleString();
}

function display(kpi: Kpi): { readonly value: string; readonly exact?: string } {
  if (kpi.value === null) return { value: '—' };
  if (kpi.unit === 'ratio') return { value: pct(kpi.value, 1) };
  const shown = compact(kpi.value);
  return shown === kpi.value.toLocaleString()
    ? { value: shown }
    : { value: shown, exact: kpi.value.toLocaleString() };
}

function delta(kpi: Kpi, spec: KpiSpec): Delta | undefined {
  if (kpi.change === null) return undefined;
  const direction = kpi.change > 0 ? 'up' : kpi.change < 0 ? 'down' : 'flat';
  const judgement =
    spec.better === 'neutral' || direction === 'flat'
      ? 'neutral'
      : (direction === 'up') === (spec.better === 'up')
        ? 'good'
        : 'bad';
  const text =
    kpi.unit === 'ratio'
      ? `${(Math.abs(kpi.change) * 100).toFixed(1)} pts`
      : `${(Math.abs(kpi.change) * 100).toFixed(0)}%`;
  return { direction, text, judgement };
}

function note(kpi: Kpi): string {
  if (kpi.reason !== null) return REASON[kpi.reason] ?? label(kpi.reason);
  return 'No earlier data to compare';
}

function KpiSection({
  data,
  comparison,
}: {
  readonly data: Summary;
  readonly comparison: string;
}): React.JSX.Element {
  const byKey = new Map(data.kpis.map((k) => [k.key, k]));
  const secondary = data.kpis.filter((k) => !PRIMARY.includes(k.key));
  return (
    <section className="kpi-section" aria-label="Key figures">
      <Stats label="Key figures">
        {PRIMARY.map((key) => {
          const kpi = byKey.get(key);
          const spec = KPI[key];
          if (kpi === undefined || spec === undefined) return null;
          const shown = display(kpi);
          const change = delta(kpi, spec);
          return (
            <Stat
              key={key}
              label={spec.label}
              value={shown.value}
              exact={shown.exact}
              hint={spec.hint}
              delta={change}
              comparison={change === undefined ? undefined : comparison}
              note={change === undefined ? note(kpi) : undefined}
            />
          );
        })}
      </Stats>
      <div className="kpi-section__foot">
        <StatStrip
          label="More figures"
          items={secondary.map((kpi) => {
            const spec = KPI[kpi.key];
            const change = spec === undefined ? undefined : delta(kpi, spec);
            return {
              key: kpi.key,
              label: spec?.label ?? kpi.key,
              value: display(kpi).value,
              hint:
                kpi.reason !== null
                  ? note(kpi)
                  : change === undefined
                    ? undefined
                    : `${change.direction === 'up' ? '↑' : change.direction === 'down' ? '↓' : '→'} ${change.text}`,
            };
          })}
        />
        <MetaLine meta={data.meta} />
      </div>
    </section>
  );
}

const COMPARISON: Readonly<Record<string, string>> = {
  '24h': 'vs previous 24 hours',
  '7d': 'vs previous 7 days',
  '30d': 'vs previous 30 days',
  '90d': 'vs previous 90 days',
  '365d': 'vs previous year',
  custom: 'vs the period before',
};

function SummarySection({ params }: { readonly params: URLSearchParams }): React.JSX.Element {
  const query = useApi('/api/v1/analytics/summary', params, summarySchema);
  const { filters } = useFilters();
  const comparison = COMPARISON[filters.range] ?? 'vs the period before';
  if (query.isPending) {
    return (
      <div className="stats-loading" aria-busy="true">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="stat">
            <Loading kind="kpi" label={i === 0 ? 'Loading key figures…' : ''} />
          </div>
        ))}
      </div>
    );
  }
  if (query.isError) return <ErrorNotice error={query.error.error} />;
  return <KpiSection data={query.data} comparison={comparison} />;
}

// ---------------------------------------------------------------------------
// The page
// ---------------------------------------------------------------------------

export default function OverviewPage(): React.JSX.Element {
  const { params, zone } = useFilters();
  const summary = useApi('/api/v1/analytics/summary', params, summarySchema);
  return (
    <div className="page">
      <PageHeader
        title="Overview"
        description="Visits to your links: how many, who they were, and how complete the picture is."
        filters
        status={<Freshness iso={summary.data?.meta.refreshed_at} zone={zone} />}
      />
      <SummarySection params={params} />
      <TimeSeriesPanel params={params} zone={zone} />
      <div className="grid-2">
        <TopStatesPanel params={params} />
        <RecentVisitsPanel params={params} zone={zone} />
      </div>
      <div className="grid-2">
        <CalendarPanel zone={zone} />
        <FunnelPanel params={params} />
      </div>
    </div>
  );
}

const SPLITS = [
  ['none', 'No split'],
  ['classification', 'Classification'],
  ['device_class', 'Device class'],
  ['connection_class', 'Connection'],
  ['country', 'Country'],
  ['admin1', 'State'],
  ['link', 'Link'],
] as const;

/** The same filters over the equal-length window just before (DESIGN §12 E6). */
function previousWindow(params: URLSearchParams): URLSearchParams | null {
  const from = Date.parse(params.get('from') ?? '');
  const to = Date.parse(params.get('to') ?? '');
  if (Number.isNaN(from) || Number.isNaN(to) || to <= from) return null;
  return withParams(params, {
    from: new Date(from - (to - from)).toISOString(),
    to: new Date(from).toISOString(),
  });
}

function TimeSeriesPanel({
  params,
  zone,
}: {
  readonly params: URLSearchParams;
  readonly zone: string;
}): React.JSX.Element {
  const [search] = useSearchParams();
  const hourlyAllowed = resolveWindow(parseFilters(search), zone).hourly;
  const [bucket, setBucket] = useState<'day' | 'hour'>('day');
  const [split, setSplit] = useState<string>('none');
  const [compare, setCompare] = useState(true);
  const effective = hourlyAllowed ? bucket : 'day';
  const own = withParams(params, { bucket: effective, split_by: split });
  const query = useApi('/api/v1/analytics/timeseries', own, timeSeriesSchema);
  const priorParams = previousWindow(own);
  const comparing = compare && split === 'none' && priorParams !== null;
  const prior = useApi('/api/v1/analytics/timeseries', priorParams, timeSeriesSchema, {
    enabled: comparing,
  });
  const chart = useMemo(
    () =>
      query.data ? timeSeriesChart(query.data, zone, comparing ? prior.data : undefined) : null,
    [query.data, prior.data, comparing, zone],
  );
  return (
    <Panel
      query={query}
      kind="chart"
      title="Visits over time"
      description="Buckets are local to the reporting timezone; automated traffic follows the filters."
      isEmpty={(d) => d.series.every((s) => s.values.every((v) => v === 0))}
      empty="No visits in this period with these filters."
      meta={(d) => d.meta}
      actions={
        <>
          {split === 'none' && (
            <Switch label="Previous period" checked={compare} onChange={setCompare} />
          )}
          <SegmentedControl
            label="Bucket"
            value={effective}
            onChange={(v) => {
              setBucket(v === 'hour' ? 'hour' : 'day');
            }}
            options={[
              { value: 'day', label: 'Day' },
              { value: 'hour', label: 'Hour', disabled: !hourlyAllowed },
            ]}
          />
          <Select
            label="Split by"
            size="sm"
            value={split}
            onChange={setSplit}
            options={SPLITS.map(([value, text]) => ({ value, label: text }))}
          />
        </>
      }
    >
      {() =>
        chart && (
          <EChart
            option={chart.option}
            table={chart.table}
            decals={chart.decals}
            label="Visits over time"
            height={320}
          />
        )
      }
    </Panel>
  );
}

/** The five states with the most visits; a row filters the dashboard to it (E3, E4). */
function TopStatesPanel({ params }: { readonly params: URLSearchParams }): React.JSX.Element {
  const query = useApi(
    '/api/v1/analytics/breakdown',
    withParams(params, { dimension: 'admin1', limit: '5' }),
    breakdownSchema,
  );
  const navigate = useNavigate();
  const location = useLocation();
  const [search] = useSearchParams();
  return (
    <Panel
      query={query}
      kind="list"
      title="Top states"
      description="Best-guess location. Choose one to filter the dashboard to it."
      isEmpty={(d) => d.total === 0}
      empty="No visits in this period with these filters."
      meta={(d) => d.meta}
    >
      {(data) => (
        <RankedList
          caption="Visits by state"
          labelHeader="State"
          rows={rankedRows(data)}
          onSelect={(key) => {
            const next = filterForBreakdown('admin1', key, parseFilters(search));
            if (next !== null) {
              void navigate({
                pathname: location.pathname,
                search: serializeFilters(next).toString(),
              });
            }
          }}
        />
      )}
    </Panel>
  );
}

/** The latest visits, each linking to its full derivation (E4). */
function RecentVisitsPanel({
  params,
  zone,
}: {
  readonly params: URLSearchParams;
  readonly zone: string;
}): React.JSX.Element {
  const query = useApi('/api/v1/visits', withParams(params, { limit: '6' }), visitPageSchema);
  const location = useLocation();
  return (
    <Panel
      query={query}
      kind="table"
      title="Recent visits"
      description="The latest six with these filters."
      isEmpty={(d) => d.items.length === 0}
      empty="No visits in this period with these filters."
      actions={
        <Link className="link t-secondary" to={{ pathname: '/visits', search: location.search }}>
          All visits
        </Link>
      }
    >
      {(data) => (
        <DataTable
          caption="Recent visits"
          compact
          rowKey={(v) => v.id}
          rows={data.items}
          columns={[
            {
              key: 'when',
              header: 'When',
              render: (v) => (
                <Link className="link" to={`/visits/${v.id}`}>
                  <Timestamp iso={v.occurred_at} zone={zone} />
                </Link>
              ),
            },
            {
              key: 'class',
              header: 'Class',
              render: (v) => <Badge dot={v.classification}>{label(v.classification)}</Badge>,
            },
            { key: 'where', header: 'Location', render: (v) => placeLabel(placeOf(v)) },
          ]}
        />
      )}
    </Panel>
  );
}

function CalendarPanel({ zone }: { readonly zone: string }): React.JSX.Element {
  const [search] = useSearchParams();
  // The heatmap is about weekly and campaign patterns, so it always shows a year,
  // with every filter except the period applied.
  const yearFilters = { ...parseFilters(search), range: '365d' as const, from: null, to: null };
  const window = resolveWindow(yearFilters, zone);
  const { params } = useFilters();
  const query = useApi(
    '/api/v1/analytics/calendar',
    withParams(params, { from: window.from, to: window.to }),
    calendarSchema,
  );
  const chart = useMemo(() => (query.data ? calendarChart(query.data) : null), [query.data]);
  return (
    <Panel
      query={query}
      kind="chart"
      title="Daily volume"
      description="The last 365 days, with the other filters applied."
      isEmpty={(d) => d.days.every((x) => x.count === 0)}
      empty="No visits in the last year with these filters."
      meta={(d) => d.meta}
    >
      {() =>
        chart && (
          <EChart
            option={chart.option}
            table={chart.table}
            decals={chart.decals}
            label="Daily visits, last 365 days"
            height={200}
          />
        )
      }
    </Panel>
  );
}

function FunnelPanel({ params }: { readonly params: URLSearchParams }): React.JSX.Element {
  const query = useApi('/api/v1/analytics/funnel', params, funnelSchema);
  const chart = useMemo(() => (query.data ? funnelChart(query.data) : null), [query.data]);
  return (
    <Panel
      query={query}
      kind="chart"
      title="Stage funnel"
      description="Where visits are lost: in-app browsers and content blockers show between captured and enriched."
      isEmpty={(d) => (d.steps[0]?.count ?? 0) === 0}
      empty="No requests in this period with these filters."
      meta={(d) => d.meta}
    >
      {(data) => (
        <>
          {chart && (
            <EChart
              option={chart.option}
              table={chart.table}
              decals={chart.decals}
              label="Stage funnel"
              height={200}
            />
          )}
          {data.steps
            .filter((s) => s.count === null)
            .map((s) => (
              <p key={s.step} className="t-meta m-0">
                {s.step === 'notified' ? 'Notified' : s.step}: not measured yet
                {s.reason === 'notifications_not_built'
                  ? ' — Telegram notifications arrive in M6.'
                  : '.'}
              </p>
            ))}
        </>
      )}
    </Panel>
  );
}
