/**
 * Ground truth and accuracy (docs/API.md §11, F4.AC15, F4.AC17, F9.AC10, M8, ADR-0024).
 *
 * A label is the owner's truth beside a visit; it never changes the visit's inference. The
 * figures are a replay of the consensus over the labelled visits, each proportion with its
 * count and a 95 % Wilson interval, so a page can never show a percentage without its sample
 * (RISKS R9). Writing a label is owner-only; the server enforces it.
 */

import type { QueryClient } from '@tanstack/react-query';
import { noContent, request, type ApiResult } from '@/api/client';
import type { components } from '@/api/generated/schema';
import {
  consentState,
  interval,
  labelSchema,
  visitSummarySchema,
  type GroundTruthLabel,
} from '@/api/schemas';
// Configured for the CSP (ERRORS E44, E54): never import zod directly.
import { z } from '@/api/zod';

type S = components['schemas'];

export const GT = '/api/v1/ground-truth';

const geoLevel = z.enum(['country', 'admin1', 'admin2', 'city', 'point']) satisfies z.ZodType<
  S['GeoLevel']
>;
const source = z.enum([
  'gps',
  'geolite2',
  'ip2location',
  'ipinfo',
  'dbip',
  'rdns',
  'asn_org',
  'cf_colo',
  'external_api',
  'latency',
  'timezone',
]) satisfies z.ZodType<S['InferenceSource']>;
export const population = z.enum(['all', 'consented', 'non_consented', 'network_only']);
export type Population = z.infer<typeof population>;
const metric = z.enum(['strict_precision', 'strict_coverage', 'advisory_accuracy']);

export const proportionSchema: z.ZodType<S['Proportion']> = z.object({
  k: z.number(),
  n: z.number(),
  value: z.number().nullable(),
  ci95: interval,
});
export type Proportion = S['Proportion'];

const levelReport: z.ZodType<S['LevelReport']> = z.object({
  level: geoLevel,
  label_count: z.number(),
  strict_precision: proportionSchema,
  strict_coverage: proportionSchema,
  advisory_accuracy: proportionSchema,
});
export type LevelReport = S['LevelReport'];

const targetCheck: z.ZodType<S['TargetCheck']> = z.object({
  id: z.string(),
  population,
  level: geoLevel,
  metric,
  target: z.number().nullable(),
  value: z.number().nullable(),
  n: z.number(),
  gated: z.boolean(),
  status: z.enum(['met', 'missed', 'unmeasured', 'reported']),
});
export type TargetCheck = S['TargetCheck'];

export const reportSchema: z.ZodType<S['Report']> = z.object({
  settings_version: z.number().nullable(),
  inference_version: z.string(),
  classifier_version: z.string(),
  label_count: z.number(),
  cant_tell: z.number(),
  pending: z.number(),
  populations: z.array(
    z.object({ population, label_count: z.number(), levels: z.array(levelReport) }),
  ),
  paths: z.array(
    z.object({
      path: z.enum(['cloudflare', 'direct']),
      label_count: z.number(),
      city_strict_precision: proportionSchema,
      city_strict_coverage: proportionSchema,
    }),
  ),
  sources: z.array(
    z.object({
      source,
      levels: z.array(
        z.object({
          level: geoLevel,
          claims: z.number(),
          accepted: z.number(),
          correct: proportionSchema,
        }),
      ),
    }),
  ),
  matrix: z.array(
    z.object({
      network: z.string().nullable(),
      connection_kind: z.string().nullable(),
      vpn_used: z.boolean().nullable(),
      count: z.number(),
    }),
  ),
  targets: z.array(targetCheck),
  passed: z.boolean().nullable(),
});
export type Report = S['Report'];

export const labelListSchema: z.ZodType<S['LabelList']> = z.object({
  items: z.array(labelSchema),
  total: z.number(),
  cant_tell: z.number(),
});

export const queueSchema: z.ZodType<S['Queue']> = z.object({
  items: z.array(
    z.object({
      visit: visitSummarySchema,
      consent_state: consentState,
      conflict_score: z.number().nullable(),
      agreement_score: z.number().nullable(),
    }),
  ),
  labelled: z.number(),
  remaining: z.number(),
});
export type Queue = S['Queue'];

