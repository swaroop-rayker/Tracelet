/**
 * Ground truth on a visit's page (DESIGN §16 M8, §12 E38, F4.AC15).
 *
 * The owner's truth beside what the engine recorded, level by level, right or wrong in words.
 * The label never changes the inference above it on the page; it is compared with it.
 * Labelling, changing and deleting are an owner's (invariant 9); an analyst sees the label.
 */

import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import type { ApiError } from '@/api/client';
import { deleteLabel, networkName, refreshAccuracy } from '@/api/groundtruth';
import type { GroundTruthLabel, VisitDetail } from '@/api/schemas';
import {
  Badge,
  Button,
  Card,
  DataTable,
  Dialog,
  EmptyState,
  ErrorNotice,
  toast,
} from '@/components/ui';
import { countryName, label as labelOf } from '@/format';
import { useSession } from '@/session';
import { LEVELS, OWNER_ONLY_LABEL, gpsPlaceOf, isRight, truthText, type Level } from './figures';
import { LabelForm } from './LabelForm';

function shown(level: Level, value: string | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return level === 'country' ? countryName(value) : value;
}

export function GroundTruthCard({ visit }: { readonly visit: VisitDetail }): React.JSX.Element {
  const { me } = useSession();
  const owner = me.role === 'owner';
  const label = visit.ground_truth_label ?? null;
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const actions = label !== null && (
    <div className="row-actions">
      <Button
        size="sm"
        icon="Edit"
        disabled={!owner}
        disabledReason={owner ? null : OWNER_ONLY_LABEL}
        onClick={() => {
          setEditing(true);
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
          setDeleting(true);
        }}
      >
        Delete
      </Button>
    </div>
  );

  return (
    <Card
      title="Ground truth"
      description="Where this visit really was, as an owner recorded it. It is compared with the engine's answer, never applied to it."
      actions={actions}
    >
      {label === null ? (
        <EmptyState
          icon="GroundTruth"
          title="No label"
          reason={
            visit.inferred_at === null
              ? 'Label it now; it is scored once inference has run.'
              : 'If you know where this visit came from, a label measures how right the engine was.'
          }
          action={
            <Button
              variant="primary"
              disabled={!owner}
              disabledReason={owner ? null : OWNER_ONLY_LABEL}
              onClick={() => {
                setEditing(true);
              }}
            >
              Label this visit
            </Button>
          }
        />
      ) : label.cant_tell ? (
        <p className="m-0">
          Recorded as <strong>can’t tell</strong>
          {label.labeled_by === null ? '' : ` by ${label.labeled_by.name}`}. It is not scored.
        </p>
      ) : (
        <Comparison label={label} />
      )}
      {editing && (
        <Dialog
          open
          size="lg"
          title={label === null ? 'Label this visit' : 'Change the label'}
          onClose={() => {
            setEditing(false);
          }}
        >
          <LabelForm
            visitId={visit.id}
            existing={label}
            gps={gpsPlaceOf(visit)}
            carry={null}
            onSaved={() => {
              setEditing(false);
            }}
          />
        </Dialog>
      )}
      {deleting && label !== null && (
        <DeleteDialog
          label={label}
          onClose={() => {
            setDeleting(false);
          }}
        />
      )}
    </Card>
  );
}

function Comparison({ label }: { readonly label: GroundTruthLabel }): React.JSX.Element {
  const truth = { ...label.truth };
  const rows = LEVELS.filter(
    (level) => truth[level === 'country' ? 'country_code' : level] !== null,
  );
  const { strict, advisory } = label.recorded;
  return (
    <div className="stack">
      <DataTable
        caption="The label beside the engine's recorded answer"
        compact
        rowKey={(level) => level}
        rows={rows}
        columns={[
          { key: 'level', header: 'Level', render: (level) => labelOf(level) },
          {
            key: 'truth',
            header: 'Truth',
            render: (level) => shown(level, truth[level === 'country' ? 'country_code' : level]),
          },
          {
            key: 'confirmed',
            header: 'Confirmed answer',
            render: (level) => {
              const value = strict[level === 'country' ? 'country_code' : level] ?? null;
              if (value === null) return <span className="muted">Abstained</span>;
              return verdict(isRight(truth, strict, level), shown(level, value));
            },
          },
          {
            key: 'guess',
            header: 'Best guess',
            render: (level) => {
              const value = advisory[level === 'country' ? 'country_code' : level] ?? null;
              if (value === null) return <span className="muted">None</span>;
              return verdict(isRight(truth, advisory, level), shown(level, value));
            },
          },
        ]}
      />
      <p className="t-meta m-0">
        {truthText(label)}
        {label.connection_kind === null ? '' : ` · ${labelOf(label.connection_kind)}`}
        {label.vpn_used === true ? ' · VPN on' : label.vpn_used === false ? ' · VPN off' : ''}
        {label.network === null ? '' : ` · ${networkName(label.network)}`}
        {label.has_coordinates ? ' · with the visit’s GPS fix' : ''}
        {label.labeled_by === null ? '' : ` · labelled by ${label.labeled_by.name}`}
        {` · recorded under ${label.recorded.inference_version ?? 'no version'}`}
      </p>
      {label.notes !== null && <p className="m-0">{label.notes}</p>}
    </div>
  );
}

function verdict(right: boolean, value: string): React.JSX.Element {
  return (
    <span>
      {value} <Badge tone={right ? 'ok' : 'error'}>{right ? 'Right' : 'Wrong'}</Badge>
    </span>
  );
}

export function DeleteDialog({
  label,
  onClose,
}: {
  readonly label: GroundTruthLabel;
  readonly onClose: () => void;
}): React.JSX.Element {
  const { me } = useSession();
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function remove(): Promise<void> {
    setBusy(true);
    setError(null);
    const result = await deleteLabel(me.csrf_token, label.id);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    toast('Label deleted.');
    refreshAccuracy(client, label.visit_id);
    onClose();
  }

  return (
    <Dialog
      open
      size="sm"
      title="Delete this label?"
      dismissible={!busy}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="danger"
            busy={busy}
            busyLabel="Deleting…"
            onClick={() => {
              void remove();
            }}
          >
            Delete label
          </Button>
        </>
      }
    >
      <div className="stack">
        <p className="m-0">
          The label <strong>{truthText(label)}</strong> on this visit goes, and accuracy is measured
          without it. The visit and its inference stay. The deletion is recorded in the audit log.
        </p>
        {error !== null && <ErrorNotice error={error} />}
      </div>
    </Dialog>
  );
}
