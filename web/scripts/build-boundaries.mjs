// Builds the choropleth boundaries in web/public/geo/ (F9.AC5).
//
//   node scripts/build-boundaries.mjs <dir>
//
// <dir> holds four files, none of them committed:
//   ne_10m_admin_0_countries_ind.geojson     Natural Earth v5.1.2 countries, India point of
//                                            view (owner decision, M5). Public domain.
//   ne_10m_admin_1_states_provinces.geojson  Natural Earth v5.1.2 first-order divisions,
//                                            every country. Public domain.
//   cities1000.txt, admin1CodesASCII.txt     GeoNames (CC BY 4.0) -- the files the inference
//                                            engine itself loads (tracelet geodb status).
//
// Output (committed, so the build needs no network):
//   countries.json          every country, simplified
//   admin1/<CC>.json        one file per country: its first-order divisions, *named exactly
//                           as the engine names them*, fetched by the map only for countries
//                           that have state-level visits
//   admin1/index.json       which countries have a file
//
// **Why GeoNames decides the names.** The engine emits the GeoNames ASCII admin1 name
// (inference/geodb/geonames.py), so the choropleth must join on that, worldwide. Natural
// Earth's own names are local spellings ("Piemonte", where GeoNames says "Piedmont"), and
// in several countries its units are a level finer than GeoNames' (Italy's provinces,
// France's departments, the UK's council areas). So each Natural Earth polygon is
// assigned to a GeoNames admin1:
//   1. by code, where its `gn_a1_code` is a GeoNames admin1 code (exact);
//   2. otherwise by the admin1 most of the GeoNames places inside it belong to -- but only
//      if at least 60 % of them do. Below that, the polygon spans several GeoNames
//      divisions (Kenya's 8 old provinces against its 47 counties), and painting a whole
//      province as one county would be a confidently wrong map. It is left off the map;
//      those divisions are still counted in the table under it;
//   3. otherwise, with no place inside at all (a small island), by the nearest GeoNames
//      place in the same country.
// Polygons assigned to the same admin1 are then grouped into one feature, so 101 French
// departments become France's 13 regions -- the regions the engine reports.
//
// Zero dependencies on purpose (ES5).

import { readFileSync, writeFileSync, mkdirSync, rmSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const source = process.argv[2];
if (!source) {
  console.error('usage: node scripts/build-boundaries.mjs <dir with the four source files>');
  process.exit(2);
}
const out = join(here, '..', 'public', 'geo');
mkdirSync(out, { recursive: true });

// ---------------------------------------------------------------------------
// Simplification
// ---------------------------------------------------------------------------

/** Perpendicular distance from p to the segment a-b, in degrees. */
function distance(p, a, b) {
  const [x, y] = p;
  const [x1, y1] = a;
  const [x2, y2] = b;
  const dx = x2 - x1;
  const dy = y2 - y1;
  if (dx === 0 && dy === 0) return Math.hypot(x - x1, y - y1);
  const t = Math.max(0, Math.min(1, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)));
  return Math.hypot(x - (x1 + t * dx), y - (y1 + t * dy));
}

/** Iterative Douglas-Peucker; keeps the first and last point. */
function simplify(points, tolerance) {
  if (points.length < 3) return points;
  const keep = new Uint8Array(points.length);
  keep[0] = 1;
  keep[points.length - 1] = 1;
  const stack = [[0, points.length - 1]];
  while (stack.length > 0) {
    const [first, last] = stack.pop();
    let worst = 0;
    let index = -1;
    for (let i = first + 1; i < last; i += 1) {
      const d = distance(points[i], points[first], points[last]);
      if (d > worst) {
        worst = d;
        index = i;
      }
    }
    if (index !== -1 && worst > tolerance) {
      keep[index] = 1;
      stack.push([first, index], [index, last]);
    }
  }
  return points.filter((_, i) => keep[i] === 1);
}

function ring(points, tolerance, digits) {
  const factor = 10 ** digits;
  const rounded = simplify(points, tolerance).map(([x, y]) => [
    Math.round(x * factor) / factor,
    Math.round(y * factor) / factor,
  ]);
  // A ring needs four positions (a closed triangle) to remain a polygon.
  return rounded.length >= 4 ? rounded : null;
}

