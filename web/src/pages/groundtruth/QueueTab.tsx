/**
 * The labelling queue (DESIGN §16 M8): one visit at a time, its derivation beside the form.
 *
 * "Most disagreement" ranks by `conflict_score`, so scarce labels go where sources disagreed
 * (RISKS R9); "Newest" is for the test visit you have just made on a network. `j` / `k` move,
 * `s` skips for this session only (nothing is stored), and Enter in the form saves. The
 * shortcuts are inert while a text field has focus.
 */

import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router';
import { GT, labelListSchema, queueSchema } from '@/api/groundtruth';
import { useApi } from '@/api/query';
import { visitDetailSchema } from '@/api/schemas';
import { Panel } from '@/components/Panel';
import {
  Button,
  Card,
  EmptyState,
  ErrorNotice,
  Kbd,
  Loading,
  SegmentedControl,
} from '@/components/ui';
import { carryFrom, gpsPlaceOf } from '@/components/groundtruth/figures';
import { LabelForm } from '@/components/groundtruth/LabelForm';
import { CandidatesTable } from '@/components/visit/VisitDetail';
import { countryName, label, pct, when } from '@/format';
import { useSession } from '@/session';

const ORDERS = [
  { value: 'conflict', label: 'Most disagreement' },
  { value: 'recent', label: 'Newest' },
] as const;

function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement
  );
}

export function QueueTab({
  search,
  set,
}: {
  readonly search: URLSearchParams;
  readonly set: (key: string, value: string | null) => void;
}): React.JSX.Element {
  const order = search.get('order') === 'recent' ? 'recent' : 'conflict';
  const queue = useApi(`${GT}/queue`, new URLSearchParams({ order, limit: '100' }), queueSchema);
  const labels = useApi(GT, null, labelListSchema);
  const [skipped, setSkipped] = useState<ReadonlySet<string>>(new Set());
  const [at, setAt] = useState(0);

  const items = useMemo(
    () => (queue.data?.items ?? []).filter((i) => !skipped.has(i.visit.id)),
    [queue.data, skipped],
  );
  const index = Math.min(at, Math.max(items.length - 1, 0));
  const current = items[index];

  useEffect(() => {
    function onKey(event: KeyboardEvent): void {
      if (event.altKey || event.ctrlKey || event.metaKey || typing(event.target)) return;
      if (event.key === 'j') setAt((i) => Math.min(i + 1, Math.max(items.length - 1, 0)));
      else if (event.key === 'k') setAt((i) => Math.max(i - 1, 0));
      else if (event.key === 's' && current !== undefined) {
        const id = current.visit.id;
        setSkipped((s) => new Set([...s, id]));
      } else return;
      event.preventDefault();
    }
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
    };
  }, [items.length, current]);

  return (
    <div className="stack">
      <div className="toolbar">
        <SegmentedControl
          label="Order"
          value={order}
          onChange={(value) => {
            set('order', value === 'conflict' ? null : value);
            setAt(0);
          }}
          options={ORDERS}
        />
        {queue.data !== undefined && (
          <span className="t-meta toolbar__end">
            {queue.data.labelled.toLocaleString()} of{' '}
            {(queue.data.labelled + queue.data.remaining).toLocaleString()} labelled
            {skipped.size > 0 ? ` · ${String(skipped.size)} skipped this session` : ''}
          </span>
        )}
      </div>
      <Panel
        query={queue}
        title="Next visit to label"
        description="Keys: j next, k previous, s skip, Enter in the form saves."
        kind="table"
        isEmpty={() => items.length === 0}
        empty="Nothing left to label. Open a link on another network to add a test visit, then label it here (Newest)."
      >
        {() =>
          current === undefined ? null : (
            <>
              <div className="row-actions">
                <Button
                  size="sm"
                  variant="ghost"
                  icon="Previous"
                  disabled={index === 0}
                  onClick={() => {
                    setAt(index - 1);
                  }}
                >
                  Previous <Kbd>k</Kbd>
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  icon="Next"
                  disabled={index >= items.length - 1}
                  onClick={() => {
                    setAt(index + 1);
                  }}
                >
                  Next <Kbd>j</Kbd>
                </Button>
                <span className="t-meta">
                  {String(index + 1)} of {String(items.length)} in this list
                </span>
              </div>
              <QueueItem
                key={current.visit.id}
                visitId={current.visit.id}
                conflict={current.conflict_score}
                carry={carryFrom(labels.data?.items[0])}
                onSkip={() => {
                  const id = current.visit.id;
                  setSkipped((s) => new Set([...s, id]));
                }}
              />
            </>
          )
        }
      </Panel>
    </div>
  );
}

function QueueItem({
  visitId,
  conflict,
  carry,
  onSkip,
}: {
  readonly visitId: string;
  readonly conflict: number | null;
  readonly carry: ReturnType<typeof carryFrom>;
  readonly onSkip: () => void;
}): React.JSX.Element {
  const { me } = useSession();
  const visit = useApi(`/api/v1/visits/${encodeURIComponent(visitId)}`, null, visitDetailSchema);
  if (visit.isPending) return <Loading kind="table" label="Loading the visit…" />;
  if (visit.isError) return <ErrorNotice error={visit.error.error} />;
  const v = visit.data;
  const country = v.location.advisory.country_code ?? null;
  const guess = [
    v.location.advisory.city ?? null,
    v.location.advisory.admin1 ?? null,
    country === null ? null : countryName(country),
  ]
    .filter((x): x is string => x !== null)
    .join(', ');
  return (
    <div className="grid-2">
      <Card
        title="What the engine saw"
        description={`${when(v.occurred_at, me.timezone)} · ${v.link.label} · ${v.network.asn_org ?? 'unknown network'} · ${label(v.network.connection_class)}`}
        actions={
          <Link to={`/visits/${encodeURIComponent(v.id)}`} className="link">
            Open visit
          </Link>
        }
      >
        <div className="stack">
          <p className="m-0">
            Best guess <strong>{guess === '' ? 'none' : guess}</strong>
            {v.location.strict.admin1 === null || v.location.strict.admin1 === undefined
              ? ''
              : ` · confirmed state ${v.location.strict.admin1}`}
            {conflict === null ? '' : ` · sources disagreed ${pct(conflict)}`}
          </p>
          {v.candidates.length === 0 ? (
            <EmptyState title="No source proposed a candidate" />
          ) : (
            <CandidatesTable candidates={v.candidates} />
          )}
        </div>
      </Card>
      <Card title="Where was this visit really?">
        <LabelForm
          visitId={v.id}
          existing={null}
          gps={gpsPlaceOf(v)}
          carry={carry}
          onSaved={() => {
            // The queue refetches; this visit leaves it and the next takes its place.
          }}
          extraActions={
            <Button variant="ghost" onClick={onSkip}>
              Skip <Kbd>s</Kbd>
            </Button>
          }
        />
      </Card>
    </div>
  );
}
