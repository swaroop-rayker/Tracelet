/**
 * Ground truth's other tabs (DESIGN §16 M8): every label, per-source accuracy (F4.AC17), the
 * coverage matrix the M8 checklist counts, and recorded runs. Tables, not charts: these are
 * small counts, and their samples and intervals are the point (RISKS R9).
 */

import type { UseQueryResult } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router';
import {
  CONNECTION_KINDS,
  CONNECTION_LABEL,
  GT,
  NETWORKS,
  NETWORK_LABEL,
  labelListSchema,
  networkName,
  runDetailSchema,
  runListSchema,
  type Report,
  type Run,
} from '@/api/groundtruth';
import { useApi, type ApiFailure } from '@/api/query';
import type { GroundTruthLabel } from '@/api/schemas';
import { Panel } from '@/components/Panel';
import { Badge, Button, Card, DataTable, Dialog, ErrorNotice, Loading } from '@/components/ui';
import { DeleteDialog } from '@/components/groundtruth/GroundTruthCard';
import {
  interval,
  labels,
  recordedText,
  sample,
  share,
  truthText,
} from '@/components/groundtruth/figures';
import { OWNER_ONLY_LABEL } from '@/components/groundtruth/figures';
import { LabelForm } from '@/components/groundtruth/LabelForm';
import { label, when } from '@/format';
import { useSession } from '@/session';
import { AccuracyLevels } from './AccuracyLevels';

// ---------------------------------------------------------------------------
// Labels
// ---------------------------------------------------------------------------

