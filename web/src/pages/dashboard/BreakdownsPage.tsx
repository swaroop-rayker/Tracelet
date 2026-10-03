/**
 * Breakdowns (F9.AC4, DESIGN §10.5): one ranked list per dimension, all under the same filters.
 *
 * Ranked lists, not bar charts (ADR-0019): each is a semantic table with inline bars, so it
 * needs no separate data table to be accessible. Location dimensions count the best-guess
 * location (ADR-0018); a row of a filterable dimension applies that filter (E3).
 */

import { useLocation, useNavigate, useSearchParams } from 'react-router';
import { useApi } from '@/api/query';
import { breakdownDimensions, breakdownSchema, type BreakdownDimension } from '@/api/schemas';
import { rankedRows } from '@/charts';
import { Panel } from '@/components/Panel';
import { PageHeader } from '@/components/shell/PageHeader';
import { filterForBreakdown, isFilterableDimension } from '@/components/shell/filterDefs';
import { RankedList } from '@/components/ui';
import { parseFilters, serializeFilters, withParams } from '@/filters';
import { DIMENSION_LABEL } from '@/format';
import { useFilters } from '@/session';

const LOCATION = new Set<string>(['country', 'admin1', 'city']);

export default function BreakdownsPage(): React.JSX.Element {
  const { params } = useFilters();
  return (
    <div className="page">
      <PageHeader
        title="Breakdowns"
        description="Each visit counted at its best-guess location: the highest-confidence place the engine found. A visit no source could place at a level is Unknown."
        filters
      />
      <div className="grid-3">
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
  const navigate = useNavigate();
  const location = useLocation();
  const [search] = useSearchParams();
  const title = DIMENSION_LABEL[dimension] ?? dimension;
  const filterable = isFilterableDimension(dimension);
  return (
    <Panel
      query={query}
      kind="list"
      title={title}
      isEmpty={(d) => d.total === 0}
      empty="No visits in this period with these filters."
      meta={(d) => d.meta}
    >
      {(data) => (
        <>
          {LOCATION.has(dimension) && data.rows.length === 0 && (
            <p className="t-meta m-0">
              No source could place any visit at this level ({data.unknown.toLocaleString()}).
            </p>
          )}
          <RankedList
            caption={`Visits by ${title.toLowerCase()}`}
            labelHeader={title}
            rows={rankedRows(data)}
            onSelect={
              filterable
                ? (key) => {
                    const next = filterForBreakdown(dimension, key, parseFilters(search));
                    if (next !== null) {
                      void navigate({
                        pathname: location.pathname,
                        search: serializeFilters(next).toString(),
                      });
                    }
                  }
                : undefined
            }
          />
        </>
      )}
    </Panel>
  );
}
