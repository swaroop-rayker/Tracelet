/**
 * Annotations and saved views (docs/API.md §10a, F9.AC25, F9.AC26, M7.7).
 *
 * The two places an analyst writes, and only their own (SPEC §11 row 29): any admin adds a
 * note and changes or deletes their own, an owner deletes anyone's; saved views are each
 * admin's alone. The server enforces it; these calls only carry it.
 */

import { noContent, request, type ApiResult } from '@/api/client';
import type { components } from '@/api/generated/schema';
// Configured for the CSP (ERRORS E44, E54): never import zod directly.
import { z } from '@/api/zod';

type S = components['schemas'];

export const NOTES = '/api/v1/annotations';
export const VIEWS = '/api/v1/saved-views';

export const annotationSchema: z.ZodType<S['AnnotationOut']> = z.object({
  id: z.string(),
  at: z.string(),
  text: z.string(),
  link_id: z.string().nullable(),
  link_label: z.string().nullable(),
  author: z.object({ id: z.string(), name: z.string() }).nullable(),
  mine: z.boolean(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type Annotation = S['AnnotationOut'];

export const annotationListSchema: z.ZodType<S['AnnotationList']> = z.object({
  items: z.array(annotationSchema),
});

export const savedViewSchema: z.ZodType<S['SavedViewOut']> = z.object({
  id: z.string(),
  name: z.string(),
  path: z.string(),
  query: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type SavedView = S['SavedViewOut'];

export const savedViewListSchema = z.array(savedViewSchema);

function parseWith<T>(schema: z.ZodType<T>): (value: unknown) => T | null {
  return (value) => {
    const parsed = schema.safeParse(value);
    return parsed.success ? parsed.data : null;
  };
}

export interface NoteFields {
  readonly at: string;
  readonly text: string;
  readonly link_id: string | null;
}

export function createNote(csrfToken: string, note: NoteFields): Promise<ApiResult<Annotation>> {
  return request(NOTES, {
    method: 'POST',
    body: note,
    csrfToken,
    parse: parseWith(annotationSchema),
  });
}

export function updateNote(
  csrfToken: string,
  id: string,
  note: Partial<NoteFields>,
): Promise<ApiResult<Annotation>> {
  return request(`${NOTES}/${id}`, {
    method: 'PATCH',
    body: note,
    csrfToken,
    parse: parseWith(annotationSchema),
  });
}

export function deleteNote(csrfToken: string, id: string): Promise<ApiResult<null>> {
  return request(`${NOTES}/${id}`, { method: 'DELETE', csrfToken, parse: noContent });
}

export function createView(
  csrfToken: string,
  view: { readonly name: string; readonly path: string; readonly query: string },
): Promise<ApiResult<SavedView>> {
  return request(VIEWS, {
    method: 'POST',
    body: view,
    csrfToken,
    parse: parseWith(savedViewSchema),
  });
}

export function renameView(
  csrfToken: string,
  id: string,
  name: string,
): Promise<ApiResult<SavedView>> {
  return request(`${VIEWS}/${id}`, {
    method: 'PATCH',
    body: { name },
    csrfToken,
    parse: parseWith(savedViewSchema),
  });
}

export function deleteView(csrfToken: string, id: string): Promise<ApiResult<null>> {
  return request(`${VIEWS}/${id}`, { method: 'DELETE', csrfToken, parse: noContent });
}

/** Where a saved view opens: its page with its query, exactly as it was saved. */
export function viewHref(view: Pick<SavedView, 'path' | 'query'>): string {
  return view.query === '' ? view.path : `${view.path}?${view.query}`;
}
