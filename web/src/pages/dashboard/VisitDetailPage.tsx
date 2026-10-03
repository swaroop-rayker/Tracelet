/**
 * One visit, as a page (F9.AC14, DESIGN §10.3). The same body opens in a drawer from the
 * Visits list (E5); a direct link to /visits/:id always loads this full page.
 */

import { useParams } from 'react-router';
import { useApi } from '@/api/query';
import { visitDetailSchema } from '@/api/schemas';
import { PageHeader } from '@/components/shell/PageHeader';
import { Identifier } from '@/components/ui';
import { VisitDetailBody } from '@/components/visit/VisitDetail';
import { useSession } from '@/session';
import { visitTitle } from '@/visits';

export default function VisitDetailPage(): React.JSX.Element {
  const { visitId = '' } = useParams();
  const { me } = useSession();
  // The same request the body makes (one fetch, cached): the title needs the visit's time.
  const query = useApi(`/api/v1/visits/${encodeURIComponent(visitId)}`, null, visitDetailSchema);
  return (
    <div className="page">
      <PageHeader
        title={query.data === undefined ? 'Visit' : visitTitle(query.data.occurred_at, me.timezone)}
        description={<Identifier value={visitId} label="Copy visit id" full />}
      />
      <VisitDetailBody visitId={visitId} zone={me.timezone} />
    </div>
  );
}
