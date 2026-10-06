/**
 * Words for geofences, shared by the list, the editor and the coordinate test (DESIGN §16).
 */

import type { GeofenceState, NotifyPriority, ShapeKind } from '@/api/geofences';
import type { IconName } from '@/components/icons';
import { countryName } from '@/format';

/** `IN` → "India"; `IN|Karnataka` → "Karnataka, India". The key is GeoNames' spelling. */
export function regionLabel(key: string): string {
  const [country = '', admin1] = key.split('|', 2);
  return admin1 === undefined ? countryName(country) : `${admin1}, ${countryName(country)}`;
}

export const SHAPE: Readonly<
  Record<ShapeKind, { readonly label: string; readonly icon: IconName }>
> = {
  region: { label: 'Region', icon: 'ShapeRegion' },
  polygon: { label: 'Polygon', icon: 'ShapePolygon' },
  circle: { label: 'Circle', icon: 'ShapeCircle' },
};

export const PRIORITY: Readonly<
  Record<NotifyPriority, { readonly label: string; readonly tone: 'error' | 'info' | undefined }>
> = {
  high: { label: 'High', tone: 'error' },
  normal: { label: 'Normal', tone: 'info' },
  silent: { label: 'Silent', tone: undefined },
};

export const STATE: Readonly<
  Record<GeofenceState, { readonly label: string; readonly tone: 'ok' | 'warn' | undefined }>
> = {
  inside: { label: 'Inside', tone: 'ok' },
  outside: { label: 'Outside', tone: undefined },
  undetermined: { label: 'Undetermined', tone: 'warn' },
};

/** Why a geofence could not be decided (API §9 `reason`), in words (F6.AC6). */
export function undeterminedReason(reason: string | null): string {
  switch (reason) {
    case 'no_strict_country':
      return 'No country could be stated for this point.';
    case 'no_strict_admin1':
      return 'The country is known, but not the state, so a state cannot be ruled in or out.';
    case 'no_geopoint':
      return 'No point to test the shape against.';
    default:
      return 'Could not be decided.';
  }
}

type Tone = 'ok' | 'warn' | 'error' | 'info';

/** Badge props for an optional tone: no tone is the neutral badge. */
export function toned(tone: Tone | undefined): { readonly tone?: Tone } {
  return tone === undefined ? {} : { tone };
}
