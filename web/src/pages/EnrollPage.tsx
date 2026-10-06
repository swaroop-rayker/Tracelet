/**
 * `/enroll?token=…` — the one-time invitation link (F8.AC15).
 *
 * Three steps, and the middle one is the only place in the system where a secret is
 * ever shown:
 *
 * 1. choose a password;
 * 2. add the account to an authenticator app, from the base32 secret;
 * 3. type a code, which activates the account and signs in.
 *
 * **The recovery codes are displayed exactly once.** That is the correct security
 * property and a real operational hazard, so the warning is prominent and the step
 * requires an explicit acknowledgement rather than a link that is easy to click past.
 *
 * **No QR code**, deliberately: rendering one means another dependency (ES5) to save
 * one or two admins a single manual entry, and every authenticator app accepts a
 * typed base32 secret. The `otpauth://` URI is offered as a link for the case where
 * the browser and the authenticator are on the same device.
 */

import { useState } from 'react';
import { confirmTotp, enroll, type Enrollment } from '@/api/auth';
import { fieldMessage, type ApiError } from '@/api/client';
import { Callout, ErrorNotice, Field, Secret, Submit } from '@/components/ui';
import { navigate } from '@/router';
import { BrandMark } from '@/components/shell/BrandMark';
import { PasswordRules } from '@/components/PasswordRules';
import { RecoveryCodes } from '@/components/RecoveryCodes';

type Phase =
  | { readonly step: 'password' }
  | { readonly step: 'authenticator'; readonly enrollment: Enrollment }
  | { readonly step: 'codes'; readonly enrollment: Enrollment };

export default function EnrollPage({
  token,
  onSignedIn,
}: {
  readonly token: string | null;
  readonly onSignedIn: () => void;
}): React.JSX.Element {
  const [phase, setPhase] = useState<Phase>({ step: 'password' });

  if (token === null || token.length === 0) {
    return (
      <AuthShell title="Finish setting up your account">
        <Callout tone="error" title="This link is incomplete">
          <p>
            The address is missing its one-time token. Open the enrolment link exactly as it was
            given to you, or ask an owner to issue a fresh one.
          </p>
        </Callout>
      </AuthShell>
    );
  }

  if (phase.step === 'password') {
    return (
      <AuthShell title="Choose a password" step={1}>
        <PasswordStep
          token={token}
          onEnrolled={(enrollment) => {
            setPhase({ step: 'authenticator', enrollment });
          }}
        />
      </AuthShell>
    );
  }

  if (phase.step === 'authenticator') {
    return (
      <AuthShell title="Add your authenticator" step={2}>
        <AuthenticatorStep
          enrollment={phase.enrollment}
          onConfirmed={() => {
            setPhase({ step: 'codes', enrollment: phase.enrollment });
          }}
        />
      </AuthShell>
    );
  }

  return (
    <AuthShell title="Save your recovery codes" step={3}>
      <RecoveryCodesStep codes={phase.enrollment.recovery_codes} onDone={onSignedIn} />
    </AuthShell>
  );
}

const STEPS = ['Password', 'Authenticator', 'Codes'] as const;

function AuthShell({
  title,
  step,
  children,
}: {
  readonly title: string;
  /** Which of the three steps this is (DESIGN §10.9, §12 E28). */
  readonly step?: 1 | 2 | 3;
  readonly children: React.ReactNode;
}): React.JSX.Element {
  return (
    <main className="auth">
      <header>
        <h1 className="auth__brand">
          <BrandMark />
          Tracelet
        </h1>
        <p className="muted">{title}</p>
        {step !== undefined && (
          <ol className="steps" aria-label={`Step ${String(step)} of 3`}>
            {STEPS.map((name, i) => (
              <li
                key={name}
                className={i + 1 === step ? 'is-current' : i + 1 < step ? 'is-done' : undefined}
                aria-current={i + 1 === step ? 'step' : undefined}
              >
                <span className="steps__n">{i + 1}</span>
                {name}
              </li>
            ))}
          </ol>
        )}
      </header>
      {children}
    </main>
  );
}

// ---------------------------------------------------------------------------
// Step 1
// ---------------------------------------------------------------------------

