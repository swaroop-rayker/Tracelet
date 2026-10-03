/**
 * The account page: who you are, your password, your sessions, your recovery channel.
 *
 * Built in M1 as the whole authenticated shell, and moved under the dashboard layout in
 * M5 (which now owns sign-out and the theme). Everything F8 promises stays reachable
 * from a browser (F10.AC9), because a security control nobody can operate is not a
 * control.
 *
 * Role is reflected here, never decided here (F8.AC12): the owner-only panel is
 * hidden for an analyst *and* every route behind it is refused server-side. The
 * hiding is courtesy; the refusal is the control.
 */

import { useCallback, useEffect, useState } from 'react';
import {
  changePassword,
  confirmTelegramVerification,
  fetchAdmins,
  fetchSessions,
  regenerateRecoveryCodes,
  revokeSession,
  startTelegramVerification,
  type AdminSummary,
  type Me,
  type SessionSummary,
} from '@/api/auth';
import { fieldMessage, type ApiError } from '@/api/client';
import { fetchReadiness, type ApiResult, type Readiness } from '@/api/health';
import { Callout, Empty, ErrorNotice, Field, Loading, Submit } from '@/components/ui';
import { useSession } from '@/session';
import { PageHeader } from '@/components/shell/PageHeader';
import { RecoveryCodes } from '@/components/RecoveryCodes';

const LOW_CODES_WARNING = 3;

