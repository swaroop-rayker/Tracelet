/**
 * The HTTP layer for the admin API.
 *
 * Three decisions here shape every page above it.
 *
 * **Errors are values, not exceptions.** Every call resolves to a result the UI can
 * render. A rejected promise in a component is how a blank screen happens (B5), and
 * F9.AC18 requires an explicit error state everywhere -- which is only writable if
 * the error arrives as data.
 *
 * **RFC 9457 Problem Details are parsed once.** The API has exactly one error shape
 * (ES4), so exactly one place needs to understand it: `code` for a decision,
 * `errors[]` for per-field messages, `trace_id` to show the operator.
 *
 * **A response body is `unknown` until something proves its shape.** `zod` is not
 * installed until M5, so narrowing is hand-written. Casting instead would move the
 * failure from a caught error here to a `TypeError` inside a component.
 */

/** The CSRF header name. Sent on every state-changing request (F8.AC11). */
export const CSRF_HEADER = 'X-CSRF-Token';

/** One field-level validation failure, as the API reports it. */
export interface FieldProblem {
  readonly field: string;
  readonly code: string;
  readonly message: string;
  /** A point on the map the error refers to: where a geofence ring crosses itself (API §12). */
  readonly location?: { readonly lat: number; readonly lng: number };
}

/** Everything a page needs in order to say something useful about a failure. */
export interface ApiError {
  /** The API's own `code`, or `TRANSPORT` when the request never arrived. */
  readonly code: string;
  readonly message: string;
  readonly status: number | null;
  readonly traceId: string | null;
  readonly fields: readonly FieldProblem[];
  /** Seconds, from `Retry-After` on a 429. */
  readonly retryAfter: number | null;
}

export type ApiResult<T> =
  | { readonly ok: true; readonly data: T; readonly csrfToken: string | null }
  | { readonly ok: false; readonly error: ApiError };

export interface RequestOptions<T> {
  readonly method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  readonly body?: unknown;
  /** Required on every state-changing request once a session exists. */
  readonly csrfToken?: string | null;
  /** Proves the response shape. Return `null` to reject it. */
  readonly parse: (value: unknown) => T | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function parseFieldProblems(value: unknown): readonly FieldProblem[] {
  if (!Array.isArray(value)) return [];
  const problems: FieldProblem[] = [];
  for (const entry of value) {
    if (!isRecord(entry)) continue;
    const { field, code, message, location } = entry;
    if (typeof field === 'string' && typeof code === 'string' && typeof message === 'string') {
      const at =
        isRecord(location) && typeof location.lat === 'number' && typeof location.lng === 'number'
          ? { lat: location.lat, lng: location.lng }
          : null;
      problems.push(
        at === null ? { field, code, message } : { field, code, message, location: at },
      );
    }
  }
  return problems;
}

/**
 * Read a header without caring about its case.
 *
 * `Headers.get` is case-insensitive by specification, and using it is the whole
 * point: the stack normalises `X-CSRF-Token` to `X-Csrf-Token`, so any code that
 * indexes a plain object by the exact name it sent finds nothing and every
 * subsequent state-changing request fails with a 403 that looks like a server bug.
 */
function header(response: Response, name: string): string | null {
  return response.headers.get(name);
}

function retryAfterSeconds(response: Response): number | null {
  const raw = header(response, 'Retry-After');
  if (raw === null) return null;
  const seconds = Number.parseInt(raw, 10);
  return Number.isFinite(seconds) ? seconds : null;
}

/** Fallback text for a status the API did not describe itself. */
function genericMessage(status: number): string {
  if (status === 401) return 'Sign in to continue.';
  if (status === 403) return 'That action is not permitted.';
  if (status === 404) return 'Not found.';
  if (status >= 500) return 'The server could not complete the request.';
  return `Request failed (HTTP ${String(status)}).`;
}

function toApiError(response: Response, body: unknown): ApiError {
  const traceId = header(response, 'X-Trace-Id');
  if (isRecord(body) && typeof body.code === 'string') {
    const detail = typeof body.detail === 'string' && body.detail.length > 0 ? body.detail : null;
    const title = typeof body.title === 'string' ? body.title : null;
    return {
      code: body.code,
      message: detail ?? title ?? genericMessage(response.status),
      status: response.status,
      traceId: typeof body.trace_id === 'string' ? body.trace_id : traceId,
      fields: parseFieldProblems(body.errors),
      retryAfter: retryAfterSeconds(response),
    };
  }
  return {
    code: `HTTP_${String(response.status)}`,
    message: genericMessage(response.status),
    status: response.status,
    traceId,
    fields: [],
    retryAfter: retryAfterSeconds(response),
  };
}

/** A parser for the endpoints that answer 204 with no body. */
export function noContent(): null {
  return null;
}

export async function request<T>(path: string, options: RequestOptions<T>): Promise<ApiResult<T>> {
  const method = options.method ?? 'GET';
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  if (options.csrfToken !== undefined && options.csrfToken !== null) {
    headers[CSRF_HEADER] = options.csrfToken;
  }

  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers,
      // The session is an opaque cookie, so the browser has to send it. `same-origin`
      // rather than `include`: there is no cross-origin caller, and saying so keeps
      // it that way.
      credentials: 'same-origin',
      body: options.body === undefined ? null : JSON.stringify(options.body),
    });
  } catch {
    return {
      ok: false,
      error: {
        code: 'TRANSPORT',
        message: 'Could not reach the server. Check your connection and try again.',
        status: null,
        traceId: null,
        fields: [],
        retryAfter: null,
      },
    };
  }

  const csrfToken = header(response, CSRF_HEADER);

  // Read the body as text first, so "the server sent nothing" and "the server sent
  // something unreadable" stay distinguishable. Keying that off the status code
  // instead was a real bug: `/reset/request` and `/telegram/verify/start` answer
  // **202** with no body, and a check that exempted only 204 reported both as a
  // malformed response — after the server had already done the work and sent the
  // Telegram message (docs/ERRORS.md E18).
  const raw = await response.text().catch(() => '');
  const isEmpty = raw.trim().length === 0;

  let body: unknown = null;
  let unreadable = false;
  if (!isEmpty) {
    try {
      body = JSON.parse(raw);
    } catch {
      unreadable = true;
    }
  }

  if (!response.ok) {
    return { ok: false, error: toApiError(response, body) };
  }

  const parsed = options.parse(body);
  // An empty body is a legitimate success for every no-content endpoint whatever
  // its status, so only a body that arrived and could not be understood is a fault.
  if (unreadable || (parsed === null && !isEmpty)) {
    return {
      ok: false,
      error: {
        code: 'MALFORMED_RESPONSE',
        message: 'The server returned something this page does not understand.',
        status: response.status,
        traceId: header(response, 'X-Trace-Id'),
        fields: [],
        retryAfter: null,
      },
    };
  }

  return { ok: true, data: parsed as T, csrfToken };
}

/** The message for one field, if the API blamed that field. */
export function fieldMessage(error: ApiError | null, field: string): string | null {
  if (error === null) return null;
  const match = error.fields.find((problem) => problem.field === field);
  return match?.message ?? null;
}
