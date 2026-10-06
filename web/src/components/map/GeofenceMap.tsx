/**
 * The geofence editor's map (DESIGN §16, ADR-0020): the M5 outlines, no tiles, and the
 * drawing tools of Leaflet-Geoman (CSP-clean in three engines, ADR-0020 "Spike results").
 *
 * **What is drawn is the editor's state, not Geoman's.** A shape Geoman creates is read back
 * into a ring or a circle, handed to the editor, and removed; the map then draws the shape
 * from the editor's state. So typed coordinates, an imported shape and a drawn one are one
 * thing, and the map never holds a second, divergent copy.
 *
 * **Two views** (owner, 2026-10-06). With no country chosen, the world: every country, named,
 * no cities; clicking a country opens it. With a country chosen, that country alone: its
 * states and union territories, named, and its cities and towns. Clicking a division toggles
 * `IN|Karnataka` (Pick regions). A division's key comes from the region catalogue by its
 * GeoNames code, so the key is spelled exactly as the engine spells a strict state.
 *
 * **Names are printed on the map, never hover tooltips**: a sticky tooltip outlived the layer
 * it belonged to when the outlines were redrawn under the pointer, and stayed on screen (E58).
 * Area names and city names share one collision pass, largest first, so none overlap.
 *
 * Zoom stops at 9: the outlines are simplified to about 1 km, a few pixels at that zoom, and
 * past it they stop being truthful (ADR-0020 decision 1). Precise shapes are typed.
 *
 * Loaded lazily with the editor, so Geoman's 74 KB never reaches another page (UI-24
 * exception, DESIGN §16).
 */

import 'leaflet/dist/leaflet.css';
import '@geoman-io/leaflet-geoman-free/dist/leaflet-geoman.css';
import { useEffect, useRef, useState } from 'react';
import L from 'leaflet';
import '@geoman-io/leaflet-geoman-free';
import { Alert } from '@/components/ui';
import { countryName } from '@/format';
import { palette } from '@/theme';

export type Tool = 'select' | 'regions' | 'polygon' | 'circle' | 'edit' | 'move';

/** `[lat, lng]` pairs, the exterior ring, not closed. */
export type Ring = readonly (readonly [number, number])[];

export interface CircleShape {
  readonly lat: number;
  readonly lng: number;
  readonly radius_m: number;
}

/** A city or town for context (API §9 `/places`): never interactive, never a geofence. */
export interface MapPlace {
  readonly name: string;
  readonly lat: number;
  readonly lng: number;
  readonly tier: 'metro' | 'tier1' | 'tier2' | 'tier3';
}

export interface GeofenceMapProps {
  readonly tool: Tool;
  /** The chosen country's cities and towns, largest first. */
  readonly places: readonly MapPlace[];
  readonly regionKeys: readonly string[];
  /** The country opened on the map, or null for the world view. */
  readonly focusCountry: string | null;
  /** A country was clicked in the world view: open it. */
  readonly onFocusCountry: (country: string) => void;
  /** GeoNames admin1 code (`IN.19`) to region key (`IN|Karnataka`), from the catalogue. */
  readonly keyByCode: ReadonlyMap<string, string>;
  readonly ring: Ring | null;
  readonly circle: CircleShape | null;
  /** Where the server said the ring crosses itself (API §12 `location`). */
  readonly errorAt: { readonly lat: number; readonly lng: number } | null;
  readonly onToggleRegion: (key: string) => void;
  readonly onRing: (ring: Ring) => void;
  readonly onCircle: (circle: CircleShape) => void;
  /** A draw tool finished: the editor returns to Select. */
  readonly onToolDone: () => void;
}

type Outlines = GeoJSON.FeatureCollection;

const cache = new Map<string, Promise<Outlines>>();

