/**
 * Display primitives (DESIGN §5.4, §5.7): Card, Badge, Glyph, Kbd, KeyValue, Identifier,
 * Timestamp, Legend, Secret, Tabs.
 */

import { useId, type ReactNode } from 'react';
import { Icon, type IconName } from '@/components/icons';
import { CopyButton } from '@/components/ui/Button';
import { cx, middleTruncate, relativeTime } from '@/components/ui/util';

/**
 * A card: surface, border, radius, no shadow. Cards never nest (DESIGN §5.4). The header
 * holds a title (an `h2` by default), a one-line description, and actions on the right.
 */
export function Card({
  title,
  description,
  actions,
  footer,
  children,
  headingLevel = 2,
  className,
  labelledBy,
}: {
  readonly title?: ReactNode;
  readonly description?: ReactNode;
  readonly actions?: ReactNode;
  readonly footer?: ReactNode;
  readonly children: ReactNode;
  readonly headingLevel?: 2 | 3;
  readonly className?: string;
  /** Use an existing heading id instead of generating one. */
  readonly labelledBy?: string;
}): React.JSX.Element {
  const generated = useId();
  const headingId = labelledBy ?? generated;
  const Heading = headingLevel === 2 ? 'h2' : 'h3';
  return (
    <section
      className={cx('ui-card', className)}
      aria-labelledby={title === undefined ? undefined : headingId}
    >
      {(title !== undefined || actions !== undefined) && (
        <header className="ui-card__head">
          <div className="ui-card__titles">
            {title !== undefined && (
              <Heading id={headingId} className="t-section">
                {title}
              </Heading>
            )}
            {description !== undefined && <p className="t-secondary m-0">{description}</p>}
          </div>
          {actions !== undefined && <div className="ui-card__actions">{actions}</div>}
        </header>
      )}
      {children}
      {footer !== undefined && <footer className="ui-card__foot">{footer}</footer>}
    </section>
  );
}

/** Classification and status dots: fixed product-wide colours (DESIGN §4.1). */
export type Dot =
  | 'human'
  | 'unknown'
  | 'crawler'
  | 'datacenter'
  | 'bot'
  | 'spam'
  | 'spoofed'
  | 'ok'
  | 'warn'
  | 'error'
  | 'accent';

/**
 * A small state marker (DESIGN §5.4). The word is the signal; the dot or tint reinforces it
 * (NFR7.AC3). Badges mark state only -- never decoration, never navigation.
 */
export function Badge({
  children,
  dot,
  tone,
}: {
  readonly children: ReactNode;
  readonly dot?: Dot;
  /** A tinted status badge, for delivery and health states. */
  readonly tone?: 'ok' | 'warn' | 'error' | 'info';
}): React.JSX.Element {
  return (
    <span className={cx('badge', tone !== undefined && `badge--${tone}`)}>
      {dot !== undefined && <span className={`dot dot--${dot}`} aria-hidden="true" />}
      {children}
    </span>
  );
}

/**
 * One setting (DESIGN §10.8): its name and a line of explanation on the left, its state or
 * action on the right; on a phone the right side drops under the text. Rows sit inside a Card,
 * whose title is an `h2`, so a row's name is an `h3`.
 */
export function SettingRow({
  title,
  description,
  children,
}: {
  readonly title: ReactNode;
  readonly description?: ReactNode;
  readonly children?: ReactNode;
}): React.JSX.Element {
  return (
    <div className="setting-row">
      <div className="setting-row__text">
        <h3 className="setting-row__title">{title}</h3>
        {description !== undefined && <div className="setting-row__desc">{description}</div>}
      </div>
      {children !== undefined && <div className="setting-row__aside">{children}</div>}
    </div>
  );
}

/**
 * A decorative glyph before a value in a table cell (DESIGN §12 E22). Hidden from assistive
 * technology: the words beside it carry the meaning.
 */
export function Glyph({ name }: { readonly name: IconName }): React.JSX.Element {
  const Shape = Icon[name];
  return <Shape className="glyph" size={14} strokeWidth={1.75} aria-hidden="true" />;
}

