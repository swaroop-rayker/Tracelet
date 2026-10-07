/**
 * System health (docs/API.md §10; F10, F11.AC9, F12, F15.AC6).
 *
 * Reads go through `useApi` with the schemas below; writes are one function per endpoint and
 * return `ApiResult`, so a failure stays a value with its trace id (UI-14). Each schema is
 * checked against the generated type with `satisfies`, so a contract change fails `tsc`.
 */

import { request, type ApiResult } from '@/api/client';
import type { components } from '@/api/generated/schema';
// Configured for the CSP (ERRORS E44, E54): never import zod directly.
import { z } from '@/api/zod';

type S = components['schemas'];

export const HEALTH = '/api/v1/health';

function parseWith<T>(schema: z.ZodType<T>): (value: unknown) => T | null {
  return (value) => {
    const parsed = schema.safeParse(value);
    return parsed.success ? parsed.data : null;
  };
}

const iso = z.string();

// ---------------------------------------------------------------------------
// Host metrics and degradation
// ---------------------------------------------------------------------------

const usage = z.object({
  used: z.number(),
  total: z.number(),
  percent: z.number(),
  warn_percent: z.number(),
  state: z.string(),
}) satisfies z.ZodType<S['UsageOut']>;
export type Usage = z.infer<typeof usage>;

export const systemSchema = z.object({
  scope: z.string(),
  scope_reason: z.string().nullable(),
  sampled_at: iso,
  cpu: z.object({
    percent: z.number(),
    count: z.number(),
    load: z.tuple([z.number(), z.number(), z.number()]),
  }),
  memory: usage,
  swap: usage,
  disk: usage.extend({ path: z.string() }),
  uptime_seconds: z.number(),
  database_bytes: z.number(),
  temperature: z.object({
    celsius: z.number().nullable(),
    sensor: z.string().nullable(),
    reason: z.string().nullable(),
  }),
  poll_seconds: z.number(),
}) satisfies z.ZodType<S['SystemOut']>;
export type SystemSample = z.infer<typeof systemSchema>;

export const severity = z.enum(['critical', 'warning', 'notice']);
export type Severity = z.infer<typeof severity>;

const condition = z.object({
  key: z.string(),
  severity,
  title: z.string(),
  detail: z.string(),
  still_works: z.string(),
}) satisfies z.ZodType<S['ConditionOut']>;
export type Condition = z.infer<typeof condition>;

export const degradationSchema = z.object({
  conditions: z.array(condition),
  checked_at: iso,
}) satisfies z.ZodType<S['DegradationOut']>;

// ---------------------------------------------------------------------------
// Geo databases
// ---------------------------------------------------------------------------

export const dbState = z.enum([
  'updating',
  'update_failed',
  'unable_to_update',
  'not_installed',
  'update_available',
  'up_to_date',
]);
export type DbState = z.infer<typeof dbState>;

const database = z.object({
  name: z.string(),
  kind: z.string(),
  feeds: z.string().nullable(),
  attribution: z.string(),
  configured: z.boolean(),
  auto_update: z.boolean(),
  staleness_days: z.number(),
  stale: z.boolean(),
  state: dbState,
  progress: z.object({ phase: z.string(), percent: z.number().nullable() }).nullable(),
  age_days: z.number().nullable(),
  installed: z
    .object({
      version: z.string().nullable(),
      released_at: iso.nullable(),
      installed_at: iso.nullable(),
      size_bytes: z.number().nullable(),
      sha256: z.string().nullable(),
    })
    .nullable(),
  latest: z.object({ version: z.string().nullable(), released_at: iso.nullable() }).nullable(),
  last_attempt: z.object({ status: z.string(), at: iso, error: z.string().nullable() }).nullable(),
  check_error: z.string().nullable(),
  checked_at: iso.nullable(),
}) satisfies z.ZodType<S['DatabaseOut']>;
export type GeoDatabase = z.infer<typeof database>;

export const databasesSchema = z.object({
  databases: z.array(database),
}) satisfies z.ZodType<S['DatabasesOut']>;

const started = z.object({ name: z.string(), status: z.string() }) satisfies z.ZodType<
  S['UpdateStarted']
>;

export function setAutoUpdate(
  csrfToken: string,
  name: string,
  autoUpdate: boolean,
): Promise<ApiResult<GeoDatabase>> {
  return request(`${HEALTH}/databases/${encodeURIComponent(name)}`, {
    method: 'PATCH',
    body: { auto_update: autoUpdate },
    csrfToken,
    parse: parseWith(database),
  });
}

export function checkDatabases(
  csrfToken: string,
): Promise<ApiResult<z.infer<typeof databasesSchema>>> {
  return request(`${HEALTH}/databases/check`, {
    method: 'POST',
    csrfToken,
    parse: parseWith(databasesSchema),
  });
}

