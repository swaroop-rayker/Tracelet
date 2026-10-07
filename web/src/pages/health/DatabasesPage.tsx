/**
 * System health › Geo databases (DESIGN §16 M7; F10.AC3, F10.AC4).
 *
 * Every database inference reads, installed or not: version, age, the verdict, the last
 * attempt. **Update** is confirm → progress → result (UI-15): the row says "Updating…" while
 * the server works, and the page polls until it has finished. A failure leaves the previous
 * version serving, and the row says why (F10.AC4).
 */

import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import type { ApiError } from '@/api/client';
import { useApi } from '@/api/query';
import { HEALTH, databasesSchema, updateDatabase, type GeoDatabase } from '@/api/system';
import { Panel } from '@/components/Panel';
import { Freshness } from '@/components/shell/Freshness';
import {
  Badge,
  Button,
  DataTable,
  Dialog,
  ErrorNotice,
  Identifier,
  Tooltip,
  toast,
} from '@/components/ui';
import { OWNER_ONLY, POLL_MS, VERDICT, bytes, words } from '@/pages/health/health-format';
import { toned } from '@/pages/configure/geofence-format';
import { useSession } from '@/session';

const PATH = `${HEALTH}/databases`;

export default function DatabasesPage(): React.JSX.Element {
  const { me } = useSession();
  const [polling, setPolling] = useState(false);
  const query = useApi(PATH, null, databasesSchema, {
    refetchInterval: polling ? POLL_MS / 3 : POLL_MS * 4,
  });
  const anyUpdating = query.data?.databases.some((d) => d.updating) ?? false;
  // Poll fast only while something is updating; the server's rows say when it has finished.
  useEffect(() => {
    if (query.data !== undefined) setPolling(anyUpdating);
  }, [anyUpdating, query.data]);
  const [confirming, setConfirming] = useState<GeoDatabase | null>(null);

  return (
    <>
      <Panel
        query={query}
        kind="table"
        title="Databases"
        description="A failed update leaves the previous version serving. Stale means older than its vendor's release cycle allows."
        actions={
          <Freshness
            iso={query.dataUpdatedAt ? new Date(query.dataUpdatedAt).toISOString() : null}
            zone={me.timezone}
          />
        }
        isEmpty={(d) => d.databases.length === 0}
        empty={<p className="muted">No databases are catalogued.</p>}
      >
        {(d) => (
          <DataTable
            caption="Geo databases"
            rows={d.databases}
            rowKey={(row) => row.name}
            compact
            columns={[
              {
                key: 'name',
                header: 'Database',
                render: (row) => (
                  <>
                    <strong className="nowrap">{row.name}</strong>
                    <br />
                    <span className="small muted">
                      {row.feeds ? `feeds ${row.feeds}` : 'reference data'}
                    </span>
                  </>
                ),
              },
              {
                key: 'version',
                header: 'Version',
                render: (row) => (
                  <>
                    <span className="nowrap">{row.installed?.version ?? '—'}</span>
                    <br />
                    <span className="small muted nowrap">
                      {row.age_days === null ? '' : `${String(row.age_days)} days old`}
                    </span>
                  </>
                ),
              },
              { key: 'verdict', header: 'State', render: (row) => <Verdict row={row} /> },
              {
                key: 'file',
                header: 'File',
                render: (row) =>
                  row.installed ? (
                    <>
                      <span className="nowrap">{bytes(row.installed.size_bytes)}</span>
                      <br />
                      {row.installed.sha256 ? (
                        <span className="nowrap">
                          <Identifier value={row.installed.sha256} label="Copy SHA-256" />
                        </span>
                      ) : null}
                    </>
                  ) : (
                    '—'
                  ),
              },
              {
                key: 'update',
                header: <span className="sr-only">Update</span>,
                render: (row) => (
                  <Button
                    size="sm"
                    disabled={row.updating}
                    disabledReason={
                      me.role !== 'owner'
                        ? OWNER_ONLY
                        : !row.configured
                          ? 'Its vendor credentials are not set on the server.'
                          : null
                    }
                    onClick={() => {
                      setConfirming(row);
                    }}
                  >
                    {row.updating ? 'Updating…' : 'Update'}
                  </Button>
                ),
              },
            ]}
          />
        )}
      </Panel>
      {confirming !== null && (
        <ConfirmUpdate
          database={confirming}
          onClose={() => {
            setConfirming(null);
          }}
          onStarted={() => {
            setPolling(true);
          }}
        />
      )}
    </>
  );
}

function Verdict({ row }: { readonly row: GeoDatabase }): React.JSX.Element {
  const verdict = VERDICT[row.verdict] ?? { label: row.verdict };
  const badge = <Badge {...toned(verdict.tone)}>{verdict.label}</Badge>;
  const attempt = row.last_attempt;
  if (attempt === null || attempt.status !== 'failed') return badge;
  return (
    <span>
      {badge}{' '}
      <Tooltip content={`Last attempt failed: ${attempt.error ?? 'no reason recorded'}`}>
        {(describedBy) => (
          <span className="small muted" tabIndex={0} aria-describedby={describedBy}>
            last attempt failed
          </span>
        )}
      </Tooltip>
    </span>
  );
}

function ConfirmUpdate({
  database,
  onClose,
  onStarted,
}: {
  readonly database: GeoDatabase;
  readonly onClose: () => void;
  readonly onStarted: () => void;
}): React.JSX.Element {
  const { me } = useSession();
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function start(): Promise<void> {
    setBusy(true);
    const result = await updateDatabase(me.csrf_token, database.name);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    onStarted();
    await client.invalidateQueries({ queryKey: [PATH] });
    toast(`Updating ${database.name}. This page follows it.`);
    onClose();
  }

  return (
    <Dialog
      open
      onClose={onClose}
      title={`Update ${database.name}?`}
      size="sm"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={busy} busyLabel="Starting…" onClick={() => void start()}>
            Update
          </Button>
        </>
      }
    >
      <p>
        Download the current release, check it in a memory-capped process, then switch to it. The
        installed copy{database.installed?.version ? ` (${database.installed.version})` : ''} keeps
        serving until the new one passes.
      </p>
      {database.verdict === 'up_to_date' && (
        <p className="small muted">It is up to date; this fetches it again anyway.</p>
      )}
      {error !== null && <ErrorNotice error={error} />}
      {database.last_attempt?.error && (
        <p className="small muted">
          Last attempt: {words(database.last_attempt.status)} — {database.last_attempt.error}
        </p>
      )}
    </Dialog>
  );
}
