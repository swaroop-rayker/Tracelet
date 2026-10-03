/**
 * The signed-in shell (DESIGN §9): a collapsible sidebar, a compact header, and the page.
 *
 * Responsive recomposition (§9.4): from 1280 px the sidebar is open unless the admin collapsed
 * it; from 768 px it is at least a 68 px icon rail; below 768 px it is a drawer opened from the
 * header. The collapse choice is a per-device convenience, kept in localStorage (UI-9).
 *
 * Each page renders its own `PageHeader` (title, period, filter toolbar), so the shell holds no
 * page-specific state; the filters stay in the URL exactly as in M5 (F9.AC13).
 */

import { useCallback, useState } from 'react';
import { Outlet } from 'react-router';
import { CommandPalette } from '@/components/shell/CommandPalette';
import { Header } from '@/components/shell/Header';
import { HelpDialog } from '@/components/shell/HelpDialog';
import { Sidebar } from '@/components/shell/Sidebar';
import { useMediaQuery } from '@/components/shell/useMediaQuery';
import { useShortcuts } from '@/components/shell/useShortcuts';
import { Dialog, cx } from '@/components/ui';

const STORAGE_KEY = 'tracelet.sidebar';

function storedCollapsed(): boolean | null {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value === 'collapsed' ? true : value === 'open' ? false : null;
  } catch {
    return null;
  }
}

function storeCollapsed(collapsed: boolean): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, collapsed ? 'collapsed' : 'open');
  } catch {
    // Storage blocked (private window): the choice simply lasts for this page view.
  }
}

export function Layout(): React.JSX.Element {
  const narrow = useMediaQuery('(max-width: 1279px)');
  const rail = useMediaQuery('(max-width: 1023px)');
  const [choice, setChoice] = useState<boolean | null>(storedCollapsed);
  const collapsed = rail || (choice ?? narrow);
  const [drawer, setDrawer] = useState(false);
  const [palette, setPalette] = useState(false);
  const [help, setHelp] = useState(false);

  const toggleSidebar = useCallback(() => {
    setChoice((current) => {
      const next = !(current ?? narrow);
      storeCollapsed(next);
      return next;
    });
  }, [narrow]);
  const openPalette = useCallback(() => {
    setPalette(true);
  }, []);
  const openHelp = useCallback(() => {
    setHelp(true);
  }, []);
  useShortcuts({ openPalette, openHelp, toggleSidebar });

  return (
    <div className={cx('app-shell', collapsed && 'is-collapsed')}>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar" aria-label="Sidebar">
        <Sidebar
          collapsed={collapsed}
          onToggle={rail ? undefined : toggleSidebar}
          onShowShortcuts={openHelp}
        />
      </aside>
      <div className="app-main">
        <Header
          onMenu={() => {
            setDrawer(true);
          }}
          onSearch={openPalette}
          onHelp={openHelp}
        />
        <main id="main" className="app-content" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
      <Dialog
        open={drawer}
        onClose={() => {
          setDrawer(false);
        }}
        title="Navigation"
        drawer
        side="left"
        className="nav-drawer"
      >
        <Sidebar
          collapsed={false}
          inDrawer
          onNavigate={() => {
            setDrawer(false);
          }}
          onShowShortcuts={() => {
            setDrawer(false);
            setHelp(true);
          }}
        />
      </Dialog>
      <CommandPalette
        open={palette}
        onClose={() => {
          setPalette(false);
        }}
      />
      <HelpDialog
        open={help}
        onClose={() => {
          setHelp(false);
        }}
      />
    </div>
  );
}
