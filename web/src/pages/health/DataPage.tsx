/**
 * System health › Data (DESIGN §16 M7; F10.AC11, F10.AC12, F12.AC7-AC12).
 *
 * **Retention:** three periods, saved; then **Preview purge** -- the exact counts, deleting
 * nothing -- then **Purge…**, a typed confirmation quoting those counts, then progress, then
 * the result (UI-15, UI-16). The purge deletes exactly what the preview counted: the server
 * takes the preview back and refuses one that is stale (API §10).
 *
 * **Backups:** when the last one ran, the last restore check, the last download (the only
 * copy off this machine, RISKS R11); "Back up now"; and each backup with Download and Verify.
 * A backup never restored is not yet a backup (F12.AC10), so each row says whether it was.
 */

import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import type { ApiError } from '@/api/client';
import { useApi } from '@/api/query';
import {
  HEALTH,
  backupDownloadUrl,
  backupsSchema,
  previewPurge,
  retentionSchema,
  startBackup,
  startPurge,
  updateRetention,
  verifyRestore,
  type Backup,
  type Backups,
  type PurgeCounts,
  type PurgePreview,
  type Retention,
} from '@/api/system';
import { Panel } from '@/components/Panel';
import {
  Alert,
  Badge,
  Button,
  ButtonLink,
  DataTable,
  Dialog,
  ErrorNotice,
  Field,
  Identifier,
  Stat,
  Stats,
  Timestamp,
  toast,
} from '@/components/ui';
import { count, day } from '@/format';
import { OWNER_ONLY, bytes } from '@/pages/health/health-format';
import { toned } from '@/pages/configure/geofence-format';
import { useSession } from '@/session';

const RETENTION = `${HEALTH}/retention`;
const BACKUPS = `${HEALTH}/backups`;
const FAST_MS = 3_000;
const SLOW_MS = 60_000;

export default function DataPage(): React.JSX.Element {
  return (
    <>
      <RetentionPanel />
      <BackupsPanel />
    </>
  );
}

// ---------------------------------------------------------------------------
// Retention
// ---------------------------------------------------------------------------

const COUNT_ROWS: readonly {
  readonly key: keyof PurgeCounts;
  readonly label: string;
  /** In a sentence: "1 204 visits, 3 audit-log rows". */
  readonly noun: string;
}[] = [
  { key: 'visits', label: 'Visits', noun: 'visits' },
  { key: 'visit_candidates', label: 'Their location candidates', noun: 'location candidates' },
  {
    key: 'ip_addresses',
    label: 'Encrypted IP addresses cleared (visits kept)',
    noun: 'encrypted IP addresses',
  },
  { key: 'audit_rows', label: 'Audit-log rows', noun: 'audit-log rows' },
  { key: 'delivered_alerts', label: 'Delivered alerts', noun: 'delivered alerts' },
];

function total(c: PurgeCounts): number {
  return c.visits + c.visit_candidates + c.ip_addresses + c.audit_rows + c.delivered_alerts;
}

function RetentionPanel(): React.JSX.Element {
  const [watching, setWatching] = useState(false);
  const query = useApi(RETENTION, null, retentionSchema, {
    refetchInterval: watching ? FAST_MS : SLOW_MS,
  });
  const running = query.data?.purge_running ?? false;
  useEffect(() => {
    if (query.data !== undefined && !running) setWatching(false);
  }, [query.data, running]);
  return (
    <Panel
      query={query}
      kind="text"
      title="Retention"
      description="Older data is deleted every night, an hour before the backup. Rollups are kept forever, so trends survive (NFR5.AC4)."
      isEmpty={() => false}
      empty={null}
    >
      {(r) => (
        <RetentionBody
          retention={r}
          onPurgeStarted={() => {
            setWatching(true);
          }}
        />
      )}
    </Panel>
  );
}

