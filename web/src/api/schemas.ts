/**
 * Runtime schemas for the dashboard's API payloads (docs/API.md sections 7, 8, 14).
 *
 * Two layers, on purpose. The generated types in `generated/schema.d.ts` prove the
 * **contract**; these schemas prove the **payload**. Each schema is annotated with its
 * generated type (`z.ZodType<S['Summary']>`), so if the API's response model changes
 * and the client is regenerated, a schema that no longer matches fails `tsc` -- the
 * drift surfaces at build time, not as a `TypeError` inside a chart (ADR-0003).
 */

import type { components } from '@/api/generated/schema';
// Configured for the CSP (ERRORS E44, E54): never import zod directly.
import { z } from '@/api/zod';

type S = components['schemas'];

const classification = z.enum([
  'human',
  'bot',
  'crawler',
  'datacenter',
  'spam',
  'spoofed',
  'unknown',
]) satisfies z.ZodType<S['Classification']>;

const stage = z.enum(['server', 'enriched', 'server_only', 'rate_limited']) satisfies z.ZodType<
  S['VisitStage']
>;

// null: no active geofence applied, never "outside" (ADR-0020 decision 5).
const geofenceState = z.enum(['inside', 'outside', 'undetermined']) satisfies z.ZodType<
  S['GeofenceState']
>;

const level = z.enum(['country', 'admin1', 'admin2', 'city']);

export const stageMixSchema: z.ZodType<S['StageMix']> = z.object({
  total: z.number(),
  server: z.number(),
  enriched: z.number(),
  server_only: z.number(),
  rate_limited: z.number(),
});

export const metaSchema: z.ZodType<S['Meta']> = z.object({
  start: z.string(),
  end: z.string(),
  reporting_tz: z.string(),
  computed_from: z.enum(['rollup', 'raw']),
  refreshed_at: z.string().nullable(),
  stage_mix: stageMixSchema,
});

export const summarySchema: z.ZodType<S['Summary']> = z.object({
  meta: metaSchema,
  previous_start: z.string(),
  kpis: z.array(
    z.object({
      key: z.string(),
      unit: z.enum(['count', 'ratio']),
      value: z.number().nullable(),
      previous: z.number().nullable(),
      change: z.number().nullable(),
      reason: z.string().nullable(),
    }),
  ),
});

export const timeSeriesSchema: z.ZodType<S['TimeSeries']> = z.object({
  meta: metaSchema,
  bucket: z.enum(['day', 'hour']),
  buckets: z.array(z.string()),
  series: z.array(z.object({ key: z.string(), label: z.string(), values: z.array(z.number()) })),
});

export const calendarSchema: z.ZodType<S['Calendar']> = z.object({
  meta: metaSchema,
  days: z.array(z.object({ day: z.string(), count: z.number() })),
});

export const breakdownDimensions = [
  'country',
  'admin1',
  'city',
  'asn',
  'isp',
  'device_class',
  'browser',
  'app_medium',
  'os',
  'screen',
  'connection_class',
  'classification',
] as const satisfies readonly S['BreakdownDimension'][];

/** Sources (F9.AC21, M7.6): on their own page, not on Breakdowns. `unknown` reads None. */
export const sourceDimensions = [
  'referrer_host',
  'utm_source',
  'utm_medium',
  'utm_campaign',
] as const satisfies readonly S['BreakdownDimension'][];

export const breakdownSchema: z.ZodType<S['Breakdown']> = z.object({
  meta: metaSchema,
  dimension: z.enum([...breakdownDimensions, ...sourceDimensions]),
  total: z.number(),
  rows: z.array(z.object({ key: z.string(), count: z.number(), share: z.number() })),
  unknown: z.number(),
  other: z.number(),
});

export const signalsSchema: z.ZodType<S['Signals']> = z.object({
  meta: metaSchema,
  visits: z.number(),
  rows: z.array(
    z.object({
      rule_id: z.string(),
      category: z.string(),
      count: z.number(),
      share: z.number(),
    }),
  ),
});

export const confidenceSchema: z.ZodType<S['Confidence']> = z.object({
  meta: metaSchema,
  levels: z.array(z.object({ level, bins: z.array(z.number()), unscored: z.number() })),
});

export const sourceFlowSchema: z.ZodType<S['SourceFlow']> = z.object({
  meta: metaSchema,
  visits: z.number(),
  sources: z.array(z.string()),
  levels: z.array(z.string()),
  links: z.array(z.object({ source: z.string(), target: z.string(), value: z.number() })),
});

export const funnelSchema: z.ZodType<S['Funnel']> = z.object({
  meta: metaSchema,
  steps: z.array(
    z.object({
      step: z.enum(['requests', 'captured', 'enriched', 'consented', 'notified']),
      count: z.number().nullable(),
      reason: z.string().nullable(),
    }),
  ),
});

/** A 95 % Wilson interval (ADR-0024), or null when nothing was measured. */
export const interval = z.tuple([z.number(), z.number()]).nullable();

