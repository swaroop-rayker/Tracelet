/**
 * The small set of primitives every auth page uses.
 *
 * They exist so that F9.AC18 -- explicit loading, error and empty states everywhere
 * -- is the path of least resistance rather than discipline applied page by page. A
 * panel that renders nothing is a defect (B5), so every component here renders
 * something for every input.
 *
 * Accessibility is built in rather than added later (NFR7): an error is an
 * `role="alert"` associated with its input by `aria-describedby`, status text is a
 * live region, and no state is signalled by colour alone (NFR7.AC3).
 */

import { useId, type ReactNode } from 'react';
import type { ApiError } from '@/api/client';

export type Tone = 'ok' | 'warn' | 'error' | 'info';

const TONE_LABEL: Readonly<Record<Tone, string>> = {
  ok: 'Success',
  warn: 'Warning',
  error: 'Error',
  info: 'Note',
};

/**
 * A boxed message. `title` is rendered as text, not as colour with a border --
 * colour is reinforcement here, never the signal.
 */
export function Callout({
  tone,
  title,
  children,
}: {
  readonly tone: Tone;
  readonly title?: string;
  readonly children?: ReactNode;
}): React.JSX.Element {
  return (
    <div
      className={`card ${tone === 'info' ? '' : tone}`.trim()}
      role={tone === 'error' ? 'alert' : 'status'}
    >
      <strong>{title ?? TONE_LABEL[tone]}</strong>
      {children !== undefined && children !== null ? (
        <div className="stack-sm">{children}</div>
      ) : null}
    </div>
  );
}

/**
 * The standard rendering of an `ApiError`.
 *
 * The trace id is shown whenever there is one. It is the single identifier that ties
 * what the operator saw to the server log line (F15.AC2), so a problem is
 * diagnosable from a screenshot without reproducing anything.
 */
export function ErrorNotice({ error }: { readonly error: ApiError }): React.JSX.Element {
  return (
    <Callout tone="error" title={error.code === 'RATE_LIMITED' ? 'Too many attempts' : 'Problem'}>
      <p>{error.message}</p>
      {error.retryAfter !== null && (
        <p className="muted">Try again in {String(error.retryAfter)} seconds.</p>
      )}
      {error.fields.length > 0 && (
        <ul className="plain">
          {error.fields.map((problem) => (
            <li key={`${problem.field}:${problem.code}`}>{problem.message}</li>
          ))}
        </ul>
      )}
      {error.traceId !== null && <p className="muted mono">trace {error.traceId}</p>}
    </Callout>
  );
}

export function Loading({ label = 'Loading…' }: { readonly label?: string }): React.JSX.Element {
  return (
    <p className="muted" role="status">
      {label}
    </p>
  );
}

export function Empty({ children }: { readonly children: ReactNode }): React.JSX.Element {
  return <p className="muted empty">{children}</p>;
}

/**
 * A labelled input with its own error slot.
 *
 * `hint` and `error` are wired to the input through `aria-describedby`, so a screen
 * reader announces the reason a field was rejected instead of leaving the user to
 * guess which of five policy rules they broke.
 */
export function Field({
  label,
  value,
  onChange,
  type = 'text',
  autoComplete,
  hint,
  error,
  required = true,
  inputMode,
  disabled = false,
  placeholder,
}: {
  readonly label: string;
  readonly value: string;
  readonly onChange: (value: string) => void;
  readonly type?: 'text' | 'password' | 'email' | 'number';
  readonly autoComplete?: string;
  readonly hint?: string;
  readonly error?: string | null;
  readonly required?: boolean;
  readonly inputMode?: 'numeric' | 'text' | 'email';
  readonly disabled?: boolean;
  readonly placeholder?: string;
}): React.JSX.Element {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const described = [hint === undefined ? null : hintId, error == null ? null : errorId]
    .filter((candidate): candidate is string => candidate !== null)
    .join(' ');

  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type={type}
        value={value}
        required={required}
        disabled={disabled}
        autoComplete={autoComplete}
        inputMode={inputMode}
        placeholder={placeholder}
        aria-invalid={error == null ? undefined : true}
        aria-describedby={described.length > 0 ? described : undefined}
        onChange={(event) => {
          onChange(event.target.value);
        }}
      />
      {hint !== undefined && (
        <p className="muted small" id={hintId}>
          {hint}
        </p>
      )}
      {error != null && (
        <p className="error-text small" id={errorId} role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

/**
 * A submit button that shows it is working.
 *
 * Disabled while in flight, because a second submission of a single-use token is an
 * error the admin then has to understand -- and on the enrolment path it used to cost
 * them their invite.
 */
export function Submit({
  busy,
  children,
  busyLabel = 'Working…',
}: {
  readonly busy: boolean;
  readonly children: ReactNode;
  readonly busyLabel?: string;
}): React.JSX.Element {
  return (
    <button type="submit" className="primary" disabled={busy} aria-busy={busy}>
      {busy ? busyLabel : children}
    </button>
  );
}

/** A value the admin has to copy accurately. Selectable, monospaced, wrapped. */
export function Secret({
  label,
  value,
}: {
  readonly label: string;
  readonly value: string;
}): React.JSX.Element {
  return (
    <div className="secret">
      <span className="muted small">{label}</span>
      <code className="mono selectable">{value}</code>
    </div>
  );
}