const runShape = {
  id: z.string(),
  run_at: z.string(),
  origin: z.enum(['cli', 'dashboard']),
  settings_version: z.number(),
  inference_version: z.string(),
  classifier_version: z.string(),
  git_sha: z.string().nullable(),
  label_count: z.number(),
  passed: z.boolean().nullable(),
  note: z.string().nullable(),
  recorded_by: z.object({ id: z.string(), name: z.string() }).nullable(),
};
export const runSchema: z.ZodType<S['RunOut']> = z.object(runShape);
export const runListSchema = z.array(runSchema);
export const runDetailSchema: z.ZodType<S['RunDetail']> = z.object({
  ...runShape,
  metrics: reportSchema,
});
export type Run = S['RunOut'];

export const CONNECTION_KINDS = ['wifi', 'mobile_data', 'ethernet'] as const;
export const NETWORKS = ['airtel', 'jio', 'vi', 'bsnl', 'act', 'other'] as const;
export type ConnectionKind = (typeof CONNECTION_KINDS)[number];
export type NetworkFamily = (typeof NETWORKS)[number];

export const CONNECTION_LABEL: Readonly<Record<ConnectionKind, string>> = {
  wifi: 'Wi-Fi',
  mobile_data: 'Mobile data',
  ethernet: 'Ethernet',
};
export const NETWORK_LABEL: Readonly<Record<NetworkFamily, string>> = {
  airtel: 'Airtel',
  jio: 'Jio',
  vi: 'Vi',
  bsnl: 'BSNL',
  act: 'ACT',
  other: 'Other',
};

/** A network family's name; an unknown value is shown as stored. */
export function networkName(network: string): string {
  const known = NETWORKS.find((n) => n === network);
  return known === undefined ? network : NETWORK_LABEL[known];
}

/** What a label says, as the form sends it (API §11). */
export interface LabelFields {
  readonly cant_tell: boolean;
  readonly country_code: string | null;
  readonly admin1: string | null;
  readonly admin2: string | null;
  readonly city: string | null;
  /** True copies the visit's GPS fix; false clears it; null keeps what is stored. */
  readonly use_gps: boolean | null;
  readonly connection_kind: ConnectionKind | null;
  readonly vpn_used: boolean | null;
  readonly network: NetworkFamily | null;
  readonly notes: string | null;
}

function parseWith<T>(schema: z.ZodType<T>): (value: unknown) => T | null {
  return (value) => {
    const parsed = schema.safeParse(value);
    return parsed.success ? parsed.data : null;
  };
}

export function createLabel(
  csrfToken: string,
  visitId: string,
  fields: LabelFields,
): Promise<ApiResult<GroundTruthLabel>> {
  return request(GT, {
    method: 'POST',
    body: { visit_id: visitId, ...fields },
    csrfToken,
    parse: parseWith(labelSchema),
  });
}

export function updateLabel(
  csrfToken: string,
  id: string,
  fields: LabelFields,
): Promise<ApiResult<GroundTruthLabel>> {
  return request(`${GT}/${id}`, {
    method: 'PATCH',
    body: fields,
    csrfToken,
    parse: parseWith(labelSchema),
  });
}

export function deleteLabel(csrfToken: string, id: string): Promise<ApiResult<null>> {
  return request(`${GT}/${id}`, { method: 'DELETE', csrfToken, parse: noContent });
}

export function recordRun(
  csrfToken: string,
  note: string | null,
): Promise<ApiResult<S['RunDetail']>> {
  return request(`${GT}/runs`, {
    method: 'POST',
    body: { note },
    csrfToken,
    parse: parseWith(runDetailSchema),
  });
}

/**
 * After a label changes: every ground-truth view, the Inference page's accuracy card and the
 * visit's own page read it, so all of them refetch.
 */
export function refreshAccuracy(client: QueryClient, visitId: string | null): void {
  void client.invalidateQueries({
    predicate: (query) => {
      const key = query.queryKey[0];
      return (
        typeof key === 'string' &&
        (key.startsWith(GT) ||
          key === '/api/v1/analytics/accuracy' ||
          (visitId !== null && key === `/api/v1/visits/${encodeURIComponent(visitId)}`))
      );
    },
  });
}
