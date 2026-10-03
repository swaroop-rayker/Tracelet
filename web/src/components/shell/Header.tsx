/**
 * The header (DESIGN §9.2): menu button (phones), breadcrumb, the search trigger and help.
 * Compact and quiet; it never holds page actions. Who is signed in lives in the sidebar's user
 * menu, not repeated here.
 */

import { Link, useLocation } from 'react-router';
import { Icon } from '@/components/icons';
import { locate } from '@/components/shell/nav';
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
        <IconButton icon="Help" label="Help and keyboard shortcuts" onClick={onHelp} />
      </div>
    </header>
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
      : null;
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
