/**
 * The visit timeline (F9.AC1) and export (F9.AC15).
 *
 * Visits are grouped under day or hour headings in the admin's display timezone, each
 * row expandable in place, with the full derivation one click further (F9.AC14).
 * Pages load on demand through the API's keyset cursor, so a long period never fetches
 * everything at once.
 */

import { useInfiniteQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useLocation } from 'react-router';
import { getParsed, type ApiFailure } from '@/api/query';
import { visitPageSchema, type VisitPage, type VisitSummary } from '@/api/schemas';
import { PanelView } from '@/components/Panel';
import { withParams } from '@/filters';
import { label, pct, when } from '@/format';
import { placeLabel, placeOf } from '@/visits';
import { useFilters, useSession } from '@/session';
import { PageHeader } from '@/components/shell/PageHeader';

const PAGE = 100;

export default function VisitsPage(): React.JSX.Element {
  const { params } = useFilters();
  const { me } = useSession();
  const location = useLocation();
  const [grain, setGrain] = useState<'day' | 'hour'>('day');
  const listParams = withParams(params, { limit: String(PAGE) });

  const query = useInfiniteQuery<VisitPage, ApiFailure>({
    queryKey: ['/api/v1/visits', listParams.toString()],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      getParsed(
        '/api/v1/visits',
        typeof pageParam === 'string' ? withParams(listParams, { cursor: pageParam }) : listParams,
        visitPageSchema,
      ),
    getNextPageParam: (last) => last.next_cursor,
  });

  const exportHref = (format: 'csv' | 'ndjson'): string =>
    `/api/v1/visits/export?${withParams(params, { format }).toString()}`;

  const state = query.isPending
    ? ({ status: 'pending' } as const)
    : query.isError
      ? ({ status: 'error', error: query.error } as const)
      : ({ status: 'success', data: query.data.pages.flatMap((p) => p.items) } as const);

  return (
    <div className="page">
      <PageHeader
        title="Visits"
        description="Every visit, newest first. Open one for its full derivation."
        filters
      />
      <PanelView
        state={state}
        title="Timeline"
        description="Newest first. Expand a visit for a summary; open it for the full derivation."
        isEmpty={(items) => items.length === 0}
        empty="No visits in this period with these filters."
        actions={
          <div className="panel-actions">
            <label className="control">
              <span>Group by</span>
              <select
                value={grain}
                onChange={(event) => {
                  setGrain(event.target.value === 'hour' ? 'hour' : 'day');
                }}
              >
                <option value="day">Day</option>
                <option value="hour">Hour</option>
              </select>
            </label>
            <a className="button" href={exportHref('csv')} download>
              Export CSV
            </a>
            <a className="button" href={exportHref('ndjson')} download>
              Export NDJSON
            </a>
          </div>
        }
      >
        {(items) => (
          <>
            <Timeline items={items} grain={grain} zone={me.timezone} search={location.search} />
            <div className="load-more">
              {query.hasNextPage ? (
                <button
                  type="button"
                  disabled={query.isFetchingNextPage}
                  onClick={() => {
                    void query.fetchNextPage();
                  }}
                >
                  {query.isFetchingNextPage ? 'Loading…' : `Load ${String(PAGE)} more`}
                </button>
              ) : (
                <p className="muted small">
                  All {items.length.toLocaleString()} visits shown. Exports contain no IP address.
                </p>
              )}
              {query.isFetchNextPageError && (
                <p className="error-text small" role="alert">
                  The next page could not be loaded: {query.error.message}
                </p>
              )}
            </div>
          </>
        )}
      </PanelView>
    </div>
  );
}

function heading(iso: string, grain: 'day' | 'hour', zone: string): string {
  const date = new Date(iso);
  const dayPart = date.toLocaleDateString('en-IN', {
    timeZone: zone,
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  });
  if (grain === 'day') return dayPart;
  const hour = date.toLocaleTimeString('en-IN', {
    timeZone: zone,
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
  return `${dayPart}, ${hour.slice(0, 2)}:00`;
}

function Timeline({
  items,
  grain,
  zone,
  search,
}: {
  readonly items: readonly VisitSummary[];
  readonly grain: 'day' | 'hour';
  readonly zone: string;
  readonly search: string;
}): React.JSX.Element {
  const groups: { title: string; visits: VisitSummary[] }[] = [];
  for (const visit of items) {
    const title = heading(visit.occurred_at, grain, zone);
    const last = groups.at(-1);
    if (last?.title === title) last.visits.push(visit);
    else groups.push({ title, visits: [visit] });
  }
  return (
    <div className="timeline">
      {groups.map((group) => (
        <section key={group.title} aria-label={group.title}>
          <h4 className="timeline-head">
            {group.title} <span className="muted">· {group.visits.length}</span>
          </h4>
          <ul className="plain">
            {group.visits.map((visit) => (
              <VisitRow key={visit.id} visit={visit} zone={zone} search={search} />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function VisitRow({
  visit,
  zone,
  search,
}: {
  readonly visit: VisitSummary;
  readonly zone: string;
  readonly search: string;
}): React.JSX.Element {
  const place = placeOf(visit);
  const time = new Date(visit.occurred_at).toLocaleTimeString('en-IN', {
    timeZone: zone,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
  return (
    <li className="visit-row">
      <details>
        <summary>
          <span className="mono">{time}</span>
          <span className={`badge ${visit.classification}`}>{label(visit.classification)}</span>
          <span>{placeLabel(place)}</span>
          <span className="muted">
            {visit.device.browser ?? 'Unknown browser'} · {visit.device.os ?? 'unknown OS'} ·{' '}
            {visit.network.asn_org ?? 'unknown network'}
          </span>
        </summary>
        <dl className="pairs">
          <dt>When</dt>
          <dd>{when(visit.occurred_at, zone, true)}</dd>
          <dt>Link</dt>
          <dd>
            {visit.link.slug} — {visit.link.label}
          </dd>
          <dt>Stage</dt>
          <dd>{label(visit.stage)}</dd>
          <dt>Location</dt>
          <dd>
            {place.text}
            {place.confidence === null
              ? ''
              : ` · ${place.confirmed ? 'confirmed' : 'best guess'}, ${pct(place.confidence)} confidence`}
          </dd>
          <dt>Scores</dt>
          <dd>
            bot {visit.bot_score ?? '—'} · spoof {visit.spoof_score ?? '—'}
          </dd>
          <dt>Device</dt>
          <dd>
            {label(visit.device.class)}
            {visit.device.webview_host === null ? '' : ` · in-app: ${visit.device.webview_host}`}
            {visit.device.screen === null ? '' : ` · ${visit.device.screen}`}
          </dd>
          <dt>Network</dt>
          <dd>
            {visit.network.asn === null ? 'unknown ASN' : `AS${String(visit.network.asn)}`} ·{' '}
            {label(visit.network.connection_class)} · {visit.network.ip_prefix ?? 'no prefix'}
          </dd>
          <dt>Visitor</dt>
          <dd>
            {visit.visitor_id === null ? (
              'No visitor ID (server-only visit)'
            ) : (
              <Link to={`/visitors/${visit.visitor_id}`}>
                {visit.is_returning === true ? 'Returning visitor' : 'First visit'} — view history
              </Link>
            )}
          </dd>
        </dl>
        <Link to={`/visits/${visit.id}${search}`}>Open the full derivation</Link>
      </details>
    </li>
  );
}
