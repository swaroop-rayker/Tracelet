/**
 * The navigation, defined once (DESIGN §9.1): the sidebar, the breadcrumb, the command
 * palette and the `g` shortcuts all read this list, so they cannot disagree.
 *
 * Slots for later milestones (Geofences and Alerts in M6, System health in M7) are added
 * here when they ship -- never before, because a link to nothing is a dead control (UI-12).
 */

import type { IconName } from '@/components/icons';

export interface NavItem {
  readonly to: string;
  readonly label: string;
  readonly icon: IconName;
  /** Analytics pages carry the filters in their URL from page to page. */
  readonly filtered: boolean;
  /** The second key of its `g` shortcut: `g o` goes to Overview. */
  readonly key: string;
  /** One line for the command palette. */
  readonly hint: string;
}

export interface NavGroup {
  readonly label: string;
  readonly items: readonly NavItem[];
}

export const NAV_GROUPS: readonly NavGroup[] = [
  {
    label: 'Analytics',
    items: [
      {
        to: '/',
        label: 'Overview',
        icon: 'Overview',
        filtered: true,
        key: 'o',
        hint: 'KPIs, visits over time, funnel',
      },
      {
        to: '/visits',
        label: 'Visits',
        icon: 'Visits',
        filtered: true,
        key: 'v',
        hint: 'Every visit, newest first',
      },
      {
        to: '/geography',
        label: 'Geography',
        icon: 'Geography',
        filtered: true,
        key: 'g',
        hint: 'Map by state and country',
      },
      {
        to: '/breakdowns',
        label: 'Breakdowns',
        icon: 'Breakdowns',
        filtered: true,
        key: 'b',
        hint: 'Location, network, device',
      },
      {
        to: '/links',
        label: 'Links',
        icon: 'Link',
        filtered: true,
        key: 'l',
        hint: 'Every tracking link, and each one’s own dashboard',
      },
    ],
  },
  {
    label: 'Engine',
    items: [
      {
        to: '/inference',
        label: 'Inference',
        icon: 'Inference',
        filtered: true,
        key: 'i',
        hint: 'Sources, confidence, accuracy',
      },
      {
        to: '/detection',
        label: 'Detection',
        icon: 'Detection',
        filtered: true,
        key: 'd',
        hint: 'Bot and spoofing rules',
      },
    ],
  },
];

/** The Settings area (DESIGN §10.8); `/account` redirects to it. */
export const ACCOUNT_ITEM: NavItem = {
  to: '/settings',
  label: 'Settings',
  icon: 'Account',
  filtered: false,
  key: 'a',
  hint: 'Profile, security, sessions, team',
};

export interface SettingsSection {
  readonly path: string;
  readonly label: string;
  readonly icon: IconName;
  /** The page's one line under its title. */
  readonly description: string;
  /** Hidden from analysts (UI-17: a pure configuration page); the server refuses them anyway. */
  readonly ownerOnly: boolean;
}

/** The Settings pages, in sub-navigation order (DESIGN §10.8). */
export const SETTINGS_SECTIONS: readonly SettingsSection[] = [
  {
    path: 'profile',
    label: 'Profile',
    icon: 'Profile',
    description: 'Who you are signed in as, and this session.',
    ownerOnly: false,
  },
  {
    path: 'preferences',
    label: 'Preferences',
    icon: 'Theme',
    description: 'How the dashboard looks and which time zone it shows times in.',
    ownerOnly: false,
  },
  {
    path: 'security',
    label: 'Security',
    icon: 'Security',
    description: 'Password, two-factor, recovery codes and the Telegram recovery chat.',
    ownerOnly: false,
  },
  {
    path: 'sessions',
    label: 'Sessions',
    icon: 'Sessions',
    description: 'Every device signed in to your account.',
    ownerOnly: false,
  },
  {
    path: 'team',
    label: 'Team',
    icon: 'Invite',
    description: 'The admins of this dashboard: invite, change role, disable or remove.',
    ownerOnly: true,
  },
  {
    path: 'system',
    label: 'System',
    icon: 'System',
    description: 'Whether the server is ready: database, PostGIS and migrations.',
    ownerOnly: false,
  },
];

export const ALL_ITEMS: readonly NavItem[] = [...NAV_GROUPS.flatMap((g) => g.items), ACCOUNT_ITEM];

/** The nav item a path belongs to, and the group it sits in (for the breadcrumb). */
export function locate(path: string): {
  readonly group: string | null;
  readonly item: NavItem | null;
} {
  if (path.startsWith('/visits/') || path.startsWith('/visitors/')) {
    return { group: 'Analytics', item: NAV_GROUPS[0]?.items[1] ?? null };
  }
  for (const group of NAV_GROUPS) {
    for (const item of group.items) {
      if (item.to === '/' ? path === '/' : path === item.to || path.startsWith(`${item.to}/`)) {
        return { group: group.label, item };
      }
    }
  }
  if (path.startsWith('/settings') || path.startsWith('/account')) {
    return { group: null, item: ACCOUNT_ITEM };
  }
  return { group: null, item: null };
}

/** Pages about one thing, not a filtered set, show no filter toolbar. */
export function isFilteredPath(path: string): boolean {
  const { item } = locate(path);
  return (
    item?.filtered === true &&
    !/^\/visits\/[^/]+/.test(path) &&
    !path.startsWith('/visitors/') &&
    // The Links index counts all-time visits; a link's own page is filtered (DESIGN §10.11).
    path !== '/links'
  );
}
