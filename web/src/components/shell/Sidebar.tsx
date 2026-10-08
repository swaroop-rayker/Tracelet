/**
 * The sidebar (DESIGN §9.1): product mark, grouped navigation with icons, the account link and
 * the user menu. Collapses to a 68 px rail of icons, each with its label as a tooltip.
 *
 * Navigation links carry the current query string to analytics pages, so moving from Overview
 * to Geography keeps the period and filters (unchanged from M5).
 */

import { useState } from 'react';
import { Link, NavLink, useLocation, useNavigate } from 'react-router';
import { logout } from '@/api/auth';
import { useApi } from '@/api/query';
import { VIEWS, savedViewListSchema, viewHref } from '@/api/workflow';
import { Icon } from '@/components/icons';
import { BrandMark } from '@/components/shell/BrandMark';
import { ACCOUNT_ITEM, NAV_GROUPS, type NavItem } from '@/components/shell/nav';
import { IconButton, MenuItem, MenuLabel, MenuSeparator, Popover, Tooltip } from '@/components/ui';
import { useSession } from '@/session';
import { useThemeSwitch } from '@/components/shell/useThemeSwitch';
import { THEMES, asTheme } from '@/theme';

export function Sidebar({
  collapsed,
  onToggle,
  onNavigate,
  onShowShortcuts,
  inDrawer = false,
}: {
  readonly collapsed: boolean;
  readonly onToggle?: (() => void) | undefined;
  /** Called after a link is followed (closes the mobile drawer). */
  readonly onNavigate?: (() => void) | undefined;
  readonly onShowShortcuts: () => void;
  readonly inDrawer?: boolean;
}): React.JSX.Element {
  const location = useLocation();
  return (
    <div className="sidebar__inner">
      <div className="sidebar__brand">
        <BrandMark />
        {!collapsed && <span className="brand-name">Tracelet</span>}
        {onToggle !== undefined && !inDrawer && (
          <span className="sidebar__toggle">
            <IconButton
              icon={collapsed ? 'Expand' : 'Collapse'}
              label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
              size="sm"
              onClick={onToggle}
            />
          </span>
        )}
      </div>
      <nav aria-label="Dashboard" className="sidebar__nav">
        {NAV_GROUPS.map((group) => (
          <div key={group.label} className="nav-group">
            {!collapsed && <p className="nav-group__label">{group.label}</p>}
            <ul className="plain">
              {group.items.map((item) => (
                <li key={item.to}>
                  <NavEntry
                    item={item}
                    collapsed={collapsed}
                    search={location.search}
                    onNavigate={onNavigate}
                  />
                </li>
              ))}
            </ul>
          </div>
        ))}
        {!collapsed && <SavedViewsGroup onNavigate={onNavigate} />}
      </nav>
      <div className="sidebar__foot">
        <NavEntry item={ACCOUNT_ITEM} collapsed={collapsed} search="" onNavigate={onNavigate} />
        <UserMenu collapsed={collapsed} onShowShortcuts={onShowShortcuts} />
      </div>
    </div>
  );
}

const SHOWN_VIEWS = 8;

/** The admin's saved views (F9.AC26, DESIGN §12 E12), each a plain link; past eight, "Show all". */
function SavedViewsGroup({
  onNavigate,
}: {
  readonly onNavigate?: (() => void) | undefined;
}): React.JSX.Element | null {
  const views = useApi(VIEWS, null, savedViewListSchema).data ?? [];
  const [all, setAll] = useState(false);
  if (views.length === 0) return null;
  const shown = all ? views : views.slice(0, SHOWN_VIEWS);
  const Glyph = Icon.SaveView;
  return (
    <div className="nav-group">
      <p className="nav-group__label">Saved views</p>
      <ul className="plain">
        {shown.map((view) => (
          <li key={view.id}>
            <Link className="nav-link" to={viewHref(view)} onClick={onNavigate} title={view.name}>
              <Glyph size={18} strokeWidth={1.75} aria-hidden="true" />
              <span className="nav-link__text">{view.name}</span>
            </Link>
          </li>
        ))}
      </ul>
      {views.length > SHOWN_VIEWS && (
        <button
          type="button"
          className="nav-more"
          onClick={() => {
            setAll(!all);
          }}
        >
          {all ? 'Show fewer' : `Show all ${String(views.length)}`}
        </button>
      )}
    </div>
  );
}

