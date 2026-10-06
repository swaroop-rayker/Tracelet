/**
 * Link writes from the dashboard (docs/API.md §6). Owner-only and audited on the server.
 * Today one: whether a link asks for the visitor's location (F1.AC11, ADR-0021); the full
 * link editor is M7's (F10.AC6).
 */

import { request, type ApiResult } from '@/api/client';
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
