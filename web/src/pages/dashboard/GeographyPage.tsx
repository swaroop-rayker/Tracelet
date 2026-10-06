/**
 * Geography (F9.AC5): the visit map (components/map/VisitMap) and, under it, every country
 * and state as a ranked list, each of which filters the dashboard to itself (DESIGN 12 E3).
 * The map's own rules -- best-guess location, no tiles, colour never alone -- are documented
 * where it is drawn.
 */

import { useState } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router';
import { useApi } from '@/api/query';
import { geoSchema, type Geo } from '@/api/schemas';
import VisitMap, { type Layer } from '@/components/map/VisitMap';
import { Panel } from '@/components/Panel';
import { PageHeader } from '@/components/shell/PageHeader';
import { filterForBreakdown } from '@/components/shell/filterDefs';
import { RankedList, SegmentedControl, Select, type RankedRow } from '@/components/ui';
import { parseFilters, serializeFilters, withParams, type Filters } from '@/filters';
import { countryName } from '@/format';
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
    </div>
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
