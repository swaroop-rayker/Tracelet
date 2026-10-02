/**
 * The sign-in routes: enrolment, login, recovery and reset.
 *
 * Built in M1 as the whole router. From M5, `react-router` owns every signed-in page
 * (nested layout, URL filter state); this module still decides only whether the URL is
 * one of the pages that must work *without* a session, and anything else is the
 * dashboard's to resolve. `navigate()` dispatches `popstate`, which react-router's
 * history listens to, so the two never disagree about the current URL.
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
      // Every other path belongs to the dashboard, whose router has its own 404.
      return { name: 'dashboard' };
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
