/**
 * The dashboard's panels, shared by Overview (DESIGN §10.1) and a link's detail page (§10.11):
 * each takes the API parameters it should use, so the link page can pin `link_id` and reuse
 * them unchanged.
 *
 * All from existing endpoints: KPIs with sparklines (F9.AC2, §12 E16), visits over time with
 * the previous period (F9.AC3, E6), a ranked breakdown (F9.AC4, E3), the live feed (E18), the
 * map card (F9.AC5, E17), the hour × weekday heatmap (E19), the calendar (F9.AC6, E21) and the
 * stage funnel (F9.AC8).
 */

import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router';
import { useApi } from '@/api/query';
import {
  breakdownSchema,
  calendarSchema,
  funnelSchema,
  geoSchema,
  summarySchema,
  timeSeriesSchema,
  visitPageSchema,
  type Kpi,
  type Summary,
  type TimeSeries,
} from '@/api/schemas';
import {
  calendarChart,
  funnelChart,
  hourWeekdayChart,
  rankedRows,
  timeSeriesChart,
} from '@/charts';
import { EChart } from '@/components/EChart';
import { Icon } from '@/components/icons';
import { MetaLine, Panel, PanelView } from '@/components/Panel';
import { filterForBreakdown } from '@/components/shell/filterDefs';
import {
  Badge,
  DataTable,
  ErrorNotice,
  Glyph,
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
  type Trend,
} from '@/components/ui';
import { parseFilters, resolveWindow, serializeFilters, withParams } from '@/filters';
import { count, label, pct } from '@/format';
import { deviceGlyph } from '@/glyphs';
import { useFilters } from '@/session';
import { placeLabel, placeOf } from '@/visits';

const VisitMap = lazy(() => import('@/components/map/VisitMap'));

// ---------------------------------------------------------------------------
// KPIs (F9.AC2) with sparklines (E16)
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