function RetentionBody({
  retention,
  onPurgeStarted,
}: {
  readonly retention: Retention;
  readonly onPurgeStarted: () => void;
}): React.JSX.Element {
  const { me } = useSession();
  const client = useQueryClient();
  const owner = me.role === 'owner';
  const [visits, setVisits] = useState(String(retention.policy.visit_days));
  const [ip, setIp] = useState(String(retention.policy.ip_days));
  const [audit, setAudit] = useState(String(retention.policy.audit_days));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [preview, setPreview] = useState<PurgePreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    setVisits(String(retention.policy.visit_days));
    setIp(String(retention.policy.ip_days));
    setAudit(String(retention.policy.audit_days));
  }, [retention.policy.visit_days, retention.policy.ip_days, retention.policy.audit_days]);

  const dirty =
    visits !== String(retention.policy.visit_days) ||
    ip !== String(retention.policy.ip_days) ||
    audit !== String(retention.policy.audit_days);

  async function save(): Promise<void> {
    setSaving(true);
    const result = await updateRetention(me.csrf_token, {
      visit_days: Number(visits),
      ip_days: Number(ip),
      audit_days: Number(audit),
    });
    setSaving(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setError(null);
    setPreview(null);
    client.setQueryData([RETENTION, ''], result.data);
    toast('Retention periods saved. Nothing is deleted until a purge runs.');
  }

  async function runPreview(): Promise<void> {
    setPreviewing(true);
    const result = await previewPurge(me.csrf_token);
    setPreviewing(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setError(null);
    setPreview(result.data);
  }

  const fieldError = (name: string): string | null =>
    error?.fields.find((f) => f.field.endsWith(name))?.message ?? null;

  return (
    <div className="stack">
      <div className="form-grid">
        <Field
          label="Visits, days"
          type="number"
          inputMode="numeric"
          value={visits}
          onChange={setVisits}
          disabled={!owner}
          hint="At least 8: the last week's daily figures are still being settled."
          error={fieldError('visit_days')}
        />
        <Field
          label="Encrypted IP addresses, days"
          type="number"
          inputMode="numeric"
          value={ip}
          onChange={setIp}
          disabled={!owner}
          hint="Cleared from visits that are kept; never longer than visits."
          error={fieldError('ip_days')}
        />
        <Field
          label="Audit log, days"
          type="number"
          inputMode="numeric"
          value={audit}
          onChange={setAudit}
          disabled={!owner}
          error={fieldError('audit_days')}
        />
      </div>
      <p className="small muted">
        Rollups: {retention.rollups}. Delivered alerts: {String(retention.delivered_alerts_days)}{' '}
        days; undelivered ones stay until retried.
      </p>
      <div className="row-actions">
        <Button
          variant="primary"
          busy={saving}
          busyLabel="Saving…"
          disabled={!dirty}
          disabledReason={owner ? null : OWNER_ONLY}
          onClick={() => void save()}
        >
          Save periods
        </Button>
        <Button
          busy={previewing}
          busyLabel="Counting…"
          disabled={dirty || retention.purge_running}
          disabledReason={owner ? null : OWNER_ONLY}
          onClick={() => void runPreview()}
        >
          Preview purge
        </Button>
        {retention.purge_running && <Badge tone="info">A purge is running…</Badge>}
      </div>
      {error !== null && error.fields.length === 0 && <ErrorNotice error={error} />}

      {preview !== null && (
        <div className="stack">
          <DataTable
            caption="What a purge now would delete"
            compact
            rows={COUNT_ROWS}
            rowKey={(row) => row.key}
            columns={[
              { key: 'what', header: 'What', render: (row) => row.label },
              {
                key: 'count',
                header: 'Count',
                numeric: true,
                render: (row) => count(preview.counts[row.key]),
              },
            ]}
          />
          <div className="row-actions">
            <Button
              variant="danger"
              disabled={total(preview.counts) === 0}
              disabledReason={owner ? null : OWNER_ONLY}
              onClick={() => {
                setConfirming(true);
              }}
            >
              Purge…
            </Button>
            <span className="small muted">
              Counted at <Timestamp iso={preview.as_of} zone={me.timezone} mode="time" />. Valid for
              15 minutes.
            </span>
          </div>
        </div>
      )}

      {confirming && preview !== null && (
        <ConfirmPurge
          preview={preview}
          onClose={() => {
            setConfirming(false);
          }}
          onStarted={() => {
            setConfirming(false);
            setPreview(null);
            onPurgeStarted();
            void client.invalidateQueries({ queryKey: [RETENTION] });
          }}
        />
      )}

      <LastPurge retention={retention} />
    </div>
  );
}

function ConfirmPurge({
  preview,
  onClose,
  onStarted,
}: {
  readonly preview: PurgePreview;
  readonly onClose: () => void;
  readonly onStarted: () => void;
}): React.JSX.Element {
  const { me } = useSession();
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const summary = COUNT_ROWS.filter((r) => preview.counts[r.key] > 0)
    .map((r) => `${count(preview.counts[r.key])} ${r.noun}`)
    .join(', ');

  async function purge(): Promise<void> {
    setBusy(true);
    const result = await startPurge(me.csrf_token, preview);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    toast('Purging. The result appears below when it finishes.');
    onStarted();
  }

  return (
    <Dialog
      open
      size="sm"
      onClose={onClose}
      title="Delete this data for good?"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="danger"
            busy={busy}
            busyLabel="Starting…"
            disabled={typed.trim() !== 'PURGE'}
            onClick={() => void purge()}
          >
            Purge
          </Button>
        </>
      }
    >
      <p>This deletes {summary}. It cannot be undone; a backup taken before it still has them.</p>
      <Field label="Type PURGE to confirm" value={typed} onChange={setTyped} autoFocus mono />
      {error !== null && <ErrorNotice error={error} />}
    </Dialog>
  );
}