function polygon(rings, tolerance, digits) {
  const kept = [];
  for (const [i, r] of rings.entries()) {
    const simplified = ring(r, tolerance, digits);
    if (simplified) kept.push(simplified);
    else if (i === 0) return null; // the outer ring vanished: drop the whole polygon
  }
  return kept;
}

/** A geometry's polygons, each as a list of rings. */
function polygonsOf(geom) {
  return geom.type === 'Polygon' ? [geom.coordinates] : geom.coordinates;
}

function simplifiedPolygons(geom, tolerance, digits) {
  return polygonsOf(geom)
    .map((p) => polygon(p, tolerance, digits))
    .filter((p) => p !== null);
}

function asGeometry(polygons) {
  return polygons.length === 1
    ? { type: 'Polygon', coordinates: polygons[0] }
    : { type: 'MultiPolygon', coordinates: polygons };
}

function write(name, body) {
  const text = JSON.stringify(body);
  writeFileSync(join(out, name), text);
  return text.length;
}

// ---------------------------------------------------------------------------
// Countries
// ---------------------------------------------------------------------------

const countries = JSON.parse(
  readFileSync(join(source, 'ne_10m_admin_0_countries_ind.geojson'), 'utf8'),
);
const countryFeatures = countries.features
  .map((f) => {
    const p = f.properties;
    // ISO_A2 is -99 for a few countries with an unusual status (France, Norway); the
    // _EH column carries the code anyway.
    const code = p.ISO_A2 !== '-99' ? p.ISO_A2 : p.ISO_A2_EH;
    const polygons = simplifiedPolygons(f.geometry, 0.05, 2);
    if (code === '-99' || polygons.length === 0) return null;
    return { type: 'Feature', properties: { code, name: p.NAME }, geometry: asGeometry(polygons) };
  })
  .filter((f) => f !== null);
const countriesSize = write('countries.json', { type: 'FeatureCollection', features: countryFeatures });
console.log(`countries.json: ${countryFeatures.length} features, ${(countriesSize / 1024).toFixed(0)} KB`);

// ---------------------------------------------------------------------------
// GeoNames: admin1 names, and places to vote with
// ---------------------------------------------------------------------------

const admin1Names = new Map();
for (const line of readFileSync(join(source, 'admin1CodesASCII.txt'), 'utf8').split('\n')) {
  const fields = line.split('\t');
  // Field 2 is the ASCII name, which is what geonames.py::load_admin1 reads.
  if (fields.length >= 3) admin1Names.set(fields[0], fields[2] || fields[1]);
}

// Places bucketed on a one-degree grid, so a polygon only tests the places near it.
const grid = new Map();
const byCountry = new Map();
for (const line of readFileSync(join(source, 'cities1000.txt'), 'utf8').split('\n')) {
  const f = line.split('\t');
  if (f.length < 11) continue;
  const code = `${f[8]}.${f[10]}`;
  if (!admin1Names.has(code)) continue;
  const place = { lng: Number(f[5]), lat: Number(f[4]), code };
  const key = `${Math.floor(place.lng)},${Math.floor(place.lat)}`;
  if (!grid.has(key)) grid.set(key, []);
  grid.get(key).push(place);
  if (!byCountry.has(f[8])) byCountry.set(f[8], []);
  byCountry.get(f[8]).push(place);
}

function bbox(polygons) {
  let [minX, minY, maxX, maxY] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const p of polygons) {
    for (const [x, y] of p[0]) {
      minX = Math.min(minX, x);
      minY = Math.min(minY, y);
      maxX = Math.max(maxX, x);
      maxY = Math.max(maxY, y);
    }
  }
  return { minX, minY, maxX, maxY };
}