const COMPARISON: Readonly<Record<string, string>> = {
  '24h': 'vs previous 24 hours',
  '7d': 'vs previous 7 days',
  '30d': 'vs previous 30 days',
  '90d': 'vs previous 90 days',
  '365d': 'vs previous year',
  custom: 'vs the period before',
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

function trendOf(
  values: readonly (number | null)[],
  bucket: 'day' | 'hour',
  format: (n: number) => string,
): Trend | undefined {
  const known = values.filter((v): v is number => v !== null);
  if (known.length < 2) return undefined;
  const unit = bucket === 'day' ? 'days' : 'hours';
  return {
    values,
    spoken: `Over ${String(values.length)} ${unit}: low ${format(Math.min(...known))}, high ${format(Math.max(...known))}.`,
  };
}

function ratio(part: readonly number[], whole: readonly number[]): (number | null)[] {
  return whole.map((w, i) => (w > 0 ? (part[i] ?? 0) / w : null));
}

/**
 * The three trends (E16). Human share is over every classification whatever the filter, as
 * the KPI is (API §8.1), so its request includes automated traffic and drops a classification
 * filter; consent is over the filtered visits, as its KPI is.
 */
function useTrends(
  params: URLSearchParams,
  bucket: 'day' | 'hour',
): Readonly<Record<string, Trend>> {
  const visits = useApi(
    '/api/v1/analytics/timeseries',
    withParams(params, { bucket, split_by: 'none' }),
    timeSeriesSchema,
  );
  const everyClass = withParams(params, {
    bucket,
    split_by: 'classification',
    include_automated: 'true',
  });
  everyClass.delete('classification');
  const classes = useApi('/api/v1/analytics/timeseries', everyClass, timeSeriesSchema);
  const consented = useApi(
    '/api/v1/analytics/timeseries',
    withParams(params, { bucket, split_by: 'none', metric: 'consented' }),
    timeSeriesSchema,
  );
  return useMemo(() => {
    const out: Record<string, Trend> = {};
    const all = visits.data?.series[0]?.values;
    if (all !== undefined) {
      const t = trendOf(all, bucket, (n) => count(n));
      if (t !== undefined) out['visits'] = t;
      const granted = consented.data?.series[0]?.values;
      if (granted !== undefined) {
        const c = trendOf(ratio(granted, all), bucket, (n) => pct(n, 1));
        if (c !== undefined) out['consent_grant_rate'] = c;
      }
    }
    const series = classes.data?.series;
    if (series !== undefined && series.length > 0) {
      const length = Math.max(...series.map((s) => s.values.length));
      const totals = Array.from({ length }, (_, i) =>
        series.reduce((n, s) => n + (s.values[i] ?? 0), 0),
      );
      const human = series.find((s) => s.key === 'human')?.values ?? totals.map(() => 0);
      const h = trendOf(ratio(human, totals), bucket, (n) => pct(n, 1));
      if (h !== undefined) out['human_share'] = h;
    }
    return out;
  }, [visits.data, classes.data, consented.data, bucket]);
}

function KpiSection({
  data,
  comparison,
  trends,
}: {
  readonly data: Summary;
  readonly comparison: string;
  readonly trends: Readonly<Record<string, Trend>>;
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
              trend={trends[key]}
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
              trend: trends[kpi.key],
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

export function SummarySection({
  params,
}: {
  readonly params: URLSearchParams;
}): React.JSX.Element {
  const query = useApi('/api/v1/analytics/summary', params, summarySchema);
  const { filters } = useFilters();
  const comparison = COMPARISON[filters.range] ?? 'vs the period before';
  const trends = useTrends(params, filters.range === '24h' ? 'hour' : 'day');
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
  return <KpiSection data={query.data} comparison={comparison} trends={trends} />;
}

// ---------------------------------------------------------------------------
// Visits over time (F9.AC3, E6)
// ---------------------------------------------------------------------------

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

export function TimeSeriesPanel({
  params,
  zone,
  splits = true,
}: {
  readonly params: URLSearchParams;
  readonly zone: string;
  /** Offer "Split by"; off on a link's page, where splitting by link means nothing. */
  readonly splits?: boolean;
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
          {splits && (
            <Select
              label="Split by"
              size="sm"
              value={split}
              onChange={setSplit}
              options={SPLITS.map(([value, text]) => ({ value, label: text }))}
            />
          )}
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

// ---------------------------------------------------------------------------
// A ranked breakdown whose rows filter the page (F9.AC4, E3, E4)
// ---------------------------------------------------------------------------

export function BreakdownPanel({
  params,
  dimension,
  title,
  description,
  labelHeader,
  limit = 5,
}: {
  readonly params: URLSearchParams;
  readonly dimension: 'admin1' | 'app_medium' | 'device_class' | 'connection_class';
  readonly title: string;
  readonly description: string;
  readonly labelHeader: string;
  readonly limit?: number;
}): React.JSX.Element {
  const query = useApi(
    '/api/v1/analytics/breakdown',
    withParams(params, { dimension, limit: String(limit) }),
    breakdownSchema,
  );
  const navigate = useNavigate();
  const location = useLocation();
  const [search] = useSearchParams();
  return (
    <Panel
      query={query}
      kind="list"
      title={title}
      description={description}
      isEmpty={(d) => d.total === 0}
      empty="No visits in this period with these filters."
      meta={(d) => d.meta}
    >
      {(data) => (
        <RankedList
          caption={title}
          labelHeader={labelHeader}
          rows={rankedRows(data)}
          onSelect={(key) => {
            const next = filterForBreakdown(dimension, key, parseFilters(search));
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

// ---------------------------------------------------------------------------
// The live feed (E18)
// ---------------------------------------------------------------------------

const POLL_MS = 15_000;

/** The current time, refreshed every `ms`, for "updated 8 s ago". */
function useNow(ms: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => {
      setNow(Date.now());
    }, ms);
    return () => {
      window.clearInterval(id);
    };
  }, [ms]);
  return now;
}

/** The other filters over the last 30 minutes, whatever the period: "who is here now". */
function lastHalfHour(params: URLSearchParams, now: number): URLSearchParams {
  const minute = Math.floor(now / 60_000) * 60_000;
  return withParams(params, {
    from: new Date(minute - 30 * 60_000).toISOString(),
    to: new Date(minute + 2 * 60_000).toISOString(),
    limit: '200',
  });
}

export function LiveFeedPanel({
  params,
  zone,
}: {
  readonly params: URLSearchParams;
  readonly zone: string;
}): React.JSX.Element {
  const now = useNow(5_000);
  const query = useApi('/api/v1/visits', withParams(params, { limit: '6' }), visitPageSchema, {
    refetchInterval: POLL_MS,
  });
  const recent = useApi('/api/v1/visits', lastHalfHour(params, now), visitPageSchema, {
    refetchInterval: POLL_MS,
  });
  const location = useLocation();

  // Rows that arrived with the latest poll carry a "New" badge until the next one.
  const seen = useRef<ReadonlySet<string> | null>(null);
  const [fresh, setFresh] = useState<ReadonlySet<string>>(new Set());
  useEffect(() => {
    const items = query.data?.items;
    if (items === undefined) return;
    const ids = new Set(items.map((v) => v.id));
    const before = seen.current;
    setFresh(before === null ? new Set() : new Set([...ids].filter((id) => !before.has(id))));
    seen.current = ids;
  }, [query.data]);

  const live = Date.parse(params.get('to') ?? '') > now;
  const ago =
    query.dataUpdatedAt > 0 ? Math.max(0, Math.round((now - query.dataUpdatedAt) / 1000)) : null;
  const visitors =
    recent.data === undefined
      ? null
      : new Set(recent.data.items.map((v) => v.visitor_id).filter((id) => id !== null)).size;

  return (
    <Panel
      query={query}
      kind="table"
      title="Live feed"
      description={
        live
          ? 'The newest visits with these filters, checked every 15 seconds.'
          : 'The latest visits in the chosen period. Choose a period that includes today to follow new ones.'
      }
      isEmpty={(d) => d.items.length === 0}
      empty="No visits in this period with these filters."
      actions={
        <>
          <span className="live-status t-meta" aria-live="off">
            {live && <span className="live-dot" aria-hidden="true" />}
            {live ? 'Live' : 'Paused'}
            {ago !== null && ` · updated ${String(ago)} s ago`}
          </span>
          <Link className="link t-secondary" to={{ pathname: '/visits', search: location.search }}>
            All visits
          </Link>
        </>
      }
    >
      {(data) => (
        <>
          {visitors !== null && (
            <p className="live-count m-0">
              <Icon.Live size={14} strokeWidth={1.75} aria-hidden="true" />
              <strong>
                {recent.data?.next_cursor === null ? count(visitors) : `${count(visitors)}+`}
              </strong>{' '}
              {visitors === 1 ? 'visitor' : 'visitors'} in the last 30 minutes
            </p>
          )}
          <DataTable
            caption="Live feed: the newest visits"
            compact
            rowKey={(v) => v.id}
            rows={data.items}
            columns={[
              {
                key: 'when',
                header: 'When',
                render: (v) => (
                  <span className="live-when">
                    <Link className="link" to={`/visits/${v.id}`}>
                      <Timestamp iso={v.occurred_at} zone={zone} />
                    </Link>
                    {fresh.has(v.id) && (
                      <span className="live-new">
                        <Badge tone="info">New</Badge>
                      </span>
                    )}
                  </span>
                ),
              },
              {
                key: 'class',
                header: 'Class',
                render: (v) => <Badge dot={v.classification}>{label(v.classification)}</Badge>,
              },
              {
                key: 'device',
                header: 'Device',
                className: 'live-device',
                render: (v) => (
                  <span className="muted">
                    <Glyph
                      name={v.device.is_inapp_webview ? 'InApp' : deviceGlyph(v.device.class)}
                    />
                    {label(v.device.class)}
                  </span>
                ),
              },
              { key: 'where', header: 'Location', render: (v) => placeLabel(placeOf(v)) },
            ]}
          />
        </>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// The map card (F9.AC5, E17)
// ---------------------------------------------------------------------------

export function MapCardPanel({ params }: { readonly params: URLSearchParams }): React.JSX.Element {
  // Geography's default clustering, so the two share one cached response.
  const query = useApi(
    '/api/v1/analytics/geo',
    withParams(params, { cell_degrees: '0.25' }),
    geoSchema,
  );
  const navigate = useNavigate();
  const location = useLocation();
  const [search] = useSearchParams();
  return (
    <Panel
      query={query}
      kind="map"
      title="Where visits came from"
      description="Best-guess state, shaded by visits. Choose a state to filter the dashboard to it."
      isEmpty={(d) => d.countries.length === 0 && d.abstained === 0}
      empty="No visits in this period with these filters."
      meta={(d) => d.meta}
      actions={
        <Link className="link t-secondary" to={{ pathname: '/geography', search: location.search }}>
          Open the map
        </Link>
      }
    >
      {(data) => (
        <Suspense fallback={<Loading kind="map" label="Loading the map…" />}>
          <VisitMap
            data={data}
            layer="admin1"
            compact
            showPoints={false}
            onArea={(kind, key) => {
              const next = filterForBreakdown(kind, key, parseFilters(search));
              if (next !== null) {
                void navigate({
                  pathname: location.pathname,
                  search: serializeFilters(next).toString(),
                });
              }
            }}
          />
        </Suspense>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// When links are opened: hour × weekday (E19)
// ---------------------------------------------------------------------------

export function HourWeekdayPanel({
  params,
  zone,
}: {
  readonly params: URLSearchParams;
  readonly zone: string;
}): React.JSX.Element {
  const [search] = useSearchParams();
  const hourly = resolveWindow(parseFilters(search), zone).hourly;
  const query = useApi(
    '/api/v1/analytics/timeseries',
    withParams(params, { bucket: 'hour', split_by: 'none' }),
    timeSeriesSchema,
    { enabled: hourly },
  );
  const chart = useMemo(
    () => (query.data ? hourWeekdayChart(query.data, zone) : null),
    [query.data, zone],
  );
  const title = 'When links are opened';
  const description = 'Visits by weekday and hour, in the reporting time zone.';
  if (!hourly) {
    return (
      <PanelView<null>
        state={{ status: 'success', data: null }}
        title={title}
        description={description}
        isEmpty={() => true}
        empty="Hourly figures cover at most 31 days. Choose a shorter period to see the pattern."
      >
        {() => null}
      </PanelView>
    );
  }
  return (
    <Panel
      query={query}
      kind="chart"
      title={title}
      description={description}
      isEmpty={(d: TimeSeries) => d.series.every((s) => s.values.every((v) => v === 0))}
      empty="No visits in this period with these filters."
      meta={(d) => d.meta}
    >
      {() =>
        chart && (
          <EChart
            option={chart.option}
            table={chart.table}
            decals={chart.decals}
            label="Visits by weekday and hour"
            height={260}
          />
        )
      }
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Daily volume (F9.AC6, E21) and the stage funnel (F9.AC8)
// ---------------------------------------------------------------------------

export function CalendarPanel({
  params,
  zone,
}: {
  readonly params: URLSearchParams;
  readonly zone: string;
}): React.JSX.Element {
  const [search] = useSearchParams();
  // The heatmap is about weekly and campaign patterns, so it always shows a year,
  // with every filter except the period applied.
  const yearFilters = { ...parseFilters(search), range: '365d' as const, from: null, to: null };
  const window = resolveWindow(yearFilters, zone);
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

export function FunnelPanel({ params }: { readonly params: URLSearchParams }): React.JSX.Element {
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
