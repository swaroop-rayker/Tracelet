/**
 * The Settings area (DESIGN §10.8, §12 E23): one page per job, with a sub-navigation on the
 * left at ≥ 1024 px and a select above the content below it. Replaces M1's single Account
 * page, which still redirects here.
 *
 * Role is reflected, never decided (F8.AC12): Team is left out of the navigation for an
 * analyst (UI-17, a pure configuration page), and every `/admins` route still refuses them.
 */

import { Suspense } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router';
import { Icon } from '@/components/icons';
import { PageHeader } from '@/components/shell/PageHeader';
import { SETTINGS_SECTIONS } from '@/components/shell/nav';
import { Loading, Select, cx } from '@/components/ui';
import { useSession } from '@/session';

export default function SettingsLayout(): React.JSX.Element {
  const { me } = useSession();
  const location = useLocation();
  const navigate = useNavigate();
  const sections = SETTINGS_SECTIONS.filter((s) => !s.ownerOnly || me.role === 'owner');
  const current = SETTINGS_SECTIONS.find((s) => location.pathname === `/settings/${s.path}`);

  return (
    <div className="page settings">
      <PageHeader
        title={current?.label ?? 'Settings'}
        description={current?.description ?? 'Your account, this dashboard, and its admins.'}
      />
      <div className="settings__body">
        <nav className="settings__nav" aria-label="Settings">
          <ul>
            {sections.map((s) => {
              const Glyph = Icon[s.icon];
              return (
                <li key={s.path}>
                  <NavLink
                    to={s.path}
                    className={({ isActive }) => cx('settings__link', isActive && 'is-active')}
                  >
                    <Glyph size={16} strokeWidth={1.75} aria-hidden="true" />
                    {s.label}
                  </NavLink>
                </li>
              );
            })}
          </ul>
        </nav>
        <div className="settings__picker">
          <Select
            label="Settings page"
            value={current?.path ?? ''}
            onChange={(path) => {
              void navigate(`/settings/${path}`);
            }}
            options={sections.map((s) => ({ value: s.path, label: s.label }))}
          />
        </div>
        <div className="settings__content">
          <Suspense fallback={<Loading label="Loading…" />}>
            <Outlet />
          </Suspense>
        </div>
      </div>
    </div>
  );
}
