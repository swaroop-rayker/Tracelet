/**
 * System health (DESIGN §16 M7, F10): five pages behind one sub-navigation, laid out like
 * Settings (§10.8) -- a list on the left at ≥ 1024 px, a select above the content below it.
 * Every admin sees every page; the writes are the owner's, disabled with the reason for an
 * analyst (UI-17).
 */

import { Suspense } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router';
import { Icon } from '@/components/icons';
import { PageHeader } from '@/components/shell/PageHeader';
import { HEALTH_SECTIONS } from '@/components/shell/nav';
import { Loading, Select, cx } from '@/components/ui';

function pathOf(section: string): string {
  return section === '' ? '/health' : `/health/${section}`;
}

export default function HealthLayout(): React.JSX.Element {
  const location = useLocation();
  const navigate = useNavigate();
  const current =
    HEALTH_SECTIONS.find((s) => location.pathname.replace(/\/$/, '') === pathOf(s.path)) ??
    HEALTH_SECTIONS[0];

  return (
    <div className="page settings">
      <PageHeader
        title={current?.path === '' ? 'System health' : (current?.label ?? 'System health')}
        description={current?.description ?? 'The server, its data and its safety nets.'}
      />
      <div className="settings__body">
        <nav className="settings__nav" aria-label="System health">
          <ul>
            {HEALTH_SECTIONS.map((s) => {
              const Glyph = Icon[s.icon];
              return (
                <li key={s.path}>
                  <NavLink
                    to={pathOf(s.path)}
                    end
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
            label="System health page"
            value={current?.path ?? ''}
            onChange={(path) => {
              void navigate(pathOf(path));
            }}
            options={HEALTH_SECTIONS.map((s) => ({ value: s.path, label: s.label }))}
          />
        </div>
        <div className="settings__content settings__content--wide">
          <Suspense fallback={<Loading label="Loading…" />}>
            <Outlet />
          </Suspense>
        </div>
      </div>
    </div>
  );
}
