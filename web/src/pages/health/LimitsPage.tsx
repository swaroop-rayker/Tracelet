/**
 * System health › Rate limits (DESIGN §16 M7; F11.AC9).
 *
 * Every limit with what it protects, its value in words, and whether it was changed. **Edit**
 * opens a dialog with the three numbers and "Back to default". No redeploy: each worker picks
 * the change up within `applies_within_seconds`. An outbound limit states its third party's
 * own ceiling, which the server refuses to exceed.
 */

import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import type { ApiError } from '@/api/client';
import { useApi } from '@/api/query';
import { HEALTH, rateLimitsSchema, updateRateLimits, type RateLimit } from '@/api/system';
import { Panel } from '@/components/Panel';
import { Badge, Button, DataTable, Dialog, ErrorNotice, Field, toast } from '@/components/ui';
import { OWNER_ONLY, limitInWords } from '@/pages/health/health-format';
import { useSession } from '@/session';

const PATH = `${HEALTH}/ratelimits`;

const GROUPS: readonly { readonly group: string; readonly label: string }[] = [
  { group: 'capture', label: 'Visitors' },
  { group: 'admin', label: 'Admins' },
  { group: 'outbound', label: 'Outbound lookups' },
];

export default function LimitsPage(): React.JSX.Element {
  const { me } = useSession();
  const query = useApi(PATH, null, rateLimitsSchema);
  const [editing, setEditing] = useState<RateLimit | null>(null);
  return (
    <>
      <Panel
        query={query}
        kind="table"
        title="Limits"
        description="Over a visitor limit, the visitor is still redirected, just not captured (F11.AC3)."
        isEmpty={(d) => d.limits.length === 0}
        empty={<p className="muted">No limits are registered.</p>}
      >
        {(d) => (
          <div className="stack">
            <p className="small muted">
              Changes apply within {String(d.applies_within_seconds)} seconds, with no restart.
            </p>
            {GROUPS.map((g) => {
              const rows = d.limits.filter((l) => l.group === g.group);
              if (rows.length === 0) return null;
              return (
                <DataTable
                  key={g.group}
                  caption={g.label}
                  rows={rows}
                  rowKey={(row) => row.name}
                  columns={[
                    {
                      key: 'label',
                      header: g.label,
                      render: (row) => (
                        <>
                          <strong>{row.label}</strong>
                          <br />
                          <span className="small muted">{row.description}</span>
                        </>
                      ),
                    },
                    {
                      key: 'value',
                      header: 'Limit',
                      render: (row) => (
                        <>
                          {limitInWords(row)}
                          {row.overridden && (
                            <>
                              <br />
                              <Badge tone="info">Edited</Badge>{' '}
                              <span className="small muted">
                                default {limitInWords(row.default)}
                              </span>
                            </>
                          )}
                        </>
                      ),
                    },
                    {
                      key: 'edit',
                      header: <span className="sr-only">Edit</span>,
                      render: (row) => (
                        <Button
                          size="sm"
                          disabledReason={me.role === 'owner' ? null : OWNER_ONLY}
                          onClick={() => {
                            setEditing(row);
                          }}
                        >
                          Edit
                        </Button>
                      ),
                    },
                  ]}
                />
              );
            })}
          </div>
        )}
      </Panel>
      {editing !== null && (
        <EditLimit
          limit={editing}
          onClose={() => {
            setEditing(null);
          }}
        />
      )}
    </>
  );
}

function EditLimit({
  limit,
  onClose,
}: {
  readonly limit: RateLimit;
  readonly onClose: () => void;
}): React.JSX.Element {
  const { me } = useSession();
  const client = useQueryClient();
  const [perPeriod, setPerPeriod] = useState(String(limit.per_period));
  const [period, setPeriod] = useState(String(limit.period_seconds));
  const [burst, setBurst] = useState(String(limit.burst));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function save(
    value: { per_period: number; period_seconds: number; burst: number } | null,
  ): Promise<void> {
    setBusy(true);
    const result = await updateRateLimits(me.csrf_token, { [limit.name]: value });
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    client.setQueryData([PATH, ''], result.data);
    toast(value === null ? `${limit.label}: back to the default.` : `${limit.label}: saved.`);
    onClose();
  }

  const reason = error?.fields[0]?.message ?? null;
  return (
    <Dialog
      open
      size="sm"
      onClose={onClose}
      title={limit.label}
      footer={
        <>
          {limit.overridden && (
            <Button disabled={busy} onClick={() => void save(null)}>
              Back to default
            </Button>
          )}
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            busy={busy}
            busyLabel="Saving…"
            onClick={() =>
              void save({
                per_period: Number(perPeriod),
                period_seconds: Number(period),
                burst: Number(burst),
              })
            }
          >
            Save
          </Button>
        </>
      }
    >
      <p className="small muted">{limit.description}</p>
      <div className="form-grid">
        <Field
          label="Requests"
          type="number"
          inputMode="numeric"
          value={perPeriod}
          onChange={setPerPeriod}
          autoFocus
        />
        <Field
          label="Per seconds"
          type="number"
          inputMode="numeric"
          value={period}
          onChange={setPeriod}
          hint="60 is a minute, 3600 an hour, 86400 a day."
        />
        <Field
          label="Burst"
          type="number"
          inputMode="numeric"
          value={burst}
          onChange={setBurst}
          hint="How many may come back to back."
        />
      </div>
      {limit.ceiling_per_second !== null && (
        <p className="small">
          The third party allows at most{' '}
          {limit.ceiling_per_second >= 1
            ? `${String(limit.ceiling_per_second)} a second`
            : `${String(Math.round(limit.ceiling_per_second * 86_400))} a day`}
          ; no setting may exceed it.
        </p>
      )}
      <p className="small muted">Default: {limitInWords(limit.default)}.</p>
      {reason !== null ? (
        <p className="error-text small" role="alert">
          {reason}
        </p>
      ) : (
        error !== null && <ErrorNotice error={error} />
      )}
    </Dialog>
  );
}
