/**
 * Visits (F9.AC1, F9.AC15; DESIGN §10.2): every visit, newest first, as a table grouped under
 * day or hour headings in the admin's display timezone. A row expands in place for a summary;
 * "Details" opens the full derivation in a drawer (E5), kept in the URL as `?visit=…` so it can
 * be shared -- and /visits/:id still loads the full page.
 *
 * Pages load on demand through the API's keyset cursor, so a long period never fetches
 * everything at once. Exports honour every filter and contain no IP address (F9.AC15).
 */

import { useInfiniteQuery } from '@tanstack/react-query';
import { Fragment, useEffect, useId, useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { getParsed, type ApiFailure } from '@/api/query';
import { visitPageSchema, type VisitPage, type VisitSummary } from '@/api/schemas';
import { Icon } from '@/components/icons';
import { PanelView } from '@/components/Panel';
import { PageHeader } from '@/components/shell/PageHeader';
import { setScalar } from '@/components/shell/filterDefs';
import {
  Badge,
  Glyph,
  Button,
  Dialog,
  KeyValue,
  Menu,
  MenuItem,
  SearchInput,
  SegmentedControl,
} from '@/components/ui';
import { VisitDetailBody } from '@/components/visit/VisitDetail';
import { parseFilters, serializeFilters, withParams } from '@/filters';
import { label, pct, when } from '@/format';
import { connectionGlyph, deviceGlyph } from '@/glyphs';
import { useFilters, useSession } from '@/session';
import { placeLabel, placeOf } from '@/visits';

const PAGE = 100;

export default function VisitsPage(): React.JSX.Element {
  const { params } = useFilters();
  const { me } = useSession();
  const [search, setSearch] = useSearchParams();
  const [grain, setGrain] = useState<'day' | 'hour'>('day');
  const listParams = withParams(params, { limit: String(PAGE) });
  const openId = search.get('visit');

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

  const setOpen = (id: string | null): void => {
    const next = new URLSearchParams(search);
    if (id === null) next.delete('visit');
    else next.set('visit', id);
    setSearch(next);
  };

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
        actions={
          <Menu label="Export" icon="Download" align="end">
            {(close) => (
              <>
                <MenuItem href={exportHref('csv')} download onSelect={close} hint="Spreadsheets">
                  CSV
                </MenuItem>
                <MenuItem href={exportHref('ndjson')} download onSelect={close} hint="One per line">
                  NDJSON
                </MenuItem>
              </>
            )}
          </Menu>
        }
      />
      <PanelView
        state={state}
        kind="table"
        title="Timeline"
        description="Expand a visit for a summary; Details opens its full derivation."
        isEmpty={(items) => items.length === 0}
        empty="No visits in this period with these filters."
        actions={
          <>
            <VisitSearch />
            <SegmentedControl
              label="Group by"
              value={grain}
              onChange={(v) => {
                setGrain(v === 'hour' ? 'hour' : 'day');
              }}
              options={[
                { value: 'day', label: 'Day' },
                { value: 'hour', label: 'Hour' },
              ]}
            />
          </>
        }
      >
        {(items) => (
          <>
            <VisitTable items={items} grain={grain} zone={me.timezone} onOpen={setOpen} />
            <div className="load-more">
              {query.hasNextPage ? (
                <Button
                  busy={query.isFetchingNextPage}
                  busyLabel="Loading…"
                  onClick={() => {
                    void query.fetchNextPage();
                  }}
                >
                  {`Load ${String(PAGE)} more · showing ${items.length.toLocaleString()}`}
                </Button>
              ) : (
                <p className="t-meta m-0">
                  All {items.length.toLocaleString()} visits shown. Exports contain no IP address.
                </p>
              )}
              {query.isFetchNextPageError && (
                <p className="field__error" role="alert">
                  The next page could not be loaded: {query.error.message}
                </p>
              )}
            </div>
          </>
        )}
      </PanelView>
      <Dialog
        open={openId !== null}
        onClose={() => {
          setOpen(null);
        }}
        title={
          openId === null ? (
            'Visit'
          ) : (
            <span className="drawer-title">
              Visit
              <Link className="link t-secondary" to={`/visits/${openId}`}>
                Open as a page
                <Icon.External size={14} strokeWidth={1.75} aria-hidden="true" />
              </Link>
            </span>
          )
        }
        drawer
        wide
      >
        {openId !== null && <VisitDetailBody visitId={openId} zone={me.timezone} />}
      </Dialog>
    </div>
  );
}

