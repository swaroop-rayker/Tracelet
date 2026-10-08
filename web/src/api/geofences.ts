/**
 * Geofences and notifications (docs/API.md §9, §9a, §10; F6, F7).
 *
 * Reads go through `useApi` with the schemas below; writes are one function per endpoint,
 * returning `ApiResult` so a failure stays a value with its trace id (UI-14). Each schema is
 * checked against the generated type with `satisfies`, so a contract change fails `tsc`, and
 * keeps its own narrower type -- a polygon's geometry is a GeoJSON Polygon here, where the
 * contract only says "an object".
 */

import { noContent, request, type ApiResult } from '@/api/client';
import type { components } from '@/api/generated/schema';
// Configured for the CSP (ERRORS E44, E54): never import zod directly.
import { z } from '@/api/zod';

type S = components['schemas'];

const FENCES = '/api/v1/geofences';

// ---------------------------------------------------------------------------
// Shapes
// ---------------------------------------------------------------------------

export const shapeKind = z.enum(['polygon', 'circle', 'region']) satisfies z.ZodType<
  S['ShapeKind']
>;
export type ShapeKind = z.infer<typeof shapeKind>;

export const notifyPriority = z.enum(['high', 'normal', 'silent']) satisfies z.ZodType<
  S['NotifyPriority']
>;
export type NotifyPriority = z.infer<typeof notifyPriority>;

export const geofenceState = z.enum(['inside', 'outside', 'undetermined']) satisfies z.ZodType<
  S['GeofenceState']
>;
export type GeofenceState = z.infer<typeof geofenceState>;

const latLng = z.object({ lat: z.number(), lng: z.number() }) satisfies z.ZodType<S['LatLng']>;
export type LatLng = z.infer<typeof latLng>;

/** RFC 7946: longitude first. Each ring is closed. */
const polygon = z.object({
  type: z.literal('Polygon'),
  coordinates: z.array(z.array(z.array(z.number()).min(2))),
});
export type PolygonGeometry = z.infer<typeof polygon>;

export const geofenceSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string().nullable(),
  shape_kind: shapeKind,
  region_keys: z.array(z.string()).nullable(),
  geometry: polygon.nullable(),
  center: latLng.nullable(),
  radius_m: z.number().nullable(),
  priority: z.number(),
  is_active: z.boolean(),
  notify_priority: notifyPriority,
  link_ids: z.array(z.string()).nullable(),
  unknown_region_keys: z.array(z.string()),
  matches_7d: z.number(),
  created_by: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
}) satisfies z.ZodType<S['GeofenceOut']>;
export type Geofence = z.infer<typeof geofenceSchema>;

export const geofencesSchema = z.array(geofenceSchema);

export const regionsSchema = z.object({
  countries: z.array(z.object({ key: z.string() })),
  divisions: z.array(
    z.object({ key: z.string(), code: z.string(), country: z.string(), name: z.string() }),
  ),
}) satisfies z.ZodType<S['RegionsOut']>;
export type Regions = z.infer<typeof regionsSchema>;

export const placesSchema = z.object({
  country: z.string(),
  places: z.array(
    z.object({
      name: z.string(),
      admin1: z.string().nullable(),
      lat: z.number(),
      lng: z.number(),
      population: z.number(),
      tier: z.enum(['metro', 'tier1', 'tier2', 'tier3']),
    }),
  ),
}) satisfies z.ZodType<S['PlacesOut']>;
export type Places = z.infer<typeof placesSchema>;

export const testSchema = z.object({
  placed: z.object({ country_code: z.string().nullable(), admin1: z.string().nullable() }),
  state: geofenceState.nullable(),
  results: z.array(
    z.object({
      geofence_id: z.string(),
      name: z.string(),
      result: geofenceState,
      reason: z.string().nullable(),
    }),
  ),
}) satisfies z.ZodType<S['TestOut']>;
export type CoordinateTest = z.infer<typeof testSchema>;

/** What a create or a full edit sends. Exactly one shape's fields are present. */
export type GeofenceShape =
  | { readonly shape_kind: 'region'; readonly region_keys: readonly string[] }
  | { readonly shape_kind: 'circle'; readonly center: LatLng; readonly radius_m: number }
  | { readonly shape_kind: 'polygon'; readonly geometry: PolygonGeometry };

export interface GeofenceFields {
  readonly name: string;
  readonly description: string | null;
  readonly priority: number;
  readonly is_active: boolean;
  readonly notify_priority: NotifyPriority;
  readonly link_ids: readonly string[] | null;
}

function parseWith<T>(schema: z.ZodType<T>): (value: unknown) => T | null {
  return (value) => {
    const parsed = schema.safeParse(value);
    return parsed.success ? parsed.data : null;
  };
}

export function createGeofence(
  csrfToken: string,
  body: GeofenceFields & GeofenceShape,
): Promise<ApiResult<Geofence>> {
  return request(FENCES, { method: 'POST', body, csrfToken, parse: parseWith(geofenceSchema) });
}

