# ADR-0017 — The Geography map draws self-hosted outlines, not a tile basemap

**Status:** Accepted (M5, 2026-10-02)
**Deciders:** repository owner, recorded before the code per CLAUDE.md §2
**Supersedes:** ADR-0003's "Basemap tiles: CARTO raster, no API key"
**Relates to:** F9.AC5, F6.AC1, C6, NFR6, ADR-0003, RISKS R14, R16, R26

---

## Context

ADR-0003 chose CARTO's raster basemap because it needed **no API key** (Gate 1 declined
further sign-ups, C6). RISKS R14 recorded that this rested on CARTO's terms.

Those terms changed. Checked on 2026-10-02 while verifying M5's Geography page: every
`basemaps.cartocdn.com` tile, dark and light, is a `200 image/png` reading **"API KEY
REQUIRED — carto.com/basemaps/apikey"**. The map as first built in M5 rendered a grid of
watermarks behind the choropleth -- and an automated check that counted *loaded* tiles
had passed, because the watermark is a loaded tile (ERRORS E37).

Meanwhile M5 already ships the geometry a choropleth needs: Natural Earth countries (India
point of view) and every country's first-order divisions, self-hosted under `/geo`.

## Decision

**No third-party tiles.** The Geography map is drawn entirely from the shipped outlines:

- the sea is the map container's background, a theme token (`--map-sea`);
- every country is drawn as land (`--map-land`), shaded by visit count when shading by
  country;
- when shading by state or province, the divisions of countries with visits are drawn over
  the land, with country borders on top for context;
- clustered points are drawn over all of it.

Zoom is limited to what the outlines can support (simplified to roughly 1 km). Attribution
names Natural Earth and GeoNames. The SPA's CSP exception for CARTO images is removed, so
the dashboard makes no request to a third party at all.

## Alternatives considered

| Option | Why not |
|---|---|
| **A CARTO key** | Reopens C6 (no sign-ups); the key would be visible to every browser |
| **OpenStreetMap's tile server** | No key, but light style only, and its usage policy requires a Referer, which would tell OSM the dashboard's hostname |
| **MapTiler / Mapbox / Google** | Keys, and for two of them billing (ADR-0003) |
| **Self-hosting raster or vector tiles** | Gigabytes of tiles or a tile server process on a 1 GB, 30 GB box (NFR6, disk budget) |

## Consequences

**Positive**

- No key, no account, no third-party request, no CSP exception -- the stricter posture.
- All three themes are exact: land and sea are tokens, not a vendor's two styles.
- One fewer external service whose terms can change under the product (R14 closed).

**Negative, and accepted**

- **No streets, towns or terrain.** A choropleth and clustered points do not need them.
- **Geofence drawing (F6.AC1, M6) does.** Drawing a boundary around a neighbourhood needs
  street-level context that outlines cannot give. M6 must choose a basemap before it
  builds the drawing canvas (RISKS R26). This ADR decides only the analytics map.
- The outlines are served from the VM, so map views count against GCP egress (R16): about
  0.3 MB gzipped for countries plus a few tens of KB per country's divisions, cached by the
  browser. Negligible at two admins.

## Revisit if

- M6 chooses a basemap for geofence drawing -- the analytics map could then share it.
- Admins need to see places at street level on the analytics map, which would be a new
  requirement rather than F9.AC5.
