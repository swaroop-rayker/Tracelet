/**
 * The level cards (DESIGN §16 M8): per level, confirmed precision and coverage and best-guess
 * accuracy, each with its sample, its 95 % interval and its F4.AC13 target in words, under
 * a verdict that says whether every gated target was met. Shared by the page and a run's
 * dialog.
 */

import type { LevelReport, Population, Report } from '@/api/groundtruth';
import { Alert, Card, KeyValue } from '@/components/ui';
import { interval, labels, sample, share, targetText } from '@/components/groundtruth/figures';
import { label } from '@/format';

/** Four level cards and a verdict in words: the figures, their samples and intervals. */
export function AccuracyLevels({
  report,
  population,
  notActive,
}: {
  readonly report: Report;
  readonly population: Population;
  readonly notActive: boolean;
}): React.JSX.Element {
  const chosen = report.populations.find((p) => p.population === population);
  const gated = report.targets.filter((t) => t.gated);
  const missed = gated.filter((t) => t.status === 'missed').length;
  return (
    <div className="stack">
      {notActive && (
        <Alert tone="info">
          Scored under v{String(report.settings_version)}, which is not active: a replay only,
          nothing on the live engine changes.
        </Alert>
      )}
      <Alert
        tone={report.passed === false ? 'warn' : report.passed === true ? 'ok' : 'info'}
        title={
          report.passed === true
            ? 'Every gated target met'
            : report.passed === false
              ? `${String(missed)} gated ${missed === 1 ? 'target' : 'targets'} missed`
              : 'Nothing gated could be measured yet'
        }
      >
        {report.inference_version} · {labels(report.label_count)} scored ·{' '}
        {report.cant_tell.toLocaleString()} can’t tell · {report.pending.toLocaleString()} not
        inferred yet. Targets are F4.AC13’s, gated on the network-only population.
      </Alert>
      {chosen === undefined ? null : (
        <div className="grid-2">
          {chosen.levels.map((level) => (
            <LevelCard key={level.level} level={level} report={report} population={population} />
          ))}
        </div>
      )}
    </div>
  );
}

function figure(
  name: string,
  p: LevelReport['strict_precision'],
  target: string | null,
): { readonly key: string; readonly label: string; readonly value: React.ReactNode } {
  return {
    key: name,
    label: name,
    value: (
      <span className="stack-sm">
        <strong>{share(p)}</strong>
        <span className="t-meta">
          {sample(p)} · {interval(p.ci95)}
        </span>
        {target !== null && <span className="t-meta">{target}</span>}
      </span>
    ),
  };
}

function LevelCard({
  level,
  report,
  population,
}: {
  readonly level: LevelReport;
  readonly report: Report;
  readonly population: Population;
}): React.JSX.Element {
  const target = (metric: string): string | null =>
    targetText(
      report.targets.find(
        (t) => t.population === population && t.level === level.level && t.metric === metric,
      ),
    );
  return (
    <Card
      title={label(level.level)}
      description={`${labels(level.label_count)} ${level.label_count === 1 ? 'reaches' : 'reach'} this level.`}
    >
      <KeyValue
        items={[
          figure('Confirmed: precision', level.strict_precision, target('strict_precision')),
          figure('Confirmed: coverage', level.strict_coverage, target('strict_coverage')),
          figure('Best guess: accuracy', level.advisory_accuracy, target('advisory_accuracy')),
        ]}
      />
    </Card>
  );
}
