/**
 * Buttons (DESIGN §5.1): `Button`, `IconButton`, `ButtonLink`, `CopyButton`.
 *
 * One primary action per view. A button's text is a verb. An `IconButton` always has an
 * accessible name, and the tooltip shows the same words.
 */

import { useEffect, useState, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { Icon, type IconName } from '@/components/icons';
import { Tooltip } from '@/components/ui/Tooltip';
import { copyText, cx } from '@/components/ui/util';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'type'> {
  readonly variant?: ButtonVariant;
  readonly size?: 'md' | 'sm';
  readonly icon?: IconName;
  /** Shows a spinner in place of the icon and blocks a second press while working. */
  readonly busy?: boolean;
  readonly busyLabel?: ReactNode;
  readonly type?: 'button' | 'submit' | 'reset';
  readonly ref?: React.Ref<HTMLButtonElement>;
  /**
   * Visible but unavailable, and why (UI-17): "Only the owner can create geofences". The
   * button stays focusable (`aria-disabled`, not `disabled`), so the reason is reachable by
   * keyboard and hover, and a press does nothing.
   */
  readonly disabledReason?: string | null;
}

export function Button({
  variant = 'secondary',
  size = 'md',
  icon,
  busy = false,
  busyLabel,
  type = 'button',
  className,
  disabled,
  disabledReason,
  children,
  onClick,
  ...rest
}: ButtonProps): React.JSX.Element {
  const Glyph = icon === undefined ? null : Icon[icon];
  if (disabledReason !== undefined && disabledReason !== null) {
    return (
      <Tooltip content={disabledReason}>
        {(describedBy) => (
          <button
            type="button"
            className={cx(
              'btn',
              variant !== 'secondary' && `btn--${variant}`,
              size === 'sm' && 'btn--sm',
              className,
            )}
            aria-disabled="true"
            aria-describedby={describedBy}
            {...rest}
          >
            {Glyph !== null && <Glyph size={16} strokeWidth={1.75} aria-hidden="true" />}
            {children}
          </button>
        )}
      </Tooltip>
    );
  }
  return (
    <button
      type={type}
      className={cx(
        'btn',
        variant !== 'secondary' && `btn--${variant}`,
        size === 'sm' && 'btn--sm',
        className,
      )}
      disabled={disabled === true || busy}
      aria-busy={busy || undefined}
      onClick={onClick}
      {...rest}
    >
      {busy ? (
        <span className="spinner" aria-hidden="true" />
      ) : (
        Glyph !== null && <Glyph size={16} strokeWidth={1.75} aria-hidden="true" />
      )}
      {busy && busyLabel !== undefined ? busyLabel : children}
    </button>
  );
}

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'type'> {
  readonly icon: IconName;
  /** The accessible name, also shown as the tooltip. Required: an icon is never alone. */
  readonly label: string;
  readonly size?: 'md' | 'sm';
  readonly pressed?: boolean;
  readonly ref?: React.Ref<HTMLButtonElement>;
}

export function IconButton({
  icon,
  label,
  size = 'md',
  pressed,
  className,
  ...rest
}: IconButtonProps): React.JSX.Element {
  const Glyph = Icon[icon];
  return (
    <Tooltip content={label} labelOnly>
      <button
        type="button"
        className={cx('icon-btn', size === 'sm' && 'icon-btn--sm', className)}
        aria-label={label}
        aria-pressed={pressed}
        {...rest}
      >
        <Glyph size={size === 'sm' ? 14 : 16} strokeWidth={1.75} aria-hidden="true" />
      </button>
    </Tooltip>
  );
}

/** A link that looks like a button: downloads and navigation that are not actions. */
export function ButtonLink({
  href,
  icon,
  variant = 'secondary',
  size = 'md',
  download,
  children,
}: {
  readonly href: string;
  readonly icon?: IconName;
  readonly variant?: ButtonVariant;
  readonly size?: 'md' | 'sm';
  readonly download?: boolean;
  readonly children: ReactNode;
}): React.JSX.Element {
  const Glyph = icon === undefined ? null : Icon[icon];
  return (
    <a
      href={href}
      download={download}
      className={cx(
        'btn',
        variant !== 'secondary' && `btn--${variant}`,
        size === 'sm' && 'btn--sm',
      )}
    >
      {Glyph !== null && <Glyph size={16} strokeWidth={1.75} aria-hidden="true" />}
      {children}
    </a>
  );
}

/** Copies `value`; confirms with a check for a moment, and says so to screen readers. */
export function CopyButton({
  value,
  label = 'Copy',
}: {
  readonly value: string;
  readonly label?: string;
}): React.JSX.Element {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return undefined;
    const timer = window.setTimeout(() => {
      setCopied(false);
    }, 1500);
    return () => {
      window.clearTimeout(timer);
    };
  }, [copied]);
  return (
    <>
      <IconButton
        icon={copied ? 'Check' : 'Copy'}
        label={copied ? 'Copied' : label}
        size="sm"
        onClick={() => {
          void copyText(value).then(setCopied);
        }}
      />
      <span className="sr-only" role="status">
        {copied ? 'Copied to the clipboard' : ''}
      </span>
    </>
  );
}
