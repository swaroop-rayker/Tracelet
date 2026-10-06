/**
 * How live the figures are (DESIGN §12 E7, UI-18): "Live · updated 10:25" beside the page
 * title, from the response's `refreshed_at` (F9.AC20). Amber once the rollups are more than
 * 15 minutes old -- today refreshes every five (F9.AC19), so that means the job is behind.
 */

import { cx, relativeTime } from '@/components/ui';

const STALE_MS = 15 * 60_000;

export function Freshness({
  iso,
  zone,
}: {
  readonly iso: string | null | undefined;
  readonly zone: string;
}): React.JSX.Element | null {
  if (iso === null || iso === undefined) return null;
  const stale = Date.now() - Date.parse(iso) > STALE_MS;
  const time = new Date(iso).toLocaleTimeString('en-IN', {
    timeZone: zone,
    hour: '2-digit',
    minute: '2-digit',
  });
  return (
    <span
      className={cx('fresh', stale && 'fresh--stale')}
      title={`Figures computed at ${time} (${zone})`}
    >
      <span className={cx('dot', stale ? 'dot--warn' : 'dot--ok')} aria-hidden="true" />
      {stale ? `Updated ${relativeTime(iso)}` : `Live · updated ${time}`}
    </span>
  );
}
