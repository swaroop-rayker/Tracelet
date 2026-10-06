/**
 * The signed-in shell: navigation, the filter bar, theme and sign-out.
 *
 * Navigation links carry the current query string, so moving from Overview to
 * Geography keeps the period and filters -- the filters describe *which visits*, and
 * every page is a different view of the same visits.
 */

import { useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router';
import { logout, updatePreferences } from '@/api/auth';
import { FilterBar } from '@/components/FilterBar';
import { useSession } from '@/session';
import { THEMES, applyTheme, asTheme } from '@/theme';

const NAV: readonly { readonly to: string; readonly label: string; readonly filtered: boolean }[] =
  [
    { to: '/', label: 'Overview', filtered: true },
    { to: '/visits', label: 'Visits', filtered: true },
    { to: '/geography', label: 'Geography', filtered: true },
    { to: '/breakdowns', label: 'Breakdowns', filtered: true },
    { to: '/inference', label: 'Inference', filtered: true },
    { to: '/detection', label: 'Detection', filtered: true },
    { to: '/account', label: 'Account', filtered: false },
  ];

/** Pages that are about one thing, not a filtered set, hide the filter bar. */
function showsFilters(path: string): boolean {
  return !(
    path.startsWith('/account') ||
    /^\/visits\/[^/]+/.test(path) ||
    path.startsWith('/visitors/')
  );
}

export function Layout(): React.JSX.Element {
  const { me, onSignedOut, setMe } = useSession();
  const location = useLocation();
  const [themeError, setThemeError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <p className="brand">Tracelet</p>
        <nav aria-label="Dashboard">
          <ul className="plain">
            {NAV.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={{ pathname: item.to, search: item.filtered ? location.search : '' }}
                  end={item.to === '/'}
                  className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
                >
                  {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <div className="sidebar-foot">
          <p className="small">
            {me.display_name}
            <br />
            <span className="muted">{me.role}</span>
          </p>
          <label className="control">
            <span>Theme</span>
            <select
              value={asTheme(me.theme)}
              onChange={(event) => {
                const theme = asTheme(event.target.value);
                applyTheme(theme);
                setThemeError(null);
                void updatePreferences(me.csrf_token, { theme }).then((result) => {
                  if (result.ok) setMe(result.data);
                  else setThemeError('Not saved: it will reset when you sign in again.');
                });
              }}
            >
              {THEMES.map((t) => (
                <option key={t.name} value={t.name}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          {themeError !== null && (
            <p className="error-text small" role="alert">
              {themeError}
            </p>
          )}
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              void logout(me.csrf_token).then(() => {
                onSignedOut();
              });
            }}
          >
            {busy ? 'Signing out…' : 'Sign out'}
          </button>
        </div>
      </aside>
      <main id="main" className="content" tabIndex={-1}>
        {showsFilters(location.pathname) && <FilterBar zone={me.reporting_tz} />}
        <Outlet />
      </main>
    </div>
  );
}