export function updateDatabase(
  csrfToken: string,
  name: string,
): Promise<ApiResult<z.infer<typeof started>>> {
  return request(`${HEALTH}/databases/${encodeURIComponent(name)}/update`, {
    method: 'POST',
    csrfToken,
    parse: parseWith(started),
  });
}

// ---------------------------------------------------------------------------
// Rate limits
// ---------------------------------------------------------------------------

const limitValue = z.object({
  per_period: z.number(),
  period_seconds: z.number(),
  burst: z.number(),
}) satisfies z.ZodType<S['LimitValue']>;
export type LimitValue = z.infer<typeof limitValue>;

const limit = limitValue.extend({
  name: z.string(),
  group: z.string(),
  label: z.string(),
  description: z.string(),
  default: limitValue,
  overridden: z.boolean(),
  ceiling_per_second: z.number().nullable(),
}) satisfies z.ZodType<S['LimitOut']>;
export type RateLimit = z.infer<typeof limit>;

export const rateLimitsSchema = z.object({
  limits: z.array(limit),
  applies_within_seconds: z.number(),
}) satisfies z.ZodType<S['RateLimitsOut']>;
export type RateLimits = z.infer<typeof rateLimitsSchema>;

export function updateRateLimits(
  csrfToken: string,
  limits: Readonly<Record<string, LimitValue | null>>,
): Promise<ApiResult<RateLimits>> {
  return request(`${HEALTH}/ratelimits`, {
    method: 'PATCH',
    body: { limits },
    csrfToken,
    parse: parseWith(rateLimitsSchema),
  });
}

// ---------------------------------------------------------------------------
// Retention
// ---------------------------------------------------------------------------

const policy = z.object({
  visit_days: z.number(),
  ip_days: z.number(),
  audit_days: z.number(),
}) satisfies z.ZodType<S['PolicyModel']>;
export type Policy = z.infer<typeof policy>;

const counts = z.object({
  visits: z.number(),
  visit_candidates: z.number(),
  ip_addresses: z.number(),
  audit_rows: z.number(),
  delivered_alerts: z.number(),
}) satisfies z.ZodType<S['CountsModel']>;
export type PurgeCounts = z.infer<typeof counts>;

export const retentionSchema = z.object({
  policy,
  delivered_alerts_days: z.number(),
  rollups: z.string(),
  updated_at: iso,
  purge_running: z.boolean(),
  last_purge: z
    .object({ at: iso, trigger: z.string(), counts, by: z.string().nullable() })
    .nullable(),
}) satisfies z.ZodType<S['RetentionOut']>;
export type Retention = z.infer<typeof retentionSchema>;

const preview = z.object({
  as_of: iso,
  policy,
  cutoffs: z.object({ visits: iso, ip: iso, audit: iso, outbox: iso }),
  counts,
}) satisfies z.ZodType<S['PreviewOut']>;
export type PurgePreview = z.infer<typeof preview>;

export function updateRetention(csrfToken: string, value: Policy): Promise<ApiResult<Retention>> {
  return request(`${HEALTH}/retention`, {
    method: 'PATCH',
    body: value,
    csrfToken,
    parse: parseWith(retentionSchema),
  });
}

export function previewPurge(csrfToken: string): Promise<ApiResult<PurgePreview>> {
  return request(`${HEALTH}/retention/preview`, {
    method: 'POST',
    csrfToken,
    parse: parseWith(preview),
  });
}

const purgeStarted = z.object({ as_of: iso, status: z.string() }) satisfies z.ZodType<
  S['PurgeAccepted']
>;

export function startPurge(
  csrfToken: string,
  shown: PurgePreview,
): Promise<ApiResult<z.infer<typeof purgeStarted>>> {
  return request(`${HEALTH}/retention/purge`, {
    method: 'POST',
    body: { as_of: shown.as_of, policy: shown.policy },
    csrfToken,
    parse: parseWith(purgeStarted),
  });
}

// ---------------------------------------------------------------------------
// Backups
// ---------------------------------------------------------------------------

const restoreCheck = z.object({
  id: z.number(),
  backup_id: z.string(),
  kind: z.enum(['scheduled', 'manual']),
  status: z.enum(['running', 'passed', 'failed']),
  mismatches: z.record(z.string(), z.unknown()).nullable(),
  error: z.string().nullable(),
  started_at: iso,
  finished_at: iso.nullable(),
}) satisfies z.ZodType<S['RestoreCheckOut']>;
export type RestoreCheck = z.infer<typeof restoreCheck>;

const backup = z.object({
  id: z.string(),
  kind: z.enum(['scheduled', 'manual']),
  status: z.enum(['running', 'ok', 'failed', 'pruned']),
  file_name: z.string().nullable(),
  size_bytes: z.number().nullable(),
  sha256: z.string().nullable(),
  tables: z.number().nullable(),
  rows: z.number().nullable(),
  error: z.string().nullable(),
  started_at: iso,
  finished_at: iso.nullable(),
  last_downloaded_at: iso.nullable(),
  last_restore_check: restoreCheck.nullable(),
}) satisfies z.ZodType<S['BackupOut']>;
export type Backup = z.infer<typeof backup>;

