/**
 * Detection: which rules fire most often (F9.AC11), and the classification mix.
 *
 * Automated traffic is what this page is about, so it is shown whatever the filter bar
 * says -- the toggle is overridden here, and the page says so.
 */

import { useMemo } from 'react';
import { useApi } from '@/api/query';
import { signalsSchema } from '@/api/schemas';
import { signalsChart } from '@/charts';
import { EChart } from '@/components/EChart';
import { Panel } from '@/components/Panel';
import { withParams } from '@/filters';
import { useFilters } from '@/session';
import { BreakdownPanel } from '@/pages/dashboard/BreakdownsPage';
import { PageHeader } from '@/components/shell/PageHeader';

export default function DetectionPage(): React.JSX.Element {
  const { params } = useFilters();
  const all = withParams(params, { include_automated: 'true' });
  const query = useApi('/api/v1/analytics/signals', all, signalsSchema);
  const chart = useMemo(() => (query.data ? signalsChart(query.data) : null), [query.data]);
  return (
    <div className="page">
      <PageHeader
        title="Detection"
        description="Which bot and spoofing rules fire most, and on what."
        filters
      />
      <p className="muted small">This page always includes automated traffic.</p>
      <div className="grid-2">
        <Panel
          query={query}
          title="Rules that fire most"
          description="Bot, spoof, spam and network rules. Each bar is the number of visits on which the rule fired."
          isEmpty={(d) => d.rows.length === 0}
          empty={(d) =>
            d.visits === 0
              ? 'No visits in this period with these filters.'
              : `None of the ${d.visits.toLocaleString()} visits fired a detection rule.`
          }
          meta={(d) => d.meta}
        >
          {() =>
            chart && (
              <EChart
                option={chart.option}
                table={chart.table}
                decals={chart.decals}
                label="Detection rules by frequency"
                height={Math.max(160, 26 * chart.table.rows.length + 24)}
              />
            )
          }
        </Panel>
        <BreakdownPanel dimension="classification" params={all} />
      </div>
    </div>
  );
}
