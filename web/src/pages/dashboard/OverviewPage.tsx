/**
 * Overview: KPIs (F9.AC2), visits over time (F9.AC3), the calendar heatmap (F9.AC6) and
 * the stage funnel (F9.AC8).
 */

import { useMemo, useState } from 'react';
import { useApi } from '@/api/query';
import {
  calendarSchema,
  funnelSchema,
  summarySchema,
  timeSeriesSchema,
  type Kpi,
} from '@/api/schemas';
import { calendarChart, funnelChart, timeSeriesChart } from '@/charts';
import { EChart } from '@/components/EChart';
import { Panel } from '@/components/Panel';
import { parseFilters, resolveWindow, withParams } from '@/filters';
import { count, pct } from '@/format';
import { useFilters } from '@/session';
import { useSearchParams } from 'react-router';
import { PageHeader } from '@/components/shell/PageHeader';

const KPI_LABEL: Readonly<Record<string, string>> = {
  visits: 'Visits',
  unique_visitors: 'Unique visitors',
  human_share: 'Human share',
  bot_share: 'Automated share',
  consent_grant_rate: 'Location consent rate',
  geofence_hit_rate: 'Geofence hit rate',
  enrichment_completion_rate: 'Enrichment completed',
};

const REASON: Readonly<Record<string, string>> = {
  no_geofence_evaluations: 'No geofences evaluated yet (geofencing arrives in M6).',
  past_visit_retention: 'Not countable past the visit retention window.',
};

const SPLITS = [
  ['none', 'Total'],
  ['classification', 'Classification'],
  ['device_class', 'Device class'],
  ['connection_class', 'Connection'],
  ['country', 'Country'],
  ['admin1', 'State'],
  ['link', 'Link'],
] as const;

export default function OverviewPage(): React.JSX.Element {
  const { params, zone } = useFilters();
  return (
    <div className="page">
      <PageHeader
        title="Overview"
        description="Visits to your links: how many, who they were, and how complete the picture is."
        filters
      />
      <SummaryPanel params={params} />
      <TimeSeriesPanel params={params} zone={zone} />
      <div className="grid-2">
        <CalendarPanel zone={zone} />
        <FunnelPanel params={params} />
      </div>
    </div>
  );
}

function SummaryPanel({ params }: { readonly params: URLSearchParams }): React.JSX.Element {
  const query = useApi('/api/v1/analytics/summary', params, summarySchema);
  return (
    <Panel
      query={query}
      title="Summary"
      description="Against the equal-length period immediately before."
      isEmpty={() => false}
      empty={null}
      meta={(d) => d.meta}
    >
      {(data) => (
        <ul className="kpis plain">
          {data.kpis.map((kpi) => (
            <KpiTile key={kpi.key} kpi={kpi} />
          ))}
        </ul>
      )}
    </Panel>
  );
}

function KpiTile({ kpi }: { readonly kpi: Kpi }): React.JSX.Element {
  const value = kpi.unit === 'ratio' ? pct(kpi.value, 1) : count(kpi.value);
  let change = '';
  let direction = 'flat';
  if (kpi.change !== null) {
    direction = kpi.change > 0 ? 'up' : kpi.change < 0 ? 'down' : 'flat';
    const arrow = direction === 'up' ? '▲' : direction === 'down' ? '▼' : '■';
    const amount =
      kpi.unit === 'ratio'
        ? `${(Math.abs(kpi.change) * 100).toFixed(1)} pts`
        : `${(Math.abs(kpi.change) * 100).toFixed(0)}%`;
    change = `${arrow} ${amount}`;
  }
  return (
    <li className="kpi">
      <span className="kpi-label">{KPI_LABEL[kpi.key] ?? kpi.key}</span>
      <span className="kpi-value">{value}</span>
      {kpi.reason !== null ? (
        <span className="muted small">{REASON[kpi.reason] ?? kpi.reason}</span>
      ) : (
        <span className={`kpi-change ${direction}`}>
          {change === '' ? 'No previous data' : change}
          <span className="sr-only">
            {' '}
            versus the previous period (
            {kpi.unit === 'ratio' ? pct(kpi.previous, 1) : count(kpi.previous)})
          </span>
        </span>
      )}
    </li>
  );
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
  const effective = hourlyAllowed ? bucket : 'day';
  const query = useApi(
    '/api/v1/analytics/timeseries',
    withParams(params, { bucket: effective, split_by: split }),
    timeSeriesSchema,
  );
  const chart = useMemo(
    () => (query.data ? timeSeriesChart(query.data, zone) : null),
    [query.data, zone],
  );
  return (
    <Panel
      query={query}
      title="Visits over time"
      description="Automated traffic follows the filter above. Buckets are local to the reporting timezone."
      isEmpty={(d) => d.series.every((s) => s.values.every((v) => v === 0))}
      empty="No visits in this period with these filters."
      meta={(d) => d.meta}
      actions={
        <div className="panel-actions">
          <label className="control">
            <span>Bucket</span>
            <select
              value={effective}
              onChange={(event) => {
                setBucket(event.target.value === 'hour' ? 'hour' : 'day');
              }}
            >
              <option value="day">Day</option>
              <option value="hour" disabled={!hourlyAllowed}>
                Hour{hourlyAllowed ? '' : ' (31 days or less)'}
              </option>
            </select>
          </label>
          <label className="control">
            <span>Split by</span>
            <select
              value={split}
              onChange={(event) => {
                setSplit(event.target.value);
              }}
            >
              {SPLITS.map(([value, text]) => (
                <option key={value} value={value}>
                  {text}
                </option>
              ))}
            </select>
          </label>
        </div>
      }
    >
      {() =>
        chart && (
          <EChart option={chart.option} table={chart.table} label="Visits over time" height={300} />
        )
      }
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
      title="Stage funnel"
      description="Where visits are lost: in-app webviews and content blockers show up between captured and enriched."
      isEmpty={(d) => (d.steps[0]?.count ?? 0) === 0}
      empty="No requests in this period with these filters."
      meta={(d) => d.meta}
    >
      {(data) => (
        <>
          {chart && (
            <EChart option={chart.option} table={chart.table} label="Stage funnel" height={220} />
          )}
          {data.steps
            .filter((s) => s.count === null)
            .map((s) => (
              <p key={s.step} className="muted small">
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