export function Kbd({ children }: { readonly children: ReactNode }): React.JSX.Element {
  return <kbd className="kbd">{children}</kbd>;
}

export interface KeyValueItem {
  readonly key: string;
  readonly label: ReactNode;
  readonly value: ReactNode;
}

/** Labelled values in two columns (one on mobile). */
export function KeyValue({
  items,
}: {
  readonly items: readonly KeyValueItem[];
}): React.JSX.Element {
  return (
    <dl className="kv">
      {items.map((item) => (
        <div key={item.key} className="contents">
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** A long identifier: monospace, middle-truncated, with a copy button (DESIGN §5.4). */
export function Identifier({
  value,
  label = 'Copy',
  full = false,
}: {
  readonly value: string;
  readonly label?: string;
  /** Show the whole value instead of the truncated form. */
  readonly full?: boolean;
}): React.JSX.Element {
  return (
    <span className="ident">
      <span title={full ? undefined : value}>{full ? value : middleTruncate(value)}</span>
      <CopyButton value={value} label={label} />
    </span>
  );
}

export function Timestamp({
  iso,
  zone,
  mode = 'relative',
}: {
  readonly iso: string;
  readonly zone: string;
  readonly mode?: 'relative' | 'absolute' | 'time';
}): React.JSX.Element {
  const exact = new Date(iso).toLocaleString('en-IN', {
    timeZone: zone,
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
  const shown =
    mode === 'relative'
      ? relativeTime(iso)
      : mode === 'time'
        ? new Date(iso).toLocaleTimeString('en-IN', {
            timeZone: zone,
            hour: '2-digit',
            minute: '2-digit',
          })
        : exact;
  return (
    <time dateTime={iso} title={`${exact} (${zone})`} className="tabular">
      {shown}
    </time>
  );
}

export type Swatch =
  1 | 2 | 3 | 4 | 5 | 6 | 'seq-1' | 'seq-2' | 'seq-3' | 'seq-4' | 'seq-5' | 'land';

export function Legend({
  items,
  label,
}: {
  readonly items: readonly { readonly label: ReactNode; readonly swatch: Swatch }[];
  readonly label: string;
}): React.JSX.Element {
  return (
    <ul className="legend-list" aria-label={label}>
      {items.map((item, i) => (
        <li key={i}>
          <span className={`swatch swatch--${String(item.swatch)}`} aria-hidden="true" />
          {item.label}
        </li>
      ))}
    </ul>
  );
}

/** A value the admin has to copy accurately. Selectable, monospaced, wrapped, copyable. */
export function Secret({
  label,
  value,
  groups,
}: {
  readonly label: string;
  readonly value: string;
  /** Shown in groups of this many characters, for typing (DESIGN §12 E28); Copy is raw. */
  readonly groups?: number;
}): React.JSX.Element {
  const shown =
    groups === undefined
      ? value
      : (value.match(new RegExp(`.{1,${String(groups)}}`, 'g')) ?? []).join(' ');
  return (
    <div className="secret">
      <span className="t-meta">{label}</span>
      <span className="secret__value">
        <code className="t-mono selectable">{shown}</code>
        <CopyButton value={value} label={`Copy ${label.toLowerCase()}`} />
      </span>
    </div>
  );
}

/** Views within one object (DESIGN §5.7); the caller keeps the selection in the URL. */
export function Tabs({
  label,
  tabs,
  value,
  onChange,
}: {
  readonly label: string;
  readonly tabs: readonly { readonly value: string; readonly label: string }[];
  readonly value: string;
  readonly onChange: (value: string) => void;
}): React.JSX.Element {
  return (
    <div
      className="tabs"
      role="tablist"
      aria-label={label}
      onKeyDown={(event) => {
        if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
        const at = tabs.findIndex((t) => t.value === value);
        const next = tabs[(at + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length];
        if (next !== undefined) onChange(next.value);
      }}
    >
      {tabs.map((t) => (
        <button
          key={t.value}
          type="button"
          role="tab"
          aria-selected={t.value === value}
          tabIndex={t.value === value ? 0 : -1}
          className="tabs__tab"
          onClick={() => {
            onChange(t.value);
          }}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