function NavEntry({
  item,
  collapsed,
  search,
  onNavigate,
}: {
  readonly item: NavItem;
  readonly collapsed: boolean;
  readonly search: string;
  readonly onNavigate?: (() => void) | undefined;
}): React.JSX.Element {
  const Glyph = Icon[item.icon];
  const link = (
    <NavLink
      to={{ pathname: item.to, search: item.filtered ? search : '' }}
      end={item.to === '/'}
      className={({ isActive }) => (isActive ? 'nav-link is-active' : 'nav-link')}
      aria-label={collapsed ? item.label : undefined}
      onClick={onNavigate}
    >
      <Glyph size={18} strokeWidth={1.75} aria-hidden="true" />
      {!collapsed && <span>{item.label}</span>}
    </NavLink>
  );
  return collapsed ? (
    <Tooltip content={item.label} labelOnly side="below">
      {link}
    </Tooltip>
  ) : (
    link
  );
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  return (
    (parts[0]?.[0] ?? '') + (parts.length > 1 ? (parts.at(-1)?.[0] ?? '') : '')
  ).toUpperCase();
}

/** Who is signed in, the theme, shortcuts and sign-out (DESIGN §9.1). */
function UserMenu({
  collapsed,
  onShowShortcuts,
}: {
  readonly collapsed: boolean;
  readonly onShowShortcuts: () => void;
}): React.JSX.Element {
  const { me, onSignedOut } = useSession();
  const navigate = useNavigate();
  const { switchTo, error: themeError } = useThemeSwitch();
  const [busy, setBusy] = useState(false);
  const current = asTheme(me.theme);

  return (
    <>
      <Popover
        label="Account menu"
        role="menu"
        trigger={({ ref, ...props }) => (
          <button
            type="button"
            ref={ref}
            className="user-btn"
            aria-label={collapsed ? `${me.display_name}, account menu` : undefined}
            {...props}
          >
            <span className="avatar" aria-hidden="true">
              {initials(me.display_name)}
            </span>
            {!collapsed && (
              <span className="user-btn__who">
                <span className="user-btn__name">{me.display_name}</span>
                <span className="user-btn__role">{me.role}</span>
              </span>
            )}
          </button>
        )}
      >
        {(close) => (
          <div className="menu">
            <MenuLabel>Theme</MenuLabel>
            {THEMES.map((t) => (
              <MenuItem
                key={t.name}
                checked={current === t.name}
                onSelect={() => {
                  switchTo(t.name);
                  close();
                }}
              >
                {t.label}
              </MenuItem>
            ))}
            <MenuSeparator />
            <MenuItem
              icon="Account"
              onSelect={() => {
                close();
                void navigate('/settings/profile');
              }}
            >
              Settings
            </MenuItem>
            <MenuItem
              icon="Shortcuts"
              hint="?"
              onSelect={() => {
                close();
                onShowShortcuts();
              }}
            >
              Keyboard shortcuts
            </MenuItem>
            <MenuSeparator />
            <MenuItem
              icon="SignOut"
              disabled={busy}
              onSelect={() => {
                setBusy(true);
                void logout(me.csrf_token).then(() => {
                  onSignedOut();
                });
              }}
            >
              {busy ? 'Signing out…' : 'Sign out'}
            </MenuItem>
          </div>
        )}
      </Popover>
      {themeError !== null && (
        <p className="field__error" role="alert">
          {themeError}
        </p>
      )}
    </>
  );
}