export const backupsSchema = z.object({
  backups: z.array(backup),
  last_restore_check: restoreCheck.nullable(),
  backup_running: z.boolean(),
  restore_check_running: z.boolean(),
  download: z.object({
    last_downloaded_at: iso.nullable(),
    reminder_days: z.number(),
    overdue: z.boolean(),
  }),
}) satisfies z.ZodType<S['BackupsOut']>;
export type Backups = z.infer<typeof backupsSchema>;

const jobStarted = z.object({ id: z.string(), status: z.string() }) satisfies z.ZodType<
  S['Started']
>;

export function startBackup(csrfToken: string): Promise<ApiResult<z.infer<typeof jobStarted>>> {
  return request(`${HEALTH}/backups`, { method: 'POST', csrfToken, parse: parseWith(jobStarted) });
}

export function verifyRestore(
  csrfToken: string,
  backupId: string,
): Promise<ApiResult<z.infer<typeof jobStarted>>> {
  return request(`${HEALTH}/backups/${encodeURIComponent(backupId)}/verify-restore`, {
    method: 'POST',
    csrfToken,
    parse: parseWith(jobStarted),
  });
}

/** The download is a plain navigation: the browser streams the file to disk itself. */
export function backupDownloadUrl(backupId: string): string {
  return `${HEALTH}/backups/${encodeURIComponent(backupId)}/download`;
}

// ---------------------------------------------------------------------------
// The inference flow diagram
// ---------------------------------------------------------------------------

const fired = z.object({
  level: z.string(),
  value: z.string(),
  accepted: z.boolean(),
  suppressed_reason: z.string().nullable(),
  effective_weight: z.number(),
}) satisfies z.ZodType<S['FiredOut']>;

const sourceOutcome = z.object({
  source: z.string(),
  status: z.string(),
  reason: z.string().nullable(),
  candidates: z.array(fired),
}) satisfies z.ZodType<S['SourceOutcomeOut']>;
export type SourceOutcome = z.infer<typeof sourceOutcome>;

const levelOutcome = z.object({
  level: z.string(),
  strict: z.string().nullable(),
  advisory: z.string().nullable(),
  confidence: z.number().nullable(),
  abstain_reason: z.string().nullable(),
}) satisfies z.ZodType<S['LevelOutcomeOut']>;
export type LevelOutcome = z.infer<typeof levelOutcome>;

const sample = z.object({
  visit_id: z.string(),
  inference_version: z.string().nullable(),
  classification: z.string(),
  geo_source_primary: z.string().nullable(),
  geofence_state: z.string().nullable(),
  sources: z.array(sourceOutcome),
  levels: z.array(levelOutcome),
  rules_fired: z.array(z.string()),
  alert: z.record(z.string(), z.unknown()).nullable(),
}) satisfies z.ZodType<S['SampleOut']>;
export type FlowSample = z.infer<typeof sample>;

export const flowSchema = z.object({
  inference_version: z.string(),
  stages: z.array(z.string()),
  families: z.array(
    z.object({ family: z.string(), label: z.string(), sources: z.array(z.string()) }),
  ),
  sources: z.array(
    z.object({
      source: z.string(),
      code: z.string(),
      label: z.string(),
      family: z.string(),
      order: z.number(),
      enabled: z.boolean(),
      timeout_ms: z.number(),
      priors: z.record(z.string(), z.number()),
    }),
  ),
  rules: z.array(z.object({ rule: z.string(), label: z.string(), description: z.string() })),
  levels: z.array(z.object({ level: z.string(), threshold: z.number() })),
  sample: sample.nullable(),
}) satisfies z.ZodType<S['FlowOut']>;
export type Flow = z.infer<typeof flowSchema>;

// ---------------------------------------------------------------------------
// Inference source switches (F10.AC7)
// ---------------------------------------------------------------------------

/**
 * The settings are read loosely and sent back whole: a PATCH must carry the complete object
 * (API §10), and this page only flips `sources.<name>.enabled`. Everything else round-trips
 * untouched, so this page never has to know the classifier's or the priors' shape -- and so
 * this schema is deliberately not tied to the generated type.
 */
const inferenceSettings = z.looseObject({
  sources: z.record(z.string(), z.looseObject({ enabled: z.boolean() })),
});
export type InferenceSettings = z.infer<typeof inferenceSettings>;

export const inferenceSchema = z.object({
  engine_revision: z.string(),
  active_version: z.number(),
  inference_version: z.string(),
  settings: inferenceSettings,
});
export type InferenceState = z.infer<typeof inferenceSchema>;

export function saveInferenceSettings(
  csrfToken: string,
  settings: InferenceSettings,
  note: string,
): Promise<ApiResult<InferenceState>> {
  return request(`${HEALTH}/inference`, {
    method: 'PATCH',
    body: { settings, note },
    csrfToken,
    parse: parseWith(inferenceSchema),
  });
}
