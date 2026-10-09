/**
 * How accuracy figures read (DESIGN §16 M8, RISKS R9): never a percentage without its
 * sample, and the interval beside it, because 31 of 31 is not certainty.
 */

import {
  CONNECTION_KINDS,
  NETWORKS,
  type ConnectionKind,
  type NetworkFamily,
  type Proportion,
  type TargetCheck,
} from '@/api/groundtruth';
import type { GroundTruthLabel, VisitDetail } from '@/api/schemas';
import { countryName, pct } from '@/format';

export const LEVELS = ['country', 'admin1', 'admin2', 'city'] as const;
export type Level = (typeof LEVELS)[number];

/** "1 label", "31 labels". */
export function labels(n: number): string {
  return `${n.toLocaleString()} ${n === 1 ? 'label' : 'labels'}`;
}

/** The value, or a dash when nothing was measured. */
export function share(p: Proportion): string {
  return p.value === null ? '—' : pct(p.value, p.value === 1 || p.value === 0 ? 0 : 1);
}

/** "31 of 31" -- the sample, always beside the share. */
export function sample(p: Proportion): string {
  return `${p.k.toLocaleString()} of ${p.n.toLocaleString()}`;
}

/** "95 %: 89–100 %", or why there is none. */
export function interval(ci: readonly [number, number] | null): string {
  return ci === null ? 'no interval: nothing measured' : `95 %: ${pct(ci[0])}–${pct(ci[1])}`;
}

/** A target as words, with the verdict spelt out (colour is never the only cue). */
export function targetText(t: TargetCheck | undefined): string | null {
  if (t === undefined) return null;
  if (t.target === null) return 'Reported; no target yet';
  const sign = t.direction === 'at_most' ? '≤' : '≥';
  const goal = `${sign} ${pct(t.target, t.target * 100 === Math.round(t.target * 100) ? 0 : 1)}`;
  switch (t.status) {
    case 'met':
      return `${goal} · target met ✓`;
    case 'missed':
      return `${goal} · target missed ✗`;
    case 'unmeasured':
      return `${goal} · not measured yet`;
    case 'reported':
      return goal;
  }
}

/** The engine's case- and punctuation-insensitive key (inference/types.py normalise_place). */
export function normalisePlace(name: string): string {
  return name.toLowerCase().replace(/[.-]/g, ' ').split(/\s+/).filter(Boolean).join(' ');
}

type Place = Readonly<Record<string, string | null>>;

function at(place: Place, level: Level): string | null {
  return place[level === 'country' ? 'country_code' : level] ?? null;
}

/**
 * Whether ``stated`` is right at ``level`` against the truth: equal there, and no shallower
 * level the truth names contradicted (accuracy/replay.py ``right``).
 */
export function isRight(truth: Place, stated: Place, level: Level): boolean {
  const want = at(truth, level);
  const got = at(stated, level);
  if (want === null || got === null || normalisePlace(want) !== normalisePlace(got)) return false;
  for (const shallower of LEVELS.slice(0, LEVELS.indexOf(level))) {
    const t = at(truth, shallower);
    const s = at(stated, shallower);
    if (t !== null && s !== null && normalisePlace(t) !== normalisePlace(s)) return false;
  }
  return true;
}

/** "Bengaluru, Karnataka, India", "Can't tell", or the deepest level known. */
export function truthText(label: GroundTruthLabel): string {
  if (label.cant_tell) return 'Can’t tell';
  const { country_code, admin1, admin2, city } = label.truth;
  return [city, admin2, admin1, country_code === null ? null : countryName(country_code)]
    .filter((x): x is string => x !== null)
    .join(', ');
}

/** The engine's recorded answer: strict where it spoke, else the best guess, marked. */
export function recordedText(label: GroundTruthLabel): string {
  const { strict, advisory } = label.recorded;
  for (const level of [...LEVELS].reverse()) {
    const confirmed = at(strict, level);
    if (confirmed !== null) return `${placeLine(strict, level)} (confirmed)`;
    const guess = at(advisory, level);
    if (guess !== null) return `${placeLine(advisory, level)} (best guess)`;
  }
  return 'No answer';
}

function placeLine(place: Place, deepest: Level): string {
  return LEVELS.slice(0, LEVELS.indexOf(deepest) + 1)
    .reverse()
    .flatMap((level) => {
      const value = at(place, level);
      if (value === null) return [];
      return [level === 'country' ? countryName(value) : value];
    })
    .join(', ');
}

export const OWNER_ONLY_LABEL = 'Only an owner can label visits.';

/** Where the visit's own GPS placed it, when it was consented. */
export interface GpsPlace {
  readonly country_code: string | null;
  readonly admin1: string | null;
  readonly admin2: string | null;
  readonly city: string | null;
}

/** How the last label was made: a test session runs on one network. */
export interface Carry {
  readonly connection_kind: ConnectionKind | null;
  readonly vpn_used: boolean | null;
  readonly network: NetworkFamily | null;
}

export function asConnection(value: string | null): ConnectionKind | null {
  return CONNECTION_KINDS.find((k) => k === value) ?? null;
}

export function asNetwork(value: string | null): NetworkFamily | null {
  return NETWORKS.find((n) => n === value) ?? null;
}

export function carryFrom(label: GroundTruthLabel | undefined): Carry | null {
  if (label === undefined) return null;
  return {
    connection_kind: asConnection(label.connection_kind),
    vpn_used: label.vpn_used,
    network: asNetwork(label.network),
  };
}

/** Where the visit's own consented GPS candidate placed it. */
export function gpsPlaceOf(visit: VisitDetail): GpsPlace | null {
  const fix = visit.candidates.find((c) => c.source === 'gps' && c.country_code !== null);
  if (fix === undefined || !visit.location.has_gps) return null;
  return { country_code: fix.country_code, admin1: fix.admin1, admin2: fix.admin2, city: fix.city };
}