function LastPurge({ retention }: { readonly retention: Retention }): React.JSX.Element | null {
  const { me } = useSession();
  const last = retention.last_purge;
  if (last === null) return <p className="small muted">No purge has run yet.</p>;
  const deleted = COUNT_ROWS.filter((r) => last.counts[r.key] > 0)
    .map((r) => `${count(last.counts[r.key])} ${r.noun}`)
    .join(', ');
  return (
    <p className="small">
      Last purge (<span>{last.trigger === 'manual' ? 'by hand' : 'nightly'}</span>):{' '}
      <Timestamp iso={last.at} zone={me.timezone} /> — {deleted || 'nothing was due'}.
    </p>
  );
}

// ---------------------------------------------------------------------------
// Backups
// ---------------------------------------------------------------------------

const BACKUP_STATUS: Readonly<
  Record<string, { readonly label: string; readonly tone?: 'ok' | 'warn' | 'error' | 'info' }>
> = {
  running: { label: 'Running…', tone: 'info' },
  ok: { label: 'Complete', tone: 'ok' },
  failed: { label: 'Failed', tone: 'error' },
  pruned: { label: 'Rotated away' },
};

function BackupsPanel(): React.JSX.Element {
  const [watching, setWatching] = useState(false);
  const query = useApi(BACKUPS, null, backupsSchema, {
    refetchInterval: watching ? FAST_MS : SLOW_MS,
  });
  const busy =
    (query.data?.backup_running ?? false) || (query.data?.restore_check_running ?? false);
  useEffect(() => {
    if (query.data !== undefined) setWatching(busy);
  }, [query.data, busy]);
  return (
    <Panel
      query={query}
      kind="table"
      title="Backups"
      description="Nightly, kept 7 days and 4 weeks. Downloading one is the only copy off this machine."
      isEmpty={() => false}
      empty={null}
    >
      {(b) => (
        <BackupsBody
          backups={b}
          onStarted={() => {
            setWatching(true);
          }}
        />
      )}
    </Panel>
  );
}

