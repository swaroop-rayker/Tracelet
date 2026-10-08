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

import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Navigate, Route, Routes } from 'react-router';
import { fetchMe, type Me } from '@/api/auth';
import type { ApiError } from '@/api/client';
import { Layout } from '@/components/Layout';
import { PageHeader } from '@/components/shell/PageHeader';
import { Button, EmptyState, ErrorNotice, Loading } from '@/components/ui';
import EnrollPage from '@/pages/EnrollPage';
import LoginPage from '@/pages/LoginPage';
import { RecoveryCodePage, ResetConfirmPage, ResetRequestPage } from '@/pages/RecoveryPages';
import { navigate, useRoute } from '@/router';
import { SessionContext, type Session as SessionValue } from '@/session';
import { applyTheme, asTheme } from '@/theme';
import { BrandMark } from '@/components/shell/BrandMark';

// Each dashboard page is its own chunk, so ECharts and Leaflet download only when a
// page that draws with them is opened -- and the sign-in pages never pay for either.
const OverviewPage = lazy(() => import('@/pages/dashboard/OverviewPage'));
const VisitsPage = lazy(() => import('@/pages/dashboard/VisitsPage'));
const VisitDetailPage = lazy(() => import('@/pages/dashboard/VisitDetailPage'));
const VisitorPage = lazy(() => import('@/pages/dashboard/VisitorPage'));
const GeographyPage = lazy(() => import('@/pages/dashboard/GeographyPage'));
const BreakdownsPage = lazy(() => import('@/pages/dashboard/BreakdownsPage'));
const LinksPage = lazy(() => import('@/pages/dashboard/LinksPage'));
const LinkDetailPage = lazy(() => import('@/pages/dashboard/LinkDetailPage'));
const InferencePage = lazy(() => import('@/pages/dashboard/InferencePage'));
const DetectionPage = lazy(() => import('@/pages/dashboard/DetectionPage'));
const GeofencesPage = lazy(() => import('@/pages/configure/GeofencesPage'));
const GeofenceEditorPage = lazy(() => import('@/pages/configure/GeofenceEditorPage'));
const AlertsPage = lazy(() => import('@/pages/configure/AlertsPage'));
const SettingsLayout = lazy(() => import('@/pages/settings/SettingsLayout'));
const ProfilePage = lazy(() => import('@/pages/settings/ProfilePage'));
const PreferencesPage = lazy(() => import('@/pages/settings/PreferencesPage'));
const SecurityPage = lazy(() => import('@/pages/settings/SecurityPage'));
const SessionsPage = lazy(() => import('@/pages/settings/SessionsPage'));
const TeamPage = lazy(() => import('@/pages/settings/TeamPage'));
const SystemPage = lazy(() => import('@/pages/settings/SystemPage'));
const HealthLayout = lazy(() => import('@/pages/health/HealthLayout'));
const HealthOverviewPage = lazy(() => import('@/pages/health/OverviewPage'));
const HealthDatabasesPage = lazy(() => import('@/pages/health/DatabasesPage'));
const HealthInferencePage = lazy(() => import('@/pages/health/InferencePage'));
const HealthDataPage = lazy(() => import('@/pages/health/DataPage'));
const HealthLimitsPage = lazy(() => import('@/pages/health/LimitsPage'));
// The component gallery exists only in development builds (DESIGN §11): Vite replaces
// import.meta.env.DEV with false in production, and the import is dropped with it.
const DesignGallery = import.meta.env.DEV ? lazy(() => import('@/pages/dev/DesignGallery')) : null;

type Session =
  | { readonly phase: 'loading' }
  | { readonly phase: 'anonymous' }
  | { readonly phase: 'signed-in'; readonly me: Me }
  /** Reachable, but not answering. Distinct from anonymous, deliberately. */
  | { readonly phase: 'unavailable'; readonly error: ApiError };