/** Sends every field: the editor always knows the whole geofence it is saving. */
export function updateGeofence(
  csrfToken: string,
  id: string,
  body: Partial<GeofenceFields> & Partial<GeofenceShape>,
): Promise<ApiResult<Geofence>> {
  return request(`${FENCES}/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body,
    csrfToken,
    parse: parseWith(geofenceSchema),
  });
}

export function deleteGeofence(csrfToken: string, id: string): Promise<ApiResult<null>> {
  return request(`${FENCES}/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    csrfToken,
    parse: noContent,
  });
}

export function testCoordinate(
  csrfToken: string,
  point: LatLng,
): Promise<ApiResult<CoordinateTest>> {
  return request(`${FENCES}/test`, {
    method: 'POST',
    body: point,
    csrfToken,
    parse: parseWith(testSchema),
  });
}

export function importGeofences(
  csrfToken: string,
  collection: unknown,
): Promise<ApiResult<Geofence[]>> {
  return request(`${FENCES}/import`, {
    method: 'POST',
    body: collection,
    csrfToken,
    parse: parseWith(geofencesSchema),
  });
}

/** The export, as text for a download. Any JSON object is accepted: it is not rendered. */
export function exportGeofences(): Promise<ApiResult<string>> {
  return request(`${FENCES}/export`, {
    parse: (value) =>
      typeof value === 'object' && value !== null ? JSON.stringify(value, null, 1) : null,
  });
}

// ---------------------------------------------------------------------------
// Notifications (§9a) and the outbox (§10)
// ---------------------------------------------------------------------------

const quietHours = z.object({
  enabled: z.boolean(),
  start: z.string(),
  end: z.string(),
  timezone: z.string(),
});
export type QuietHours = z.infer<typeof quietHours>;

/** F7.AC10–F7.AC15: four more alert types, each off until an owner switches it on. */
const alertTypes = z.object({
  digest: z.object({ enabled: z.boolean(), at: z.string() }),
  spike: z.object({ enabled: z.boolean(), floor: z.number(), k: z.number() }),
  new_place: z.object({ enabled: z.boolean() }),
  returning: z.object({ enabled: z.boolean(), after_days: z.number() }),
});
export type AlertTypes = z.infer<typeof alertTypes>;

export const notificationSettingsSchema = z.object({
  telegram: z.object({
    bot_token_set: z.boolean(),
    chat_id_set: z.boolean(),
    chat_verified: z.boolean(),
  }),
  quiet_hours: quietHours.extend({ active_now: z.boolean() }),
  alert_types: alertTypes,
}) satisfies z.ZodType<S['NotificationSettingsOut']>;
export type NotificationSettings = z.infer<typeof notificationSettingsSchema>;

export const outboxStatus = z.enum([
  'pending',
  'in_flight',
  'done',
  'failed',
  'dead',
]) satisfies z.ZodType<S['OutboxStatus']>;
export type OutboxStatus = z.infer<typeof outboxStatus>;

const delivery = z.object({
  id: z.number(),
  kind: z.enum([
    'telegram.visit_alert',
    'telegram.password_reset',
    'telegram.health_alert',
    'telegram.test',
    'telegram.digest',
    'telegram.spike',
    'telegram.new_place',
    'telegram.returning',
  ]),
  priority: notifyPriority,
  status: outboxStatus,
  attempts: z.number(),
  max_attempts: z.number(),
  created_at: z.string(),
  next_attempt_at: z.string(),
  completed_at: z.string().nullable(),
  last_error: z.string().nullable(),
  visit_id: z.string().nullable(),
  link_label: z.string().nullable(),
  geofence_name: z.string().nullable(),
  geofence_state: z.string().nullable(),
}) satisfies z.ZodType<S['DeliveryOut']>;
export type Delivery = z.infer<typeof delivery>;

export const outboxSchema = z.object({
  counts: z.object({
    pending: z.number(),
    in_flight: z.number(),
    failed: z.number(),
    dead: z.number(),
    held: z.number(),
  }),
  items: z.array(delivery),
  next_cursor: z.number().nullable(),
}) satisfies z.ZodType<S['OutboxOut']>;
export type Outbox = z.infer<typeof outboxSchema>;

export function updateQuietHours(
  csrfToken: string,
  value: QuietHours,
): Promise<ApiResult<NotificationSettings>> {
  return request('/api/v1/notifications/settings', {
    method: 'PATCH',
    body: { quiet_hours: value },
    csrfToken,
    parse: parseWith(notificationSettingsSchema),
  });
}

export function updateAlertTypes(
  csrfToken: string,
  value: AlertTypes,
): Promise<ApiResult<NotificationSettings>> {
  return request('/api/v1/notifications/settings', {
    method: 'PATCH',
    body: { alert_types: value },
    csrfToken,
    parse: parseWith(notificationSettingsSchema),
  });
}

export function retryDelivery(csrfToken: string, id: number): Promise<ApiResult<Delivery>> {
  return request(`/api/v1/health/outbox/${String(id)}/retry`, {
    method: 'POST',
    csrfToken,
    parse: parseWith(delivery),
  });
}

const sent = z.object({ delivered_at: z.string(), message_id: z.number() }) satisfies z.ZodType<
  S['TestSentOut']
>;

export function sendTestMessage(
  csrfToken: string,
): Promise<ApiResult<{ readonly delivered_at: string; readonly message_id: number }>> {
  return request('/api/v1/health/telegram/test', {
    method: 'POST',
    csrfToken,
    parse: parseWith(sent),
  });
}