function BackupsBody({
  backups,
  onStarted,
}: {
  readonly backups: Backups;
  readonly onStarted: () => void;
}): React.JSX.Element {
  const { me } = useSession();
  const client = useQueryClient();
  const owner = me.role === 'owner';
  const [error, setError] = useState<ApiError | null>(null);
  const [starting, setStarting] = useState(false);
  const newest = backups.backups.find((b) => b.status === 'ok' || b.status === 'pruned');
  const check = backups.last_restore_check;

  async function backupNow(): Promise<void> {
    setStarting(true);
    const result = await startBackup(me.csrf_token);
    setStarting(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setError(null);
    onStarted();
    await client.invalidateQueries({ queryKey: [BACKUPS] });
    toast('Backing up. The row below follows it.');
  }

  async function verify(row: Backup): Promise<void> {
    const result = await verifyRestore(me.csrf_token, row.id);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setError(null);
    onStarted();
    await client.invalidateQueries({ queryKey: [BACKUPS] });
    toast('Restoring into a scratch database to check it.');
  }

  return (
    <div className="stack">
      {backups.download.overdue && newest !== undefined && (
        <Alert tone="info" title="Download a backup">
          None has been downloaded in the last {String(backups.download.reminder_days)} days. The
          download is the only copy that survives losing this machine (RISKS R11).
        </Alert>
      )}
      <Stats label="Backup state">
        <Stat
          label="Last backup"
          value={newest ? day(newest.started_at, me.timezone) : '—'}
          note={newest ? bytes(newest.size_bytes) : 'None yet'}
        />
        <Stat
          label="Last restore check"
          value={
            check
              ? check.status === 'passed'
                ? 'Passed'
                : check.status === 'failed'
                  ? 'Failed'
                  : 'Running'
              : '—'
          }
          note={
            check ? <Timestamp iso={check.started_at} zone={me.timezone} /> : 'Monthly, on the 1st'
          }
        />
        <Stat
          label="Last download"
          value={
            backups.download.last_downloaded_at
              ? day(backups.download.last_downloaded_at, me.timezone)
              : 'Never'
          }
          note={backups.download.overdue ? 'Overdue' : 'Within the reminder period'}
        />
      </Stats>
      <div className="row-actions">
        <Button
          variant="primary"
          busy={starting}
          busyLabel="Starting…"
          disabled={backups.backup_running}
          disabledReason={owner ? null : OWNER_ONLY}
          onClick={() => void backupNow()}
        >
          {backups.backup_running ? 'Backing up…' : 'Back up now'}
        </Button>
      </div>
      {error !== null && <ErrorNotice error={error} />}
      {backups.backups.length === 0 ? (
        <p className="muted">No backup has run yet. The first runs tonight, or now.</p>
      ) : (
        <DataTable
          caption="Backups, newest first"
          rows={backups.backups}
          rowKey={(row) => row.id}
          columns={[
            {
              key: 'started',
              header: 'Started',
              render: (row) => (
                <span className="nowrap">
                  <Timestamp iso={row.started_at} zone={me.timezone} />
                </span>
              ),
            },
            {
              key: 'kind',
              header: 'Kind',
              render: (row) => (row.kind === 'manual' ? 'By hand' : 'Nightly'),
            },
            {
              key: 'status',
              header: 'Status',
              render: (row) => {
                const s = BACKUP_STATUS[row.status] ?? { label: row.status };
                return (
                  <>
                    <Badge {...toned(s.tone)}>{s.label}</Badge>
                    {row.error && <span className="small muted"> {row.error}</span>}
                  </>
                );
              },
            },
            {
              key: 'size',
              header: 'Size',
              numeric: true,
              render: (row) => <span className="nowrap">{bytes(row.size_bytes)}</span>,
            },
            {
              key: 'rows',
              header: 'Rows',
              numeric: true,
              render: (row) =>
                row.rows === null ? '—' : `${count(row.rows)} in ${String(row.tables)} tables`,
            },
            {
              key: 'sha',
              header: 'SHA-256',
              render: (row) => (row.sha256 ? <Identifier value={row.sha256} /> : '—'),
            },
            {
              key: 'restored',
              header: 'Restore check',
              render: (row) => <RestoreBadge row={row} />,
            },
            {
              key: 'actions',
              header: <span className="sr-only">Actions</span>,
              render: (row) =>
                row.status === 'ok' ? (
                  <span className="row-actions">
                    {owner ? (
                      <ButtonLink
                        href={backupDownloadUrl(row.id)}
                        size="sm"
                        icon="Download"
                        download
                      >
                        Download
                      </ButtonLink>
                    ) : (
                      <Button size="sm" icon="Download" disabledReason={OWNER_ONLY}>
                        Download
                      </Button>
                    )}
                    <Button
                      size="sm"
                      disabled={backups.restore_check_running}
                      disabledReason={owner ? null : OWNER_ONLY}
                      onClick={() => void verify(row)}
                    >
                      Verify now
                    </Button>
                  </span>
                ) : null,
            },
          ]}
        />
      )}
    </div>
  );
}

function RestoreBadge({ row }: { readonly row: Backup }): React.JSX.Element {
  const check = row.last_restore_check;
  if (check === null) return <span className="small muted">Not yet checked</span>;
  if (check.status === 'running') return <Badge tone="info">Checking…</Badge>;
  if (check.status === 'passed') return <Badge tone="ok">Restored, counts equal</Badge>;
  const tables = check.mismatches ? Object.keys(check.mismatches).join(', ') : '';
  return (
    <>
      <Badge tone="error">Failed</Badge>{' '}
      <span className="small muted">{tables ? `differs in ${tables}` : (check.error ?? '')}</span>
    </>
  );
}