function PasswordStep({
  token,
  onEnrolled,
}: {
  readonly token: string;
  readonly onEnrolled: (enrollment: Enrollment) => void;
}): React.JSX.Element {
  const [password, setPassword] = useState('');
  const [repeat, setRepeat] = useState('');
  const [mismatch, setMismatch] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function submit(): Promise<void> {
    setMismatch(null);
    if (password !== repeat) {
      // Checked here rather than server-side: the API has no second field to
      // compare against, and a round trip to say "they do not match" is wasteful.
      setMismatch('The two passwords do not match.');
      return;
    }
    setBusy(true);
    setError(null);
    const result = await enroll(token, password);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    onEnrolled(result.data);
  }

  return (
    <>
      <Callout tone="info" title="One-time link">
        <p>
          This link works once. Nobody — including the owner who invited you — has set or can see a
          password for this account.
        </p>
      </Callout>

      {error !== null && error.code !== 'VALIDATION_FAILED' && <ErrorNotice error={error} />}

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
          revealable
          hint="At least 12 characters. Not your account name, not the site name."
          error={fieldMessage(error, 'new_password') ?? fieldMessage(error, 'password')}
          after={<PasswordRules password={password} repeat={repeat} />}
        />
        <Field
          label="Repeat password"
          type="password"
          autoComplete="new-password"
          value={repeat}
          onChange={setRepeat}
          revealable
          error={mismatch}
        />
        <Submit busy={busy} busyLabel="Setting your password…">
          Continue
        </Submit>
      </form>
    </>
  );
}

// ---------------------------------------------------------------------------
// Step 2
// ---------------------------------------------------------------------------

function AuthenticatorStep({
  enrollment,
  onConfirmed,
}: {
  readonly enrollment: Enrollment;
  readonly onConfirmed: () => void;
}): React.JSX.Element {
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function submit(): Promise<void> {
    setBusy(true);
    setError(null);
    const result = await confirmTotp(enrollment.confirm_token, code);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      setCode('');
      return;
    }
    onConfirmed();
  }

  return (
    <>
      <Callout tone="warn" title="Two-factor authentication is required">
        <p>
          This account cannot be used without it. Add the secret below to an authenticator app now —
          it is not shown again.
        </p>
      </Callout>

      <div className="card">
        <Secret
          label="Secret (type this into your app; the spaces are for reading only)"
          value={enrollment.secret}
          groups={4}
        />
        <p className="muted small">{enrollment.hint}</p>
        <p className="small">
          {/* Useful only when the browser and the authenticator are on the same
              device; harmless otherwise, and it saves the typing when they are. */}
          <a href={enrollment.otpauth_uri}>Open in an authenticator app on this device</a>
        </p>
      </div>

      {error !== null && <ErrorNotice error={error} />}

      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <Field
          label="Six-digit code"
          value={code}
          onChange={setCode}
          inputMode="numeric"
          autoComplete="one-time-code"
          placeholder="123456"
          maxLength={10}
          hint="The rotating code your authenticator app shows for this account — not the secret above."
          error={fieldMessage(error, 'code')}
        />
        <Submit busy={busy} busyLabel="Checking the code…">
          Confirm and sign in
        </Submit>
      </form>
    </>
  );
}

// ---------------------------------------------------------------------------
// Step 3
// ---------------------------------------------------------------------------

function RecoveryCodesStep({
  codes,
  onDone,
}: {
  readonly codes: readonly string[];
  readonly onDone: () => void;
}): React.JSX.Element {
  const [acknowledged, setAcknowledged] = useState(false);

  return (
    <>
      <Callout tone="warn" title="Shown once, and never again">
        <p>
          Each of these codes signs you in without your password or your authenticator, once. Save
          them somewhere you will actually find them — a password manager, or paper somewhere safe.
        </p>
        <p>
          With no codes and no Telegram access, only{' '}
          <code className="mono">tracelet admin reset-password</code> on the server can recover this
          account.
        </p>
      </Callout>

      {codes.length === 0 ? (
        <Callout tone="error" title="No codes were issued">
          <p>
            That should not happen. Sign in and use “Regenerate recovery codes” before doing
            anything else.
          </p>
        </Callout>
      ) : (
        <RecoveryCodes codes={codes} />
      )}

      <div className="card">
        <label className="checkbox">
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(event) => {
              setAcknowledged(event.target.checked);
            }}
          />
          <span>I have saved these codes somewhere I can find them.</span>
        </label>
      </div>

      <button
        type="button"
        className="primary"
        disabled={!acknowledged}
        onClick={() => {
          // Replace rather than push: the spent token must not sit in history where
          // the back button resubmits a link that no longer works.
          navigate({ name: 'dashboard' }, { replace: true });
          onDone();
        }}
      >
        Go to the dashboard
      </button>
    </>
  );
}
