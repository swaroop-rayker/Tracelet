/**
 * System health › Geo databases (DESIGN §16 M7; F10.AC3, F10.AC4, SPEC §11 row 24).
 *
 * Every database inference reads, installed or not, with its **state**: up to date, update
 * available (from the last release check), updating with its percent -- a number, never a
 * bar -- update failed or unable to update with the reason, or not installed. Each has an
 * **Auto-update** switch (the scheduler's; Update still works when it is off). **Update** is
 * confirm → "Updating n %" → the result (UI-15): the page polls every 2 s while anything is
 * updating, then every minute.
 */

import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import type { ApiError } from '@/api/client';
import { useApi } from '@/api/query';
import {
  HEALTH,
  checkDatabases,
  databasesSchema,
  setAutoUpdate,
  updateDatabase,
  type GeoDatabase,
} from '@/api/system';
import { Panel } from '@/components/Panel';
import {
  Badge,
  Button,
  DataTable,
  Dialog,
  ErrorNotice,
  Identifier,
  Switch,
  Timestamp,
  toast,
} from '@/components/ui';
import { day } from '@/format';
import { OWNER_ONLY, bytes, words } from '@/pages/health/health-format';
import { useSession } from '@/session';

const PATH = `${HEALTH}/databases`;
const FAST_MS = 2_000;
const SLOW_MS = 60_000;

export default function DatabasesPage(): React.JSX.Element {
  const { me } = useSession();
  const client = useQueryClient();
  const owner = me.role === 'owner';
  const [fast, setFast] = useState(false);
  const query = useApi(PATH, null, databasesSchema, { refetchInterval: fast ? FAST_MS : SLOW_MS });
  const anyUpdating = query.data?.databases.some((d) => d.state === 'updating') ?? false;
  useEffect(() => {
    if (query.data !== undefined) setFast(anyUpdating);
  }, [anyUpdating, query.data]);
  const [confirming, setConfirming] = useState<GeoDatabase | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const lastChecked = query.data?.databases
    .map((d) => d.checked_at)
    .filter((t): t is string => t !== null)
    .sort()
    .at(-1);

  async function check(): Promise<void> {
    setChecking(true);
    const result = await checkDatabases(me.csrf_token);
    setChecking(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setError(null);
    client.setQueryData([PATH, ''], result.data);
    const available = result.data.databases.filter((d) => d.state === 'update_available').length;
    toast(
      available === 0
        ? 'Checked. Nothing new to install.'
        : `Checked. ${String(available)} update${available === 1 ? '' : 's'} available.`,
    );
  }

  return (
    <>
      <Panel
        query={query}
        kind="table"
        title="Databases"
        description="A failed update leaves the installed copy serving. Auto-update is the scheduler's; Update works either way."
        actions={
          <span className="row-actions">
            {lastChecked !== undefined && (
              <span className="small muted">
                Checked <Timestamp iso={lastChecked} zone={me.timezone} />
              </span>
            )}
            <Button
              size="sm"
              icon="Retry"
              busy={checking}
              busyLabel="Checking…"
              disabledReason={owner ? null : OWNER_ONLY}
              onClick={() => void check()}
            >
              Check for updates
            </Button>
          </span>
        }
        isEmpty={(d) => d.databases.length === 0}
        empty={<p className="muted">No databases are catalogued.</p>}
      >
        {(d) => (
          <div className="stack">
            {error !== null && <ErrorNotice error={error} />}
            <DataTable
              caption="Geo databases"
              compact
              rows={d.databases}
              rowKey={(row) => row.name}
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
                  key: 'installed',
                  header: 'Installed',
                  render: (row) =>
                    row.installed?.installed_at ? (
                      <span title={`Version ${row.installed.version ?? 'unknown'}`}>
                        <span className="nowrap">
                          {day(row.installed.installed_at, me.timezone)}
                        </span>
                        <br />
                        <span className="small muted nowrap">
                          {row.age_days === null ? '' : `${String(row.age_days)} days old`}
                        </span>
                      </span>
                    ) : (
                      '—'
                    ),
                },
                { key: 'state', header: 'State', render: (row) => <StateCell row={row} /> },
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
                  key: 'updates',
                  header: 'Updates',
                  render: (row) => (
                    <span className="row-actions nowrap">
                      <AutoCell row={row} />
                      <Button
                        size="sm"
                        disabled={row.state === 'updating'}
                        disabledReason={
                          !owner
                            ? OWNER_ONLY
                            : !row.configured
                              ? 'Its vendor credentials are not set on the server.'
                              : null
                        }
                        onClick={() => {
                          setConfirming(row);
                        }}
                      >
                        {row.state === 'updating' ? 'Updating…' : 'Update'}
                      </Button>
                    </span>
                  ),
                },
              ]}
            />
          </div>
        )}
      </Panel>
      {confirming !== null && (
        <ConfirmUpdate
          database={confirming}
          onClose={() => {
            setConfirming(null);
          }}
          onStarted={() => {
            setFast(true);
          }}
        />
      )}
    </>
  );
}

