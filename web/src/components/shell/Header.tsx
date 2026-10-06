/**
 * The header (DESIGN §9.2): menu button (phones), breadcrumb, the search trigger and help.
 * Compact and quiet; it never holds page actions. Who is signed in lives in the sidebar's user
 * menu, not repeated here.
 */

import { Link, useLocation, useNavigate } from 'react-router';
import { outboxSchema } from '@/api/geofences';
import { useApi } from '@/api/query';
import { Icon } from '@/components/icons';
import { locate, SETTINGS_SECTIONS } from '@/components/shell/nav';
import { IconButton, Kbd, modKey } from '@/components/ui';

export function Header({
  onMenu,
  onSearch,
  onHelp,
}: {
  readonly onMenu: () => void;
  readonly onSearch: () => void;
  readonly onHelp: () => void;
}): React.JSX.Element {
  const location = useLocation();
  const mod = modKey();
  return (
    <header className="topbar">
      <span className="topbar__menu">
        <IconButton icon="Menu" label="Open navigation" onClick={onMenu} />
      </span>
      <Breadcrumb path={location.pathname} search={location.search} />
      <div className="topbar__end">
        <button type="button" className="search-trigger" onClick={onSearch}>
          <Icon.Search size={16} strokeWidth={1.75} aria-hidden="true" />
          <span className="search-trigger__text">Search or jump to…</span>
          <span className="search-trigger__keys" aria-hidden="true">
            <Kbd>{mod}</Kbd>
            <Kbd>K</Kbd>
          </span>
        </button>
        <DeadLetterBell />
        <IconButton icon="Help" label="Help and keyboard shortcuts" onClick={onHelp} />
      </div>
    </header>
  );
}

const BELL_POLL_MS = 60_000;

/**
 * The notification bell (DESIGN §16): dead-lettered deliveries only -- the one thing about
 * alerts that needs a person. A count appears only when there are some; the bell opens the
 * Alerts page filtered to them. Polls once a minute while the tab is visible (UI-18).
 */
function DeadLetterBell(): React.JSX.Element {
  const navigate = useNavigate();
  const query = useApi(
    '/api/v1/health/outbox',
    new URLSearchParams({ limit: '1', status: 'dead' }),
    outboxSchema,
    { refetchInterval: BELL_POLL_MS },
  );
  const dead = query.data?.counts.dead ?? 0;
  const label =
    dead === 0
      ? 'Alerts: no failed deliveries'
      : dead === 1
        ? 'Alerts: 1 failed delivery'
        : `Alerts: ${String(dead)} failed deliveries`;
  return (
    <span className="bell">
      <IconButton
        icon="Notifications"
        label={label}
        onClick={() => {
          void navigate(dead > 0 ? '/alerts?status=dead' : '/alerts');
        }}
      />
      {dead > 0 && (
        <span className="bell__count" aria-hidden="true">
          {dead > 99 ? '99+' : String(dead)}
        </span>
      )}
    </span>
  );
}

function Breadcrumb({
  path,
  search,
}: {
  readonly path: string;
  readonly search: string;
}): React.JSX.Element {
  const { group, item } = locate(path);
  const detail = /^\/visits\/[^/]+/.test(path)
    ? 'Visit'
    : path.startsWith('/visitors/')
      ? 'Visitor'
      : /^\/links\/[^/]+/.test(path)
        ? decodeURIComponent(path.slice('/links/'.length))
        : path === '/geofences/new'
          ? 'New geofence'
          : /^\/geofences\/[^/]+/.test(path)
            ? 'Edit geofence'
            : (SETTINGS_SECTIONS.find((s) => path === `/settings/${s.path}`)?.label ?? null);
  return (
    <nav aria-label="Breadcrumb" className="crumbs">
      <ol>
        {group !== null && <li className="crumbs__group">{group}</li>}
        {item !== null &&
          (detail === null ? (
            <li aria-current="page">{item.label}</li>
          ) : (
            <li>
              <Link to={{ pathname: item.to, search }}>{item.label}</Link>
            </li>
          ))}
        {detail !== null && <li aria-current="page">{detail}</li>}
        {item === null && <li aria-current="page">Not found</li>}
      </ol>
    </nav>
  );
}
