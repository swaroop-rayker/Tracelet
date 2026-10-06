/**
 * Server state for the dashboard: one fetch helper, one hook (ADR-0003).
 *
 * Everything goes through `request()` (client.ts), so errors stay values with a trace
 * id, and every payload is proven by a zod schema before a component sees it. React
 * Query needs a *thrown* error to enter its error state, so a failed result is thrown
 * as an `ApiFailure` carrying the `ApiError` -- and only here.
 */

import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import type { z } from 'zod';
import { request, type ApiError } from '@/api/client';

export class ApiFailure extends Error {
  readonly error: ApiError;

  constructor(error: ApiError) {
    super(error.message);
    this.name = 'ApiFailure';
    this.error = error;
  }
}

export async function getParsed<T>(
  path: string,
  params: URLSearchParams | null,
  schema: z.ZodType<T>,
): Promise<T> {
  const query = params?.toString() ?? '';
  const result = await request<T>(query === '' ? path : `${path}?${query}`, {
    parse: (value) => {
      const parsed = schema.safeParse(value);
      return parsed.success ? parsed.data : null;
    },
  });
  if (!result.ok) throw new ApiFailure(result.error);
  return result.data;
}

/** A GET whose cache key is its path and exact query string. */
export function useApi<T>(
  path: string,
  params: URLSearchParams | null,
  schema: z.ZodType<T>,
  options?: {
    readonly enabled?: boolean;
    /**
     * Poll every so many milliseconds (DESIGN 12 E18). React Query pauses polling while the
     * tab is hidden, so a background tab costs nothing (UI-18).
     */
    readonly refetchInterval?: number;
  },
): UseQueryResult<T, ApiFailure> {
  const key = params?.toString() ?? '';
  return useQuery<T, ApiFailure>({
    queryKey: [path, key],
    queryFn: () => getParsed(path, params, schema),
    enabled: options?.enabled ?? true,
    refetchInterval: options?.refetchInterval ?? false,
  });
}
