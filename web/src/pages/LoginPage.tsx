/**
 * `/login` — password, then authenticator code (F8.AC1, F8.AC4).
 *
 * Two steps because TOTP is mandatory: a correct password produces a short-lived
 * challenge, not a session, and no route is reachable until the second factor is
 * proved.
 *
 * **Every failure says the same thing.** Wrong password, unknown account, disabled
 * account, enrolment not finished — one message. That is enumeration resistance
 * (F8.AC10), not a missing feature, and the UI must not helpfully undo it by
 * rendering a different hint per status code.
 */

import { useState } from 'react';
import { login, submitMfa, type MfaChallenge } from '@/api/auth';
import { fieldMessage, type ApiError } from '@/api/client';
import { Callout, ErrorNotice, Field, Submit } from '@/components/ui';
import { navigate } from '@/router';

type Step =
  { readonly name: 'password' } | { readonly name: 'code'; readonly challenge: MfaChallenge };

export default function LoginPage({
  onSignedIn,
  notice,
}: {
  readonly onSignedIn: () => void;
  readonly notice?: string;
}): React.JSX.Element {
  const [step, setStep] = useState<Step>({ name: 'password' });
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function submitPassword(): Promise<void> {
    setBusy(true);
    setError(null);
    const result = await login(email, password);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    // Held only in memory, and only until the code is accepted. It is a credential.
    setStep({ name: 'code', challenge: result.data });
    setPassword('');
  }

  async function submitCode(): Promise<void> {
    if (step.name !== 'code') return;
    setBusy(true);
    setError(null);
    const result = await submitMfa(step.challenge.mfa_token, code);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      setCode('');
      // A wrong code does not send the admin back to the password step: the
      // challenge is only spent once a code is accepted.
      return;
    }
    onSignedIn();
  }

  return (
    <main className="shell narrow">
      <header>
        <h1>Tracelet</h1>
        <p className="muted">
          {step.name === 'password' ? 'Sign in' : 'Enter your authenticator code'}
        </p>
      </header>

      {notice !== undefined && (
        <Callout tone="ok" title="Done">
          {notice}
        </Callout>
      )}
      {error !== null && <ErrorNotice error={error} />}

      {step.name === 'password' ? (
        <form
          className="stack"
          onSubmit={(event) => {
            event.preventDefault();
            void submitPassword();
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
            label="Password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={setPassword}
          />
          <Submit busy={busy} busyLabel="Checking…">
            Continue
          </Submit>
        </form>
      ) : (
        <form
          className="stack"
          onSubmit={(event) => {
            event.preventDefault();
            void submitCode();
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
            hint="A code can only be used once, so wait for the next one if you have just used it."
            error={fieldMessage(error, 'code')}
          />
          <Submit busy={busy} busyLabel="Signing in…">
            Sign in
          </Submit>
          <button
            type="button"
            className="link"
            onClick={() => {
              setStep({ name: 'password' });
              setError(null);
              setCode('');
            }}
          >
            Start again
          </button>
        </form>
      )}

      <nav className="stack-sm small">
        <button
          type="button"
          className="link"
          onClick={() => {
            navigate({ name: 'reset-request' });
          }}
        >
          I have forgotten my password
        </button>
        <button
          type="button"
          className="link"
          onClick={() => {
            navigate({ name: 'recovery' });
          }}
        >
          I have lost my authenticator — use a recovery code
        </button>
      </nav>
    </main>
  );
}
