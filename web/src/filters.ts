/**
 * Dashboard filter state, kept in the URL (F9.AC13).
 *
 * The query string *is* the state: there is no second copy in React state to drift
 * from it, every filter change is a history entry, and a copied URL reproduces the
 * view. URL keys are the API's own parameter names, so a filter means the same thing
 * in the address bar, in the request and in docs/API.md section 7.
 *
 * **Windows are cut in the reporting timezone** (ADR-0016), which the server states in
 * `/auth/me`. "Last 7 days" means seven whole local days ending tonight at midnight --
 * the boundaries the rollups use, so a preset is answered from rollups rather than
 * forcing the raw path with a window that starts at an arbitrary minute.
 *
 * Presets are relative: a shared "last 7 days" link shows the last seven days on the
 * day it is opened. A custom range (`range=custom&from=…&to=…`) is absolute and
 * reproduces exactly.
 */

export const PRESETS = ['24h', '7d', '30d', '90d', '365d'] as const;
export type Preset = (typeof PRESETS)[number];
export type RangeKind = Preset | 'custom';

export const PRESET_LABEL: Readonly<Record<Preset, string>> = {
  '24h': 'Last 24 hours',
  '7d': 'Last 7 days',
  '30d': 'Last 30 days',
  '90d': 'Last 90 days',
  '365d': 'Last 365 days',
};

/** Single-valued filters, by their API parameter name. */
export const SCALAR_KEYS = [
  'link_id',
  'country_code',
  'admin1',
  'city',
  'asn',
  'consent_state',
  'visitor_id',
  'min_confidence_admin1',
  'min_confidence_city',
  'has_gps',
  'is_proxy_suspected',
  'include_automated',
  'search',
  // Sources (F9.AC21, M7.6)
  'referrer_host',
  'utm_source',
  'utm_medium',
  'utm_campaign',
] as const;
export type ScalarKey = (typeof SCALAR_KEYS)[number];

/** Repeatable filters. */
export const LIST_KEYS = ['classification', 'device_class', 'connection_class'] as const;
export type ListKey = (typeof LIST_KEYS)[number];

export interface Filters {
  readonly range: RangeKind;
  /** Inclusive local dates, `YYYY-MM-DD`, for a custom range only. */
  readonly from: string | null;
  readonly to: string | null;
  readonly scalars: Readonly<Partial<Record<ScalarKey, string>>>;
  readonly lists: Readonly<Record<ListKey, readonly string[]>>;
}

const DATE = /^\d{4}-\d{2}-\d{2}$/;

export function parseFilters(params: URLSearchParams): Filters {
  const range = params.get('range');
  const from = params.get('from');
  const to = params.get('to');
  const custom = range === 'custom' && from !== null && to !== null;
  const scalars: Partial<Record<ScalarKey, string>> = {};
  for (const key of SCALAR_KEYS) {
    const value = params.get(key);
    if (value !== null && value !== '') scalars[key] = value;
  }
  const lists = Object.fromEntries(
    LIST_KEYS.map((key) => [key, params.getAll(key).filter((v) => v !== '')]),
  ) as Record<ListKey, string[]>;
  return {
    range: custom && DATE.test(from) && DATE.test(to) ? 'custom' : asPreset(range),
    from: custom ? from : null,
    to: custom ? to : null,
    scalars,
    lists,
  };
}

function asPreset(value: string | null): Preset {
  return (PRESETS as readonly string[]).includes(value ?? '') ? (value as Preset) : '30d';
}

export function serializeFilters(filters: Filters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.range !== '30d') params.set('range', filters.range);
  if (filters.range === 'custom' && filters.from !== null && filters.to !== null) {
    params.set('from', filters.from);
    params.set('to', filters.to);
  }
  for (const key of SCALAR_KEYS) {
    const value = filters.scalars[key];
    if (value !== undefined) params.set(key, value);
  }
  for (const key of LIST_KEYS) {
    for (const value of filters.lists[key]) params.append(key, value);
  }
  return params;
}

