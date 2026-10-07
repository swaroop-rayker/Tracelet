/**
 * The global degradation banner (DESIGN §5.6, §16 M7; F10.AC14, NFR3.AC3): on every page,
 * the most severe critical or warning condition, how many more there are, and a link to
 * System health. Notices (download a backup) stay on System health's Overview. Polls every
 * 60 s while the tab is visible; says nothing when nothing is degraded, and nothing when the
 * check itself fails -- a broken banner must not become a banner.
 */

import { Link, useLocation } from 'react-router';
import { useApi } from '@/api/query';
import { HEALTH, degradationSchema } from '@/api/system';
import { Banner } from '@/components/ui';

const POLL_MS = 60_000;

export function DegradationBanner(): React.JSX.Element | null {
  const location = useLocation();
  const query = useApi(`${HEALTH}/degradation`, null, degradationSchema, {
    refetchInterval: POLL_MS,
  });
  const shown = (query.data?.conditions ?? []).filter((c) => c.severity !== 'notice');
  const top = shown[0];
  // The Overview lists every condition itself; repeating the first one above it is noise.
  if (top === undefined || location.pathname === '/health') return null;
  const more = shown.length - 1;
  return (
    <Banner
      tone={top.severity === 'critical' ? 'error' : 'warn'}
      action={<Link to="/health">System health ›</Link>}
    >
      <strong>{top.title}.</strong> {top.still_works}
      {more > 0 && ` (+${String(more)} more)`}
    </Banner>
  );
}
