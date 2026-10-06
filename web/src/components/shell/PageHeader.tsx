/**
 * The page template's head (DESIGN §9.3): the `h1`, one line of description, the period and
 * page actions beside it, and the filter toolbar beneath on analytics pages.
 */

import type { ReactNode } from 'react';
import { FilterToolbar, PeriodPicker } from '@/components/shell/FilterToolbar';
import { useSession } from '@/session';

export function PageHeader({
  title,
  description,
  actions,
  filters = false,
  linkLocked = false,
  status,
}: {
  readonly title: ReactNode;
  readonly description?: ReactNode;
  /** Secondary page actions (export, group by); the period is added on filtered pages. */
  readonly actions?: ReactNode;
  /** Show the period picker and the filter toolbar (analytics pages). */
  readonly filters?: boolean;
  /** The page is one link's (DESIGN §10.11): the toolbar leaves out the link selector. */
  readonly linkLocked?: boolean;
  /** A freshness indicator or a count, beside the title (DESIGN §12 E7). */
  readonly status?: ReactNode;
}): React.JSX.Element {
  const { me } = useSession();
  return (
    <header className="page-head">
      <div className="page-head__row">
        <div className="page-head__titles">
          <div className="page-head__title-line">
            <h1 className="t-title">{title}</h1>
            {status}
          </div>
          {description !== undefined && <p className="t-secondary m-0">{description}</p>}
        </div>
        {(filters || actions !== undefined) && (
          <div className="page-head__actions">
            {actions}
            {filters && <PeriodPicker zone={me.reporting_tz} />}
          </div>
        )}
      </div>
      {filters && <FilterToolbar linkSelect={!linkLocked} />}
    </header>
  );
}
