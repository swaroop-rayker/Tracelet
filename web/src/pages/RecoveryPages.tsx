/**
 * The three ways back in when something has been lost (ADR-0008).
 *
 * Each fails differently, which is why there are three: a recovery code can be lost,
 * a Telegram account can be locked out, and the break-glass CLI needs shell access.
 * Only the first two are reachable from a browser; the third is documented here so an
 * admin who has run out of options knows it exists.
 */

import { useState } from 'react';
import { confirmPasswordReset, requestPasswordReset, signInWithRecoveryCode } from '@/api/auth';
import { fieldMessage, type ApiError } from '@/api/client';
import { Callout, ErrorNotice, Field, Submit } from '@/components/ui';
import { navigate } from '@/router';
import { BrandMark } from '@/components/shell/BrandMark';

function Shell({
  subtitle,
  children,
}: {
  readonly subtitle: string;
  readonly children: React.ReactNode;
}): React.JSX.Element {
  return (
    <main className="auth">
      <header>
        <h1 className="auth__brand">
          <BrandMark />
          Tracelet
        </h1>
        <p className="muted">{subtitle}</p>
      </header>
      {children}
      <nav className="stack-sm small">
        <button
          type="button"
          className="link"
          onClick={() => {
            navigate({ name: 'login' });
          }}
        >
          Back to sign in
        </button>
      </nav>
    </main>
  );
}

// ---------------------------------------------------------------------------
// Recovery code
// ---------------------------------------------------------------------------

/**
 * Signs in bypassing both the password and the authenticator.
 *
 * That is the whole point of a recovery code, and why it is single-use and
 * rate-limited far more tightly than a password: each attempt costs ten Argon2
 * verifications at 32 MiB, so the endpoint is a memory-amplification vector as well
 * as a credential one.
 */
export function RecoveryCodePage({
  onSignedIn,
}: {
  readonly onSignedIn: () => void;
}): React.JSX.Element {
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function submit(): Promise<void> {
    setBusy(true);
    setError(null);
    const result = await signInWithRecoveryCode(email, code);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    onSignedIn();
  }

  return (
    <Shell subtitle="Sign in with a recovery code">
      <Callout tone="info" title="One of your ten codes">
        <p>
          Case and spacing do not matter. Each code works once, and using one does not change your
          password — set a new one afterwards if you have forgotten it.
        </p>
      </Callout>

      {error !== null && <ErrorNotice error={error} />}

      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <Field
          label="Email"
          type="email"
          inputMode="email"
          autoComplete="username"
          value={email}
          onChange={setEmail}
        />
        <Field
          label="Recovery code"
          value={code}
          onChange={setCode}
          autoComplete="one-time-code"
          placeholder="ABCDE-FGHJK"
          maxLength={32}
          error={fieldMessage(error, 'code')}
        />
        <Submit busy={busy} busyLabel="Checking…">
          Sign in
        </Submit>
      </form>

      <Callout tone="warn" title="Out of codes and out of Telegram?">
        <p>
          Someone with server access can run{' '}
          <code className="mono">tracelet admin reset-password</code>. It is the last resort, and it
          is audited like everything else.
        </p>
      </Callout>
    </Shell>
  );
}

// ---------------------------------------------------------------------------
// Reset request
// ---------------------------------------------------------------------------

/**
 * Asks for a reset link over Telegram (F8.AC7).
 *
 * The response is **always** the same, whether or not the account exists, whether or
 * not it has a verified chat, and whether or not delivery worked. Saying any of those
 * out loud would be a reliable account-enumeration oracle, so the page says what it
 * can honestly say: if there is an account with a verified chat, a link is on its way.
 */
export function ResetRequestPage(): React.JSX.Element {
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function submit(): Promise<void> {
    setBusy(true);
    setError(null);
    const result = await requestPasswordReset(email);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setSent(true);
  }

  if (sent) {
    return (
      <Shell subtitle="Check Telegram">
        <Callout tone="ok" title="Request received">
          <p>
            If that address belongs to an account with a verified Telegram chat, a reset link is on
            its way. It works once and expires in 30 minutes.
          </p>
          <p className="muted">
            You will still need your authenticator code to sign in afterwards. If you have lost it,
            use a recovery code instead.
          </p>
        </Callout>
      </Shell>
    );
  }

  return (
    <Shell subtitle="Request a password reset">
      <Callout tone="info" title="Delivered over Telegram">
        <p>
          This system sends no email. A reset link goes to the Telegram chat you verified — and only
          if you verified one.
        </p>
      </Callout>

      {error !== null && <ErrorNotice error={error} />}

      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <Field
          label="Email"
          type="email"
          inputMode="email"
          autoComplete="username"
          value={email}
          onChange={setEmail}
        />
        <Submit busy={busy} busyLabel="Sending…">
          Send a reset link
        </Submit>
      </form>
    </Shell>
  );
}

// ---------------------------------------------------------------------------
// Reset confirm
// ---------------------------------------------------------------------------

/**
 * `/reset?token=…` — set a new password from a Telegram-delivered link.
 *
 * A rejected password does **not** consume the link. That was a real bug
 * (docs/ERRORS.md E15): on a 30-minute token, being told "too common" and then having
 * to request another link is a genuinely bad afternoon.
 */
export function ResetConfirmPage({
  token,
  onReset,
}: {
  readonly token: string | null;
  readonly onReset: () => void;
}): React.JSX.Element {
  const [password, setPassword] = useState('');
  const [repeat, setRepeat] = useState('');
  const [mismatch, setMismatch] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  if (token === null || token.length === 0) {
    return (
      <Shell subtitle="Set a new password">
        <Callout tone="error" title="This link is incomplete">
          <p>
            The address is missing its one-time token. Open the link from Telegram exactly as it
            arrived, or request a new one.
          </p>
        </Callout>
      </Shell>
    );
  }

  // Narrowed above, but a hoisted function declaration cannot see that, so the
  // narrowing is captured explicitly rather than asserted away.
  const liveToken: string = token;

  async function submit(): Promise<void> {
    setMismatch(null);
    if (password !== repeat) {
      setMismatch('The two passwords do not match.');
      return;
    }
    setBusy(true);
    setError(null);
    const result = await confirmPasswordReset(liveToken, password);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    onReset();
  }

  return (
    <Shell subtitle="Set a new password">
      <Callout tone="info" title="Every session will be signed out">
        <p>
          Completing this revokes all active sessions for the account. You will still need your
          authenticator code to sign in.
        </p>
      </Callout>

      {error !== null && error.code !== 'VALIDATION_FAILED' && <ErrorNotice error={error} />}
      {error !== null && error.code === 'NOT_FOUND' && (
        <Callout tone="error" title="This link is no longer valid">
          <p>It has expired or has already been used. Request a new one.</p>
        </Callout>
      )}

      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <Field
          label="New password"
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={setPassword}
          hint="At least 12 characters. Not your account name, not the site name."
          error={fieldMessage(error, 'new_password')}
        />
        <Field
          label="Repeat password"
          type="password"
          autoComplete="new-password"
          value={repeat}
          onChange={setRepeat}
          error={mismatch}
        />
        <Submit busy={busy} busyLabel="Setting your password…">
          Set password
        </Submit>
      </form>
    </Shell>
  );
}
