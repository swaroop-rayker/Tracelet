/**
 * Geography (F9.AC5): a choropleth by country or Indian state, and clustered points.
 *
 * **Strict location only.** The map shows where the engine was willing to place
 * visits; abstentions are counted beside it, never drawn somewhere convenient
 * (CLAUDE.md invariant 5).
 *
 * Boundaries are Natural Earth (public domain) in its India point-of-view variant,
 * simplified and served from /geo (scripts/build-boundaries.mjs). Indian states are
 * drawn; other countries' states are listed in the table. The basemap is CARTO raster
 * tiles, no key, sent no referrer (ADR-0003).
 *
 * Colour is never the only signal (NFR7.AC3): every shaded area has a text tooltip and
 * the legend states its range in numbers, and the tables under the map carry every
 * figure. The map itself is keyboard-pannable (Leaflet's own handling).
 */

import 'leaflet/dist/leaflet.css';
import { useEffect, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import { useApi } from '@/api/query';
import { geoSchema, type Geo } from '@/api/schemas';
import { TableView } from '@/components/EChart';
import { escapeHtml } from '@/components/chartkit';
import { Panel } from '@/components/Panel';
import { withParams } from '@/filters';
import { count, countryName } from '@/format';
import { useFilters } from '@/session';
import { palette, type Palette } from '@/theme';

type Layer = 'countries' | 'in-states';

/** Features carry `{code, name}` (countries) or `{country, name, iso}` (states). */
type Boundaries = GeoJSON.FeatureCollection;

const cache = new Map<Layer, Promise<Boundaries>>();

/** Our own static file, but still proven before Leaflet is handed it. */
function isBoundaries(value: unknown): value is Boundaries {
  return (
    typeof value === 'object' &&
    value !== null &&
    (value as { type?: unknown }).type === 'FeatureCollection' &&
    Array.isArray((value as { features?: unknown }).features)
  );
}

function boundaries(layer: Layer): Promise<Boundaries> {
  let pending = cache.get(layer);
  if (pending === undefined) {
    pending = fetch(`/geo/${layer}.json`).then(async (response) => {
      if (!response.ok) throw new Error(`boundaries ${String(response.status)}`);
      const body: unknown = await response.json();
      if (!isBoundaries(body)) throw new Error('boundaries malformed');
      return body;
    });
    pending.catch(() => cache.delete(layer));
    cache.set(layer, pending);
  }
  return pending;
}

/** Five classes by count, so the legend can state each range as numbers. */
function classes(values: readonly number[]): number[] {
  const max = Math.max(0, ...values);
  if (max <= 5) return [1, 2, 3, 4, 5].map((n) => Math.min(n, Math.max(max, 1)));
  return [0.2, 0.4, 0.6, 0.8, 1].map((f) => Math.ceil(max * f));
}

export default function GeographyPage(): React.JSX.Element {
  const { params } = useFilters();
  const [layer, setLayer] = useState<Layer>('countries');
  const [cell, setCell] = useState('0.25');
  const query = useApi(
    '/api/v1/analytics/geo',
    withParams(params, { cell_degrees: cell }),
    geoSchema,
  );

  return (
    <div className="page">
      <h2 className="page-title">Geography</h2>
      <Panel
        query={query}
        title="Where visits came from"
        description="Strict locations only. Points are consented GPS or a strict city's coordinates, clustered on a grid."
        isEmpty={(d) => d.countries.length === 0 && d.abstained === 0}
        empty="No visits in this period with these filters."
        meta={(d) => d.meta}
        actions={
          <div className="panel-actions">
            <label className="control">
              <span>Shade by</span>
              <select
                value={layer}
                onChange={(event) => {
                  setLayer(event.target.value === 'in-states' ? 'in-states' : 'countries');
                }}
              >
                <option value="countries">Country</option>
                <option value="in-states">Indian state</option>
              </select>
            </label>
            <label className="control">
              <span>Cluster size</span>
              <select
                value={cell}
                onChange={(event) => {
                  setCell(event.target.value);
                }}
              >
                <option value="0.05">~5 km</option>
                <option value="0.25">~25 km</option>
                <option value="1">~100 km</option>
                <option value="5">~500 km</option>
              </select>
            </label>
          </div>
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
  const element = useRef<HTMLDivElement | null>(null);
  const map = useRef<L.Map | null>(null);
  const [themeTick, setThemeTick] = useState(0);
  const [boundaryError, setBoundaryError] = useState<string | null>(null);

  const counts = useMemo(() => {
    if (layer === 'countries') return new Map(data.countries.map((c) => [c.country_code, c.count]));
    return new Map(
      data.admin1.filter((a) => a.country_code === 'IN').map((a) => [a.admin1, a.count]),
    );
  }, [data, layer]);
  const steps = useMemo(() => classes([...counts.values()]), [counts]);

  useEffect(() => {
    if (element.current === null) return undefined;
    const instance = L.map(element.current, {
      center: layer === 'in-states' ? [22.5, 80] : [20, 30],
      zoom: layer === 'in-states' ? 4 : 2,
      worldCopyJump: true,
      keyboard: true,
    });
    map.current = instance;
    const onTheme = (): void => {
      setThemeTick((n) => n + 1);
    };
    window.addEventListener('tracelet:theme', onTheme);
    return () => {
      window.removeEventListener('tracelet:theme', onTheme);
      instance.remove();
      map.current = null;
    };
    // The map is created once per layer choice; data changes only redraw layers.
  }, [layer]);

  useEffect(() => {
    const instance = map.current;
    if (instance === null) return undefined;
    const p = palette();
    const group = L.layerGroup().addTo(instance);
    L.tileLayer(`https://{s}.basemaps.cartocdn.com/${p.tiles}_all/{z}/{x}/{y}{r}.png`, {
      subdomains: 'abcd',
      maxZoom: 12,
      referrerPolicy: 'no-referrer',
      attribution:
        '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors · © <a href="https://carto.com/attributions">CARTO</a> · Boundaries: Natural Earth',
    }).addTo(group);

    let cancelled = false;
    boundaries(layer)
      .then((collection) => {
        if (cancelled) return;
        setBoundaryError(null);
        L.geoJSON(collection, {
          style: (feature) => shade(featureCount(feature, layer, counts), steps, p),
          onEachFeature: (feature, shape) => {
            const n = featureCount(feature, layer, counts);
            const name = featureName(feature, layer);
            shape.bindTooltip(
              `${escapeHtml(name)}: ${n === 0 ? 'no visits' : `${count(n)} visits`}`,
              {
                sticky: true,
              },
            );
          },
        }).addTo(group);
        for (const point of data.points) {
          L.circleMarker([point.lat, point.lng], {
            radius: 4 + Math.sqrt(point.count) * 2,
            color: p.text,
            weight: 1,
            fillColor: p.series[1] ?? p.accent,
            fillOpacity: 0.8,
          })
            .bindTooltip(`${count(point.count)} visit${point.count === 1 ? '' : 's'} near here`)
            .addTo(group);
        }
      })
      .catch(() => {
        if (!cancelled)
          setBoundaryError('Boundaries could not be loaded; the tables below have every figure.');
      });

    return () => {
      cancelled = true;
      group.remove();
    };
  }, [data, layer, counts, steps, themeTick]);

  const countryRows = data.countries.map((c) => [countryName(c.country_code), c.count] as const);
  const stateRows = data.admin1.map((a) => [`${a.admin1} (${a.country_code})`, a.count] as const);
  const p = palette();

  return (
    <div className="stack">
      <div
        ref={element}
        className="map"
        role="region"
        aria-label="Map of visits by strict location. Use the arrow keys to pan and plus or minus to zoom."
      />
      {boundaryError !== null && (
        <p className="error-text small" role="alert">
          {boundaryError}
        </p>
      )}
      <Legend steps={steps} p={p} />
      <p className="muted small">
        {count(data.abstained)} visits are not on the map because the engine abstained at country
        level.{data.points_truncated ? ' Only the 2,000 largest clusters are drawn.' : ''}
      </p>
      <div className="grid-2">
        <TableView
          caption="Visits by country"
          table={{ columns: ['Country', 'Visits'], rows: countryRows }}
        />
        <TableView
          caption="Visits by state"
          table={{ columns: ['State', 'Visits'], rows: stateRows }}
        />
      </div>
    </div>
  );
}

function featureCount(
  feature: GeoJSON.Feature | undefined,
  layer: Layer,
  counts: Map<string, number>,
): number {
  const props = (feature?.properties ?? {}) as { code?: string; name?: string };
  const key = layer === 'countries' ? props.code : props.name;
  return key === undefined ? 0 : (counts.get(key) ?? 0);
}

function featureName(feature: GeoJSON.Feature | undefined, layer: Layer): string {
  const props = (feature?.properties ?? {}) as { code?: string; name?: string };
  if (layer === 'countries' && props.code !== undefined) return countryName(props.code);
  return props.name ?? 'Unknown';
}

function shade(n: number, steps: readonly number[], p: Palette): L.PathOptions {
  if (n === 0) return { color: p.border, weight: 0.5, fillColor: p.surface, fillOpacity: 0.1 };
  const index = steps.findIndex((s) => n <= s);
  return {
    color: p.border,
    weight: 0.6,
    fillColor: p.sequential[index === -1 ? p.sequential.length - 1 : index] ?? p.accent,
    fillOpacity: 0.75,
  };
}

function Legend({
  steps,
  p,
}: {
  readonly steps: readonly number[];
  readonly p: Palette;
}): React.JSX.Element {
  const ranges = steps.map((upper, i) => {
    const lower = i === 0 ? 1 : (steps[i - 1] ?? 0) + 1;
    return lower > upper
      ? null
      : {
          color: p.sequential[i] ?? p.accent,
          text: lower === upper ? count(upper) : `${count(lower)}–${count(upper)}`,
        };
  });
  return (
    <ul className="legend plain" aria-label="Map shading, visits per area">
      {ranges.map((r, i) =>
        r === null ? null : (
          <li key={i}>
            <Swatch color={r.color} />
            {r.text}
          </li>
        ),
      )}
    </ul>
  );
}

/** A colour sample drawn as SVG: an attribute, so the stylesheet CSP allows it. */
function Swatch({ color }: { readonly color: string }): React.JSX.Element {
  return (
    <svg width="14" height="14" aria-hidden="true" className="swatch">
      <rect width="14" height="14" rx="2" fill={color} />
    </svg>
  );
}