/** The state, in words as well as colour, with the reason or the date beside it. */
function StateCell({ row }: { readonly row: GeoDatabase }): React.JSX.Element {
  const { me } = useSession();
  let badge: React.JSX.Element;
  let note: string | null = null;
  switch (row.state) {
    case 'updating': {
      const percent = row.progress?.percent;
      badge = (
        <Badge tone="info">
          Updating{percent === null || percent === undefined ? '' : ` ${String(percent)} %`}
        </Badge>
      );
      note = row.progress ? words(row.progress.phase) : null;
      break;
    }
    case 'update_available':
      badge = <Badge tone="info">Update available</Badge>;
      note = row.latest?.released_at
        ? `released ${day(row.latest.released_at, me.timezone)}`
        : (row.latest?.version ?? 'due by its schedule');
      break;
    case 'update_failed':
      badge = <Badge tone="error">Update failed</Badge>;
      note = row.last_attempt?.error ?? null;
      break;
    case 'unable_to_update':
      badge = <Badge tone="warn">Unable to update</Badge>;
      note = row.check_error;
      break;
    case 'not_installed':
      badge = <Badge tone="error">Not installed</Badge>;
      break;
    default:
      badge = <Badge tone="ok">Up to date</Badge>;
  }
  return (
    <span className="db-state" {...(row.state === 'updating' ? { 'aria-live': 'polite' } : {})}>
      {badge}
      {row.stale && row.state !== 'not_installed' && <Badge tone="warn">Stale</Badge>}
      {note !== null && <span className="small muted db-state__note">{note}</span>}
    </span>
  );
}

/** The scheduler's switch: saved at once, reversible (UI-13); an analyst reads it (UI-17). */
function AutoCell({ row }: { readonly row: GeoDatabase }): React.JSX.Element {
  const { me } = useSession();
  const client = useQueryClient();
  const [value, setValue] = useState(row.auto_update);
  useEffect(() => {
    setValue(row.auto_update);
  }, [row.auto_update]);
  if (me.role !== 'owner') return <span>Auto {row.auto_update ? 'on' : 'off'}</span>;
  return (
    <span className="row-actions">
      <span className="small muted" aria-hidden="true">
        Auto
      </span>
      <Switch
        label={`Update ${row.name} automatically`}
        hideLabel
        checked={value}
        onChange={(next) => {
          setValue(next);
          void setAutoUpdate(me.csrf_token, row.name, next).then((result) => {
            if (!result.ok) {
              setValue(!next);
              toast(`Could not change ${row.name}: ${result.error.message}`);
              return;
            }
            toast(
              next
                ? `${row.name} updates automatically again.`
                : `${row.name} no longer updates automatically. Update still works.`,
            );
            void client.invalidateQueries({ queryKey: [PATH] });
          });
        }}
      />
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
    toast(`Updating ${database.name}. The State column follows it.`);
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
        installed copy
        {database.installed?.version ? ` (${database.installed.version})` : ''} keeps serving until
        the new one passes.
      </p>
      {database.state === 'up_to_date' && (
        <p className="small muted">It is up to date; this fetches it again anyway.</p>
      )}
      {error !== null && <ErrorNotice error={error} />}
    </Dialog>
  );
}