export const accuracySchema: z.ZodType<S['Accuracy']> = z.object({
  meta: metaSchema,
  inferred: z.number(),
  settings_version: z.number(),
  inference_version: z.string(),
  population: z.literal('network_only'),
  levels: z.array(
    z.object({
      level,
      label_count: z.number(),
      precision: z.number().nullable(),
      precision_ci95: interval,
      coverage: z.number().nullable(),
      coverage_ci95: interval,
      advisory_accuracy: z.number().nullable(),
      advisory_ci95: interval,
      emission_rate: z.number().nullable(),
      reason: z.string().nullable(),
    }),
  ),
});

export const geoSchema: z.ZodType<S['Geo']> = z.object({
  meta: metaSchema,
  countries: z.array(z.object({ country_code: z.string(), count: z.number() })),
  admin1: z.array(z.object({ country_code: z.string(), admin1: z.string(), count: z.number() })),
  abstained: z.number(),
  cell_degrees: z.number(),
  points_computed_from: z.literal('raw'),
  points: z.array(z.object({ lat: z.number(), lng: z.number(), count: z.number() })),
  points_truncated: z.boolean(),
});

const visitSummaryShape = {
  id: z.string(),
  occurred_at: z.string(),
  link: z.object({ id: z.string(), slug: z.string(), label: z.string() }),
  stage,
  classification,
  bot_score: z.number().nullable(),
  spoof_score: z.number().nullable(),
  location: z.object({
    strict: z.record(z.string(), z.string().nullable()),
    advisory: z.record(z.string(), z.string().nullable()),
    confidence: z.record(z.string(), z.number().nullable()),
    abstain_reason: z.record(z.string(), z.unknown()),
    primary_source: z.string().nullable(),
    has_gps: z.boolean(),
  }),
  network: z.object({
    asn: z.number().nullable(),
    asn_org: z.string().nullable(),
    asn_type: z.string(),
    connection_class: z.string(),
    ip_prefix: z.string().nullable(),
    is_datacenter: z.boolean().nullable(),
    is_vpn_suspected: z.boolean().nullable(),
    is_proxy_suspected: z.boolean().nullable(),
    cf_colo: z.string().nullable(),
    cf_country: z.string().nullable(),
  }),
  device: z.object({
    class: z.string(),
    os: z.string().nullable(),
    browser: z.string().nullable(),
    is_inapp_webview: z.boolean(),
    webview_host: z.string().nullable(),
    screen: z.string().nullable(),
    cpu_cores: z.number().nullable(),
    device_memory_gb: z.number().nullable(),
    gpu_renderer: z.string().nullable(),
  }),
  geofence: z.object({ state: geofenceState.nullable(), matched: z.array(z.string()) }),
  visitor_id: z.string().nullable(),
  is_returning: z.boolean().nullable(),
};

export const visitSummarySchema: z.ZodType<S['VisitSummary']> = z.object(visitSummaryShape);

export const visitPageSchema: z.ZodType<S['VisitPage']> = z.object({
  items: z.array(visitSummarySchema),
  next_cursor: z.string().nullable(),
});

export const consentState = z.enum([
  'granted',
  'denied',
  'unavailable',
  'not_asked',
  'blocked_by_webview',
]) satisfies z.ZodType<S['ConsentState']>;

const place = z.record(z.string(), z.string().nullable());

/** A ground-truth label (docs/API.md section 11); here because the visit detail carries one. */
export const labelSchema: z.ZodType<S['LabelOut']> = z.object({
  id: z.string(),
  visit_id: z.string(),
  occurred_at: z.string(),
  link: z.object({ id: z.string(), slug: z.string(), label: z.string() }),
  consent_state: consentState,
  cant_tell: z.boolean(),
  truth: z.object({
    country_code: z.string().nullable(),
    admin1: z.string().nullable(),
    admin2: z.string().nullable(),
    city: z.string().nullable(),
  }),
  has_coordinates: z.boolean(),
  connection_kind: z.string().nullable(),
  vpn_used: z.boolean().nullable(),
  network: z.string().nullable(),
  notes: z.string().nullable(),
  labeled_by: z.object({ id: z.string(), name: z.string() }).nullable(),
  labeled_at: z.string(),
  updated_at: z.string(),
  recorded: z.object({
    inference_version: z.string().nullable(),
    strict: place,
    advisory: place,
  }),
});
export type GroundTruthLabel = S['LabelOut'];

export const visitDetailSchema: z.ZodType<S['VisitDetail']> = z.object({
  ...visitSummaryShape,
  finalized_at: z.string().nullable(),
  inferred_at: z.string().nullable(),
  trace_id: z.string().nullable(),
  classifier_version: z.string().nullable(),
  inference_version: z.string().nullable(),
  consent_state: z.string(),
  signals: z.array(z.record(z.string(), z.unknown())),
  candidates: z.array(
    z.object({
      source: z.string(),
      level: z.string(),
      country_code: z.string().nullable(),
      admin1: z.string().nullable(),
      admin2: z.string().nullable(),
      city: z.string().nullable(),
      lat: z.number().nullable(),
      lng: z.number().nullable(),
      raw_confidence: z.number(),
      weight: z.number(),
      effective_weight: z.number(),
      accepted: z.boolean(),
      suppressed_reason: z.string().nullable(),
      evidence: z.record(z.string(), z.unknown()),
      latency_ms: z.number(),
    }),
  ),
  client: z.record(z.string(), z.unknown()),
  request: z.record(z.string(), z.unknown()),
  referer: z.string().nullable(),
  utm: z.record(z.string(), z.string()).nullable(),
  honeypot_tripped: z.boolean(),
  ground_truth_label: labelSchema.nullable(),
});

