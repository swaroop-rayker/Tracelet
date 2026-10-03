/**
 * Feedback (DESIGN §5.6): Alert, ErrorNotice, Skeleton, Loading, EmptyState, Banner.
 *
 * Every state that matters has words as well as colour (NFR7.AC3). An error is
 * `role="alert"`; everything else is `role="status"`. A panel is never blank (B5): loading is
 * a skeleton shaped like what is coming, with a screen-reader label saying what it is.
 */

import type { ReactNode } from 'react';
import type { ApiError } from '@/api/client';
import { Icon, type IconName } from '@/components/icons';
import { Identifier } from '@/components/ui/display';
import { cx } from '@/components/ui/util';

export type Tone = 'ok' | 'warn' | 'error' | 'info';

const TONE_ICON: Readonly<Record<Tone, IconName>> = {
  ok: 'Check',
  warn: 'Warn',
  error: 'Error',
  info: 'Info',
};

const TONE_LABEL: Readonly<Record<Tone, string>> = {
  ok: 'Done',
  warn: 'Warning',
  error: 'Error',
  info: 'Note',
};

/** An inline message: tone icon, title, body, an optional action. */
export function Alert({
  tone,
  title,
  children,
  action,
}: {
  readonly tone: Tone;
  readonly title?: ReactNode;
  readonly children?: ReactNode;
  readonly action?: ReactNode;
}): React.JSX.Element {
  const Glyph = Icon[TONE_ICON[tone]];
  return (
    <div className={`alert alert--${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
      <Glyph size={16} strokeWidth={1.75} aria-hidden="true" />
      <div className="alert__body">
        <strong className="alert__title">{title ?? TONE_LABEL[tone]}</strong>
        {children !== undefined && children !== null && <div className="stack-sm">{children}</div>}
        {action}
      </div>
    </div>
  );
}

/** The M1 name for Alert, kept so the sign-in and account pages need no rewrite to restyle. */
export const Callout = Alert;

/**
 * The standard rendering of an `ApiError`. The trace id is shown, with a copy button,
 * whenever there is one: it ties what the operator saw to the server log line (F15.AC2).
 */
export function ErrorNotice({ error }: { readonly error: ApiError }): React.JSX.Element {
  return (
    <Alert tone="error" title={error.code === 'RATE_LIMITED' ? 'Too many attempts' : 'Problem'}>
      <p>{error.message}</p>
      {error.retryAfter !== null && (
        <p className="t-secondary">Try again in {String(error.retryAfter)} seconds.</p>
      )}
      {error.fields.length > 0 && (
        <ul className="plain">
          {error.fields.map((problem) => (
            <li key={`${problem.field}:${problem.code}`}>{problem.message}</li>
          ))}
        </ul>
      )}
      {error.traceId !== null && (
        <p className="t-meta">
          {/* In full, never truncated: a screenshot must be enough to find the log line. */}
          Trace <Identifier value={error.traceId} label="Copy trace id" full />
        </p>
      )}
    </Alert>
  );
}

export type SkeletonKind = 'text' | 'chart' | 'table' | 'list' | 'kpi' | 'map';

const LIST_WIDTHS = ['sk--w90', 'sk--w70', 'sk--w50', 'sk--w70', 'sk--w30', 'sk--w50'];

/** A placeholder shaped like the content that is loading (DESIGN §5.6). */
export function Skeleton({ kind = 'text' }: { readonly kind?: SkeletonKind }): React.JSX.Element {
  let body: ReactNode;
  switch (kind) {
    case 'chart':
      body = <div className="sk sk--chart" />;
      break;
    case 'map':
      body = <div className="sk sk--map" />;
      break;
    case 'kpi':
      body = (
        <>
          <div className="sk sk--line sk--w50" />
          <div className="sk sk--metric" />
          <div className="sk sk--line sk--w30" />
        </>
      );
      break;
    case 'table':
      body = Array.from({ length: 6 }, (_, i) => <div key={i} className="sk sk--row" />);
      break;
    case 'list':
      body = LIST_WIDTHS.map((w, i) => <div key={i} className={`sk sk--row ${w}`} />);
      break;
    case 'text':
      body = (
        <>
          <div className="sk sk--line sk--w90" />
          <div className="sk sk--line sk--w70" />
          <div className="sk sk--line sk--w50" />
        </>
      );
      break;
  }
  return (
    <div className="skeleton" aria-hidden="true">
      {body}
    </div>
  );
}

/** Loading: a skeleton for the eye, a sentence for the screen reader. */
export function Loading({
  label = 'Loading…',
  kind = 'text',
}: {
  readonly label?: string;
  readonly kind?: SkeletonKind;
}): React.JSX.Element {
  return (
    <div role="status">
      <span className="sr-only">{label}</span>
      <Skeleton kind={kind} />
    </div>
  );
}

/**
 * An empty state: a title and a reason, always (B5), and at most one action. No
 * illustrations (DESIGN §5.6).
 */
export function EmptyState({
  title,
  reason,
  action,
  icon = 'Info',
}: {
  readonly title: ReactNode;
  readonly reason?: ReactNode;
  readonly action?: ReactNode;
  readonly icon?: IconName;
}): React.JSX.Element {
  const Glyph = Icon[icon];
  return (
    <div className="empty-state" role="status">
      <Glyph size={20} strokeWidth={1.5} aria-hidden="true" />
      <p className="empty-state__title">{title}</p>
      {reason !== undefined && <p className="empty-state__reason">{reason}</p>}
      {action}
    </div>
  );
}

/** The M1/M5 name: an empty state whose only text is the reason it is empty. */
export function Empty({ children }: { readonly children: ReactNode }): React.JSX.Element {
  return <EmptyState title={children} />;
}

/** A full-width strip under the header for state that affects every page (DESIGN §5.6). */
export function Banner({
  tone,
  children,
  action,
}: {
  readonly tone: 'warn' | 'error' | 'info';
  readonly children: ReactNode;
  readonly action?: ReactNode;
}): React.JSX.Element {
  const Glyph = Icon[TONE_ICON[tone]];
  return (
    <div className={cx('banner', `banner--${tone}`)} role={tone === 'error' ? 'alert' : 'status'}>
      <Glyph size={16} strokeWidth={1.75} aria-hidden="true" />
      <span>{children}</span>
      {action}
    </div>
  );
}
