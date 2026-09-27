/**
 * The admin authentication endpoints (docs/API.md §4, §5).
 *
 * One function per endpoint, each returning a parsed result. Shapes are narrowed by
 * hand until `zod` and the generated client arrive in M5; the generated types in
 * `src/api/generated/schema.d.ts` are the contract, and these parsers are what
 * enforces it at runtime.
 *
 * Note what is deliberately absent: nothing here stores the session. The session is
 * an opaque `__Host-` cookie the browser holds and this code cannot read, which is
 * the point -- there is no token in `localStorage` for a script injection to steal.
 * The only thing kept in memory is the CSRF token.
 */

import { noContent, request, type ApiResult } from '@/api/client';

const AUTH = '/api/v1/auth';
const ADMINS = '/api/v1/admins';

export type AdminRole = 'owner' | 'analyst';
export type AdminStatus = 'pending_enrollment' | 'active' | 'disabled';

export interface Me {
  readonly id: string;
  readonly email: string;
  readonly display_name: string;
  readonly role: AdminRole;
  readonly status: AdminStatus;
  readonly timezone: string;
  readonly theme: string;
  readonly totp_enrolled: boolean;
  readonly telegram_verified: boolean;
  readonly recovery_codes_remaining: number;
  readonly csrf_token: string;
  readonly session_id: string;
  readonly session_expires_at: string;
}

export interface MfaChallenge {
  readonly mfa_token: string;
  readonly expires_at: string;
}

export interface Enrollment {
  readonly secret: string;
  readonly otpauth_uri: string;
  readonly recovery_codes: readonly string[];
  readonly hint: string;
  readonly confirm_token: string;
}

export interface SessionSummary {
  readonly id: string;
  readonly created_at: string;
  readonly last_seen_at: string;
  readonly expires_at: string;
  readonly ip_prefix: string | null;
  readonly current: boolean;
}

export interface AdminSummary {
  readonly id: string;
  readonly email: string;
  readonly display_name: string;
  readonly role: AdminRole;
  readonly status: AdminStatus;
  readonly totp_enrolled: boolean;
  readonly telegram_verified: boolean;
  readonly last_login_at: string | null;
  readonly locked_until: string | null;
  readonly created_at: string;
}

// ---------------------------------------------------------------------------
// Narrowing
// ---------------------------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function str(value: unknown): value is string {
  return typeof value === 'string';
}

function parseMe(value: unknown): Me | null {
  if (!isRecord(value)) return null;
  const {
    id,
    email,
    display_name,
    role,
    status,
    timezone,
    theme,
    totp_enrolled,
    telegram_verified,
    recovery_codes_remaining,
    csrf_token,
    session_id,
    session_expires_at,
  } = value;
  if (!str(id) || !str(email) || !str(display_name) || !str(timezone) || !str(theme)) return null;
  if (role !== 'owner' && role !== 'analyst') return null;
  if (status !== 'pending_enrollment' && status !== 'active' && status !== 'disabled') return null;
  if (typeof totp_enrolled !== 'boolean' || typeof telegram_verified !== 'boolean') return null;
  if (typeof recovery_codes_remaining !== 'number') return null;
  if (!str(csrf_token) || !str(session_id) || !str(session_expires_at)) return null;
  return {
    id,
    email,
    display_name,
    role,
    status,
    timezone,
    theme,
    totp_enrolled,
    telegram_verified,
    recovery_codes_remaining,
    csrf_token,
    session_id,
    session_expires_at,
  };
}

function parseMfaChallenge(value: unknown): MfaChallenge | null {
  if (!isRecord(value)) return null;
  const { mfa_token, expires_at } = value;
  return str(mfa_token) && str(expires_at) ? { mfa_token, expires_at } : null;
}

function parseEnrollment(value: unknown): Enrollment | null {
  if (!isRecord(value)) return null;
  const { secret, otpauth_uri, recovery_codes, hint, confirm_token } = value;
  if (!str(secret) || !str(otpauth_uri) || !str(hint) || !str(confirm_token)) return null;
  if (!Array.isArray(recovery_codes) || !recovery_codes.every(str)) return null;
  return { secret, otpauth_uri, recovery_codes, hint, confirm_token };
}

function parseSessions(value: unknown): readonly SessionSummary[] | null {
  if (!Array.isArray(value)) return null;
  const rows: SessionSummary[] = [];
  for (const entry of value) {
    if (!isRecord(entry)) return null;
    const { id, created_at, last_seen_at, expires_at, ip_prefix, current } = entry;
    if (!str(id) || !str(created_at) || !str(last_seen_at) || !str(expires_at)) return null;
    if (typeof current !== 'boolean') return null;
    rows.push({
      id,
      created_at,
      last_seen_at,
      expires_at,
      ip_prefix: str(ip_prefix) ? ip_prefix : null,
      current,
    });
  }
  return rows;
}

function parseAdmins(value: unknown): readonly AdminSummary[] | null {
  if (!Array.isArray(value)) return null;
  const rows: AdminSummary[] = [];
  for (const entry of value) {
    if (!isRecord(entry)) return null;
    const { id, email, display_name, role, status } = entry;
    if (!str(id) || !str(email) || !str(display_name)) return null;
    if (role !== 'owner' && role !== 'analyst') return null;
    if (status !== 'pending_enrollment' && status !== 'active' && status !== 'disabled') {
      return null;
    }
    rows.push({
      id,
      email,
      display_name,
      role,
      status,
      totp_enrolled: entry.totp_enrolled === true,
      telegram_verified: entry.telegram_verified === true,
      last_login_at: str(entry.last_login_at) ? entry.last_login_at : null,
      locked_until: str(entry.locked_until) ? entry.locked_until : null,
      created_at: str(entry.created_at) ? entry.created_at : '',
    });
  }
  return rows;
}

