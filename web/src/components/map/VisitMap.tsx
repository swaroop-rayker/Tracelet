/**
 * The visit map (F9.AC5): a choropleth by country or by state/province, worldwide, and
 * clustered points. Shared by the Geography page and the Overview card (DESIGN 12 E17), and
 * always loaded lazily by the latter, so Leaflet stays out of the Overview chunk (UI-24).
 *
 * **Best-guess location** (ADR-0018): each visit is shaded and plotted where the engine
 * thinks it most likely was. Visits no source could place in a country are counted beside the
 * map, never drawn somewhere convenient.
 *
 * Boundaries are Natural Earth (public domain), countries in the India point-of-view variant,
 * built by scripts/build-boundaries.mjs. First-order divisions ship as one file per country,
 * **named exactly as the inference engine names them** (GeoNames), and only the files for
 * countries whose visits have a state are fetched. A state with visits but no drawable
 * boundary is named under the map, not dropped.
 *
 * **There are no map tiles** (ADR-0017): the map is drawn from the shipped outlines alone and
 * makes no request to a third party. Zoom stops where the outlines (about 1 km) stop being
 * faithful.
 *
 * Colour is never the only signal (NFR7.AC3): every shaded area has a text tooltip and the
 * legend states its ranges in numbers. The map is keyboard-pannable (Leaflet's own handling).
 */