function outlines(path: string): Promise<Outlines> {
  let pending = cache.get(path);
  if (pending === undefined) {
    pending = fetch(path).then(async (response) => {
      if (!response.ok) throw new Error(`${path}: ${String(response.status)}`);
      const body = (await response.json()) as unknown;
      if (
        typeof body !== 'object' ||
        body === null ||
        (body as { type?: unknown }).type !== 'FeatureCollection'
      ) {
        throw new Error(`${path}: malformed`);
      }
      return body as Outlines;
    });
    pending.catch(() => cache.delete(path));
    cache.set(path, pending);
  }
  return pending;
}

/** A string property of an outline feature, or null. Properties are untyped JSON. */
function prop(feature: GeoJSON.Feature | undefined, name: string): string | null {
  const props: unknown = feature?.properties;
  if (typeof props !== 'object' || props === null) return null;
  const value: unknown = (props as Record<string, unknown>)[name];
  return typeof value === 'string' ? value : null;
}

/** Which countries have a divisions file (`/geo/admin1/index.json`: code -> count). */
async function divisionIndex(): Promise<ReadonlySet<string>> {
  const response = await fetch('/geo/admin1/index.json');
  if (!response.ok) return new Set();
  const body: unknown = await response.json();
  return typeof body === 'object' && body !== null ? new Set(Object.keys(body)) : new Set();
}

/** Where an area's name goes: the centre of its largest part, and that part's extent. */
interface Anchor {
  readonly name: string;
  readonly lat: number;
  readonly lng: number;
  readonly south: number;
  readonly west: number;
  readonly north: number;
  readonly east: number;
  readonly kind: 'country' | 'division';
}

function anchorOf(g: GeoJSON.Geometry, name: string, kind: Anchor['kind']): Anchor | null {
  const parts =
    g.type === 'Polygon' ? [g.coordinates] : g.type === 'MultiPolygon' ? g.coordinates : [];
  let best: { south: number; west: number; north: number; east: number } | null = null;
  let bestArea = -1;
  for (const part of parts) {
    const ring = part[0] ?? [];
    if (ring.length === 0) continue;
    let south = 90;
    let west = 180;
    let north = -90;
    let east = -180;
    for (const position of ring) {
      const [lng = 0, lat = 0] = position;
      south = Math.min(south, lat);
      north = Math.max(north, lat);
      west = Math.min(west, lng);
      east = Math.max(east, lng);
    }
    const area =
      (north - south) * (east - west) * Math.cos((((north + south) / 2) * Math.PI) / 180);
    if (area > bestArea) {
      bestArea = area;
      best = { south, west, north, east };
    }
  }
  if (best === null) return null;
  return {
    ...best,
    name,
    kind,
    lat: (best.south + best.north) / 2,
    lng: (best.west + best.east) / 2,
  };
}

function ringOf(layer: L.Polygon): Ring {
  const rings = layer.getLatLngs() as L.LatLng[][];
  return (rings[0] ?? []).map((p) => [p.lat, p.lng] as const);
}

const MAX_ZOOM = 9;
/** A shape smaller than this on screen is zoomed to, so it can be seen and edited. */
const MIN_SHAPE_PX = 40;

/**
 * Layer order, bottom to top (ERRORS E57). Leaflet puts every vector layer in one pane,
 * ordered by when it was added -- so outlines redrawn after a shape (a theme change, a region
 * toggle) painted their opaque land over it. Fixed panes make the order structural.
 */
const OUTLINE_PANE = 'geofence-outlines';
const PLACE_PANE = 'geofence-places';
const PANE_Z = { [OUTLINE_PANE]: '350', [PLACE_PANE]: '380' } as const; // overlayPane is 400

/** The zoom at which each tier appears, and at which its name appears (DESIGN §16). */
const SHOWN_AT = { metro: 0, tier1: 5, tier2: 6, tier3: 7 } as const;
const NAMED_AT = { metro: 0, tier1: 6, tier2: 7, tier3: 8 } as const;
const DOT = { metro: 5, tier1: 4, tier2: 3, tier3: 2.5 } as const;
/** A label's footprint for collision checks: the meta size (12 px) is about 7 px a letter. */
const LABEL_H = 14;
const LABEL_CHAR_W = 7;
/** Area names are smaller spaced capitals: about 7.5 px a letter. */
const AREA_CHAR_W = 7.5;

