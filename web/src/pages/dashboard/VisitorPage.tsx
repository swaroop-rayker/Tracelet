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
import { Badge, DataTable, Identifier } from '@/components/ui';
import { Panel } from '@/components/Panel';
import { label, when } from '@/format';
import { useSession } from '@/session';
import { placeLabel, placeOf } from '@/visits';
import { PageHeader } from '@/components/shell/PageHeader';

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
        <PageHeader title="Visitor" />
        <p className="error-text">
          That is not a visitor ID. It should be 32 hexadecimal characters.
        </p>
      </div>
    );
  }

  return (
    <div className="page">
      <PageHeader
        title="Visitor"
        description={<Identifier value={visitorId} label="Copy visitor id" full />}
        actions={
          <Link
            className="btn"
            to={`/visits?visitor_id=${visitorId}&range=365d&include_automated=true`}
          >
            Show in the visits timeline
          </Link>
        }
      />
      <Panel
        query={query}
        kind="table"
        title="Every visit"
        description="Oldest first, across all links and every classification."
        isEmpty={(d) => d.visit_count === 0}
        empty="No visits by this visitor are retained."
      >
        {(data) => (
          <div className="stack">
            <p className="t-secondary m-0">
              {data.visit_count.toLocaleString()} {data.visit_count === 1 ? 'visit' : 'visits'}
              {data.first_seen === null ? '' : `, first ${when(data.first_seen, me.timezone)}`}
              {data.last_seen === null ? '' : `, last ${when(data.last_seen, me.timezone)}`}.
              {data.truncated ? ' Only the first 500 are shown.' : ''}
            </p>
            <DataTable
              caption="Visits by this visitor"
              compact
              rowKey={(row) => row.id}
              rows={data.visits}
              columns={[
                {
                  key: 'when',
                  header: 'When',
                  render: (row) => (
                    <Link className="link" to={`/visits/${row.id}`}>
                      {when(row.occurred_at, me.timezone)}
                    </Link>
                  ),
                },
                { key: 'link', header: 'Link', render: (row) => row.link.slug },
                {
                  key: 'class',
                  header: 'Class',
                  render: (row) => (
                    <Badge dot={row.classification}>{label(row.classification)}</Badge>
                  ),
                },
                { key: 'where', header: 'Location', render: (row) => placeLabel(placeOf(row)) },
                {
                  key: 'device',
                  header: 'Device',
                  render: (row) =>
                    `${label(row.device.class)} · ${row.device.os ?? '?'} · ${row.device.browser ?? '?'}`,
                },
                {
                  key: 'net',
                  header: 'Network',
                  render: (row) => row.network.asn_org ?? 'unknown',
                },
              ]}
            />
            <div className="subsection">
              <h3 className="t-section">Changes between consecutive visits</h3>
              {data.drift.length === 0 ? (
                <p className="t-meta m-0">Only one visit, so nothing to compare.</p>
              ) : (
                <DataTable
                  caption="What changed between visits"
                  compact
                  rowKey={(d) => `${d.from_visit}-${d.to_visit}`}
                  rows={data.drift}
                  columns={[
                    { key: 'at', header: 'At', render: (d) => when(d.at, me.timezone) },
                    {
                      key: 'loc',
                      header: 'Location (best guess)',
                      render: (d) =>
                        d.location_changed.length === 0 ? (
                          <span className="muted">Same</span>
                        ) : (
                          <span className="badge-row">
                            {d.location_changed.map((level) => (
                              <Badge key={level}>{label(level)}</Badge>
                            ))}
                          </span>
                        ),
                    },
                    {
                      key: 'km',
                      header: 'Distance',
                      numeric: true,
                      render: (d) =>
                        d.distance_km === null ? '—' : `${d.distance_km.toLocaleString()} km`,
                    },
                    {
                      key: 'device',
                      header: 'Device',
                      render: (d) =>
                        d.device_changed.length === 0 ? (
                          <span className="muted">Same</span>
                        ) : (
                          <span className="badge-row">
                            {d.device_changed.map((f) => (
                              <Badge key={f}>{label(f)}</Badge>
                            ))}
                          </span>
                        ),
                    },
                    {
                      key: 'net',
                      header: 'Network',
                      render: (d) =>
                        d.network_changed ? (
                          <Badge tone="warn">Changed</Badge>
                        ) : (
                          <span className="muted">Same</span>
                        ),
                    },
                  ]}
                />
              )}
            </div>
          </div>
        )}
      </Panel>
    </div>
  );
}
