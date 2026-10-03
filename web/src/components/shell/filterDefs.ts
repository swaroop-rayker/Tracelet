/**
 * Every F9.AC13 filter, as data (DESIGN §5.3). The "＋ Filter" menu, the chips and the
 * editors all read this list, so a dimension is added once. URL keys are unchanged:
 * `filters.ts` is still the only reader and writer of the query string.
 */

import type { Filters, ListKey, ScalarKey } from '@/filters';
import { label } from '@/format';

export type FilterDef =
  | {
      readonly kind: 'list';
      readonly id: string;
      readonly key: ListKey;
      readonly label: string;
      readonly options: readonly string[];
    }
  | {
      readonly kind: 'text';
      readonly id: string;
      readonly key: ScalarKey;
      readonly label: string;
      readonly placeholder: string;
      readonly inputMode?: 'numeric' | 'decimal' | 'text';
      readonly maxLength?: number;
      /** Normalise what was typed into the API's form ("as55836" → "55836"). */
      readonly normalise?: (value: string) => string;
      readonly hint?: string;
    }
  | {
      readonly kind: 'choice';
      readonly id: string;
      readonly key: ScalarKey;
      readonly label: string;
      readonly options: readonly (readonly [value: string, label: string])[];
    };

export const FILTER_DEFS: readonly FilterDef[] = [
  {
    // Excluded by default, so the chip appears only when automated traffic is included.
    kind: 'choice',
    id: 'automated',
    key: 'include_automated',
    label: 'Automated traffic',
    options: [['true', 'Included']],
  },
  {
    kind: 'list',
    id: 'classification',
    key: 'classification',
    label: 'Classification',
    options: ['human', 'unknown', 'bot', 'crawler', 'datacenter', 'spam', 'spoofed'],
  },
  {
    kind: 'list',
    id: 'device',
    key: 'device_class',
    label: 'Device class',
    options: ['mobile', 'tablet', 'desktop', 'tv', 'bot', 'unknown'],
  },
  {
    kind: 'list',
    id: 'connection',
    key: 'connection_class',
    label: 'Connection',
    options: ['broadband', 'mobile', 'business', 'datacenter', 'vpn_suspected', 'tor', 'unknown'],
  },
  {
    kind: 'text',
    id: 'country',
    key: 'country_code',
    label: 'Country',
    placeholder: 'IN',
    maxLength: 2,
    normalise: (v) => v.toUpperCase(),
    hint: 'Two-letter code, best-guess location.',
  },
  {
    kind: 'text',
    id: 'state',
    key: 'admin1',
    label: 'State',
    placeholder: 'Karnataka',
    hint: 'As the engine names it (GeoNames).',
  },
  { kind: 'text', id: 'city', key: 'city', label: 'City', placeholder: 'Bengaluru' },
  {
    kind: 'text',
    id: 'asn',
    key: 'asn',
    label: 'ASN',
    placeholder: '55836',
    inputMode: 'numeric',
    normalise: (v) => v.replace(/^AS/i, ''),
  },
  {
    kind: 'choice',
    id: 'consent',
    key: 'consent_state',
    label: 'Consent',
    options: [
      ['granted', label('granted')],
      ['denied', label('denied')],
      ['unavailable', label('unavailable')],
      ['not_asked', label('not_asked')],
      ['blocked_by_webview', label('blocked_by_webview')],
    ],
  },
  {
    kind: 'choice',
    id: 'gps',
    key: 'has_gps',
    label: 'Coordinates',
    options: [
      ['true', 'Consented GPS'],
      ['false', 'No GPS'],
    ],
  },
  {
    kind: 'choice',
    id: 'proxy',
    key: 'is_proxy_suspected',
    label: 'Proxy suspected',
    options: [
      ['true', 'Yes'],
      ['false', 'No'],
    ],
  },
  {
    kind: 'text',
    id: 'visitor',
    key: 'visitor_id',
    label: 'Visitor',
    placeholder: '32 hex characters',
    normalise: (v) => v.toLowerCase(),
  },
  {
    kind: 'text',
    id: 'conf-state',
    key: 'min_confidence_admin1',
    label: 'Min. state confidence',
    placeholder: '0.8',
    inputMode: 'decimal',
    hint: 'Between 0 and 1.',
  },
  {
    kind: 'text',
    id: 'conf-city',
    key: 'min_confidence_city',
    label: 'Min. city confidence',
    placeholder: '0.9',
    inputMode: 'decimal',
    hint: 'Between 0 and 1.',
  },
  {
    kind: 'text',
    id: 'search',
    key: 'search',
    label: 'Search',
    placeholder: 'ISP, city, browser, link…',
  },
];

/** Whether a dimension is set in `filters`. */
export function isActive(def: FilterDef, filters: Filters): boolean {
  return def.kind === 'list'
    ? filters.lists[def.key].length > 0
    : filters.scalars[def.key] !== undefined;
}

/** A chip's value text: "mobile, tablet", "3 selected", "IN", "Consented GPS". */
export function chipValue(def: FilterDef, filters: Filters): string {
  if (def.kind === 'list') {
    const values = filters.lists[def.key];
    return values.length > 2 ? `${String(values.length)} selected` : values.map(label).join(', ');
  }
  const value = filters.scalars[def.key] ?? '';
  if (def.kind === 'choice') return def.options.find(([v]) => v === value)?.[1] ?? value;
  return value;
}

/** `scalars` without `key`. */
function without(scalars: Filters['scalars'], key: ScalarKey): Partial<Record<ScalarKey, string>> {
  return Object.fromEntries(Object.entries(scalars).filter(([k]) => k !== key));
}

/** `filters` with one dimension cleared. */
export function clearDimension(def: FilterDef, filters: Filters): Filters {
  if (def.kind === 'list') return { ...filters, lists: { ...filters.lists, [def.key]: [] } };
  return { ...filters, scalars: without(filters.scalars, def.key) };
}

/** `filters` with a scalar set (or removed, when empty). */
export function setScalar(filters: Filters, key: ScalarKey, value: string): Filters {
  const scalars = without(filters.scalars, key);
  if (value !== '') scalars[key] = value;
  return { ...filters, scalars };
}

/** `filters` with one list value toggled. */
export function toggleListValue(filters: Filters, key: ListKey, value: string): Filters {
  const current = filters.lists[key];
  const next = current.includes(value) ? current.filter((v) => v !== value) : [...current, value];
  return { ...filters, lists: { ...filters.lists, [key]: next } };
}

/** Every filter cleared except the period, the link included -- as "Clear filters" did in M5. */
export function clearAll(filters: Filters): Filters {
  return {
    ...filters,
    scalars: {},
    lists: { classification: [], device_class: [], connection_class: [] },
  };
}
