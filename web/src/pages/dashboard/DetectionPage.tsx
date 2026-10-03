/**
 * Detection (F9.AC11, DESIGN §10.7): which rules fire most often, and the classification mix.
 *
 * Automated traffic is what this page is about, so it is always included whatever the filter
 * says -- the page states it in its description.
 */

import { useApi } from '@/api/query';
import { signalsSchema } from '@/api/schemas';
import { signalRows } from '@/charts';
import { Panel } from '@/components/Panel';
import { PageHeader } from '@/components/shell/PageHeader';
import { RankedList } from '@/components/ui';
import { withParams } from '@/filters';
import { BreakdownPanel } from '@/pages/dashboard/BreakdownsPage';
import { useFilters } from '@/session';

export default function DetectionPage(): React.JSX.Element {
  const { params } = useFilters();
  const all = withParams(params, { include_automated: 'true' });
  const query = useApi('/api/v1/analytics/signals', all, signalsSchema);
  return (
    <div className="page">
      <PageHeader
        title="Detection"
        description="Which bot and spoofing rules fire most, and on what. This page always includes automated traffic."
        filters
      />
      <div className="grid-2">
        <Panel
          query={query}
          kind="list"
          title="Rules that fire most"
          description="Bot, spoof, spam and network rules, by the number of visits on which each fired."
          isEmpty={(d) => d.rows.length === 0}
          empty={(d) =>
            d.visits === 0
              ? 'No visits in this period with these filters.'
              : `None of the ${d.visits.toLocaleString()} visits fired a detection rule.`
          }
          meta={(d) => d.meta}
        >
          {(data) => (
            <RankedList
              caption="Detection rules by frequency"
              labelHeader="Rule"
              rows={signalRows(data)}
            />
          )}
        </Panel>
        <BreakdownPanel dimension="classification" params={all} />
      </div>
    </div>
  );
}
