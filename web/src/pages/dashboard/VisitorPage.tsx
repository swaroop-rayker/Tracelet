/**
 * One visitor over time (F9.AC12): every visit by a `visitor_id`, with location drift
 * and device changes between consecutive visits.
 *
 * Drift compares **advisory** locations -- the question is "did they move?", which
 * strict abstains on too often to answer -- and is labelled as a guess.
 */

import { Link, useParams } from 'react-router';
import { useApi } from '@/api/query';
import { visitorViewSchema } from '@/api/schemas';
import { TableView } from '@/components/EChart';
import { Panel } from '@/components/Panel';
import { label, when } from '@/format';
import { useSession } from '@/session';
import { placeOf } from '@/visits';

export default function VisitorPage(): React.JSX.Element {
  const { visitorId = '' } = useParams();
  const { me } = useSession();
  const valid = /^[0-9a-f]{32}$/i.test(visitorId);
  const query = useApi(`/api/v1/analytics/visitor/${visitorId}`, null, visitorViewSchema, {
    enabled: valid,
  });

  if (!valid) {
    return (
      <div className="page">
        <h2 className="page-title">Visitor</h2>
        <p className="error-text">
          That is not a visitor ID. It should be 32 hexadecimal characters.
        </p>
      </div>
    );
  }

  return (
    <div className="page">
      <h2 className="page-title">Visitor</h2>
      <p className="mono small">{visitorId}</p>
      <Panel
        query={query}
        title="Every visit"
        description="Oldest first, across all links and every classification."
        isEmpty={(d) => d.visit_count === 0}
        empty="No visits by this visitor are retained."
      >
        {(data) => (
          <div className="stack">
            <p>
              {data.visit_count.toLocaleString()} {data.visit_count === 1 ? 'visit' : 'visits'}
              {data.first_seen === null ? '' : `, first ${when(data.first_seen, me.timezone)}`}
              {data.last_seen === null ? '' : `, last ${when(data.last_seen, me.timezone)}`}.
              {data.truncated ? ' Only the first 500 are shown.' : ''}
            </p>
            <TableView
              caption="Visits"
              table={{
                columns: ['When', 'Link', 'Class', 'Location', 'Device', 'Network'],
                rows: data.visits.map((v) => [
                  when(v.occurred_at, me.timezone),
                  v.link.slug,
                  label(v.classification),
                  placeOf(v).text,
                  `${label(v.device.class)} · ${v.device.os ?? '?'} · ${v.device.browser ?? '?'}`,
                  v.network.asn_org ?? 'unknown',
                ]),
              }}
            />
            <h4>Changes between consecutive visits</h4>
            {data.drift.length === 0 ? (
              <p className="muted">Only one visit, so nothing to compare.</p>
            ) : (
              <TableView
                caption="Drift"
                table={{
                  columns: [
                    'At',
                    'Location changed (advisory)',
                    'Distance',
                    'Device changed',
                    'Network changed',
                  ],
                  rows: data.drift.map((d) => [
                    when(d.at, me.timezone),
                    d.location_changed.length === 0
                      ? 'No'
                      : d.location_changed.map(label).join(', '),
                    d.distance_km === null ? '—' : `${d.distance_km.toLocaleString()} km`,
                    d.device_changed.length === 0 ? 'No' : d.device_changed.join(', '),
                    d.network_changed ? 'Yes' : 'No',
                  ]),
                }}
              />
            )}
            <p>
              <Link to={`/visits?visitor_id=${visitorId}&range=365d&include_automated=true`}>
                Show these visits in the timeline
              </Link>
            </p>
          </div>
        )}
      </Panel>
    </div>
  );
}
