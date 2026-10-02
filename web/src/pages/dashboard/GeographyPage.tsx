/**
 * Geography (F9.AC5): a choropleth by country or by state/province -- worldwide -- and
 * clustered points.
 *
 * **Strict location only.** The map shows where the engine was willing to place visits;
 * abstentions are counted beside it, never drawn somewhere convenient (CLAUDE.md
 * invariant 5).
 *
 * Boundaries are Natural Earth (public domain), countries in the India point-of-view
 * variant, built by scripts/build-boundaries.mjs. First-order divisions ship as one file
 * per country, **named exactly as the inference engine names them** (GeoNames), and the
 * map fetches only the files for countries whose visits have a strict state. A state
 * with visits but no drawable boundary is named under the map, not dropped.
 *
 * **There are no map tiles** (ADR-0017): CARTO's basemap began requiring a key, so the map
 * is drawn from the shipped outlines alone -- land over a sea-coloured background -- and
 * the dashboard makes no request to a third party. Zoom stops where the outlines (about
 * 1 km) stop being faithful.
 *
 * Colour is never the only signal (NFR7.AC3): every shaded area has a text tooltip, the
 * legend states its ranges in numbers, and the tables under the map carry every figure.
 * The map itself is keyboard-pannable (Leaflet's own handling).
 */

import 'leaflet/dist/leaflet.css';
import { useEffect, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import { useApi } from '@/api/query';
import { geoSchema, type Geo } from '@/api/schemas';
import { escapeHtml } from '@/components/chartkit';
import { TableView } from '@/components/EChart';
import { Panel } from '@/components/Panel';
import { withParams } from '@/filters';
import { count, countryName } from '@/format';
import { useFilters } from '@/session';
import { palette, type Palette } from '@/theme';

type Layer = 'countries' | 'admin1';

/** Features carry `{code, name}` (countries) or `{country, code, name}` (divisions). */
type Boundaries = GeoJSON.FeatureCollection;

/** Our own static file, but still proven before Leaflet is handed it. */
function isBoundaries(value: unknown): value is Boundaries {
  return (
    typeof value === 'object' &&
    value !== null &&
    (value as { type?: unknown }).type === 'FeatureCollection' &&
    Array.isArray((value as { features?: unknown }).features)
  );
}

const cache = new Map<string, Promise<unknown>>();

function fetchJson(path: string): Promise<unknown> {
  let pending = cache.get(path);
  if (pending === undefined) {
    pending = fetch(path).then(async (response) => {
      if (!response.ok) throw new Error(`${path}: ${String(response.status)}`);
      return (await response.json()) as unknown;
    });
    pending.catch(() => cache.delete(path));
    cache.set(path, pending);
  }
  return pending;
}

async function boundaries(path: string): Promise<Boundaries> {
  const body = await fetchJson(path);
  if (!isBoundaries(body)) throw new Error(`${path}: malformed`);
  return body;
}

/** The countries that have a divisions file, and how many divisions each. */
async function divisionIndex(): Promise<Readonly<Record<string, number>>> {
  const body = await fetchJson('/geo/admin1/index.json');
  if (typeof body !== 'object' || body === null) throw new Error('admin1 index malformed');
  return body as Record<string, number>;
}

/** Five classes by count, so the legend can state each range as numbers. */
function classes(values: readonly number[]): number[] {
  const max = Math.max(0, ...values);
  if (max <= 5) return [1, 2, 3, 4, 5].map((n) => Math.min(n, Math.max(max, 1)));
  return [0.2, 0.4, 0.6, 0.8, 1].map((f) => Math.ceil(max * f));
}

function stateKey(country: string, name: string): string {
  return `${country}|${name}`;
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
                  setLayer(event.target.value === 'admin1' ? 'admin1' : 'countries');
                }}
              >
                <option value="countries">Country</option>
                <option value="admin1">State / province</option>
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
  const [undrawn, setUndrawn] = useState<readonly string[]>([]);

  const counts = useMemo(() => {
    if (layer === 'countries') return new Map(data.countries.map((c) => [c.country_code, c.count]));
    return new Map(data.admin1.map((a) => [stateKey(a.country_code, a.admin1), a.count]));
  }, [data, layer]);
  const steps = useMemo(() => classes([...counts.values()]), [counts]);

  useEffect(() => {
    if (element.current === null) return undefined;
    const instance = L.map(element.current, {
      center: [20, 30],
      zoom: 2,
      minZoom: 1,
      // The outlines are simplified to roughly 1 km; past this they stop being faithful.
      maxZoom: 7,
      worldCopyJump: true,
      keyboard: true,
    });
    instance.attributionControl.setPrefix(false);
    instance.attributionControl.addAttribution(
      'Boundaries: <a href="https://www.naturalearthdata.com/">Natural Earth</a> · Names: <a href="https://www.geonames.org/">GeoNames</a>',
    );
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
  }, []);

  useEffect(() => {
    const instance = map.current;
    if (instance === null) return undefined;
    const p = palette();
    const group = L.layerGroup().addTo(instance);
    // Read through a function: the cleanup sets it from another closure, which TypeScript's
    // narrowing cannot see -- a plain check reads as "always false" after the first one.
    const run = { cancelled: false };
    const stale = (): boolean => run.cancelled;
    const draw = async (): Promise<void> => {
      const countries = await boundaries('/geo/countries.json');
      if (stale()) return;
      if (layer === 'countries') {
        L.geoJSON(countries, {
          style: (feature) => shade(countryCount(feature, counts), steps, p),
          onEachFeature: (feature, shape) => {
            const n = countryCount(feature, counts);
            shape.bindTooltip(tip(featureName(feature), n), { sticky: true });
          },
        }).addTo(group);
        setUndrawn([]);
      } else {
        const index = await divisionIndex();
        const wanted = [...new Set(data.admin1.map((a) => a.country_code))].filter(
          (c) => index[c] !== undefined,
        );
        const files = await Promise.all(wanted.map((c) => boundaries(`/geo/admin1/${c}.json`)));
        if (stale()) return;
        // Land first, so countries without divisions on the map still read as land.
        L.geoJSON(countries, {
          style: () => shade(0, steps, p),
          interactive: false,
        }).addTo(group);
        const drawn = new Set<string>();
        const shaded: L.GeoJSON[] = [];
        for (const file of files) {
          const layerShapes = L.geoJSON(file, {
            style: (feature) => {
              const style = shade(divisionCount(feature, counts), steps, p);
              // A division may be several Natural Earth polygons grouped together; a
              // stroke in its own fill colour hides the seams between them.
              return { ...style, color: style.fillColor ?? p.border, weight: 0.5 };
            },
            onEachFeature: (feature, shape) => {
              const n = divisionCount(feature, counts);
              if (n > 0) drawn.add(divisionKey(feature));
              shape.bindTooltip(tip(featureName(feature), n), { sticky: true });
            },
          }).addTo(group);
          shaded.push(layerShapes);
        }
        // Country borders over the divisions, for context everywhere else.
        L.geoJSON(countries, {
          style: () => ({ color: p.muted, weight: 0.8, fill: false }),
          interactive: false,
        }).addTo(group);
        setUndrawn(
          data.admin1
            .filter((a) => !drawn.has(stateKey(a.country_code, a.admin1)))
            .map((a) => `${a.admin1} (${a.country_code}): ${count(a.count)}`),
        );
        const bounds = shaded.reduce<L.LatLngBounds | null>((acc, shapes) => {
          const b = shapes.getBounds();
          return acc === null ? b : acc.extend(b);
        }, null);
        if (bounds?.isValid() === true) {
          instance.fitBounds(bounds, { maxZoom: 6, padding: [16, 16] });
        }
      }
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
      setBoundaryError(null);
    };
    draw().catch(() => {
      if (!stale()) {
        setBoundaryError('Boundaries could not be loaded; the tables below have every figure.');
      }
    });

    return () => {
      run.cancelled = true;
      group.remove();
    };
  }, [data, layer, counts, steps, themeTick]);

  const countryRows = data.countries.map((c) => [countryName(c.country_code), c.count] as const);
  const stateRows = data.admin1.map(
    (a) => [`${a.admin1} (${countryName(a.country_code)})`, a.count] as const,
  );
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
      {layer === 'admin1' && undrawn.length > 0 && (
        <p className="muted small">
          Counted but not drawn, because no boundary matches the division exactly:{' '}
          {undrawn.join('; ')}.
        </p>
      )}
      <div className="grid-2">
        <TableView
          caption="Visits by country"
          table={{ columns: ['Country', 'Visits'], rows: countryRows }}
        />
        <TableView
          caption="Visits by state or province"
          table={{ columns: ['State or province', 'Visits'], rows: stateRows }}
        />
      </div>
    </div>
  );
}