/**
 * Cities and area names, drawn on one canvas in the places pane (E59).
 *
 * Layout per redraw, all in screen pixels and only for what is on screen (plus a margin):
 * every visible city's dot reserves its own box; then metro names; then area names, largest
 * area first, centred or nudged a line up or down, never over a dot, and only where the area
 * has room; then the other cities' names, right of the dot or else left. A uniform grid keeps
 * the collision test near-constant per label instead of a scan of every label placed.
 */
class NameLayer extends L.Layer {
  private canvas: HTMLCanvasElement | null = null;
  private frame = 0;
  /** The view the canvas was last drawn for, so a zoom can scale it from there. */
  private drawnCenter: L.LatLng | null = null;
  private drawnZoom = 0;

  constructor(
    private readonly places: readonly MapPlace[],
    private readonly anchors: readonly Anchor[],
  ) {
    super({ pane: PLACE_PANE });
  }

  override onAdd(map: L.Map): this {
    // leaflet-zoom-animated: the transform set on 'zoomanim' is transitioned by Leaflet's own
    // zoom animation, as its canvas renderer's is.
    const canvas = L.DomUtil.create(
      'canvas',
      'name-canvas leaflet-zoom-animated',
      map.getPane(PLACE_PANE),
    );
    this.canvas = canvas;
    map.on('move zoomend resize viewreset', this.schedule);
    // While zooming the names grow or shrink with the map and are redrawn crisp at the end.
    // Hiding them instead made every zoom step blink (owner report, E60).
    map.on('zoomanim', this.onZoomAnim);
    map.on('zoom', this.onZoom);
    this.redraw();
    return this;
  }

  override onRemove(map: L.Map): this {
    map.off('move zoomend resize viewreset', this.schedule);
    map.off('zoomanim', this.onZoomAnim);
    map.off('zoom', this.onZoom);
    if (this.frame !== 0) L.Util.cancelAnimFrame(this.frame);
    this.canvas?.remove();
    this.canvas = null;
    return this;
  }

  private readonly onZoomAnim = (event: L.ZoomAnimEvent): void => {
    this.scaleTo(event.center, event.zoom);
  };

  private readonly onZoom = (): void => {
    const map = this._map as L.Map | undefined;
    if (map !== undefined) this.scaleTo(map.getCenter(), map.getZoom());
  };

  /**
   * Scale the canvas as drawn to where the map is zooming: Leaflet's Renderer
   * `_updateTransform`, without its private API. Undone by the next redraw.
   */
  private scaleTo(center: L.LatLng, zoom: number): void {
    const map = this._map as L.Map | undefined;
    const canvas = this.canvas;
    if (map === undefined || canvas === null || this.drawnCenter === null) return;
    const scale = map.getZoomScale(zoom, this.drawnZoom);
    const half = map.getSize().divideBy(2);
    const panePos = L.DomUtil.getPosition(map.getPane('mapPane') ?? canvas);
    const origin = map.project(center, zoom).subtract(half).add(panePos).round();
    const offset = half
      .multiplyBy(-scale)
      .add(map.project(this.drawnCenter, zoom))
      .subtract(origin);
    L.DomUtil.setTransform(canvas, offset, scale);
  }

  private readonly schedule = (): void => {
    if (this.frame !== 0) return;
    this.frame = L.Util.requestAnimFrame(() => {
      this.frame = 0;
      this.redraw();
    });
  };