function inRing(x, y, r) {
  let inside = false;
  for (let i = 0, j = r.length - 1; i < r.length; j = i, i += 1) {
    const [xi, yi] = r[i];
    const [xj, yj] = r[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function inPolygons(x, y, polygons) {
  return polygons.some((p) => inRing(x, y, p[0]) && !p.slice(1).some((hole) => inRing(x, y, hole)));
}

const MAJORITY = 0.6;

/** The GeoNames admin1 most places inside belong to; `null` if no place is inside. */
function vote(polygons) {
  const b = bbox(polygons);
  const tally = new Map();
  for (let gx = Math.floor(b.minX); gx <= Math.floor(b.maxX); gx += 1) {
    for (let gy = Math.floor(b.minY); gy <= Math.floor(b.maxY); gy += 1) {
      for (const place of grid.get(`${gx},${gy}`) ?? []) {
        if (inPolygons(place.lng, place.lat, polygons)) {
          tally.set(place.code, (tally.get(place.code) ?? 0) + 1);
        }
      }
    }
  }
  let best = null;
  let most = 0;
  let all = 0;
  for (const [code, n] of tally) {
    all += n;
    if (n > most) {
      best = code;
      most = n;
    }
  }
  return best === null ? null : { code: best, share: most / all };
}

/** The admin1 of the GeoNames place nearest the polygon's centre, in one country. */
function nearest(polygons, country) {
  const b = bbox(polygons);
  const cx = (b.minX + b.maxX) / 2;
  const cy = (b.minY + b.maxY) / 2;
  let best = null;
  let closest = Infinity;
  for (const place of byCountry.get(country) ?? []) {
    const d = (place.lng - cx) ** 2 + (place.lat - cy) ** 2;
    if (d < closest) {
      closest = d;
      best = place.code;
    }
  }
  return best;
}

// ---------------------------------------------------------------------------
// First-order divisions, every country
// ---------------------------------------------------------------------------

const states = JSON.parse(
  readFileSync(join(source, 'ne_10m_admin_1_states_provinces.geojson'), 'utf8'),
);
const groups = new Map(); // GeoNames admin1 code -> simplified polygons
const how = { code: 0, vote: 0, nearest: 0, ambiguous: 0, dropped: 0 };
const ambiguous = new Map();
for (const f of states.features) {
  const p = f.properties;
  // Assignment uses the full-resolution polygon; only the output is simplified.
  const full = polygonsOf(f.geometry);
  let code = admin1Names.has(p.gn_a1_code) ? p.gn_a1_code : null;
  if (code !== null) how.code += 1;
  else {
    const voted = vote(full);
    if (voted !== null && voted.share < MAJORITY) {
      how.ambiguous += 1;
      ambiguous.set(p.iso_a2, (ambiguous.get(p.iso_a2) ?? 0) + 1);
      continue;
    }
    code = voted?.code ?? nearest(full, p.iso_a2);
    if (voted !== null) how.vote += 1;
    else if (code !== null) how.nearest += 1;
  }
  const simplified = simplifiedPolygons(f.geometry, 0.01, 3);
  if (code === null || simplified.length === 0) {
    how.dropped += 1;
    continue;
  }
  if (!groups.has(code)) groups.set(code, []);
  groups.get(code).push(...simplified);
}

rmSync(join(out, 'admin1'), { recursive: true, force: true });
rmSync(join(out, 'in-states.json'), { force: true });
mkdirSync(join(out, 'admin1'), { recursive: true });

const perCountry = new Map();
for (const [code, polygons] of groups) {
  const country = code.split('.')[0];
  if (!perCountry.has(country)) perCountry.set(country, []);
  perCountry.get(country).push({
    type: 'Feature',
    properties: { country, code, name: admin1Names.get(code) },
    geometry: asGeometry(polygons),
  });
}

let total = 0;
const index = {};
for (const [country, features] of [...perCountry].sort()) {
  features.sort((a, b) => a.properties.name.localeCompare(b.properties.name));
  total += write(`admin1/${country}.json`, { type: 'FeatureCollection', features });
  index[country] = features.length;
}
write('admin1/index.json', index);
const divisions = Object.values(index).reduce((a, b) => a + b, 0);
console.log(
  `admin1/: ${String(perCountry.size)} countries, ${String(divisions)} divisions, ` +
    `${(total / 1024).toFixed(0)} KB in all (largest ` +
    `${(Math.max(...[...perCountry.keys()].map((c) => readFileSync(join(out, 'admin1', `${c}.json`)).length)) / 1024).toFixed(0)} KB)`,
);
console.log(
  `assigned by GeoNames code ${String(how.code)}, by places inside ${String(how.vote)}, ` +
    `by nearest place ${String(how.nearest)}; left off as spanning several divisions ` +
    `${String(how.ambiguous)}; vanished in simplification ${String(how.dropped)}`,
);
console.log(
  `spanning several divisions, by country: ${[...ambiguous].sort((a, b) => b[1] - a[1]).map(([c, n]) => `${c} ${String(n)}`).join(', ')}`,
);