interface FeatureProps {
  readonly code?: string;
  readonly name?: string;
  readonly country?: string;
}

function props(feature: GeoJSON.Feature | undefined): FeatureProps {
  const value: FeatureProps = feature?.properties ?? {};
  return value;
}

function countryCount(feature: GeoJSON.Feature | undefined, counts: Map<string, number>): number {
  const code = props(feature).code;
  return code === undefined ? 0 : (counts.get(code) ?? 0);
}

function divisionKey(feature: GeoJSON.Feature | undefined): string {
  const { country = '', name = '' } = props(feature);
  return stateKey(country, name);
}

function divisionCount(feature: GeoJSON.Feature | undefined, counts: Map<string, number>): number {
  return counts.get(divisionKey(feature)) ?? 0;
}

function featureName(feature: GeoJSON.Feature | undefined): string {
  const { code, name, country } = props(feature);
  if (country === undefined && code !== undefined) return countryName(code);
  return country === undefined
    ? (name ?? 'Unknown')
    : `${name ?? 'Unknown'}, ${countryName(country)}`;
}

function tip(name: string, n: number): string {
  return `${escapeHtml(name)}: ${n === 0 ? 'no visits' : `${count(n)} visits`}`;
}

function shade(n: number, steps: readonly number[], p: Palette): L.PathOptions {
  if (n === 0) return { color: p.border, weight: 0.5, fillColor: p.mapLand, fillOpacity: 1 };
  const index = steps.findIndex((s) => n <= s);
  return {
    color: p.border,
    weight: 0.6,
    fillColor: p.sequential[index === -1 ? p.sequential.length - 1 : index] ?? p.accent,
    fillOpacity: 0.9,
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