export function LabelsTab(): React.JSX.Element {
  const { me } = useSession();
  const owner = me.role === 'owner';
  const list = useApi(GT, null, labelListSchema);
  const [editing, setEditing] = useState<GroundTruthLabel | null>(null);
  const [deleting, setDeleting] = useState<GroundTruthLabel | null>(null);

  return (
    <Panel
      query={list}
      title="Labels"
      description="Every label, newest first, beside what the engine recorded for that visit."
      kind="table"
      isEmpty={(d) => d.items.length === 0}
      empty="No labels yet. Label visits from the queue, or from a visit's own page."
    >
      {(data) => (
        <>
          <DataTable
            caption="Ground-truth labels"
            rowKey={(row) => row.id}
            rows={data.items}
            columns={[
              {
                key: 'when',
                header: 'Visit',
                render: (row) => (
                  <Link className="link" to={`/visits/${encodeURIComponent(row.visit_id)}`}>
                    {when(row.occurred_at, me.timezone)}
                  </Link>
                ),
              },
              { key: 'link', header: 'Link', render: (row) => row.link.label },
              { key: 'truth', header: 'Truth', render: (row) => truthText(row) },
              { key: 'engine', header: 'Engine said', render: (row) => recordedText(row) },
              {
                key: 'how',
                header: 'Network',
                render: (row) =>
                  [
                    row.network === null ? null : networkName(row.network),
                    row.connection_kind === null ? null : label(row.connection_kind),
                    row.vpn_used === true ? 'VPN on' : row.vpn_used === false ? 'VPN off' : null,
                  ]
                    .filter((x): x is string => x !== null)
                    .join(' · ') || '—',
              },
              {
                key: 'by',
                header: 'By',
                render: (row) => row.labeled_by?.name ?? 'A former admin',
              },
              {
                key: 'actions',
                header: <span className="sr-only">Actions</span>,
                render: (row) => (
                  <div className="row-actions">
                    <Button
                      size="sm"
                      variant="ghost"
                      icon="Edit"
                      disabled={!owner}
                      disabledReason={owner ? null : OWNER_ONLY_LABEL}
                      onClick={() => {
                        setEditing(row);
                      }}
                    >
                      Edit
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      icon="Delete"
                      disabled={!owner}
                      disabledReason={owner ? null : OWNER_ONLY_LABEL}
                      onClick={() => {
                        setDeleting(row);
                      }}
                    >
                      Delete
                    </Button>
                  </div>
                ),
              },
            ]}
          />
          <p className="t-meta m-0">
            {labels(data.total)}, {data.cant_tell.toLocaleString()} of them can’t tell.
          </p>
          {editing !== null && (
            <Dialog
              open
              size="lg"
              title="Change the label"
              onClose={() => {
                setEditing(null);
              }}
            >
              <LabelForm
                visitId={editing.visit_id}
                existing={editing}
                gps={null}
                carry={null}
                onSaved={() => {
                  setEditing(null);
                }}
              />
            </Dialog>
          )}
          {deleting !== null && (
            <DeleteDialog
              label={deleting}
              onClose={() => {
                setDeleting(null);
              }}
            />
          )}
        </>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Sources (F4.AC17)
// ---------------------------------------------------------------------------

type ReportQuery = UseQueryResult<Report, ApiFailure>;

export function SourcesTab({ report }: { readonly report: ReportQuery }): React.JSX.Element {
  return (
    <Panel
      query={report}
      title="Accuracy per source"
      description="How often each source's own claim was right, whatever consensus did with it. A source that is consistently wrong is the evidence for lowering its weight in a new settings version (F4.AC17)."
      kind="table"
      isEmpty={(r) => r.sources.length === 0}
      empty="No labelled visit has a candidate yet."
    >
      {(data) => (
        <DataTable
          caption="Accuracy per source and level"
          rowKey={(row) => `${row.source}-${row.level}`}
          rows={data.sources.flatMap((s) => s.levels.map((l) => ({ source: s.source, ...l })))}
          columns={[
            { key: 'source', header: 'Source', render: (row) => label(row.source) },
            { key: 'level', header: 'Level', render: (row) => label(row.level) },
            {
              key: 'claims',
              header: 'Claims',
              numeric: true,
              render: (row) => row.claims.toLocaleString(),
            },
            {
              key: 'accepted',
              header: 'Accepted',
              numeric: true,
              render: (row) => row.accepted.toLocaleString(),
            },
            {
              key: 'right',
              header: 'Right',
              render: (row) => (
                <span>
                  <strong>{share(row.correct)}</strong>{' '}
                  <span className="t-meta">
                    {sample(row.correct)} · {interval(row.correct.ci95)}
                  </span>
                </span>
              ),
            },
          ]}
        />
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Coverage: the M8 checklist's matrix
// ---------------------------------------------------------------------------

const COLUMNS = CONNECTION_KINDS.flatMap((kind) =>
  [false, true].map((vpn) => ({
    key: `${kind}-${String(vpn)}`,
    kind,
    vpn,
    header: `${CONNECTION_LABEL[kind]}${vpn ? ' + VPN' : ''}`,
  })),
);

export function CoverageTab({ report }: { readonly report: ReportQuery }): React.JSX.Element {
  return (
    <Panel
      query={report}
      title="What the labels cover"
      description="Labels by the network underneath, the connection and the VPN. M8 asks for every major Indian network on Wi-Fi and mobile data, with and without a VPN."
      kind="table"
      isEmpty={(r) => r.label_count === 0}
      empty="No labels scored yet."
    >
      {(data) => {
        const count = (network: string | null, kind: string | null, vpn: boolean | null): number =>
          data.matrix
            .filter(
              (m) => m.network === network && m.connection_kind === kind && m.vpn_used === vpn,
            )
            .reduce((n, m) => n + m.count, 0);
        const rows = [...NETWORKS, null] as const;
        const unsaid = (network: string | null): number =>
          data.matrix
            .filter(
              (m) => m.network === network && (m.connection_kind === null || m.vpn_used === null),
            )
            .reduce((n, m) => n + m.count, 0);
        return (
          <div className="stack">
            <DataTable
              caption="Labels by network, connection and VPN"
              compact
              rowKey={(network) => network ?? 'unsaid'}
              rows={rows}
              columns={[
                {
                  key: 'network',
                  header: 'Network',
                  render: (network) => (network === null ? 'Not said' : NETWORK_LABEL[network]),
                },
                ...COLUMNS.map((c) => ({
                  key: c.key,
                  header: c.header,
                  numeric: true,
                  render: (network: (typeof rows)[number]) => {
                    const n = count(network, c.kind, c.vpn);
                    return n === 0 ? <span className="muted">none yet</span> : n;
                  },
                })),
                {
                  key: 'unsaid',
                  header: 'Connection or VPN not said',
                  numeric: true,
                  render: (network) => {
                    const n = unsaid(network);
                    return n === 0 ? '—' : n;
                  },
                },
              ]}
            />
            <Card
              title="City, by path"
              description="F4.AC13 measures city coverage separately for the Cloudflare and direct paths, on the network-only population."
            >
              <DataTable
                caption="City strict precision and coverage per path"
                compact
                rowKey={(row) => row.path}
                rows={data.paths}
                columns={[
                  {
                    key: 'path',
                    header: 'Path',
                    render: (row) => (row.path === 'cloudflare' ? 'Cloudflare' : 'Direct'),
                  },
                  {
                    key: 'labels',
                    header: 'Labels',
                    numeric: true,
                    render: (row) => row.label_count.toLocaleString(),
                  },
                  {
                    key: 'precision',
                    header: 'Confirmed city: precision',
                    render: (row) =>
                      `${share(row.city_strict_precision)} (${sample(row.city_strict_precision)})`,
                  },
                  {
                    key: 'coverage',
                    header: 'Confirmed city: coverage',
                    render: (row) =>
                      `${share(row.city_strict_coverage)} (${sample(row.city_strict_coverage)})`,
                  },
                ]}
              />
            </Card>
          </div>
        );
      }}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Runs
// ---------------------------------------------------------------------------

export function RunsTab(): React.JSX.Element {
  const { me } = useSession();
  const runs = useApi(`${GT}/runs`, null, runListSchema);
  const [open, setOpen] = useState<Run | null>(null);
  return (
    <Panel
      query={runs}
      title="Recorded measurements"
      description="Each run scored one settings version against the labels of its day. Runs are history: never changed or deleted."
      kind="table"
      isEmpty={(r) => r.length === 0}
      empty="No measurement recorded yet. Use “Record this measurement”, or `tracelet accuracy run` on the server."
    >
      {(data) => (
        <>
          <DataTable
            caption="Accuracy runs"
            rowKey={(row) => row.id}
            rows={data}
            columns={[
              {
                key: 'when',
                header: 'When',
                render: (row) => (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setOpen(row);
                    }}
                  >
                    {when(row.run_at, me.timezone)}
                  </Button>
                ),
              },
              { key: 'version', header: 'Version', render: (row) => row.inference_version },
              {
                key: 'labels',
                header: 'Labels',
                numeric: true,
                render: (row) => row.label_count.toLocaleString(),
              },
              {
                key: 'passed',
                header: 'Gated targets',
                render: (row) =>
                  row.passed === true ? (
                    <Badge tone="ok">All met</Badge>
                  ) : row.passed === false ? (
                    <Badge tone="warn">Some missed</Badge>
                  ) : (
                    <span className="muted">Nothing measurable</span>
                  ),
              },
              {
                key: 'origin',
                header: 'From',
                render: (row) =>
                  row.origin === 'cli' ? 'Server CLI' : (row.recorded_by?.name ?? 'Dashboard'),
              },
              { key: 'note', header: 'Note', render: (row) => row.note ?? '—' },
            ]}
          />
          {open !== null && (
            <RunDialog
              run={open}
              onClose={() => {
                setOpen(null);
              }}
            />
          )}
        </>
      )}
    </Panel>
  );
}

function RunDialog({
  run,
  onClose,
}: {
  readonly run: Run;
  readonly onClose: () => void;
}): React.JSX.Element {
  const { me } = useSession();
  const detail = useApi(`${GT}/runs/${encodeURIComponent(run.id)}`, null, runDetailSchema);
  return (
    <Dialog open size="lg" title={`Run of ${when(run.run_at, me.timezone)}`} onClose={onClose}>
      {detail.isPending ? (
        <Loading kind="kpi" label="Loading the run…" />
      ) : detail.isError ? (
        <ErrorNotice error={detail.error.error} />
      ) : (
        <AccuracyLevels report={detail.data.metrics} population="network_only" notActive={false} />
      )}
    </Dialog>
  );
}
