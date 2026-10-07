/**
 * Link writes from the dashboard (docs/API.md §6). Owner-only and audited on the server:
 * create, edit, archive, make default, and a permanent delete after its preview (F10.AC6,
 * SPEC §11 row 25); and the one-click "asks for location" switch (F1.AC11, ADR-0021).
 */

import { noContent, request, type ApiResult } from '@/api/client';
import type { components } from '@/api/generated/schema';
import { z } from '@/api/zod';

type S = components['schemas'];

/** The part of the updated link this call reads back: what was saved. */
const saved = z.object({ id: z.string(), ask_location: z.boolean() }) satisfies z.ZodType<
  Pick<S['LinkOut'], 'id' | 'ask_location'>
>;

export function setAskLocation(
  csrfToken: string,
  linkId: string,
  askLocation: boolean,
): Promise<ApiResult<z.infer<typeof saved>>> {
  return request(`/api/v1/links/${encodeURIComponent(linkId)}`, {
    method: 'PATCH',
    body: { ask_location: askLocation },
    csrfToken,
    // Parse the link that comes back. A parser that returns null on a 200 with a body is
    // read as a malformed response, so the switch flipped back on a save that had worked
    // (ERRORS E64).
    parse: (value) => {
      const parsed = saved.safeParse(value);
      return parsed.success ? parsed.data : null;
    },
  });
}

// ---------------------------------------------------------------------------
// The full link editor (F10.AC6, SPEC §11 row 25)
// ---------------------------------------------------------------------------

const priority = z.enum(['high', 'normal', 'silent']);
export type Priority = z.infer<typeof priority>;

const notifyPolicy = z.object({
  inside: priority,
  outside: priority,
  undetermined: priority,
  automated: z.literal('silent'),
}) satisfies z.ZodType<S['NotifyPolicy']>;

const link = z.object({
  id: z.string(),
  slug: z.string(),
  label: z.string(),
  destination_url: z.string(),
  capture_url: z.string(),
  is_active: z.boolean(),
  is_default: z.boolean(),
  notify_policy: notifyPolicy,
  interstitial_ms: z.number(),
  ask_location: z.boolean(),
  cloned_from: z.string().nullable(),
  visit_count: z.number(),
  created_at: z.string(),
  updated_at: z.string(),
  archived_at: z.string().nullable(),
}) satisfies z.ZodType<S['LinkOut']>;
export type LinkFull = z.infer<typeof link>;

export const linksFullSchema = z.array(link);

/** What the dialog edits; the server takes the same shape for create and update. */
export interface LinkForm {
  readonly slug: string;
  readonly label: string;
  readonly destination_url: string;
  readonly is_active: boolean;
  readonly interstitial_ms: number;
  readonly ask_location: boolean;
  readonly notify_policy: {
    readonly inside: Priority;
    readonly outside: Priority;
    readonly undetermined: Priority;
    readonly automated: 'silent';
  };
}

function parseLink(value: unknown): LinkFull | null {
  const parsed = link.safeParse(value);
  return parsed.success ? parsed.data : null;
}

export function createLink(csrfToken: string, form: LinkForm): Promise<ApiResult<LinkFull>> {
  return request('/api/v1/links', { method: 'POST', body: form, csrfToken, parse: parseLink });
}

export function updateLink(
  csrfToken: string,
  linkId: string,
  changes: Partial<LinkForm>,
): Promise<ApiResult<LinkFull>> {
  return request(`/api/v1/links/${encodeURIComponent(linkId)}`, {
    method: 'PATCH',
    body: changes,
    csrfToken,
    parse: parseLink,
  });
}

export function archiveLink(csrfToken: string, linkId: string): Promise<ApiResult<LinkFull>> {
  return request(`/api/v1/links/${encodeURIComponent(linkId)}/archive`, {
    method: 'POST',
    csrfToken,
    parse: parseLink,
  });
}

export function makeDefault(csrfToken: string, linkId: string): Promise<ApiResult<LinkFull>> {
  return request(`/api/v1/links/${encodeURIComponent(linkId)}/default`, {
    method: 'POST',
    csrfToken,
    parse: parseLink,
  });
}

const geofenceRef = z.object({ id: z.string(), name: z.string() });
export const deletePreviewSchema = z.object({
  slug: z.string(),
  is_default: z.boolean(),
  archived: z.boolean(),
  visits: z.number(),
  visit_candidates: z.number(),
  rollup_rows: z.number(),
  geofences_updated: z.array(geofenceRef),
  geofences_deactivated: z.array(geofenceRef),
}) satisfies z.ZodType<S['DeletePreview']>;
export type DeletePreview = z.infer<typeof deletePreviewSchema>;

/** Permanent: the link, its visits and their history (SPEC §11 row 25). */
export function deleteLink(csrfToken: string, linkId: string): Promise<ApiResult<null>> {
  return request(`/api/v1/links/${encodeURIComponent(linkId)}?with_visits=true`, {
    method: 'DELETE',
    csrfToken,
    parse: noContent,
  });
}