function parseCodes(value: unknown): readonly string[] | null {
  return Array.isArray(value) && value.every(str) ? value : null;
}

// ---------------------------------------------------------------------------
// Sign in
// ---------------------------------------------------------------------------

/** Step 1 of 2. Returns a short-lived MFA token -- deliberately not a session. */
export function login(email: string, password: string): Promise<ApiResult<MfaChallenge>> {
  return request(`${AUTH}/login`, {
    method: 'POST',
    body: { email, password },
    parse: parseMfaChallenge,
  });
}

/** Step 2 of 2. The session cookie and the CSRF token arrive with the response. */
export function submitMfa(mfaToken: string, code: string): Promise<ApiResult<null>> {
  return request(`${AUTH}/mfa`, {
    method: 'POST',
    body: { mfa_token: mfaToken, code },
    parse: noContent,
  });
}

/**
 * Bypasses both factors, which is what a recovery code is for. Single-use.
 *
 * Not named after the endpoint (`useRecoveryCode`) because `use` is the React hook
 * prefix, and eslint's rules-of-hooks is right to reject it: a function that looks
 * like a hook and is called conditionally inside a handler is a real bug pattern.
 */
export function signInWithRecoveryCode(email: string, code: string): Promise<ApiResult<null>> {
  return request(`${AUTH}/recovery-code`, {
    method: 'POST',
    body: { email, code },
    parse: noContent,
  });
}

export function fetchMe(): Promise<ApiResult<Me>> {
  return request(`${AUTH}/me`, { parse: parseMe });
}

export function logout(csrfToken: string): Promise<ApiResult<null>> {
  return request(`${AUTH}/logout`, { method: 'POST', csrfToken, parse: noContent });
}

// ---------------------------------------------------------------------------
// Enrolment
// ---------------------------------------------------------------------------

/**
 * Sets the first password and returns the TOTP secret plus ten recovery codes.
 *
 * Returned exactly once. Neither is retrievable afterwards, which is why the page
 * that calls this must not navigate away before the admin has saved them.
 */
export function enroll(token: string, password: string): Promise<ApiResult<Enrollment>> {
  return request(`${AUTH}/enroll`, {
    method: 'POST',
    body: { token, password },
    parse: parseEnrollment,
  });
}

/** Proves the authenticator works, activates the account and signs in. */
export function confirmTotp(confirmToken: string, code: string): Promise<ApiResult<null>> {
  return request(`${AUTH}/totp/confirm`, {
    method: 'POST',
    body: { confirm_token: confirmToken, code },
    parse: noContent,
  });
}

export function regenerateRecoveryCodes(csrfToken: string): Promise<ApiResult<readonly string[]>> {
  return request(`${AUTH}/totp/regenerate-codes`, {
    method: 'POST',
    csrfToken,
    parse: parseCodes,
  });
}

// ---------------------------------------------------------------------------
// Password
// ---------------------------------------------------------------------------

export function changePassword(
  csrfToken: string,
  currentPassword: string,
  newPassword: string,
): Promise<ApiResult<null>> {
  return request(`${AUTH}/password`, {
    method: 'POST',
    csrfToken,
    body: { current_password: currentPassword, new_password: newPassword },
    parse: noContent,
  });
}

/** Always answers 202, whether or not the account exists (F8.AC10). */
export function requestPasswordReset(email: string): Promise<ApiResult<null>> {
  return request(`${AUTH}/reset/request`, {
    method: 'POST',
    body: { email },
    parse: noContent,
  });
}

export function confirmPasswordReset(token: string, newPassword: string): Promise<ApiResult<null>> {
  return request(`${AUTH}/reset/confirm`, {
    method: 'POST',
    body: { token, new_password: newPassword },
    parse: noContent,
  });
}

// ---------------------------------------------------------------------------
// Sessions and admins
// ---------------------------------------------------------------------------

export function fetchSessions(): Promise<ApiResult<readonly SessionSummary[]>> {
  return request(`${AUTH}/sessions`, { parse: parseSessions });
}

export function revokeSession(csrfToken: string, sessionId: string): Promise<ApiResult<null>> {
  return request(`${AUTH}/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
    csrfToken,
    parse: noContent,
  });
}

export function fetchAdmins(): Promise<ApiResult<readonly AdminSummary[]>> {
  return request(ADMINS, { parse: parseAdmins });
}

// ---------------------------------------------------------------------------
// Telegram recovery channel
// ---------------------------------------------------------------------------

export function startTelegramVerification(
  csrfToken: string,
  chatId: number,
): Promise<ApiResult<null>> {
  return request(`${AUTH}/telegram/verify/start`, {
    method: 'POST',
    csrfToken,
    body: { chat_id: chatId },
    parse: noContent,
  });
}

export function confirmTelegramVerification(
  csrfToken: string,
  code: string,
): Promise<ApiResult<null>> {
  return request(`${AUTH}/telegram/verify/confirm`, {
    method: 'POST',
    csrfToken,
    body: { code },
    parse: noContent,
  });
}