  private redraw(): void {
    const map = this._map as L.Map | undefined;
    const canvas = this.canvas;
    if (map === undefined || canvas === null) return;
    this.drawnCenter = map.getCenter();
    this.drawnZoom = map.getZoom();
    const size = map.getSize();
    const ratio = window.devicePixelRatio || 1;
    if (canvas.width !== size.x * ratio || canvas.height !== size.y * ratio) {
      canvas.width = size.x * ratio;
      canvas.height = size.y * ratio;
      // CSSOM, not a style attribute: allowed by the CSP.
      canvas.style.width = `${String(size.x)}px`;
      canvas.style.height = `${String(size.y)}px`;
    }
    // The canvas covers the visible map, wherever the pane has been panned to.
    L.DomUtil.setPosition(canvas, map.containerPointToLayerPoint([0, 0]));
    const ctx = canvas.getContext('2d');
    if (ctx === null) return;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, size.x, size.y);

    const p = palette();
    const root = getComputedStyle(document.documentElement);
    const sea = root.getPropertyValue('--map-sea').trim() || p.surface;
    const land = root.getPropertyValue('--map-land').trim() || p.surface2;
    const font = getComputedStyle(canvas).fontFamily || 'sans-serif';
    const zoom = map.getZoom();
    const view = map.getBounds().pad(0.1);
    const taken = new Grid(64);
    let named = 0;
    let areas = 0;
    const metros: string[] = [];

    const visible = this.places
      .filter((pl) => zoom >= SHOWN_AT[pl.tier] && view.contains([pl.lat, pl.lng]))
      .map((pl) => ({ place: pl, at: map.latLngToContainerPoint([pl.lat, pl.lng]) }));
    for (const { place, at } of visible) {
      const r = DOT[place.tier] + 1;
      taken.add(L.bounds([at.x - r, at.y - r], [at.x + r, at.y + r]));
    }

    const text = (
      value: string,
      x: number,
      y: number,
      align: CanvasTextAlign,
      colour: string,
      halo: string,
      weight: number,
      px: number,
    ): void => {
      ctx.font = `${String(weight)} ${String(px)}px ${font}`;
      ctx.textAlign = align;
      ctx.textBaseline = 'middle';
      ctx.lineJoin = 'round';
      ctx.lineWidth = 3;
      ctx.strokeStyle = halo;
      ctx.strokeText(value, x, y);
      ctx.fillStyle = colour;
      ctx.fillText(value, x, y);
    };

    const nameCity = (place: MapPlace, at: L.Point): void => {
      if (zoom < NAMED_AT[place.tier]) return;
      const width = 6 + place.name.length * LABEL_CHAR_W;
      const top = at.y - LABEL_H / 2 - 2;
      const bottom = at.y + LABEL_H / 2 + 2;
      const right = L.bounds([at.x + 2, top], [at.x + 2 + width, bottom]);
      const left = L.bounds([at.x - 2 - width, top], [at.x - 2, bottom]);
      const dot = L.bounds(
        [at.x - DOT[place.tier] - 1, at.y - DOT[place.tier] - 1],
        [at.x + DOT[place.tier] + 1, at.y + DOT[place.tier] + 1],
      );
      const metro = place.tier === 'metro';
      for (const [box, x, align] of [
        [right, at.x + DOT[place.tier] + 4, 'left'],
        [left, at.x - DOT[place.tier] - 4, 'right'],
      ] as const) {
        if (taken.free(box, dot)) {
          taken.add(box);
          text(place.name, x, at.y, align, metro ? p.text : p.muted, sea, metro ? 600 : 400, 12);
          named += 1;
          if (metro) metros.push(place.name);
          return;
        }
      }
    };

    for (const { place, at } of visible) if (place.tier === 'metro') nameCity(place, at);

