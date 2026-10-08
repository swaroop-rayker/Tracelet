/**
 * One link's dashboard (DESIGN §10.11, §12 E20): the Overview panels with `link_id` pinned,
 * every other filter and the period kept. The toolbar leaves out its link selector, because
 * this page *is* the link. A slug that matches no link is the not-found state, never an
 * empty dashboard that looks like "no visits".
 */

import { Suspense, lazy } from 'react';
import { Link, useLocation, useParams } from 'react-router';
import { useApi } from '@/api/query';
import { linksSchema } from '@/api/schemas';
import {
  BreakdownPanel,
  FunnelPanel,
  LiveFeedPanel,
  SummarySection,
  TimeSeriesPanel,
} from '@/components/dashboard/panels';
import { PageHeader } from '@/components/shell/PageHeader';
import { EmptyState, ErrorNotice, Loading } from '@/components/ui';
import { withParams } from '@/filters';
import { LinkStatus } from '@/pages/dashboard/LinksPage';
import { useFilters } from '@/session';

// Its own chunk, with the QR encoder (ADR-0023, UI-24).
const LinkBuilder = lazy(() => import('@/pages/dashboard/LinkBuilder'));

export default function LinkDetailPage(): React.JSX.Element {
  const { slug = '' } = useParams();
  const location = useLocation();
  const { params, zone } = useFilters();
  const links = useApi(
    '/api/v1/links',
    new URLSearchParams({ include_archived: 'true' }),
    linksSchema,
  );

  if (links.isPending) return <Loading label="Loading the link…" />;
  if (links.isError) return <ErrorNotice error={links.error.error} />;
  const link = links.data.find((l) => l.slug === slug);
  if (link === undefined) {
    return (
      <div className="page">
        <PageHeader title="Link not found" />
        <EmptyState
          title={`No link is called “${slug}”`}
          reason="It may have been renamed. The Links page lists every one, archived ones included."
          action={
            <Link className="link" to={{ pathname: '/links', search: location.search }}>
              See every link
            </Link>
          }
        />
      </div>
    );
  }

  const own = withParams(params, { link_id: link.id });
  return (
    <div className="page">
      <PageHeader
        title={link.label}
        status={<LinkStatus link={link} />}
        description={
          <span className="link-route">
            <span className="mono">{link.capture_url}</span>
            <span aria-label="redirects to"> → </span>
            <span className="mono" title={link.destination_url}>
              {link.destination_url}
            </span>
          </span>
        }
        filters
        linkLocked
      />
      <SummarySection params={own} />
      <TimeSeriesPanel params={own} zone={zone} splits={false} />
      <div className="grid-2">
        <BreakdownPanel
          params={own}
          dimension="app_medium"
          title="App or browser"
          description="Which app’s in-app browser opened the link, or a normal browser."
          labelHeader="App or browser"
        />
        <BreakdownPanel
          params={own}
          dimension="admin1"
          title="Top states"
          description="Best-guess location. Choose one to filter this link’s figures to it."
          labelHeader="State"
        />
      </div>
      <div className="grid-2">
        <LiveFeedPanel params={own} zone={zone} />
        <FunnelPanel params={own} />
      </div>
      <Suspense fallback={<Loading kind="text" label="Loading the link builder…" />}>
        <LinkBuilder captureUrl={link.capture_url} slug={link.slug} />
      </Suspense>
    </div>
  );
}
