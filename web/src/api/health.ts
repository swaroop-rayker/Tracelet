/**
 * Readiness probe client.
 *
 * Hand-written narrowing in M0 because `zod` is not installed yet — it arrives
 * with M5, when the generated OpenAPI client does. The principle is already in
 * force though: a JSON response is `unknown` until something proves its shape.
 * Casting it would move the failure from a caught error here to a `TypeError`
 * deep inside a component, which is the B5 blank-screen class of bug.
 */

export interface CheckResult {
  readonly ok: boolean;
  readonly detail: string;
}

export interface Readiness {
  readonly ready: boolean;
  readonly environment: string;
  readonly checks: Readonly<Record<string, CheckResult>>;
}

function isCheckResult(value: unknown): value is CheckResult {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return typeof candidate.ok === 'boolean' && typeof candidate.detail === 'string';
}

function isReadiness(value: unknown): value is Readiness {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Record<string, unknown>;
  if (typeof candidate.ready !== 'boolean') return false;
  if (typeof candidate.environment !== 'string') return false;
  if (typeof candidate.checks !== 'object' || candidate.checks === null) return false;
  return Object.values(candidate.checks as Record<string, unknown>).every(isCheckResult);
}

/** A failure the UI can render, rather than an exception it cannot. */
export interface ApiFailure {
  readonly kind: 'failure';
  readonly message: string;
  readonly traceId: string | null;
}

export interface ApiSuccess<T> {
  readonly kind: 'success';
  readonly data: T;
}

export type ApiResult<T> = ApiSuccess<T> | ApiFailure;

/**
 * Fetch readiness.
 *
 * A 503 is a legitimate, expected answer here — an unready instance reports
 * accurately rather than failing — so it is returned as data, not as an error.
 */
export async function fetchReadiness(): Promise<ApiResult<Readiness>> {
  let response: Response;
  try {
    response = await fetch('/readyz', { headers: { Accept: 'application/json' } });
  } catch {
    return { kind: 'failure', message: 'Could not reach the API.', traceId: null };
  }

  const traceId = response.headers.get('X-Trace-Id');

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return {
      kind: 'failure',
      message: `Malformed response (HTTP ${String(response.status)}).`,
      traceId,
    };
  }

  if (!isReadiness(body)) {
    return { kind: 'failure', message: 'Unexpected response shape from /readyz.', traceId };
  }

  return { kind: 'success', data: body };
}
