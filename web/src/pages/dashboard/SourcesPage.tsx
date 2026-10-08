/**
 * Sources (F9.AC21, DESIGN §12 E30, §16 M7.6): where visits came from -- the site that linked
 * here, the campaign tags on the link, and the app it was opened in.
 *
 * The same ranked lists as Breakdowns (ADR-0019), under the same filters. A visit with no
 * referrer or no tag is counted as **None**, never hidden: in an in-app browser it is the
 * usual case. A named row applies its filter (E3); None does not, as there is no "absent"
 * filter to apply.
 */

import { sourceDimensions } from '@/api/schemas';
import { PageHeader } from '@/components/shell/PageHeader';
import { BreakdownPanel } from '@/pages/dashboard/BreakdownsPage';
import { useFilters } from '@/session';

export default function SourcesPage(): React.JSX.Element {
  const { params } = useFilters();
  const [referrer, ...tags] = sourceDimensions;
  return (
    <div className="page">
      <PageHeader
        title="Sources"
        description="Where visits came from: the site that linked here, the campaign tags on the link, and the app it was opened in. No referrer is common in apps: it is None."
        filters
      />
      <div className="grid-3">
        <BreakdownPanel dimension={referrer} params={params} />
        <BreakdownPanel dimension="app_medium" params={params} />
        {tags.map((dimension) => (
          <BreakdownPanel key={dimension} dimension={dimension} params={params} />
        ))}
      </div>
    </div>
  );
}