    const extent = (x: Anchor): number => (x.north - x.south) * (x.east - x.west);
    for (const anchor of [...this.anchors].sort((x, y) => extent(y) - extent(x))) {
      if (!view.contains([anchor.lat, anchor.lng])) continue;
      const at = map.latLngToContainerPoint([anchor.lat, anchor.lng]);
      const ne = map.latLngToContainerPoint([anchor.north, anchor.east]);
      const sw = map.latLngToContainerPoint([anchor.south, anchor.west]);
      const country = anchor.kind === 'country';
      const value = country ? anchor.name : anchor.name.toUpperCase();
      const width = value.length * AREA_CHAR_W;
      if (Math.abs(ne.x - sw.x) < width * 0.8 || Math.abs(ne.y - sw.y) < LABEL_H) continue;
      for (const dy of [0, -LABEL_H, LABEL_H]) {
        const box = L.bounds(
          [at.x - width / 2, at.y + dy - LABEL_H / 2],
          [at.x + width / 2, at.y + dy + LABEL_H / 2],
        );
        if (!taken.free(box)) continue;
        taken.add(box);
        text(
          value,
          at.x,
          at.y + dy,
          'center',
          country ? p.muted : p.subtle,
          land,
          500,
          country ? 12 : 11,
        );
        areas += 1;
        break;
      }
    }

    for (const { place, at } of visible) if (place.tier !== 'metro') nameCity(place, at);

    // Dots last, over every name's halo.
    for (const { place, at } of visible) {
      const metro = place.tier === 'metro';
      ctx.beginPath();
      ctx.arc(at.x, at.y, DOT[place.tier], 0, Math.PI * 2);
      ctx.fillStyle = metro ? p.text : p.muted;
      ctx.globalAlpha = 0.9;
      ctx.fill();
      ctx.globalAlpha = 1;
    }
    // For tests and for anyone inspecting the map: what is on it, without a DOM per name.
    canvas.dataset['dots'] = String(visible.length);
    canvas.dataset['named'] = String(named);
    canvas.dataset['areas'] = String(areas);
    canvas.dataset['metros'] = metros.join('|');
  }
}

/** Boxes bucketed into square cells, so a collision test looks only at its neighbours. */
class Grid {
  private readonly cells = new Map<string, L.Bounds[]>();

  constructor(private readonly size: number) {}

  private keys(box: L.Bounds): string[] {
    const min = box.min ?? L.point(0, 0);
    const max = box.max ?? L.point(0, 0);
    const out: string[] = [];
    for (let x = Math.floor(min.x / this.size); x <= Math.floor(max.x / this.size); x += 1) {
      for (let y = Math.floor(min.y / this.size); y <= Math.floor(max.y / this.size); y += 1) {
        out.push(`${String(x)}:${String(y)}`);
      }
    }
    return out;
  }

  add(box: L.Bounds): void {
    for (const key of this.keys(box)) {
      const cell = this.cells.get(key);
      if (cell === undefined) this.cells.set(key, [box]);
      else cell.push(box);
    }
  }

  /** Whether `box` overlaps nothing, ignoring `except` (a label's own dot). */
  free(box: L.Bounds, except?: L.Bounds): boolean {
    for (const key of this.keys(box)) {
      for (const other of this.cells.get(key) ?? []) {
        if (other !== except && !sameBox(other, except) && other.intersects(box)) return false;
      }
    }
    return true;
  }
}

function sameBox(a: L.Bounds, b: L.Bounds | undefined): boolean {
  return (
    b !== undefined &&
    a.min?.x === b.min?.x &&
    a.min?.y === b.min?.y &&
    a.max?.x === b.max?.x &&
    a.max?.y === b.max?.y
  );
}

