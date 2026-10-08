/**
 * System health › Capture quality (F9.AC24, DESIGN §12 E33, §16 M7.6).
 *
 * The funnel per app medium: for each in-app browser, and for browsers, how many visits were
 * captured and how many the page then enriched, finalised server-only, or got consent on --
 * the in-app-browser loss of RISKS R5, measured rather than assumed. System health has no
 * filter bar, so this is the default analytics window: the last 30 days, people only.
 */

import { useMemo } from 'react';
import { useApi } from '@/api/query';
import { captureQualitySchema, type CaptureQuality } from '@/api/schemas';
import { enrichedShareChart } from '@/charts';
import { EChart } from '@/components/EChart';
import { Panel } from '@/components/Panel';
import { DataTable } from '@/components/ui';
import { count, label, pct } from '@/format';
import { useSession } from '@/session';

const PATH = '/api/v1/analytics/capture-quality';

function appName(key: string): string {
  return key === 'browser' ? 'Browsers' : label(key);
}

function ofCaptured(n: number, captured: number): string {
  return captured > 0 ? `${count(n)} (${pct(n / captured, 0)})` : count(n);
}

export default function CapturePage(): React.JSX.Element {
  const { me } = useSession();
  const query = useApi(PATH, null, captureQualitySchema);
  const chart = useMemo(
    () => (query.data ? enrichedShareChart(query.data, me.reporting_tz) : null),
    [query.data, me.reporting_tz],
  );
  return (
    <>
      <Panel
        query={query}
        kind="table"
        title="By app"
        description="The last 30 days, people only. Enriched: the page ran and reported back. Server-only: it never did, so only what the server saw is known."
        isEmpty={(d) => d.apps.length === 0}
        empty="No visits in the last 30 days."
        meta={(d) => d.meta}
      >
        {(d) => <AppTable data={d} />}
      </Panel>
      <Panel
        query={query}
        kind="chart"
        title="Enriched share per day"
        description="For the five busiest apps. A gap is a day with no visit from that app."
        isEmpty={(d) => d.series.length === 0}
        empty="No visits in the last 30 days."
        meta={(d) => d.meta}
      >
        {() =>
          chart && (
            <EChart
              option={chart.option}
              table={chart.table}
              decals={chart.decals}
              label="Enriched share per day, by app"
              height={280}
            />
          )
        }
      </Panel>
    </>
  );
}

function AppTable({ data }: { readonly data: CaptureQuality }): React.JSX.Element {
  return (
    <DataTable
      caption="Capture stages by app"
      rowKey={(a) => a.key}
      rows={data.apps}
      columns={[
        { key: 'app', header: 'App', render: (a) => appName(a.key) },
        { key: 'captured', header: 'Captured', numeric: true, render: (a) => count(a.captured) },
        {
          key: 'enriched',
          header: 'Enriched',
          numeric: true,
          render: (a) => ofCaptured(a.enriched, a.captured),
        },
        {
          key: 'server_only',
          header: 'Server only',
          numeric: true,
          render: (a) => ofCaptured(a.server_only, a.captured),
        },
        { key: 'pending', header: 'Pending', numeric: true, render: (a) => count(a.pending) },
        {
          key: 'consented',
          header: 'Consented',
          numeric: true,
          render: (a) => ofCaptured(a.consented, a.captured),
        },
      ]}
    />
  );
}
