/**
 * Route dispatch and the one piece of global state: who is signed in.
 *
 * The session lives in a cookie this code cannot read, so "am I signed in" is only
 * answerable by asking the server. `GET /api/v1/auth/me` is therefore the app's
 * bootstrap: a 200 means a live session and carries the CSRF token, a 401 means
 * anonymous, and anything else is a real error that gets its own state rather than
 * being collapsed into "signed out".
 *
 * That third case is the one worth being careful about. Treating a 500 or a dropped
 * connection as "not signed in" would bounce the admin to a login form that also
 * cannot work, and they would spend the outage retyping a password (B5).
 */

import { useCallback, useEffect, useState } from 'react';
import { fetchMe, type Me } from '@/api/auth';
import type { ApiError } from '@/api/client';
import { Callout, ErrorNotice, Loading } from '@/components/ui';
import DashboardPage from '@/pages/DashboardPage';
import EnrollPage from '@/pages/EnrollPage';
import LoginPage from '@/pages/LoginPage';
import { RecoveryCodePage, ResetConfirmPage, ResetRequestPage } from '@/pages/RecoveryPages';
import { navigate, useRoute } from '@/router';

type Session =
  | { readonly phase: 'loading' }
  | { readonly phase: 'anonymous' }
  | { readonly phase: 'signed-in'; readonly me: Me }
  /** Reachable, but not answering. Distinct from anonymous, deliberately. */
  | { readonly phase: 'unavailable'; readonly error: ApiError };

export default function App(): React.JSX.Element {
  const route = useRoute();
  const [session, setSession] = useState<Session>({ phase: 'loading' });
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = useCallback(async (): Promise<void> => {
    const result = await fetchMe();
    if (result.ok) {
      setSession({ phase: 'signed-in', me: result.data });
      return;
    }
    if (result.error.status === 401 || result.error.status === 403) {
      setSession({ phase: 'anonymous' });
      return;
    }
    setSession({ phase: 'unavailable', error: result.error });
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const signedIn = useCallback((): void => {
    setNotice(null);
    navigate({ name: 'dashboard' }, { replace: true });
    void refresh();
  }, [refresh]);

  const signedOut = useCallback((): void => {
    setSession({ phase: 'anonymous' });
    navigate({ name: 'login' }, { replace: true });
  }, []);

  // --- routes that are reachable without a session -------------------------
  // Checked before the session state, because an invitee has no session by
  // definition and a reset link is used precisely when signing in does not work.
  if (route.name === 'enroll') {
    return <EnrollPage token={route.token} onSignedIn={signedIn} />;
  }
  if (route.name === 'reset') {
    return (
      <ResetConfirmPage
        token={route.token}
        onReset={() => {
          setNotice('Your password has been changed. Sign in with it and your code.');
          navigate({ name: 'login' }, { replace: true });
        }}
      />
    );
  }
  if (route.name === 'reset-request') {
    return <ResetRequestPage />;
  }
  if (route.name === 'recovery') {
    return <RecoveryCodePage onSignedIn={signedIn} />;
  }

  if (session.phase === 'loading') {
    return (
      <main className="shell narrow">
        <header>
          <h1>Tracelet</h1>
        </header>
        <Loading label="Checking your session…" />
      </main>
    );
  }

  if (session.phase === 'unavailable') {
    return (
      <main className="shell narrow">
        <header>
          <h1>Tracelet</h1>
          <p className="muted">Cannot reach the server</p>
        </header>
        <ErrorNotice error={session.error} />
        <p className="muted small">
          You have not been signed out. This page is not working, which is a different problem —
          retry rather than reaching for your password.
        </p>
        <button
          type="button"
          onClick={() => {
            setSession({ phase: 'loading' });
            void refresh();
          }}
        >
          Try again
        </button>
      </main>
    );
  }

  if (session.phase === 'anonymous') {
    return <LoginPage onSignedIn={signedIn} {...(notice === null ? {} : { notice })} />;
  }

  if (route.name === 'not-found') {
    return (
      <main className="shell narrow">
        <header>
          <h1>Tracelet</h1>
          <p className="muted">No such page</p>
        </header>
        <Callout tone="warn" title="Nothing here">
          <p className="mono">{route.path}</p>
        </Callout>
        <button
          type="button"
          onClick={() => {
            navigate({ name: 'dashboard' });
          }}
        >
          Go to the dashboard
        </button>
      </main>
    );
  }

  return (
    <DashboardPage
      me={session.me}
      onSignedOut={signedOut}
      onRefresh={() => {
        void refresh();
      }}
    />
  );
}
