// Builds the choropleth boundaries in web/public/geo/ from Natural Earth (F9.AC5).
//
//   node scripts/build-boundaries.mjs <dir containing the two source files>
//
// Sources (public domain, Natural Earth v5.1.2, github.com/nvkelso/natural-earth-vector):
//   ne_10m_admin_0_countries_ind.geojson   countries, India point of view (owner decision,
//                                          M5): India's official depiction of its borders
//   ne_10m_admin_1_states_provinces.geojson  only India's 36 states and union territories
//                                          are kept -- the primary audience (CLAUDE.md
//                                          section 1); other countries' states are listed
//                                          in tables, not drawn
//
// The sources are 54 MB together and are never committed. The output is simplified
// (Douglas-Peucker) and rounded, small enough to ship as static assets, and is
// committed so the build needs no network. Re-run only to move to a new Natural Earth
// release. Zero dependencies on purpose (ES5).

import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const source = process.argv[2];
if (!source) {
  console.error('usage: node scripts/build-boundaries.mjs <natural-earth-dir>');
  process.exit(2);
}
const out = join(here, '..', 'public', 'geo');
mkdirSync(out, { recursive: true });

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
  // A ring needs four positions (closed triangle) to remain a polygon.
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

function geometry(geom, tolerance, digits) {
  const polygons = geom.type === 'Polygon' ? [geom.coordinates] : geom.coordinates;
  const kept = polygons.map((p) => polygon(p, tolerance, digits)).filter((p) => p !== null);
  if (kept.length === 0) return null;
  return kept.length === 1
    ? { type: 'Polygon', coordinates: kept[0] }
    : { type: 'MultiPolygon', coordinates: kept };
}

function write(name, features) {
  const body = JSON.stringify({ type: 'FeatureCollection', features });
  writeFileSync(join(out, name), body);
  console.log(`${name}: ${features.length} features, ${(body.length / 1024).toFixed(0)} KB`);
}

const countries = JSON.parse(
  readFileSync(join(source, 'ne_10m_admin_0_countries_ind.geojson'), 'utf8'),
);
write(
  'countries.json',
  countries.features
    .map((f) => {
      const p = f.properties;
      // ISO_A2 is -99 for a few countries with an unusual status (France, Norway);
      // the _EH ("eh") column carries the code anyway.
      const code = p.ISO_A2 !== '-99' ? p.ISO_A2 : p.ISO_A2_EH;
      const geom = geometry(f.geometry, 0.05, 2);
      if (code === '-99' || geom === null) return null;
      return { type: 'Feature', properties: { code, name: p.NAME }, geometry: geom };
    })
    .filter((f) => f !== null),
);

const states = JSON.parse(
  readFileSync(join(source, 'ne_10m_admin_1_states_provinces.geojson'), 'utf8'),
);
write(
  'in-states.json',
  states.features
    .filter((f) => f.properties.adm0_a3 === 'IND')
    .map((f) => {
      const p = f.properties;
      const geom = geometry(f.geometry, 0.01, 3);
      if (geom === null) return null;
      // `name` matches the GeoNames admin1 name the engine emits, verified for all 36.
      return {
        type: 'Feature',
        properties: { country: 'IN', name: p.name, iso: p.iso_3166_2 },
        geometry: geom,
      };
    })
    .filter((f) => f !== null),
);