/** How many filters (beyond the time range) are active, for the filter bar's badge. */
export function activeCount(filters: Filters): number {
  return (
    Object.keys(filters.scalars).length +
    LIST_KEYS.reduce((n, key) => n + filters.lists[key].length, 0)
  );
}

// ---------------------------------------------------------------------------
// Time zones, without a date library
// ---------------------------------------------------------------------------

interface Wall {
  readonly year: number;
  readonly month: number;
  readonly day: number;
  readonly hour: number;
  /** Needed: India is UTC+05:30, so an Indian hour starts at half past a UTC one. */
  readonly minute?: number;
}

/** The wall-clock time in `zone` at instant `ms`. */
export function wallTime(ms: number, zone: string): Wall {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: zone,
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: 'numeric',
    hourCycle: 'h23',
  }).formatToParts(new Date(ms));
  const get = (type: Intl.DateTimeFormatPartTypes): number =>
    Number(parts.find((p) => p.type === type)?.value ?? '0');
  return {
    year: get('year'),
    month: get('month'),
    day: get('day'),
    hour: get('hour'),
    minute: get('minute'),
  };
}

function asUtc(wall: Wall): number {
  return Date.UTC(wall.year, wall.month - 1, wall.day, wall.hour, wall.minute ?? 0);
}

/** The instant at which the wall clock in `zone` reads `wall`. */
export function zonedInstant(wall: Wall, zone: string): number {
  const naive = asUtc(wall);
  // Two passes settle the offset even across a DST change in zones that have one.
  let guess = naive;
  for (let i = 0; i < 2; i += 1) {
    guess += naive - asUtc(wallTime(guess, zone));
  }
  return guess;
}

function addDays(date: string, days: number): string {
  const [y, m, d] = date.split('-').map(Number) as [number, number, number];
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
}

export function localDate(ms: number, zone: string): string {
  const w = wallTime(ms, zone);
  return `${String(w.year)}-${String(w.month).padStart(2, '0')}-${String(w.day).padStart(2, '0')}`;
}

function midnight(date: string, zone: string): number {
  const [year, month, day] = date.split('-').map(Number) as [number, number, number];
  return zonedInstant({ year, month, day, hour: 0 }, zone);
}

export interface ApiWindow {
  readonly from: string;
  readonly to: string;
  /** Whether hourly buckets suit this window (the API limits hourly to 31 days). */
  readonly hourly: boolean;
}

/** The `[from, to)` instants a filter's range means, on reporting-timezone boundaries. */
export function resolveWindow(filters: Filters, zone: string, now: number = Date.now()): ApiWindow {
  if (filters.range === '24h') {
    const w = wallTime(now, zone);
    const nextHour = zonedInstant({ ...w, minute: 0 }, zone) + 3_600_000;
    return {
      from: new Date(nextHour - 24 * 3_600_000).toISOString(),
      to: new Date(nextHour).toISOString(),
      hourly: true,
    };
  }
  let first: string;
  let last: string;
  if (filters.range === 'custom' && filters.from !== null && filters.to !== null) {
    [first, last] =
      filters.from <= filters.to ? [filters.from, filters.to] : [filters.to, filters.from];
  } else {
    const days = Number.parseInt(filters.range, 10);
    last = localDate(now, zone);
    first = addDays(last, -(days - 1));
  }
  const from = midnight(first, zone);
  const to = midnight(addDays(last, 1), zone);
  return {
    from: new Date(from).toISOString(),
    to: new Date(to).toISOString(),
    hourly: to - from <= 31 * 86_400_000,
  };
}

/** Every filter as API query parameters, the window included. */
export function apiParams(filters: Filters, zone: string, now?: number): URLSearchParams {
  const params = serializeFilters(filters);
  params.delete('range');
  const window = resolveWindow(filters, zone, now);
  params.set('from', window.from);
  params.set('to', window.to);
  return params;
}

/** `base` with some parameters replaced; `base` itself is not modified. */
export function withParams(
  base: URLSearchParams,
  extra: Readonly<Record<string, string>>,
): URLSearchParams {
  const params = new URLSearchParams(base);
  for (const [key, value] of Object.entries(extra)) params.set(key, value);
  return params;
}
