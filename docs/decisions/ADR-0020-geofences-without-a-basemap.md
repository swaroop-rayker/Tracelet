# ADR-0020 — Geofences without a basemap: region geofences on strict codes, shapes on the strict point

**Status:** Accepted (2026-10-06). The owner made decisions 1–3 on 2026-10-03 and approved
decisions 4–8, which follow from them, on 2026-10-06, before any M6 code.
**Deciders:** repository owner
**Amends:** F6.AC1, F6.AC2, F6.AC5, F6.AC6 (SPEC §11 row 16); DATA_MODEL §5.3 invariant 5 and
`ck_visits_no_geopoint_is_undetermined`; DATA_MODEL §6.1; MILESTONES M6 scope ("first, choose a
basemap")
**Relates to:** ADR-0003 (Leaflet + Geoman), ADR-0005 (strict and advisory), ADR-0015
(geofencing joins the inference job), ADR-0017 (outlines, no tiles), ADR-0018 (strict is what is
acted on), RISKS R3, R26

---

## Context

F6 was written for one kind of geofence: a polygon or circle drawn over a street map, matched
with `ST_Covers(area, geopoint)`. Since then, three facts have changed what that can do.

1. **There is no street map** (ADR-0017). CARTO's keyless tiles ended. Every alternative needs a
   key (C6), a Referer that leaks the hostname to OSM, or gigabytes of self-hosted tiles. RISKS
   R26 left the choice to M6.
2. **`geopoint` almost never exists for Indian traffic.** It is set from consented GPS or a
   *strict* city (DATA_MODEL §5.3 invariant 11). Spike A measured rDNS city coverage at 2.0 %
   (R3). The dev set of 160 real ISP visits has **strict city 0 of 160** and **strict state 39
   of 160** (ADR-0018). A polygon geofence over Bengaluru would therefore evaluate to
   `undetermined` for nearly every visitor. That is correct (F6.AC6), and it is useless as the
   only kind of geofence.
3. **Strict state and country are what the engine does state.** They are the levels that pass
   F4.AC13's precision targets, so they are what may be acted on (ADR-0018 decision 4).

## Decision

1. **No basemap** *(owner, 2026-10-03)*. The geofence editor draws over the M5 outlines
   (countries and first-order divisions, ADR-0017), with no tiles and no third-party request. To
   make up for the lack of streets:
   - polygon vertices and a circle's centre and radius can be typed as coordinates;
   - GeoJSON import (F6.AC9) brings in boundaries prepared in an external tool;
   - visits that have a point (consented GPS or a strict city) can be shown as context;
   - zoom stops where the outlines stop being truthful (simplified to about 1 km).
   RISKS R26 closes as accepted: street-level drawing is not offered.

2. **Region geofences** *(owner, 2026-10-03)*. A new `shape_kind = 'region'` holds a set of
   region keys instead of an area. A key is a country (`IN`) or a first-order division
   qualified by its country (`IN|Karnataka`). That is the form the rollups already use
   (DATA_MODEL §9), spelled as the engine emits it (GeoNames names). A region geofence
   matches **only on the strict fields**: `strict_country_code`, and the pair
   `(strict_country_code, strict_admin1)`. It never matches on advisory fields or on a polygon.
   Regions are picked on the map (click to toggle) or from a searchable list of every country
   and division. The list covers the roughly 13 % of divisions that have no outline.

3. **Polygons and circles stay** *(owner, 2026-10-03)*. Their storage and evaluation are as F6.AC2
   says: `geography(Polygon,4326)`, with a circle stored buffered and its `center` and `radius_m`
   retained, matched by `ST_Covers(area, geopoint)` under a GiST index. Their state is
   `undetermined` whenever `geopoint` is NULL.

4. **Each applicable geofence gets a three-valued result.** A geofence applies to a visit when it
   is active and its `link_ids` is NULL or contains the visit's link.

   | Geofence | `inside` | `outside` | `undetermined` |
   |---|---|---|---|
   | Region key `CC` | strict country = `CC` | strict country stated and ≠ `CC` | strict country abstained |
   | Region key `CC\|State` | strict country = `CC` and strict admin1 = `State` | strict admin1 stated and different, **or** strict country stated and ≠ `CC` | strict country is `CC`, admin1 abstained; or country abstained |
   | Region geofence (a set of keys) | any key is inside | every key is outside | otherwise |
   | Polygon, circle | `ST_Covers(area, geopoint)` | `geopoint` set, not covered | `geopoint` NULL |

   A visit placed strictly in Maharashtra is therefore *outside* a Karnataka geofence. A visit
   with strict country India and no strict state is *undetermined*: India contains Karnataka,
   so it cannot be ruled out.

5. **The visit's state combines its results, and never rounds an abstention down to
   "outside".**
   - `matched_geofence_ids` = every applicable geofence whose result is `inside`. All matches
     are recorded (F6.AC7).
   - `geofence_state` = `inside` if any result is inside; else `undetermined` if any result is
     undetermined; else `outside`.
   - **No applicable geofence:** `geofence_state` stays NULL, meaning "nothing to evaluate
     against". It is distinct from all three values, so "outside" never means "we had no
     geofences".

6. **Invariant 5 is restated, and stays a `CHECK` constraint.** "`undetermined` whenever
   `geopoint IS NULL`" is false once a strict state can place a visit inside a region geofence.
   It is replaced by two constraints that keep its purpose, *an abstaining inference is never
   "outside" or "inside"*:
   - `ck_visits_outside_needs_strict`: `geofence_state <> 'outside' OR geopoint IS NOT NULL OR
     strict_country_code IS NOT NULL`;
   - `ck_visits_inside_has_match`: `geofence_state <> 'inside' OR
     cardinality(matched_geofence_ids) > 0`.
   A visit with no strict location at any level can only be `undetermined` or NULL. That is
   F6.AC6, still enforced by the database rather than by a convention.

7. **Alerts follow the state** (F7.AC3, and F6.AC7 for overlaps):
   - `inside`: the highest-`priority` matching geofence decides, by its `notify_priority`
     (`high`, `normal` or `silent`). The message names it.
   - `outside`: a normal alert.
   - `undetermined`: a **normal** alert, worded as unconfirmed: "Location not confirmed: could
     not be checked against your geofences". It shows the best guess with its confidence
     (F7.AC4). F7.AC3 does not cover this case. Without an alert, a person could visit and
     never be reported, only because the engine abstained.
   - NULL (no geofences): a normal alert with no geofence line.

8. **The drawing library stays Geoman** (ADR-0003), behind a spike before anything is built on
   it. Leaflet-Geoman must raise **zero CSP violations** in Chromium, Firefox and WebKit under
   the dashboard's exact header, measured with M5.5's Playwright sweep (listener registered
   before page scripts, positive controls; ERRORS E44). If it fails, M6 writes its own small
   tools on Leaflet's API instead: a click-to-add polygon, draggable vertex markers, and a
   circle with centre and radius handles. Geoman's free edition would be added to the ledger
   with its measured gzipped size.

## Alternatives considered

| Option | Why not |
|---|---|
| **A tile basemap** (OSM, a keyed provider, self-hosted tiles) | R26's options, already declined: a Referer leak, a key (C6), or gigabytes on a 30 GB, 1 GB-RAM box |
| **Polygons only, as F6 was written** | `undetermined` for nearly every visitor while city coverage is 2 % (R3). Correct and useless |
| **Match region geofences on advisory (best-guess) fields** | Acts on a guess: an ISP's head-office state (B1) would raise high-priority alerts. ADR-0018 keeps every action on strict |
| **Store regions as polygons (their outlines) and use `ST_Covers`** | It still needs a `geopoint`, so it inherits the 2 % problem. About 13 % of divisions have no outline, and the simplified outlines misplace border towns |
| **Keep invariant 5 literally and treat a strict state as "undetermined"** | It throws away exactly the strict information that makes a region geofence work |
| **Drop the alert for `undetermined` visits** | F7 exists to tell the owner someone visited. Silence because the *engine* abstained would hide most Indian mobile visitors |
| **`outside` when no geofence exists** | It reads as a geographic claim the system never made. NULL says "not evaluated" |

## Consequences

**Positive**

- Geofencing works for the traffic Tracelet actually sees. On the dev set, 39 of 160 visits
  have a strict state and can be placed inside or outside a state geofence, against 0 of 160
  for any polygon.
- Nothing is acted on that the engine did not state. Strict stays the only input (ADR-0018).
- No key, no tile server, no third-party request, no new CSP exception.

**Negative, and accepted**

- **No street-level drawing.** A campus polygon has to be typed as coordinates or imported as
  GeoJSON. The outlines are context, not a street map.
- **Region keys are names, not ISO codes.** They are spelled as GeoNames spells them, because
  that is what the engine emits. A GeoNames rename of a division would orphan a key. The editor
  flags keys it no longer recognises, and M7's geo-database update reports renamed divisions.
- **More `undetermined`, and so more normal alerts worded as unconfirmed.** That is honest
  rather than noisy: quiet hours (F7.AC9) still hold every non-high alert.
- Invariant 5 changes wording. Its tests change with it, and the raw-SQL test that goes around
  the application is kept for both new constraints.

## Revisit if

- M8's ground truth shows strict state precision below F4.AC13's target. Region geofences would
  then be acting on something the engine should not have stated.
- Strict city coverage rises well above R3's 2 %, for example through consented GPS, so that
  polygon geofences decide most visits.
- A keyless, Referer-free basemap appears, which would reopen street-level drawing.
