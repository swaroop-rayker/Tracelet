/**
 * Returning visitors (F9.AC22, DESIGN §12 E31, §16 M7.6): who came back to a link.
 *
 * Per link and visitor id: a visit is *new* if it is the visitor's first on that link among
 * the visits kept, otherwise *returning*. Read from raw rows only, so it can know nothing
 * before the oldest visit kept, and says so in its description (`since`, UI-8). Visits with
 * no visitor id cannot be followed; they are counted apart, never guessed.
 *
 * The cohort grid's tint is a custom property (`--share`, DESIGN §4.9), and every cell prints
 * its number, so colour is never the only cue (NFR7.AC3).
 */

import { useMemo } from 'react';
import { useApi } from '@/api/query';
import { returningSchema, type Returning } from '@/api/schemas';
import { returnBandRows, returningChart } from '@/charts';
import { EChart } from '@/components/EChart';
import { Panel } from '@/components/Panel';
import { PageHeader } from '@/components/shell/PageHeader';
import { DataTable, RankedList, cssVars } from '@/components/ui';
import { count, pct } from '@/format';
import { withParams } from '@/filters';
import { useFilters } from '@/session';

const WEEKS = 8;

function since(data: Returning): string {
  const known =
    data.since === null
      ? 'Nothing is kept yet.'
      : `Known from ${longDay(data.since)}, the oldest visit kept.`;
  const unidentified =
    data.unidentified > 0
      ? ` ${count(data.unidentified)} visit${data.unidentified === 1 ? '' : 's'} without a visitor id ${data.unidentified === 1 ? 'is' : 'are'} not counted.`
      : '';
  return known + unidentified;
}

function longDay(day: string): string {
  const [y = 0, m = 1, d = 1] = day.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString('en-IN', {
    timeZone: 'UTC',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
}

/** "31 Aug": the cohort grid's rows, which the page's period already dates. */
function shortDay(day: string): string {
  const [y = 0, m = 1, d = 1] = day.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString('en-IN', {
    timeZone: 'UTC',
    day: 'numeric',
    month: 'short',
  });
}

export default function ReturningPage(): React.JSX.Element {
  const { params } = useFilters();
  const query = useApi(
    '/api/v1/analytics/returning',
    withParams(params, { weeks: String(WEEKS) }),
    returningSchema,
  );
  const chart = useMemo(() => (query.data ? returningChart(query.data) : null), [query.data]);
  return (
    <div className="page">
      <PageHeader
        title="Returning visitors"
        description={
          query.data
            ? `Who came back to a link. ${since(query.data)}`
            : 'Who came back to a link, among the visits kept.'
        }
        filters
      />
      <Panel
        query={query}
        kind="chart"
        title="New and returning, per day"
        description="Visitors per link: new on their first visit, returning after."
        isEmpty={(d) => d.days.every((x) => x.new + x.returning === 0)}
        empty="No identified visitors in this period with these filters."
        meta={(d) => d.meta}
      >
        {() =>
          chart && (
            <EChart
              option={chart.option}
              table={chart.table}
              decals={chart.decals}
              label="New and returning visitors per day"
              height={300}
            />
          )
        }
      </Panel>
      <div className="grid-3">
        <Panel
          query={query}
          kind="table"
          title="Weekly cohorts"
          description="Visitors by the week of their first visit, and the share who came back in each week after (+0: later the same week)."
          isEmpty={(d) => d.cohorts.length === 0}
          empty="No first visits in this period with these filters."
          meta={(d) => d.meta}
        >
          {(d) => <CohortGrid data={d} />}
        </Panel>
        <Panel
          query={query}
          kind="list"
          title="Time to come back"
          description="From a visitor's first visit to a link to their second."
          isEmpty={(d) => d.return_after.every((b) => b.count === 0)}
          empty="Nobody whose first visit is in this period has come back yet."
          meta={(d) => d.meta}
        >
          {(d) => (
            <RankedList
              caption="Visitors by time to their second visit"
              labelHeader="Came back after"
              valueHeader="People"
              rows={returnBandRows(d)}
            />
          )}
        </Panel>
      </div>
    </div>
  );
}

function CohortGrid({ data }: { readonly data: Returning }): React.JSX.Element {
  const weeks = data.cohorts[0]?.returned.length ?? 0;
  return (
    <DataTable
      caption="Weekly cohorts"
      compact
      rowKey={(c) => c.week}
      rows={data.cohorts}
      columns={[
        { key: 'week', header: 'First week', render: (c) => shortDay(c.week) },
        { key: 'size', header: 'People', numeric: true, render: (c) => count(c.size) },
        ...Array.from({ length: weeks }, (_, k) => ({
          key: `w${String(k)}`,
          header: `+${String(k)}`,
          numeric: true,
          render: (c: Returning['cohorts'][number]) => {
            const n = c.returned[k];
            if (n === null || n === undefined) return <span className="muted">—</span>;
            const share = c.size > 0 ? n / c.size : 0;
            return (
              <span
                className="cohort-cell"
                style={cssVars({ '--share': share.toFixed(3) })}
                title={`${count(n)} of ${count(c.size)}`}
              >
                {pct(share, 0)}
              </span>
            );
          },
        })),
      ]}
    />
  );
}