export default function AccountPage(): React.JSX.Element {
  const { me, onSignedOut, onRefresh } = useSession();
  return (
    <div className="page account">
      <PageHeader
        title="Account & security"
        description={`${me.display_name} · ${me.email} · ${me.role}`}
      />

      <AccountPanel me={me} onRefresh={onRefresh} />
      <PasswordPanel csrfToken={me.csrf_token} onChanged={onRefresh} />
      <RecoveryCodesPanel me={me} onRefresh={onRefresh} />
      <TelegramPanel me={me} onRefresh={onRefresh} />
      <SessionsPanel csrfToken={me.csrf_token} onSignedOut={onSignedOut} />
      {me.role === 'owner' && <AdminsPanel />}
      <ReadinessPanel />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Account
// ---------------------------------------------------------------------------

function AccountPanel({
  me,
  onRefresh,
}: {
  readonly me: Me;
  readonly onRefresh: () => void;
}): React.JSX.Element {
  return (
    <section aria-labelledby="account-heading">
      <h2 id="account-heading">Account</h2>
      <div className="card">
        <dl className="pairs">
          <dt>Role</dt>
          <dd>{me.role}</dd>
          <dt>Status</dt>
          <dd>{me.status}</dd>
          <dt>Two-factor</dt>
          <dd>{me.totp_enrolled ? 'Enrolled' : 'Not enrolled'}</dd>
          <dt>Telegram recovery</dt>
          <dd>{me.telegram_verified ? 'Verified' : 'Not verified'}</dd>
          <dt>Recovery codes left</dt>
          <dd>{String(me.recovery_codes_remaining)}</dd>
          <dt>Timezone</dt>
          <dd>{me.timezone}</dd>
          <dt>This session expires</dt>
          <dd>{formatWhen(me.session_expires_at)}</dd>
        </dl>
        <button type="button" onClick={onRefresh}>
          Refresh
        </button>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Password
// ---------------------------------------------------------------------------

function PasswordPanel({
  csrfToken,
  onChanged,
}: {
  readonly csrfToken: string;
  readonly onChanged: () => void;
}): React.JSX.Element {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [repeat, setRepeat] = useState('');
  const [mismatch, setMismatch] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [done, setDone] = useState(false);

  async function submit(): Promise<void> {
    setMismatch(null);
    setDone(false);
    if (next !== repeat) {
      setMismatch('The two passwords do not match.');
      return;
    }
    setBusy(true);
    setError(null);
    const result = await changePassword(csrfToken, current, next);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setCurrent('');
    setNext('');
    setRepeat('');
    setDone(true);
    onChanged();
  }

  return (
    <section aria-labelledby="password-heading">
      <h2 id="password-heading">Password</h2>

      {done && (
        <Callout tone="ok" title="Password changed">
          <p>Every other session has been signed out. This one is still active.</p>
        </Callout>
      )}
      {error !== null && error.code !== 'VALIDATION_FAILED' && <ErrorNotice error={error} />}

      <form
        className="card stack"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <Field
          label="Current password"
          type="password"
          autoComplete="current-password"
          value={current}
          onChange={setCurrent}
        />
        <Field
          label="New password"
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={setNext}
          hint="At least 12 characters. Not your account name, not the site name."
          error={fieldMessage(error, 'new_password')}
        />
        <Field
          label="Repeat new password"
          type="password"
          autoComplete="new-password"
          value={repeat}
          onChange={setRepeat}
          error={mismatch}
        />
        <Submit busy={busy} busyLabel="Changing…">
          Change password
        </Submit>
      </form>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Recovery codes
// ---------------------------------------------------------------------------

function RecoveryCodesPanel({
  me,
  onRefresh,
}: {
  readonly me: Me;
  readonly onRefresh: () => void;
}): React.JSX.Element {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [fresh, setFresh] = useState<readonly string[] | null>(null);

  return (
    <section aria-labelledby="codes-heading">
      <h2 id="codes-heading">Recovery codes</h2>

      {me.recovery_codes_remaining <= LOW_CODES_WARNING && (
        <Callout tone="warn" title="Running low">
          <p>
            {String(me.recovery_codes_remaining)} left. Regenerate now rather than when you need
            one.
          </p>
        </Callout>
      )}

      {error !== null && <ErrorNotice error={error} />}

      {fresh !== null ? (
        <Callout tone="warn" title="New codes — shown once">
          <p>The previous set no longer works. Save these before leaving this page.</p>
          <RecoveryCodes codes={fresh} />
        </Callout>
      ) : (
        <div className="card">
          <p className="muted">
            {String(me.recovery_codes_remaining)} unused code
            {me.recovery_codes_remaining === 1 ? '' : 's'}. Regenerating invalidates every existing
            code immediately.
          </p>
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              void (async () => {
                setBusy(true);
                setError(null);
                const result = await regenerateRecoveryCodes(me.csrf_token);
                setBusy(false);
                if (!result.ok) {
                  setError(result.error);
                  return;
                }
                setFresh(result.data);
                onRefresh();
              })();
            }}
          >
            {busy ? 'Generating…' : 'Regenerate recovery codes'}
          </button>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Telegram
// ---------------------------------------------------------------------------

/**
 * Verifies a chat before it is trusted for password recovery.
 *
 * Proving reachability first matters: an unverified chat id means reset links go
 * somewhere that may not be yours, and you would only find out when you needed it.
 */
function TelegramPanel({
  me,
  onRefresh,
}: {
  readonly me: Me;
  readonly onRefresh: () => void;
}): React.JSX.Element {
  const [chatId, setChatId] = useState('');
  const [code, setCode] = useState('');
  const [phase, setPhase] = useState<'idle' | 'awaiting-code'>('idle');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [invalid, setInvalid] = useState<string | null>(null);

  async function start(): Promise<void> {
    setInvalid(null);
    const parsed = Number.parseInt(chatId.trim(), 10);
    if (!Number.isFinite(parsed) || chatId.trim().length === 0) {
      setInvalid('A chat id is a number. Ask @userinfobot in Telegram for yours.');
      return;
    }
    setBusy(true);
    setError(null);
    const result = await startTelegramVerification(me.csrf_token, parsed);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setPhase('awaiting-code');
  }

  async function confirm(): Promise<void> {
    setBusy(true);
    setError(null);
    const result = await confirmTelegramVerification(me.csrf_token, code);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setPhase('idle');
    setCode('');
    setChatId('');
    onRefresh();
  }

  return (
    <section aria-labelledby="telegram-heading">
      <h2 id="telegram-heading">Telegram recovery channel</h2>

      {me.telegram_verified ? (
        <Callout tone="ok" title="Verified">
          <p>
            Password reset links will be delivered to your verified chat. Verifying a different chat
            replaces it.
          </p>
        </Callout>
      ) : (
        <Callout tone="warn" title="Not verified">
          <p>
            Without a verified chat you cannot reset your password from the sign-in page. Your
            recovery codes and the server CLI remain, but this is the convenient one.
          </p>
        </Callout>
      )}

      {error !== null && <ErrorNotice error={error} />}

      {phase === 'idle' ? (
        <form
          className="card stack"
          onSubmit={(event) => {
            event.preventDefault();
            void start();
          }}
        >
          <Field
            label="Telegram chat id"
            value={chatId}
            onChange={setChatId}
            inputMode="numeric"
            hint="Message the bot first, then ask @userinfobot for your numeric id."
            error={invalid}
          />
          <Submit busy={busy} busyLabel="Sending a code…">
            Send a verification code
          </Submit>
        </form>
      ) : (
        <form
          className="card stack"
          onSubmit={(event) => {
            event.preventDefault();
            void confirm();
          }}
        >
          <Callout tone="info" title="Check Telegram">
            <p>Enter the six-digit code the bot just sent to that chat.</p>
          </Callout>
          <Field
            label="Verification code"
            value={code}
            onChange={setCode}
            inputMode="numeric"
            placeholder="135790"
            maxLength={10}
            error={fieldMessage(error, 'code')}
          />
          <Submit busy={busy} busyLabel="Verifying…">
            Verify this chat
          </Submit>
          <button
            type="button"
            className="link"
            onClick={() => {
              setPhase('idle');
              setError(null);
            }}
          >
            Use a different chat
          </button>
        </form>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------

type Load<T> =
  | { readonly phase: 'loading' }
  | { readonly phase: 'error'; readonly error: ApiError }
  | { readonly phase: 'ready'; readonly data: T };

function SessionsPanel({
  csrfToken,
  onSignedOut,
}: {
  readonly csrfToken: string;
  readonly onSignedOut: () => void;
}): React.JSX.Element {
  const [state, setState] = useState<Load<readonly SessionSummary[]>>({ phase: 'loading' });
  const [revoking, setRevoking] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    setState({ phase: 'loading' });
    const result = await fetchSessions();
    setState(
      result.ok ? { phase: 'ready', data: result.data } : { phase: 'error', error: result.error },
    );
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section aria-labelledby="sessions-heading">
      <h2 id="sessions-heading">Active sessions</h2>

      {state.phase === 'loading' && <Loading label="Loading sessions…" />}
      {state.phase === 'error' && <ErrorNotice error={state.error} />}
      {state.phase === 'ready' &&
        (state.data.length === 0 ? (
          <Empty>
            No active sessions. That is unexpected while you are reading this — reload the page.
          </Empty>
        ) : (
          <ul className="plain stack-sm">
            {state.data.map((session) => (
              <li key={session.id} className="card row">
                <div>
                  <p>
                    {session.current ? <strong>This device</strong> : 'Another device'}
                    {session.ip_prefix !== null && (
                      <span className="muted mono"> · {session.ip_prefix}</span>
                    )}
                  </p>
                  <p className="muted small">
                    started {formatWhen(session.created_at)} · last seen{' '}
                    {formatWhen(session.last_seen_at)} · expires {formatWhen(session.expires_at)}
                  </p>
                </div>
                <button
                  type="button"
                  disabled={revoking === session.id}
                  onClick={() => {
                    void (async () => {
                      setRevoking(session.id);
                      const result = await revokeSession(csrfToken, session.id);
                      setRevoking(null);
                      if (!result.ok) {
                        setState({ phase: 'error', error: result.error });
                        return;
                      }
                      if (session.current) {
                        // Revoking your own session is a sign-out, so say so rather
                        // than leaving a dead page that 401s on the next click.
                        onSignedOut();
                        return;
                      }
                      await load();
                    })();
                  }}
                >
                  {revoking === session.id ? 'Revoking…' : session.current ? 'Sign out' : 'Revoke'}
                </button>
              </li>
            ))}
          </ul>
        ))}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Admins (owner only)
// ---------------------------------------------------------------------------

function AdminsPanel(): React.JSX.Element {
  const [state, setState] = useState<Load<readonly AdminSummary[]>>({ phase: 'loading' });

  useEffect(() => {
    void (async () => {
      const result = await fetchAdmins();
      setState(
        result.ok ? { phase: 'ready', data: result.data } : { phase: 'error', error: result.error },
      );
    })();
  }, []);

  return (
    <section aria-labelledby="admins-heading">
      <h2 id="admins-heading">Admins</h2>

      {state.phase === 'loading' && <Loading label="Loading admins…" />}
      {state.phase === 'error' && <ErrorNotice error={state.error} />}
      {state.phase === 'ready' &&
        (state.data.length === 0 ? (
          <Empty>No admins, which cannot be true while you are signed in as one.</Empty>
        ) : (
          <ul className="plain stack-sm">
            {state.data.map((admin) => (
              <li key={admin.id} className="card">
                <p>
                  <strong>{admin.display_name}</strong>{' '}
                  <span className="muted mono small">{admin.email}</span>
                </p>
                <p className="muted small">
                  {admin.role} · {admin.status} ·{' '}
                  {admin.totp_enrolled ? 'two-factor enrolled' : 'two-factor pending'}
                  {admin.last_login_at !== null &&
                    ` · last signed in ${formatWhen(admin.last_login_at)}`}
                  {admin.locked_until !== null && ' · locked'}
                </p>
              </li>
            ))}
          </ul>
        ))}

      <p className="muted small">
        {/* Invitation and role changes are owner-only API routes that M1 ships and M5
            gives a full management screen. Listed here so the state is visible now. */}
        Inviting and editing admins is available through the API in M1; the management screen
        arrives with the dashboard in M5.
      </p>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Readiness
// ---------------------------------------------------------------------------

/**
 * The three readiness checks, shown where an operator will actually see them.
 *
 * A 503 from /readyz is a legitimate answer -- an instance behind its own migrations
 * reports that accurately rather than claiming ready -- so it renders as data.
 */
function ReadinessPanel(): React.JSX.Element {
  const [result, setResult] = useState<ApiResult<Readiness> | null>(null);

  useEffect(() => {
    void (async () => {
      setResult(await fetchReadiness());
    })();
  }, []);

  return (
    <section aria-labelledby="readiness-heading">
      <h2 id="readiness-heading">System readiness</h2>
      {result === null && <Loading label="Checking…" />}
      {result !== null && result.kind === 'failure' && (
        <Callout tone="error" title="Unavailable">
          <p>{result.message}</p>
          {result.traceId !== null && <p className="muted mono">trace {result.traceId}</p>}
        </Callout>
      )}
      {result !== null && result.kind === 'success' && (
        <div className={result.data.ready ? 'card ok' : 'card warn'}>
          <strong>{result.data.ready ? 'Ready' : 'Not ready'}</strong>
          <p className="muted small">environment: {result.data.environment}</p>
          <ul className="checks">
            {Object.entries(result.data.checks).map(([name, check]) => (
              <li key={name}>
                <span aria-hidden="true">{check.ok ? '✓' : '✗'}</span>
                {/* Never colour alone -- NFR7.AC3 applies to status as much as charts. */}
                <span className="sr-only">{check.ok ? 'pass' : 'fail'}</span>
                <code>{name}</code>
                <span className="muted">{check.detail}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------

function formatWhen(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  // The admin's own locale and zone. Timestamps from the API are always UTC with an
  // offset, so there is nothing to guess.
  return parsed.toLocaleString();
}
