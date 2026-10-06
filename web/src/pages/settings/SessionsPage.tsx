/**
 * Settings › Sessions (DESIGN §10.8): every device signed in to your account. Revoking one,
 * or every other one, asks first (UI-16). "Sign out all other sessions" is one
 * `DELETE /auth/sessions/{id}` per session -- the same request a single Revoke sends -- so
 * there is no new endpoint and no new rule.
 */

import { useCallback, useEffect, useState } from 'react';
import { fetchSessions, revokeSession, type SessionSummary } from '@/api/auth';
import type { ApiError } from '@/api/client';
import {
  Badge,
  Button,
  Card,
  DataTable,
  Empty,
  ErrorNotice,
  Loading,
  Timestamp,
  toast,
} from '@/components/ui';
import { ConfirmDialog } from '@/pages/settings/dialogs';
import { useSession } from '@/session';

type Load =
  | { readonly phase: 'loading' }
  | { readonly phase: 'error'; readonly error: ApiError }
  | { readonly phase: 'ready'; readonly data: readonly SessionSummary[] };

type Pending =
  | { readonly kind: 'one'; readonly session: SessionSummary }
  | { readonly kind: 'others'; readonly sessions: readonly SessionSummary[] };

export default function SessionsPage(): React.JSX.Element {
  const { me, onSignedOut } = useSession();
  const [state, setState] = useState<Load>({ phase: 'loading' });
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const load = useCallback(async (): Promise<void> => {
    const result = await fetchSessions();
    setState(
      result.ok ? { phase: 'ready', data: result.data } : { phase: 'error', error: result.error },
    );
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const others = state.phase === 'ready' ? state.data.filter((s) => !s.current) : [];

  async function confirm(): Promise<void> {
    if (pending === null) return;
    const targets = pending.kind === 'one' ? [pending.session] : pending.sessions;
    setBusy(true);
    setError(null);
    for (const session of targets) {
      const result = await revokeSession(me.csrf_token, session.id);
      if (!result.ok) {
        setBusy(false);
        setError(result.error);
        void load();
        return;
      }
    }
    setBusy(false);
    setPending(null);
    // Revoking your own session is a sign-out: say so, rather than leave a page that 401s.
    if (targets.some((s) => s.current)) {
      onSignedOut();
      return;
    }
    toast(
      targets.length === 1 ? 'Session revoked.' : `${String(targets.length)} sessions revoked.`,
    );
    void load();
  }

  return (
    <Card
      title="Signed-in devices"
      description="Each sign-in is its own session, tied to its browser and network."
      actions={
        <Button
          variant="secondary"
          disabled={others.length === 0}
          onClick={() => {
            setError(null);
            setPending({ kind: 'others', sessions: others });
          }}
        >
          Sign out all other sessions
        </Button>
      }
    >
      {state.phase === 'loading' && <Loading kind="table" label="Loading sessions…" />}
      {state.phase === 'error' && <ErrorNotice error={state.error} />}
      {state.phase === 'ready' &&
        (state.data.length === 0 ? (
          <Empty>
            No active sessions, which cannot be right while you read this. Reload the page.
          </Empty>
        ) : (
          <DataTable
            caption="Signed-in devices"
            rowKey={(s) => s.id}
            rows={state.data}
            columns={[
              {
                key: 'device',
                header: 'Device',
                render: (s) =>
                  s.current ? <Badge tone="info">This device</Badge> : 'Another device',
              },
              {
                key: 'network',
                header: 'Network',
                render: (s) => <span className="t-mono">{s.ip_prefix ?? '—'}</span>,
              },
              {
                key: 'started',
                header: 'Signed in',
                render: (s) => <Timestamp iso={s.created_at} zone={me.timezone} />,
              },
              {
                key: 'seen',
                header: 'Last active',
                render: (s) => <Timestamp iso={s.last_seen_at} zone={me.timezone} />,
              },
              {
                key: 'expires',
                header: 'Expires',
                render: (s) => <Timestamp iso={s.expires_at} zone={me.timezone} />,
              },
              {
                key: 'action',
                header: <span className="sr-only">Actions</span>,
                render: (s) => (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setError(null);
                      setPending({ kind: 'one', session: s });
                    }}
                  >
                    {s.current ? 'Sign out' : 'Revoke'}
                  </Button>
                ),
              },
            ]}
          />
        ))}

      <ConfirmDialog
        open={pending !== null}
        title={
          pending?.kind === 'others'
            ? 'Sign out all other sessions?'
            : pending?.session.current === true
              ? 'Sign out of this device?'
              : 'Revoke this session?'
        }
        confirmLabel={
          pending?.kind === 'others'
            ? 'Sign out others'
            : pending?.session.current === true
              ? 'Sign out'
              : 'Revoke'
        }
        busyLabel="Signing out…"
        busy={busy}
        error={error}
        onClose={() => {
          setPending(null);
        }}
        onConfirm={() => {
          void confirm();
        }}
      >
        {pending?.kind === 'others'
          ? `${String(pending.sessions.length)} other session${pending.sessions.length === 1 ? ' is' : 's are'} signed out at once. This device stays signed in.`
          : pending?.session.current === true
            ? 'You will be signed out here and returned to the sign-in page.'
            : 'That device is signed out at once and has to sign in again.'}
      </ConfirmDialog>
    </Card>
  );
}
