/**
 * Overview (DESIGN §10.1): KPIs with sparklines (F9.AC2, E16), visits over time with the
 * previous period (F9.AC3, E6), the top states (E4) beside the live feed (E18), the map card
 * (E17) beside the hour × weekday heatmap (E19), and the calendar (F9.AC6, E21) beside the
 * stage funnel (F9.AC8) -- all from existing endpoints -- and, since M7.7, the notes (F9.AC25). The panels live in
 * components/dashboard/panels, shared with a link's own page (§10.11).
 *
 * The seven KPIs: four primary cards and a strip for the other three, so all seven stay
 * visible without a ragged second row (audit A2, A3).
 */

import { useApi } from '@/api/query';
import { summarySchema } from '@/api/schemas';
import {
  BreakdownPanel,
  CalendarPanel,
  FunnelPanel,
  HourWeekdayPanel,
  LiveFeedPanel,
  MapCardPanel,
  SummarySection,
  TimeSeriesPanel,
} from '@/components/dashboard/panels';
import { NotesPanel } from '@/components/dashboard/notes';
import { Freshness } from '@/components/shell/Freshness';
import { PageHeader } from '@/components/shell/PageHeader';
import { useFilters } from '@/session';

export default function OverviewPage(): React.JSX.Element {
  const { params, zone } = useFilters();
  const summary = useApi('/api/v1/analytics/summary', params, summarySchema);
  return (
    <div className="page">
      <PageHeader
        title="Overview"
        description="Visits to your links: how many, who they were, and how complete the picture is."
        filters
        status={<Freshness iso={summary.data?.meta.refreshed_at} zone={zone} />}
      />
      <SummarySection params={params} />
      <TimeSeriesPanel params={params} zone={zone} />
      <div className="grid-2">
        <BreakdownPanel
          params={params}
          dimension="admin1"
          title="Top states"
          description="Best-guess location. Choose one to filter the dashboard to it."
          labelHeader="State"
        />
        <LiveFeedPanel params={params} zone={zone} />
      </div>
      <div className="grid-2">
        <MapCardPanel params={params} />
        <HourWeekdayPanel params={params} zone={zone} />
      </div>
      <div className="grid-2">
        <CalendarPanel params={params} zone={zone} />
        <FunnelPanel params={params} />
      </div>
      <NotesPanel params={params} />
    </div>
  );
}