export default function App(): React.JSX.Element {
  const route = useRoute();
  const queryClient = useQueryClient();
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
    // Nothing one admin's session fetched may be shown to whoever signs in next.
    queryClient.clear();
    setSession({ phase: 'anonymous' });
    navigate({ name: 'login' }, { replace: true });
  }, [queryClient]);

  const signedInMe = session.phase === 'signed-in' ? session.me : null;
  useEffect(() => {
    if (signedInMe !== null) applyTheme(asTheme(signedInMe.theme));
  }, [signedInMe]);

  const context = useMemo<SessionValue | null>(
    () =>
      signedInMe === null
        ? null
        : {
            me: signedInMe,
            onSignedOut: signedOut,
            onRefresh: () => {
              void refresh();
            },
            setMe: (me: Me) => {
              setSession({ phase: 'signed-in', me });
            },
          },
    [signedInMe, signedOut, refresh],
  );

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
      <main className="auth">
        <header>
          <h1 className="auth__brand">
            <BrandMark />
            Tracelet
          </h1>
        </header>
        <Loading label="Checking your session…" />
      </main>
    );
  }

  if (session.phase === 'unavailable') {
    return (
      <main className="auth">
        <header>
          <h1 className="auth__brand">
            <BrandMark />
            Tracelet
          </h1>
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

  if (context === null) {
    // Unreachable: every other phase returned above. Kept so the types need no cast.
    return <Loading label="Checking your session…" />;
  }

  return (
    <SessionContext.Provider value={context}>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Page component={OverviewPage} />} />
          <Route path="visits" element={<Page component={VisitsPage} />} />
          <Route path="visits/:visitId" element={<Page component={VisitDetailPage} />} />
          <Route path="visitors/:visitorId" element={<Page component={VisitorPage} />} />
          <Route path="geography" element={<Page component={GeographyPage} />} />
          <Route path="breakdowns" element={<Page component={BreakdownsPage} />} />
          <Route path="links" element={<Page component={LinksPage} />} />
          <Route path="links/:slug" element={<Page component={LinkDetailPage} />} />
          <Route path="inference" element={<Page component={InferencePage} />} />
          <Route path="detection" element={<Page component={DetectionPage} />} />
          <Route path="geofences" element={<Page component={GeofencesPage} />} />
          <Route path="geofences/new" element={<Page component={GeofenceEditorPage} />} />
          <Route path="geofences/:geofenceId" element={<Page component={GeofenceEditorPage} />} />
          <Route path="alerts" element={<Page component={AlertsPage} />} />
          <Route path="health" element={<Page component={HealthLayout} />}>
            <Route index element={<Page component={HealthOverviewPage} />} />
            <Route path="databases" element={<Page component={HealthDatabasesPage} />} />
            <Route path="inference" element={<Page component={HealthInferencePage} />} />
            <Route path="data" element={<Page component={HealthDataPage} />} />
            <Route path="limits" element={<Page component={HealthLimitsPage} />} />
          </Route>
          <Route path="settings" element={<Page component={SettingsLayout} />}>
            <Route index element={<Navigate to="profile" replace />} />
            <Route path="profile" element={<Page component={ProfilePage} />} />
            <Route path="preferences" element={<Page component={PreferencesPage} />} />
            <Route path="security" element={<Page component={SecurityPage} />} />
            <Route path="sessions" element={<Page component={SessionsPage} />} />
            <Route path="team" element={<Page component={TeamPage} />} />
            <Route path="system" element={<Page component={SystemPage} />} />
          </Route>
          {/* M1-M5.5's single Account page now lives in Settings (DESIGN §10.8). */}
          <Route path="account" element={<Navigate to="/settings/profile" replace />} />
          {DesignGallery !== null && (
            <Route path="__design" element={<Page component={DesignGallery} />} />
          )}
          {/* M1's URL, and the sign-in page for an already signed-in admin. */}
          <Route path="dashboard" element={<Navigate to="/" replace />} />
          <Route path="login" element={<Navigate to="/" replace />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </SessionContext.Provider>
  );
}

function Page({
  component: Component,
}: {
  readonly component: React.LazyExoticComponent<() => React.JSX.Element>;
}): React.JSX.Element {
  return (
    <Suspense fallback={<Loading label="Loading the page…" />}>
      <Component />
    </Suspense>
  );
}

function NotFound(): React.JSX.Element {
  return (
    <div className="page">
      <PageHeader title="Page not found" description="There is nothing at this address." />
      <EmptyState
        title={<span className="t-mono">{window.location.pathname}</span>}
        reason="Check the address, or go back to the overview."
        action={
          <Button
            variant="primary"
            onClick={() => {
              navigate({ name: 'dashboard' });
            }}
          >
            Go to the overview
          </Button>
        }
      />
    </div>
  );
}
