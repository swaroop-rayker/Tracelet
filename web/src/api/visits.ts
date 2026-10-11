/**
 * One visit's privacy-sensitive reads (docs/API.md §7): the owner's audited address
 * decryption (F12.AC4) and the data-subject export (F12.AC14).
 */

import { request, type ApiResult } from '@/api/client';
import type { components } from '@/api/generated/schema';
import { z } from '@/api/zod';

type S = components['schemas'];

const decrypted = z.object({ ip: z.string(), decrypted_at: z.string() }) satisfies z.ZodType<
  S['DecryptedIp']
>;
export type DecryptedIp = z.infer<typeof decrypted>;

/** Owner only, at most 10 an hour, and audited before it answers. 410 once purged. */
export function revealIp(visitId: string): Promise<ApiResult<DecryptedIp>> {
  return request(`/api/v1/visits/${encodeURIComponent(visitId)}/ip`, {
    parse: (value) => {
      const parsed = decrypted.safeParse(value);
      return parsed.success ? parsed.data : null;
    },
  });
}

/** The export is a plain navigation: the browser saves the JSON file itself. Audited. */
export function visitExportHref(visitId: string): string {
  return `/api/v1/visits/${encodeURIComponent(visitId)}/export`;
}
