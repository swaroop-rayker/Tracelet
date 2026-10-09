/**
 * Inference: where locations come from (F9.AC7), how confident they are (F9.AC9), and
 * how accurate they are known to be (F9.AC10).
 *
 * The accuracy panel is the one most likely to mislead, so it is the most explicit. Since M8
 * it is a replay over the labelled visits in the period (ADR-0024), each figure with its label
 * count and 95 % interval (RISKS R9); with no label it says so in words. The strict emission
 * rate stays, labelled as *not* accuracy. Ground truth has the full picture.
 */

import { useMemo } from 'react';
import { Link } from 'react-router';
import { useApi } from '@/api/query';
import { accuracySchema, confidenceSchema, sourceFlowSchema } from '@/api/schemas';
import { confidenceChart, sourceFlowChart } from '@/charts';
import { EChart, TableView } from '@/components/EChart';
import { Panel } from '@/components/Panel';
import { label, pct } from '@/format';
import { useFilters } from '@/session';
import { PageHeader } from '@/components/shell/PageHeader';
import { Alert } from '@/components/ui';

const REASON: Readonly<Record<string, string>> = {
  no_ground_truth_labels: 'No labels',
  no_strict_emissions: 'Never confirmed',
};

/** A figure with its interval, or why there is none (RISKS R9). */
function measured(
  value: number | null,
  ci: readonly [number, number] | null,
  reason: string | null,
): string {
  if (value === null) return reason === null ? 'Not measured' : (REASON[reason] ?? reason);
  return ci === null ? pct(value, 1) : `${pct(value, 1)} (${pct(ci[0])}–${pct(ci[1])})`;
}

export default function InferencePage(): React.JSX.Element {
  const { params } = useFilters();
  const flow = useApi('/api/v1/analytics/source-flow', params, sourceFlowSchema);
  const confidence = useApi('/api/v1/analytics/confidence', params, confidenceSchema);
  const accuracy = useApi('/api/v1/analytics/accuracy', params, accuracySchema);
  const flowChart = useMemo(() => (flow.data ? sourceFlowChart(flow.data) : null), [flow.data]);
  const confChart = useMemo(
    () => (confidence.data ? confidenceChart(confidence.data) : null),
    [confidence.data],
  );

  return (
    <div className="page">
      <PageHeader
        title="Inference"
        description="How the engine located visits: which sources answered, and how sure it was."
        filters
      />
      <Panel
        query={flow}
        title="Sources to emitted level"
        description="Each source that proposed a candidate, flowing to the deepest level the engine was willing to state. “Abstained” is the engine declining to guess."
        isEmpty={(d) => d.links.length === 0}
        empty="No visits in this period have been through location inference yet."
        meta={(d) => d.meta}
      >
        {(data) => (
          <>
            {flowChart && (
              <EChart
                option={flowChart.option}
                table={flowChart.table}
                decals={flowChart.decals}
                label="Inference sources flowing to the level emitted"
                height={Math.max(260, 30 * data.sources.length)}
              />
            )}
            <p className="t-meta m-0">
              {data.visits.toLocaleString()} inferred visits. A visit flows once for every source
              that spoke about it, so flows add up to more than the visit count.
            </p>
          </>
        )}
      </Panel>

      <div className="grid-2">
        <Panel
          query={confidence}
          title="Confidence distribution"
          description="How sure the engine was, per level. This is what thresholds are tuned against."
          isEmpty={(d) => d.levels.every((l) => l.bins.every((b) => b === 0))}
          empty="No visit in this period has a confidence score yet."
          meta={(d) => d.meta}
        >
          {() =>
            confChart && (
              <EChart
                option={confChart.option}
                table={confChart.table}
                decals={confChart.decals}
                label="Confidence histograms"
                height={280}
              />
            )
          }
        </Panel>

        <Panel
          query={accuracy}
          title="Accuracy"
          description="Against hand-labelled visits in this period, on what the network alone says (a consented visit's GPS set aside)."
          isEmpty={() => false}
          empty={null}
          meta={(d) => d.meta}
          actions={
            <Link className="link" to="/ground-truth">
              Ground truth
            </Link>
          }
        >
          {(data) => {
            const noLabels = data.levels.every((l) => l.label_count === 0);
            return (
              <>
                {noLabels && (
                  <Alert tone="info" title="No labelled visit in this period">
                    Precision and coverage need visits whose true location you know. Label some on{' '}
                    <Link className="link" to="/ground-truth">
                      Ground truth
                    </Link>
                    . Nothing here is an accuracy claim until then.
                  </Alert>
                )}
                <TableView
                  caption="Accuracy per level"
                  table={{
                    columns: [
                      'Level',
                      'Labels',
                      'Confirmed: precision',
                      'Confirmed: coverage',
                      'Best guess',
                      'Strict answered (not accuracy)',
                    ],
                    rows: data.levels.map((l) => [
                      label(l.level),
                      l.label_count,
                      measured(l.precision, l.precision_ci95, l.reason),
                      measured(l.coverage, l.coverage_ci95, l.label_count === 0 ? l.reason : null),
                      measured(
                        l.advisory_accuracy,
                        l.advisory_ci95,
                        l.label_count === 0 ? l.reason : null,
                      ),
                      pct(l.emission_rate, 1),
                    ]),
                  }}
                />
                <p className="t-meta m-0">
                  Scored under {data.inference_version}. Each figure is a share of the labels in its
                  row, with its 95 % interval. “Strict answered” is the share of{' '}
                  {data.inferred.toLocaleString()} inferred visits for which the engine stated that
                  level at all — how often it spoke, not whether it was right.
                </p>
              </>
            );
          }}
        </Panel>
      </div>
    </div>
  );
}