/** The free-text search, here where it applies; it writes the same `search` URL key. */
function VisitSearch(): React.JSX.Element {
  const [search, setSearch] = useSearchParams();
  const current = search.get('search') ?? '';
  const [draft, setDraft] = useState(current);
  useEffect(() => {
    setDraft(current);
  }, [current]);
  return (
    <SearchInput
      label="Search visits"
      size="sm"
      value={draft}
      placeholder="ISP, city, browser, link…"
      onChange={setDraft}
      onCommit={(value) => {
        if (value.trim() === current) return;
        setSearch(serializeFilters(setScalar(parseFilters(search), 'search', value.trim())));
      }}
    />
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

function VisitTable({
  items,
  grain,
  zone,
  onOpen,
}: {
  readonly items: readonly VisitSummary[];
  readonly grain: 'day' | 'hour';
  readonly zone: string;
  readonly onOpen: (id: string) => void;
}): React.JSX.Element {
  const groups: { title: string; visits: VisitSummary[] }[] = [];
  for (const visit of items) {
    const title = heading(visit.occurred_at, grain, zone);
    const last = groups.at(-1);
    if (last?.title === title) last.visits.push(visit);
    else groups.push({ title, visits: [visit] });
  }
  return (
    <div className="dt-wrap">
      <table className="dt visits-table">
        <caption className="sr-only">Visits, newest first</caption>
        <thead>
          <tr>
            <th scope="col">Time</th>
            <th scope="col">Class</th>
            <th scope="col">Location</th>
            <th scope="col">Device</th>
            <th scope="col">Network</th>
            <th scope="col">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {groups.map((group) => (
            <Fragment key={group.title}>
              <tr className="dt__group">
                <th scope="colgroup" colSpan={6}>
                  {group.title} <span className="t-meta">· {group.visits.length}</span>
                </th>
              </tr>
              {group.visits.map((visit) => (
                <VisitRow key={visit.id} visit={visit} zone={zone} onOpen={onOpen} />
              ))}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function VisitRow({
  visit,
  zone,
  onOpen,
}: {
  readonly visit: VisitSummary;
  readonly zone: string;
  readonly onOpen: (id: string) => void;
}): React.JSX.Element {
  const [open, setOpen] = useState(false);
  const detailId = useId();
  const place = placeOf(visit);
  const time = new Date(visit.occurred_at).toLocaleTimeString('en-IN', {
    timeZone: zone,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
  return (
    <>
      <tr className={open ? 'visit-row is-open' : 'visit-row'}>
        <td className="visit-row__time">
          <button
            type="button"
            className="row-toggle"
            aria-expanded={open}
            aria-controls={detailId}
            onClick={() => {
              setOpen((v) => !v);
            }}
          >
            <Icon.Next size={14} strokeWidth={2} aria-hidden="true" className="row-toggle__icon" />
            <span className="tabular">{time}</span>
            <span className="sr-only">: {open ? 'hide' : 'show'} summary</span>
          </button>
        </td>
        <td>
          <Badge dot={visit.classification}>{label(visit.classification)}</Badge>
        </td>
        <td className="visit-row__place">{placeLabel(place)}</td>
        <td className="muted">
          <Glyph name={visit.device.is_inapp_webview ? 'InApp' : deviceGlyph(visit.device.class)} />
          {visit.device.browser ?? 'Unknown browser'} · {visit.device.os ?? 'unknown OS'}
        </td>
        <td className="muted visit-row__net">
          <Glyph name={connectionGlyph(visit.network.connection_class)} />
          {visit.network.asn_org ?? 'Unknown network'}
        </td>
        <td className="visit-row__actions">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              onOpen(visit.id);
            }}
          >
            Details
          </Button>
        </td>
      </tr>
      <tr id={detailId} className="visit-row__detail" hidden={!open}>
        <td colSpan={6}>
          {open && (
            <KeyValue
              items={[
                { key: 'when', label: 'When', value: when(visit.occurred_at, zone, true) },
                { key: 'link', label: 'Link', value: `${visit.link.slug} — ${visit.link.label}` },
                { key: 'stage', label: 'Stage', value: label(visit.stage) },
                {
                  key: 'place',
                  label: 'Location',
                  value: `${place.text}${
                    place.confidence === null
                      ? ''
                      : ` · ${place.confirmed ? 'confirmed' : 'best guess'}, ${pct(place.confidence)} confidence`
                  }`,
                },
                {
                  key: 'scores',
                  label: 'Scores',
                  value: `bot ${String(visit.bot_score ?? '—')} · spoof ${String(visit.spoof_score ?? '—')}`,
                },
                {
                  key: 'device',
                  label: 'Device',
                  value: `${label(visit.device.class)}${
                    visit.device.webview_host === null
                      ? ''
                      : ` · in-app: ${visit.device.webview_host}`
                  }${visit.device.screen === null ? '' : ` · ${visit.device.screen}`}`,
                },
                {
                  key: 'net',
                  label: 'Network',
                  value: `${visit.network.asn === null ? 'unknown ASN' : `AS${String(visit.network.asn)}`} · ${label(visit.network.connection_class)} · ${visit.network.ip_prefix ?? 'no prefix'}`,
                },
                {
                  key: 'visitor',
                  label: 'Visitor',
                  value:
                    visit.visitor_id === null ? (
                      'No visitor ID (server-only visit)'
                    ) : (
                      <Link className="link" to={`/visitors/${visit.visitor_id}`}>
                        {visit.is_returning === true ? 'Returning visitor' : 'First visit'} — view
                        history
                      </Link>
                    ),
                },
              ]}
            />
          )}
        </td>
      </tr>
    </>
  );
}
