/**
 * The signed-in admin, available to every dashboard page.
 *
 * `App` owns the session bootstrap (`/auth/me`); this context only hands the result
 * down, so a page never re-fetches who is signed in and never has to handle "no
 * session" -- the dashboard is not rendered without one.
 */

import { createContext, useContext } from 'react';
import type { Me } from '@/api/auth';
import { apiParams, parseFilters, type Filters } from '@/filters';
import { useSearchParams } from 'react-router';

export interface Session {
  readonly me: Me;
  readonly onSignedOut: () => void;
  readonly onRefresh: () => void;
  readonly setMe: (me: Me) => void;
}

export const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (session === null) {
    // A programming error, not a runtime state: every dashboard route is mounted
    // inside the provider. Failing loudly beats rendering a blank page (B5).
    throw new Error('useSession() outside the signed-in dashboard');
  }
  return session;
}

/** The URL's filters, and a setter that writes them back to the URL. */
export function useFilters(): {
  readonly filters: Filters;
  readonly params: URLSearchParams;
  readonly zone: string;
} {
  const [search] = useSearchParams();
  const { me } = useSession();
  const filters = parseFilters(search);
  return { filters, params: apiParams(filters, me.reporting_tz), zone: me.reporting_tz };
}