export const visitorViewSchema: z.ZodType<S['VisitorView']> = z.object({
  visitor_id: z.string(),
  visit_count: z.number(),
  first_seen: z.string().nullable(),
  last_seen: z.string().nullable(),
  truncated: z.boolean(),
  visits: z.array(visitSummarySchema),
  drift: z.array(
    z.object({
      at: z.string(),
      from_visit: z.string(),
      to_visit: z.string(),
      location_changed: z.array(z.string()),
      distance_km: z.number().nullable(),
      device_changed: z.array(z.string()),
      network_changed: z.boolean(),
    }),
  ),
});

/** The link selector needs four fields of `LinkOut`; zod strips the rest. */
export type LinkChoice = Pick<S['LinkOut'], 'id' | 'slug' | 'label' | 'archived_at'>;
export const linkChoicesSchema: z.ZodType<LinkChoice[]> = z.array(
  z.object({
    id: z.string(),
    slug: z.string(),
    label: z.string(),
    archived_at: z.string().nullable(),
  }),
);

/** The Links pages (DESIGN §10.11) read these fields of `LinkOut`; zod strips the rest. */
export type LinkSummary = Pick<
  S['LinkOut'],
  | 'id'
  | 'slug'
  | 'label'
  | 'archived_at'
  | 'capture_url'
  | 'destination_url'
  | 'is_active'
  | 'is_default'
  | 'visit_count'
  | 'created_at'
  | 'ask_location'
>;
export const linksSchema: z.ZodType<LinkSummary[]> = z.array(
  z.object({
    id: z.string(),
    slug: z.string(),
    label: z.string(),
    archived_at: z.string().nullable(),
    capture_url: z.string(),
    destination_url: z.string(),
    is_active: z.boolean(),
    is_default: z.boolean(),
    visit_count: z.number(),
    created_at: z.string(),
    ask_location: z.boolean(),
  }),
);

// ---------------------------------------------------------------------------
// M7.6 (F9.AC22-F9.AC24)
// ---------------------------------------------------------------------------

export const returningSchema: z.ZodType<S['Returning']> = z.object({
  meta: metaSchema,
  since: z.string().nullable(),
  unidentified: z.number(),
  days: z.array(z.object({ day: z.string(), new: z.number(), returning: z.number() })),
  cohorts: z.array(
    z.object({ week: z.string(), size: z.number(), returned: z.array(z.number().nullable()) }),
  ),
  return_after: z.array(
    z.object({
      band: z.enum(['under_1h', '1h_1d', '1d_7d', '7d_30d', 'over_30d']),
      count: z.number(),
    }),
  ),
});

export const carriersSchema: z.ZodType<S['Carriers']> = z.object({
  meta: metaSchema,
  states: z.array(
    z.object({
      key: z.string(),
      visits: z.number(),
      confidence: z.number().nullable(),
      families: z.record(z.string(), z.number()),
      mobile: z.number(),
      broadband: z.number(),
      other_network: z.number(),
    }),
  ),
  unplaced: z.number(),
});

export const captureQualitySchema: z.ZodType<S['CaptureQuality']> = z.object({
  meta: metaSchema,
  apps: z.array(
    z.object({
      key: z.string(),
      captured: z.number(),
      enriched: z.number(),
      server_only: z.number(),
      pending: z.number(),
      consented: z.number(),
    }),
  ),
  buckets: z.array(z.string()),
  series: z.array(z.object({ key: z.string(), enriched_share: z.array(z.number().nullable()) })),
});

export type Returning = S['Returning'];
export type Carriers = S['Carriers'];
export type CaptureQuality = S['CaptureQuality'];
export type SourceDimension = (typeof sourceDimensions)[number];
export type Meta = S['Meta'];
export type Summary = S['Summary'];
export type Kpi = S['Kpi'];
export type TimeSeries = S['TimeSeries'];
export type Calendar = S['Calendar'];
export type Breakdown = S['Breakdown'];
export type BreakdownDimension = S['BreakdownDimension'];
export type Signals = S['Signals'];
export type Confidence = S['Confidence'];
export type SourceFlow = S['SourceFlow'];
export type Funnel = S['Funnel'];
export type Accuracy = S['Accuracy'];
export type Geo = S['Geo'];
export type VisitSummary = S['VisitSummary'];
export type VisitPage = S['VisitPage'];
export type VisitDetail = S['VisitDetail'];
export type VisitorView = S['VisitorView'];
export type Classification = S['Classification'];