export default function GeofenceMap(props: GeofenceMapProps): React.JSX.Element {
  const element = useRef<HTMLDivElement | null>(null);
  const map = useRef<L.Map | null>(null);
  const latest = useRef(props);
  latest.current = props;
  const [themeTick, setThemeTick] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);

  // --- the map, once -------------------------------------------------------
  useEffect(() => {
    if (element.current === null) return undefined;
    const instance = L.map(element.current, {
      center: [22, 80],
      zoom: 4,
      minZoom: 2,
      maxZoom: MAX_ZOOM,
      // Quarter steps when fitting, so a country fills the map rather than half of it; the
      // buttons and the wheel still move a whole step.
      zoomSnap: 0.25,
      zoomDelta: 1,
      worldCopyJump: true,
      keyboard: true,
    });
    for (const [name, z] of Object.entries(PANE_Z)) {
      // CSSOM, not a style attribute: allowed by the CSP (as VisitMap's points pane).
      const pane = instance.createPane(name);
      pane.style.zIndex = z;
    }
    instance.attributionControl.setPrefix(false);
    instance.attributionControl.addAttribution(
      'Boundaries: <a href="https://www.naturalearthdata.com/">Natural Earth</a> · Names: <a href="https://www.geonames.org/">GeoNames</a>',
    );
    // Geoman's own toolbar is not used: the editor's tool rail drives it (DESIGN §16).
    instance.pm.setGlobalOptions({ snappable: false });
    instance.on('pm:create', (event: { layer: L.Layer }) => {
      const { layer } = event;
      const p = latest.current;
      if (layer instanceof L.Circle) {
        const c = layer.getLatLng();
        p.onCircle({ lat: c.lat, lng: c.lng, radius_m: Math.round(layer.getRadius()) });
      } else if (layer instanceof L.Polygon) {
        p.onRing(ringOf(layer));
      }
      instance.removeLayer(layer);
      p.onToolDone();
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
  }, []);

  // --- outlines and region picking -----------------------------------------
  const { tool, regionKeys, focusCountry, keyByCode } = props;
  const fittedCountry = useRef<string | null | undefined>(undefined);
  const [anchors, setAnchors] = useState<readonly Anchor[]>([]);
  useEffect(() => {
    const instance = map.current;
    if (instance === null) return undefined;
    const p = palette();
    const selected = new Set(regionKeys);
    // Countries some of whose states are picked: outlined in the world view, not filled.
    const partly = new Set(regionKeys.filter((k) => k.includes('|')).map((k) => k.split('|')[0]));
    const picking = tool === 'regions';
    const world = focusCountry === null;
    const opening = world && (tool === 'select' || tool === 'regions');
    const group = L.layerGroup().addTo(instance);
    const run = { cancelled: false };
    const style = (key: string | null, division: boolean, interactive: boolean): L.PathOptions => {
      const on = key !== null && selected.has(key);
      const part = !division && key !== null && partly.has(key);
      return {
        pane: OUTLINE_PANE,
        color: on || part ? p.accent : p.borderStrong,
        weight: on ? 2.5 : part ? 1.5 : division ? 0.75 : world ? 0.5 : 1,
        opacity: 1,
        fillColor: on ? p.accentBg : p.mapLand,
        fillOpacity: on ? 0.85 : division ? 0 : 1,
        interactive,
      };
    };
    /** A hover outline: the sign that a click will do something. */
    const hoverable = (layer: L.Layer, base: L.PathOptions): void => {
      if (!(layer instanceof L.Path)) return;
      layer.on('mouseover', () => {
        layer.setStyle({ color: p.text, weight: Math.max(1.5, base.weight ?? 1) });
      });
      layer.on('mouseout', () => {
        layer.setStyle(base);
      });
    };
    // Read through a function: the cleanup sets it from another closure, which narrowing
    // cannot see (as in VisitMap).
    const stale = (): boolean => run.cancelled;
    void (async () => {
      try {
        const countries = await outlines('/geo/countries.json');
        if (stale()) return;
        const found: Anchor[] = [];
        const country = L.geoJSON(countries, {
          pane: OUTLINE_PANE,
          // The world, or the chosen country alone (owner decision 2026-10-06).
          filter: (f) => world || prop(f, 'code') === focusCountry,
          style: (f) => style(prop(f, 'code'), false, opening || picking),
          onEachFeature: (f, layer) => {
            const code = prop(f, 'code');
            if (code === null) return;
            if (world) {
              const anchor = anchorOf(f.geometry, countryName(code), 'country');
              if (anchor !== null) found.push(anchor);
            }
            if (opening) {
              hoverable(layer, style(code, false, true));
              layer.on('click', (event: L.LeafletMouseEvent) => {
                L.DomEvent.stopPropagation(event);
                latest.current.onFocusCountry(code);
              });
            } else if (picking) {
              layer.on('click', (event: L.LeafletMouseEvent) => {
                L.DomEvent.stopPropagation(event);
                latest.current.onToggleRegion(code);
              });
            }
          },
        }).addTo(group);
        const shape = latest.current.ring !== null || latest.current.circle !== null;
        if (fittedCountry.current !== focusCountry && !shape) {
          if (world) {
            instance.setView([20, 10], 2, { animate: false });
          } else {
            const bounds = country.getBounds();
            if (bounds.isValid()) instance.fitBounds(bounds, { padding: [20, 20], animate: false });
          }
        }
        fittedCountry.current = focusCountry;
        if (focusCountry === null) {
          setAnchors(found);
          return;
        }
        const index = await divisionIndex();
        if (stale()) return;
        if (!index.has(focusCountry)) {
          setAnchors(found);
          return;
        }
        const divisions = await outlines(`/geo/admin1/${focusCountry}.json`);
        if (stale()) return;
        L.geoJSON(divisions, {
          pane: OUTLINE_PANE,
          style: (f) => {
            const code = prop(f, 'code');
            return style(code === null ? null : (keyByCode.get(code) ?? null), true, picking);
          },
          onEachFeature: (f, layer) => {
            const code = prop(f, 'code');
            const key = code === null ? null : (keyByCode.get(code) ?? null);
            const anchor = anchorOf(f.geometry, prop(f, 'name') ?? code ?? '', 'division');
            if (anchor !== null) found.push(anchor);
            if (picking && key !== null) {
              hoverable(layer, style(key, true, true));
              layer.on('click', (event: L.LeafletMouseEvent) => {
                L.DomEvent.stopPropagation(event);
                latest.current.onToggleRegion(key);
              });
            }
          },
        }).addTo(group);
        setAnchors(found);
      } catch (error) {
        if (!stale()) setLoadError(error instanceof Error ? error.message : 'unknown');
      }
    })();
    return () => {
      run.cancelled = true;
      // On unmount the map's own cleanup ran first (effects clean up in order); touching a
      // removed map throws `_leaflet_pos` (ERRORS E55).
      if (map.current === instance) group.remove();
    };
  }, [tool, regionKeys, focusCountry, keyByCode, themeTick]);

  // --- names on the map: areas, then cities and towns, thinned by zoom -----------
  // One canvas for every dot and name (E59). A DOM element per label leaked on each zoom --
  // 1,605 labels for 1,110 cities after five steps -- and Leaflet moved every one of them on
  // every frame of a zoom, which was the stutter. Only what is on screen is drawn, once per
  // animation frame at most, and there is nothing to leak.
  const { places } = props;
  useEffect(() => {
    const instance = map.current;
    if (instance === null) return undefined;
    const layer = new NameLayer(places, anchors);
    layer.addTo(instance);
    return () => {
      if (map.current === instance) layer.remove();
    };
  }, [places, anchors, themeTick]);

  // --- the shape, drawn from state ------------------------------------------
  const { ring, circle, errorAt } = props;
  useEffect(() => {
    const instance = map.current;
    if (instance === null) return undefined;
    const p = palette();
    const shapeStyle: L.PathOptions = {
      color: p.accent,
      weight: 2.5,
      fillColor: p.accent,
      fillOpacity: 0.15,
    };
    let shape: L.Polygon | L.Circle | null = null;
    if (ring !== null && ring.length >= 3) {
      shape = L.polygon(
        ring.map(([lat, lng]) => L.latLng(lat, lng)),
        shapeStyle,
      );
    } else if (circle !== null) {
      shape = L.circle([circle.lat, circle.lng], { ...shapeStyle, radius: circle.radius_m });
    }
    if (shape !== null) {
      shape.addTo(instance);
      const read = (): void => {
        if (shape instanceof L.Circle) {
          const c = shape.getLatLng();
          latest.current.onCircle({
            lat: c.lat,
            lng: c.lng,
            radius_m: Math.round(shape.getRadius()),
          });
        } else if (shape instanceof L.Polygon) {
          latest.current.onRing(ringOf(shape));
        }
      };
      if (tool === 'edit') {
        shape.pm.enable({ allowSelfIntersection: true, snappable: false });
        shape.on('pm:edit', read);
      } else if (tool === 'move') {
        shape.pm.enableLayerDrag();
        shape.on('pm:dragend', read);
      }
    }
    const marker =
      errorAt === null
        ? null
        : L.circleMarker([errorAt.lat, errorAt.lng], {
            radius: 9,
            color: p.error,
            weight: 3,
            fillColor: p.error,
            fillOpacity: 0.35,
          })
            .bindTooltip('The ring crosses itself here', { permanent: true, direction: 'top' })
            .addTo(instance);
    return () => {
      if (map.current !== instance) return; // the map is gone, and its layers with it
      if (shape !== null) {
        if (shape.pm.enabled()) shape.pm.disable();
        shape.remove();
      }
      marker?.remove();
    };
  }, [ring, circle, errorAt, tool, themeTick]);

  // --- draw tools ---------------------------------------------------------
  useEffect(() => {
    const instance = map.current;
    if (instance === null) return undefined;
    const p = palette();
    const pathOptions = { color: p.accent, fillColor: p.accent, fillOpacity: 0.15, weight: 2.5 };
    if (tool === 'polygon') {
      instance.pm.enableDraw('Polygon', {
        snappable: false,
        pathOptions,
        templineStyle: pathOptions,
        hintlineStyle: { ...pathOptions, dashArray: [5, 5] },
      });
    } else if (tool === 'circle') {
      instance.pm.enableDraw('Circle', {
        snappable: false,
        pathOptions,
        templineStyle: pathOptions,
        hintlineStyle: { ...pathOptions, dashArray: [5, 5] },
      });
    }
    return () => {
      if (map.current === instance) instance.pm.disableDraw();
    };
  }, [tool]);

  // --- keep the shape in view -------------------------------------------------
  // Whenever the shape is not wholly in view -- drawn, loaded, or *typed* somewhere else --
  // the map moves to it. Keyed only on "a new shape" before, so a circle whose centre was typed
  // elsewhere was redrawn off-screen and looked as if it had vanished (owner report, E57).
  // A shape edited in view does not move the map.
  useEffect(() => {
    const instance = map.current;
    if (instance === null) return;
    let bounds: L.LatLngBounds | null = null;
    if (ring !== null && ring.length >= 3) {
      bounds = L.latLngBounds(ring.map(([lat, lng]) => L.latLng(lat, lng)));
    } else if (circle !== null) {
      bounds = L.latLng(circle.lat, circle.lng).toBounds(circle.radius_m * 2);
    }
    if (bounds === null || !bounds.isValid()) return;
    // In view and big enough to see: leave the map alone. A speck (a 2 km circle at country
    // zoom) is zoomed to, as is anything out of view.
    const ne = instance.latLngToContainerPoint(bounds.getNorthEast());
    const sw = instance.latLngToContainerPoint(bounds.getSouthWest());
    const speck = Math.max(Math.abs(ne.x - sw.x), Math.abs(ne.y - sw.y)) < MIN_SHAPE_PX;
    if (instance.getBounds().contains(bounds) && !speck) return;
    instance.fitBounds(bounds, { padding: [40, 40], maxZoom: MAX_ZOOM, animate: false });
  }, [ring, circle]);

  return (
    <div className="geofence-map-wrap">
      <div
        ref={element}
        className="map geofence-map"
        role="application"
        aria-label="Geofence map. Use the tool rail to pick regions or draw a shape; coordinates can also be typed in the inspector."
      />
      {loadError !== null && (
        <Alert tone="warn" title="The outlines did not load">
          Regions can still be chosen from the list in the inspector. ({loadError})
        </Alert>
      )}
    </div>
  );
}
