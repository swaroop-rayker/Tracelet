/**
 * A ~60-line router, because M1 needs five routes and not a dependency.
 *
 * `react-router` arrives in M5 with the dashboard's nested layouts, where it earns
 * its place. Installing it now for five flat routes would add a dependency to the
 * ledger for something a `switch` does (ES5), and the enrolment and reset pages have
 * to work before anything else does -- so the fewer moving parts, the better.
 *
 * Paths, not hashes: Caddy serves `index.html` for any unmatched path
 * (`try_files {path} /index.html`), and a reset link with a `#` in it is the kind of
 * thing a chat client mangles.
 */

import { useEffect, useState } from 'react';

export type Route =
  | { readonly name: 'login' }
  | { readonly name: 'recovery' }
  | { readonly name: 'reset-request' }
  /** `/enroll?token=…` -- the one-time invitation link. */
  | { readonly name: 'enroll'; readonly token: string | null }
  /** `/reset?token=…` -- delivered over Telegram (F8.AC7). */
  | { readonly name: 'reset'; readonly token: string | null }
  | { readonly name: 'dashboard' }
  | { readonly name: 'not-found'; readonly path: string };

export function parseRoute(url: string): Route {
  const parsed = new URL(url, window.location.origin);
  const path = parsed.pathname.replace(/\/+$/, '');
  const token = parsed.searchParams.get('token');

  switch (path) {
    case '':
    case '/dashboard':
      return { name: 'dashboard' };
    case '/login':
      return { name: 'login' };
    case '/recovery':
      return { name: 'recovery' };
    case '/forgot':
      return { name: 'reset-request' };
    case '/enroll':
      return { name: 'enroll', token };
    case '/reset':
      return { name: 'reset', token };
    default:
      return { name: 'not-found', path: parsed.pathname };
  }
}

export function pathFor(route: Route): string {
  switch (route.name) {
    case 'dashboard':
      return '/';
    case 'login':
      return '/login';
    case 'recovery':
      return '/recovery';
    case 'reset-request':
      return '/forgot';
    case 'enroll':
      return route.token === null ? '/enroll' : `/enroll?token=${encodeURIComponent(route.token)}`;
    case 'reset':
      return route.token === null ? '/reset' : `/reset?token=${encodeURIComponent(route.token)}`;
    case 'not-found':
      return route.path;
  }
}

/**
 * Move to another route.
 *
 * `replace` is used after enrolment and after a reset, so the one-time token is not
 * left in history where the back button would resubmit a link that is now spent.
 */
export function navigate(route: Route, options?: { readonly replace?: boolean }): void {
  const target = pathFor(route);
  if (options?.replace === true) {
    window.history.replaceState(null, '', target);
  } else {
    window.history.pushState(null, '', target);
  }
  window.dispatchEvent(new PopStateEvent('popstate'));
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.href));

  useEffect(() => {
    const onPopState = (): void => {
      setRoute(parseRoute(window.location.href));
    };
    window.addEventListener('popstate', onPopState);
    return () => {
      window.removeEventListener('popstate', onPopState);
    };
  }, []);

  return route;
}
