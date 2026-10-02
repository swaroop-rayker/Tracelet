/** Breakdowns (F9.AC4): one panel per dimension, all under the same filters. */

import { useMemo } from 'react';
import { useApi } from '@/api/query';
import { breakdownDimensions, breakdownSchema, type BreakdownDimension } from '@/api/schemas';
import { breakdownChart } from '@/charts';
import { EChart } from '@/components/EChart';
import { Panel } from '@/components/Panel';
import { withParams } from '@/filters';
import { DIMENSION_LABEL } from '@/format';
import { useFilters } from '@/session';

const LOCATION = new Set<string>(['country', 'admin1', 'city']);

export default function BreakdownsPage(): React.JSX.Element {
  const { params } = useFilters();
  return (
    <div className="page">
      <h2 className="page-title">Breakdowns</h2>
      <p className="muted small">
        Location breakdowns count each visit at its best-guess location: the highest-confidence
        place the engine found. A visit no source could place at a level is counted as “Unknown”.
      </p>
      <div className="grid-2">
        {breakdownDimensions.map((dimension) => (
          <BreakdownPanel key={dimension} dimension={dimension} params={params} />
        ))}
      </div>
    </div>
  );
}

export function BreakdownPanel({
  dimension,
  params,
  limit = 10,
}: {
  readonly dimension: BreakdownDimension;
  readonly params: URLSearchParams;
  readonly limit?: number;
}): React.JSX.Element {
  const query = useApi(
    '/api/v1/analytics/breakdown',
    withParams(params, { dimension, limit: String(limit) }),
    breakdownSchema,
  );
  const chart = useMemo(() => (query.data ? breakdownChart(query.data) : null), [query.data]);
  const title = DIMENSION_LABEL[dimension] ?? dimension;
  return (
    <Panel
      query={query}
      title={title}
      isEmpty={(d) => d.total === 0}
      empty="No visits in this period with these filters."
      meta={(d) => d.meta}
    >
      {(data) => (
        <>
          {LOCATION.has(dimension) && data.rows.length === 0 ? (
            <p className="muted small">
              No source could place any visit at this level ({data.unknown.toLocaleString()}).
            </p>
          ) : null}
          {chart && (
            <EChart
              option={chart.option}
              table={chart.table}
              label={`Visits by ${title.toLowerCase()}`}
              height={Math.max(140, 28 * chart.table.rows.length + 24)}
            />
          )}
        </>
      )}
    </Panel>
  );
}
