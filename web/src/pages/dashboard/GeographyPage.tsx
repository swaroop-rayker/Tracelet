/**
 * Geography (F9.AC5): the visit map (components/map/VisitMap) and, under it, every country
 * and state as a ranked list, each of which filters the dashboard to itself (DESIGN 12 E3);
 * then mobile networks by state (M7.6, F9.AC23).
 * The map's own rules -- best-guess location, no tiles, colour never alone -- are documented
 * where it is drawn.
 */

import { useState } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router';
import { useApi } from '@/api/query';
import { carriersSchema, geoSchema, type Carriers, type Geo } from '@/api/schemas';
import VisitMap, { type Layer } from '@/components/map/VisitMap';
import { Panel } from '@/components/Panel';
import { PageHeader } from '@/components/shell/PageHeader';
import { filterForBreakdown } from '@/components/shell/filterDefs';
import { DataTable, RankedList, SegmentedControl, Select, type RankedRow } from '@/components/ui';
import { parseFilters, serializeFilters, withParams, type Filters } from '@/filters';
import { count, countryName, pct } from '@/format';
import { useFilters } from '@/session';

export default function GeographyPage(): React.JSX.Element {
  const { params } = useFilters();
  // States first: where in a country visits came from is the usual question.
  const [layer, setLayer] = useState<Layer>('admin1');
  const [cell, setCell] = useState('0.25');
  const query = useApi(
    '/api/v1/analytics/geo',
    withParams(params, { cell_degrees: cell }),
    geoSchema,
  );

  return (
    <div className="page">
      <PageHeader
        title="Geography"
        description="Where visits came from, at their best-guess location."
        filters
      />
      <Panel
        query={query}
        title="Where visits came from"
        description="Points are consented GPS or the best-guess city, clustered. Click an area to highlight it; Esc or the sea clears it."
        isEmpty={(d) => d.countries.length === 0 && d.abstained === 0}
        empty="No visits in this period with these filters."
        meta={(d) => d.meta}
        kind="map"
        actions={
          <>
            <SegmentedControl
              label="Shade by"
              value={layer}
              onChange={(v) => {
                setLayer(v === 'admin1' ? 'admin1' : 'countries');
              }}
              options={[
                { value: 'admin1', label: 'States' },
                { value: 'countries', label: 'Countries' },
              ]}
            />
            <Select
              label="Cluster size"
              size="sm"
              value={cell}
              onChange={setCell}
              options={[
                { value: '0.05', label: 'Clusters ~5 km' },
                { value: '0.25', label: 'Clusters ~25 km' },
                { value: '1', label: 'Clusters ~100 km' },
                { value: '5', label: 'Clusters ~500 km' },
              ]}
            />
          </>
        }
      >
        {(data) => <MapView data={data} layer={layer} />}
      </Panel>
      <CarriersPanel params={params} />
    </div>
  );
}

const FAMILY_LABEL: readonly (readonly [key: string, label: string])[] = [
  ['jio', 'Jio'],
  ['airtel', 'Airtel'],
  ['vi', 'Vi'],
  ['bsnl', 'BSNL'],
  ['other', 'Other'],
];

/**
 * Mobile networks by state (F9.AC23, DESIGN §12 E32): carriers and mobile vs broadband per
 * best-guess state. The state is a guess and says so, with its confidence (ADR-0018, §7.3);
 * every share carries its count in its accessible name and tooltip.
 */
function CarriersPanel({ params }: { readonly params: URLSearchParams }): React.JSX.Element {
  const query = useApi('/api/v1/analytics/carriers', params, carriersSchema);
  return (
    <Panel
      query={query}
      kind="table"
      title="Mobile networks by state (best guess)"
      description="The carrier comes from the network's ASN; an unlisted network is Other. Each state is the best guess, with its mean confidence."
      isEmpty={(d) => d.states.length === 0}
      empty={(d) =>
        d.unplaced > 0
          ? `No visit in this period could be placed in a state (${count(d.unplaced)} unplaced).`
          : 'No visits in this period with these filters.'
      }
      meta={(d) => d.meta}
    >
      {(d) => <CarriersTable data={d} />}
    </Panel>
  );
}

function share(n: number, of: number): React.JSX.Element {
  const text = pct(of > 0 ? n / of : null, 0);
  return (
    <span title={`${count(n)} of ${count(of)}`} aria-label={`${text}, ${count(n)} of ${count(of)}`}>
      {text}
    </span>
  );
}

function CarriersTable({ data }: { readonly data: Carriers }): React.JSX.Element {
  return (
    <>
      <DataTable
        caption="Carriers and connection type by best-guess state"
        rowKey={(s) => s.key}
        rows={data.states}
        columns={[
          {
            key: 'state',
            header: 'State',
            render: (s) => {
              const [country = '', admin1 = ''] = s.key.split('|');
              return (
                <span>
                  {admin1}, {countryName(country)}{' '}
                  <span className="t-meta">· {pct(s.confidence, 0)}</span>
                </span>
              );
            },
          },
          { key: 'visits', header: 'Visits', numeric: true, render: (s) => count(s.visits) },
          ...FAMILY_LABEL.map(([key, name]) => ({
            key,
            header: name,
            numeric: true,
            render: (s: Carriers['states'][number]) => share(s.families[key] ?? 0, s.visits),
          })),
          {
            key: 'mobile',
            header: 'Mobile',
            numeric: true,
            render: (s) => share(s.mobile, s.visits),
          },
          {
            key: 'broadband',
            header: 'Broadband',
            numeric: true,
            render: (s) => share(s.broadband, s.visits),
          },
        ]}
      />
      {data.unplaced > 0 && (
        <p className="t-meta m-0">
          {count(data.unplaced)} visit{data.unplaced === 1 ? '' : 's'} could not be placed in a
          state.
        </p>
      )}
    </>
  );
}
function MapView({
  data,
  layer,
}: {
  readonly data: Geo;
  readonly layer: Layer;
}): React.JSX.Element {
  const navigate = useNavigate();
  const location = useLocation();
  const [search] = useSearchParams();
  const apply = (next: Filters | null): void => {
    if (next !== null) {
      void navigate({ pathname: location.pathname, search: serializeFilters(next).toString() });
    }
  };
  const total = data.countries.reduce((n, c) => n + c.count, 0) + data.abstained;
  const share = (n: number): number | null => (total > 0 ? n / total : null);
  const countryRows: RankedRow[] = data.countries.map((c) => ({
    key: c.country_code,
    label: countryName(c.country_code),
    count: c.count,
    share: share(c.count),
  }));
  const stateRows: RankedRow[] = data.admin1.map((a) => ({
    key: `${a.country_code}|${a.admin1}`,
    label: `${a.admin1}, ${countryName(a.country_code)}`,
    count: a.count,
    share: share(a.count),
  }));

  return (
    <div className="stack">
      <VisitMap data={data} layer={layer} />
      <div className="grid-2">
        <div className="subsection">
          <h3 className="t-section">Countries</h3>
          <RankedList
            caption="Visits by country"
            labelHeader="Country"
            rows={countryRows}
            onSelect={(key) => {
              apply(filterForBreakdown('country', key, parseFilters(search)));
            }}
          />
        </div>
        <div className="subsection">
          <h3 className="t-section">States and provinces</h3>
          <RankedList
            caption="Visits by state or province"
            labelHeader="State or province"
            rows={stateRows}
            onSelect={(key) => {
              apply(filterForBreakdown('admin1', key, parseFilters(search)));
            }}
          />
        </div>
      </div>
    </div>
  );
}
