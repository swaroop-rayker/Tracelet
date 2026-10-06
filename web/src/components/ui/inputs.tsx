/**
 * Inputs (DESIGN §5.2): Field, Input, SearchInput, Select, Checkbox, Switch,
 * SegmentedControl, Submit.
 *
 * Native elements, restyled: keyboard and screen-reader behaviour come from the browser
 * (NFR7.AC2). A form field's hint and error are wired by `aria-describedby`, and an error is
 * also `role="alert"`, so the reason a field was rejected is announced.
 */

import { useId, useRef, type InputHTMLAttributes, type ReactNode } from 'react';
import { Icon } from '@/components/icons';
import { Button } from '@/components/ui/Button';
import { Kbd } from '@/components/ui/display';
import { cx } from '@/components/ui/util';

/**
 * A labelled input with its own hint and error slots. The API is unchanged from M1, so the
 * sign-in and account forms keep working while they are restyled.
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
  maxLength,
  mono = false,
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
  /** Stops an over-long paste reaching the server at all. */
  readonly maxLength?: number;
  /** Monospaced, for codes. */
  readonly mono?: boolean;
}): React.JSX.Element {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const described = [hint === undefined ? null : hintId, error == null ? null : errorId]
    .filter((candidate): candidate is string => candidate !== null)
    .join(' ');

  return (
    <div className="field">
      <label className="field__label" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        className={cx('input', mono && 't-mono')}
        type={type}
        value={value}
        required={required}
        disabled={disabled}
        autoComplete={autoComplete}
        inputMode={inputMode}
        maxLength={maxLength}
        placeholder={placeholder}
        aria-invalid={error == null ? undefined : true}
        aria-describedby={described.length > 0 ? described : undefined}
        onChange={(event) => {
          onChange(event.target.value);
        }}
      />
      {hint !== undefined && (
        <p className="field__hint" id={hintId}>
          {hint}
        </p>
      )}
      {error != null && (
        <p className="field__error" id={errorId} role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

/** A bare styled input, for toolbars and custom layouts. Label it with `aria-label`. */
export function Input({
  size = 'md',
  className,
  ...rest
}: Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> & {
  readonly size?: 'md' | 'sm';
  readonly ref?: React.Ref<HTMLInputElement>;
}): React.JSX.Element {
  return <input className={cx('input', size === 'sm' && 'input--sm', className)} {...rest} />;
}

/** A search field: icon, clear on Escape, an optional shortcut hint. */
export function SearchInput({
  value,
  onChange,
  onCommit,
  label,
  placeholder,
  shortcut,
  size = 'md',
}: {
  readonly value: string;
  readonly onChange: (value: string) => void;
  /** Enter, or leaving the field: apply the value (filters apply then, not per keystroke). */
  readonly onCommit?: (value: string) => void;
  readonly label: string;
  readonly placeholder?: string;
  readonly shortcut?: string;
  readonly size?: 'md' | 'sm';
}): React.JSX.Element {
  const ref = useRef<HTMLInputElement | null>(null);
  return (
    <div className="search">
      <Icon.Search size={16} strokeWidth={1.75} aria-hidden="true" />
      <Input
        ref={ref}
        type="search"
        size={size}
        value={value}
        aria-label={label}
        placeholder={placeholder}
        onChange={(event) => {
          onChange(event.target.value);
        }}
        onKeyDown={(event) => {
          if (event.key === 'Enter') onCommit?.(event.currentTarget.value);
          if (event.key === 'Escape' && value !== '') {
            event.preventDefault();
            onChange('');
            onCommit?.('');
          }
        }}
        onBlur={(event) => {
          onCommit?.(event.currentTarget.value);
        }}
      />
      {shortcut !== undefined && value === '' && <Kbd>{shortcut}</Kbd>}
    </div>
  );
}

export interface Option {
  readonly value: string;
  readonly label: string;
  readonly disabled?: boolean;
}

/** A native select. In a toolbar the label is visually hidden but still read. */
export function Select({
  label,
  value,
  options,
  onChange,
  size = 'md',
  hideLabel = true,
  block = false,
}: {
  readonly label: string;
  readonly value: string;
  readonly options: readonly Option[];
  readonly onChange: (value: string) => void;
  readonly size?: 'md' | 'sm';
  readonly hideLabel?: boolean;
  readonly block?: boolean;
}): React.JSX.Element {
  const id = useId();
  return (
    <div className={cx('select-field', !hideLabel && 'field')}>
      <label htmlFor={id} className={hideLabel ? 'sr-only' : 'field__label'}>
        {label}
      </label>
      <span className={cx('select-wrap', block && 'select-wrap--block')}>
        <select
          id={id}
          className={cx('select', size === 'sm' && 'select--sm', !block && 'select--inline')}
          value={value}
          onChange={(event) => {
            onChange(event.target.value);
          }}
        >
          {options.map((o) => (
            <option key={o.value} value={o.value} disabled={o.disabled}>
              {o.label}
            </option>
          ))}
        </select>
      </span>
    </div>
  );
}

export function Checkbox({
  label,
  checked,
  onChange,
  disabled = false,
}: {
  readonly label: ReactNode;
  readonly checked: boolean;
  readonly onChange: (checked: boolean) => void;
  readonly disabled?: boolean;
}): React.JSX.Element {
  return (
    <label className="check-row">
      <input
        type="checkbox"
        className="check"
        checked={checked}
        disabled={disabled}
        onChange={(event) => {
          onChange(event.target.checked);
        }}
      />
      <span>{label}</span>
    </label>
  );
}

/** An immediately-applied setting (DESIGN §5.2). For choices inside a form, use a Checkbox. */
export function Switch({
  label,
  checked,
  onChange,
  disabled = false,
  hideLabel = false,
}: {
  readonly label: string;
  readonly checked: boolean;
  readonly onChange: (checked: boolean) => void;
  readonly disabled?: boolean;
  readonly hideLabel?: boolean;
}): React.JSX.Element {
  return (
    <label className="check-row">
      <input
        type="checkbox"
        role="switch"
        className="switch"
        checked={checked}
        disabled={disabled}
        aria-checked={checked}
        onChange={(event) => {
          onChange(event.target.checked);
        }}
      />
      <span className={hideLabel ? 'sr-only' : undefined}>{label}</span>
    </label>
  );
}

/** Two to four mutually exclusive views (DESIGN §5.2): a radiogroup with arrow keys. */
export function SegmentedControl({
  label,
  value,
  options,
  onChange,
}: {
  readonly label: string;
  readonly value: string;
  readonly options: readonly Option[];
  readonly onChange: (value: string) => void;
}): React.JSX.Element {
  const enabled = options.filter((o) => o.disabled !== true);
  return (
    <div
      className="seg"
      role="radiogroup"
      aria-label={label}
      onKeyDown={(event) => {
        if (!['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp'].includes(event.key)) return;
        event.preventDefault();
        const at = enabled.findIndex((o) => o.value === value);
        const step = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1 : -1;
        const next = enabled[(at + step + enabled.length) % enabled.length];
        if (next === undefined) return;
        onChange(next.value);
        const button = event.currentTarget.querySelector<HTMLButtonElement>(
          `[data-value="${CSS.escape(next.value)}"]`,
        );
        button?.focus();
      }}
    >
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          data-value={o.value}
          aria-checked={o.value === value}
          tabIndex={o.value === value ? 0 : -1}
          disabled={o.disabled}
          className="seg__item"
          onClick={() => {
            onChange(o.value);
          }}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/**
 * A submit button that shows it is working. Disabled while in flight, because a second
 * submission of a single-use token is an error the admin then has to understand.
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
    <Button type="submit" variant="primary" busy={busy} busyLabel={busyLabel}>
      {children}
    </Button>
  );
}
