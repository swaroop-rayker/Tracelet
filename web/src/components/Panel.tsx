/**
 * Every dashboard chart and table renders inside a `Panel` (F9.AC18).
 *
 * B5 was a panel that rendered nothing: no data, no spinner, no error -- just a blank
 * rectangle that looked like "no visits" while the request had actually failed. A
 * Panel cannot do that. It takes the query's state, and for each of the four states --
 * loading, error, empty, data -- it renders something a person can read:
 *
 * - **loading** shows a skeleton shaped like the content, and says what it is loading;
 * - **error** shows the API's message and trace id (F15.AC2);
 * - **empty** says *why* it is empty, in words the caller supplies, because "no
 *   visits in this range" and "nothing has been inferred yet" are different facts;
 * - **data** renders the children, with the stage mix and data source underneath
 *   (F9.AC20), so a chart never hides what it was computed over.
 *
 * `PanelView` is the pure part, and is what the tests render for every state.
 */

import type { ReactNode } from 'react';
import type { UseQueryResult } from '@tanstack/react-query';
import type { ApiFailure } from '@/api/query';
import type { Meta } from '@/api/schemas';
import { Empty, ErrorNotice, InfoTip, Loading, cx, type SkeletonKind } from '@/components/ui';

export type PanelState<T> =
  | { readonly status: 'pending' }
  | { readonly status: 'error'; readonly error: ApiFailure }
  | { readonly status: 'success'; readonly data: T };

export interface PanelProps<T> {
  readonly title: string;
  /** One line under the title: what the panel shows, and how to read it. */
  readonly description?: string;
  readonly isEmpty: (data: T) => boolean;
  /** Why it is empty. Required: an empty panel without a reason is B5 again. */
  readonly empty: ReactNode | ((data: T) => ReactNode);
  readonly meta?: (data: T) => Meta | null;
  readonly actions?: ReactNode;
  /** What the loading skeleton looks like: the shape of the content to come (DESIGN 5.6). */
  readonly kind?: SkeletonKind;
  readonly className?: string;
  readonly children: (data: T) => ReactNode;
}

export function PanelView<T>({
  state,
  title,
  description,
  isEmpty,
  empty,
  meta,
  actions,
  kind = 'text',
  className,
  children,
}: PanelProps<T> & { readonly state: PanelState<T> }): React.JSX.Element {
  let body: ReactNode;
  let footer: ReactNode = null;
  if (state.status === 'pending') {
    body = <Loading label={`Loading ${title.toLowerCase()}…`} kind={kind} />;
  } else if (state.status === 'error') {
    body = <ErrorNotice error={state.error.error} />;
  } else {
    const info = meta?.(state.data) ?? null;
    footer = info === null ? null : <MetaLine meta={info} />;
    body = isEmpty(state.data) ? (
      <Empty>{typeof empty === 'function' ? empty(state.data) : empty}</Empty>
    ) : (
      children(state.data)
    );
  }
  return (
    <section
      className={cx('ui-card', 'panel', className)}
      aria-labelledby={headingId(title)}
      aria-busy={state.status === 'pending'}
    >
      <header className="ui-card__head">
        <div className="ui-card__titles">
          <h2 id={headingId(title)} className="t-section">
            {title}
          </h2>
          {description !== undefined && <p className="t-secondary m-0">{description}</p>}
        </div>
        {actions !== undefined && <div className="ui-card__actions">{actions}</div>}
      </header>
      <div className="panel-body">{body}</div>
      {footer}
    </section>
  );
}

function headingId(title: string): string {
  return `panel-${title.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
}

function percent(part: number, whole: number): string {
  return whole === 0 ? '—' : `${String(Math.round((part / whole) * 100))}%`;
}

/** F9.AC20: what the figures above were computed over, stated as text. */
export function MetaLine({ meta }: { readonly meta: Meta }): React.JSX.Element {
  const mix = meta.stage_mix;
  const source = meta.computed_from === 'rollup' ? 'rollups' : 'raw rows';
  const when =
    meta.refreshed_at === null
      ? ''
      : ` as of ${new Date(meta.refreshed_at).toLocaleTimeString([], {
          hour: '2-digit',
          minute: '2-digit',
        })}`;
  return (
    <footer className="ui-card__foot panel-meta">
      <p className="t-meta m-0">
        {mix.total.toLocaleString()} requests · {percent(mix.enriched, mix.total)} enriched ·{' '}
        {percent(mix.server_only, mix.total)} server-only
        {mix.server > 0 ? ` · ${mix.server.toLocaleString()} pending` : ''}
        {mix.rate_limited > 0 ? ` · ${mix.rate_limited.toLocaleString()} rate-limited` : ''} · from{' '}
        {source}
        {when}
      </p>
      <InfoTip term="these figures">
        What these figures were computed over: every request in the period, how many the browser
        enriched, how many were captured server-side only, and whether they came from the daily
        rollups or from raw visits.
      </InfoTip>
    </footer>
  );
}

function stateOf<T>(query: UseQueryResult<T, ApiFailure>): PanelState<T> {
  if (query.isPending) return { status: 'pending' };
  if (query.isError) return { status: 'error', error: query.error };
  return { status: 'success', data: query.data };
}

/** A Panel driven by a React Query result. */
export function Panel<T>({
  query,
  ...props
}: PanelProps<T> & { readonly query: UseQueryResult<T, ApiFailure> }): React.JSX.Element {
  return <PanelView state={stateOf(query)} {...props} />;
}