import 'leaflet/dist/leaflet.css';
import { useEffect, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import type { Geo } from '@/api/schemas';
import { escapeHtml } from '@/components/chartkit';
import { Alert, Legend, type Swatch } from '@/components/ui';
import { count, countryName } from '@/format';
import { palette, type Palette } from '@/theme';

export type Layer = 'countries' | 'admin1';

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

const POINTS_PANE = 'visit-points';

function stateKey(country: string, name: string): string {
  return `${country}|${name}`;
}

export type AreaKind = 'country' | 'admin1';

export default function VisitMap({
  data,
  layer,
  showPoints = true,
  compact = false,
  onArea,
}: {
  readonly data: Geo;
  readonly layer: Layer;
  /** Clustered points over the shading; off on the Overview card, which shades only. */
  readonly showPoints?: boolean;
  /** The card height instead of the page height. */
  readonly compact?: boolean;
  /** Called with `country` and `IN`, or `admin1` and `IN|Karnataka`, when an area is clicked. */
  readonly onArea?: ((kind: AreaKind, key: string) => void) | undefined;
}): React.JSX.Element {
  const element = useRef<HTMLDivElement | null>(null);
  const map = useRef<L.Map | null>(null);
  const [themeTick, setThemeTick] = useState(0);
  const [boundaryError, setBoundaryError] = useState<string | null>(null);
  const [undrawn, setUndrawn] = useState<readonly string[]>([]);
  // Read through a ref, so a new callback each render does not redraw the map.
  const onAreaRef = useRef(onArea);
  useEffect(() => {
    onAreaRef.current = onArea;
  }, [onArea]);

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
    // Points live in their own pane above the shapes, so an area brought to the front
    // by a hover or a click never covers them.
    instance.createPane(POINTS_PANE).style.zIndex = '450';
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

    // Hover brightens an area's border; a click selects it with a bold outline that stays
    // until the map background is clicked or Escape is pressed.
    const hover: L.PathOptions = { color: p.text, weight: 1.5, opacity: 1 };
    const chosen: L.PathOptions = { color: p.text, weight: 3, opacity: 1 };
    let selected: { readonly shape: L.Path; readonly reset: () => void } | null = null;
    const clear = (): void => {
      selected?.reset();
      selected = null;
    };
    const highlightable = (owner: () => L.GeoJSON, shape: L.Layer): void => {
      if (!(shape instanceof L.Path)) return;
      const reset = (): void => {
        owner().resetStyle(shape);
      };
      shape.on('mouseover', () => {
        if (selected?.shape !== shape) shape.setStyle(hover);
        shape.bringToFront();
      });
      shape.on('mouseout', () => {
        if (selected?.shape !== shape) reset();
      });
      shape.on('click', (event: L.LeafletMouseEvent) => {
        L.DomEvent.stopPropagation(event);
        if (selected?.shape === shape) return;
        clear();
        selected = { shape, reset };
        shape.setStyle(chosen);
        shape.bringToFront();
      });
    };
    const onKey = (event: L.LeafletKeyboardEvent): void => {
      if (event.originalEvent.key === 'Escape') clear();
    };
    // With `onArea`, a click on a shaded area also applies it as a filter (DESIGN 12 E17).
    const pickable = (shape: L.Layer, kind: AreaKind, key: string): void => {
      shape.on('click', () => {
        onAreaRef.current?.(kind, key);
      });
    };
    instance.on('click', clear);
    instance.on('keydown', onKey);

    const draw = async (): Promise<void> => {
      const countries = await boundaries('/geo/countries.json');
      if (stale()) return;
      if (layer === 'countries') {
        const shapes: L.GeoJSON = L.geoJSON(countries, {
          style: (feature) => shade(countryCount(feature, counts), steps, p),
          onEachFeature: (feature, shape) => {
            const n = countryCount(feature, counts);
            shape.bindTooltip(tip(featureName(feature), n), { sticky: true });
            highlightable(() => shapes, shape);
            const code = props(feature).code;
            if (code !== undefined) pickable(shape, 'country', code);
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
          const layerShapes: L.GeoJSON = L.geoJSON(file, {
            // Every division keeps a border, so neighbours in the same shading band
            // still read as separate states rather than one merged region.
            style: (feature) => ({
              ...shade(divisionCount(feature, counts), steps, p),
              color: p.muted,
              weight: 0.5,
              opacity: 0.8,
            }),
            onEachFeature: (feature, shape) => {
              const n = divisionCount(feature, counts);
              if (n > 0) drawn.add(divisionKey(feature));
              shape.bindTooltip(tip(featureName(feature), n), { sticky: true });
              highlightable(() => layerShapes, shape);
              if (n > 0) pickable(shape, 'admin1', divisionKey(feature));
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
          // Not animated: the first load draws twice in quick succession, and Leaflet
          // silently drops a view change made while a zoom animation is still running --
          // the map stayed on the whole world.
          instance.fitBounds(bounds, { maxZoom: 6, padding: [16, 16], animate: false });
        }
      }
      for (const point of showPoints ? data.points : []) {
        L.circleMarker([point.lat, point.lng], {
          pane: POINTS_PANE,
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
      instance.off('click', clear);
      instance.off('keydown', onKey);
      group.remove();
    };
  }, [data, layer, counts, steps, themeTick, showPoints]);

  return (
    <div className="stack">
      <div
        ref={element}
        className={compact ? 'map map--compact' : 'map'}
        role="region"
        aria-label="Map of visits by best-guess location. Use the arrow keys to pan and plus or minus to zoom."
      />
      {boundaryError !== null && <Alert tone="error" title={boundaryError} />}
      <div className="map-foot">
        <MapLegend steps={steps} />
        {(data.abstained > 0 || data.points_truncated) && (
          <span className="t-meta">
            {data.abstained > 0
              ? `${count(data.abstained)} visits are not on the map: no source could place their country.`
              : ''}
            {showPoints && data.points_truncated
              ? ' Only the 2,000 largest clusters are drawn.'
              : ''}
          </span>
        )}
      </div>
      {layer === 'admin1' && undrawn.length > 0 && (
        <Alert tone="info" title="Counted but not drawn">
          No outline matches these divisions exactly, so they are counted but not drawn:{' '}
          {undrawn.join('; ')}.
        </Alert>
      )}
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
  const visits = n === 0 ? 'no visits' : `${count(n)} visit${n === 1 ? '' : 's'}`;
  return `${escapeHtml(name)}: ${visits}`;
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

function MapLegend({ steps }: { readonly steps: readonly number[] }): React.JSX.Element {
  const items = steps.flatMap((upper, i) => {
    const lower = i === 0 ? 1 : (steps[i - 1] ?? 0) + 1;
    if (lower > upper) return [];
    const swatch = `seq-${String(i + 1)}` as Swatch;
    return [{ label: lower === upper ? count(upper) : `${count(lower)}–${count(upper)}`, swatch }];
  });
  return <Legend label="Map shading, visits per area" items={items} />;
}
