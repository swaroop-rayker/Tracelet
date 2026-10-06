/**
 * The toast queue (DESIGN §5.6, §12 E26): a tiny store outside React, so any handler can say
 * "Theme saved" without threading a context through every page. `<Toaster>` renders it.
 *
 * A toast is never the only feedback (the row it came from changes too) and never an error,
 * which stays where the action happened.
 */

export interface ToastItem {
  readonly id: number;
  readonly message: string;
}

const LIFETIME_MS = 4_000;

let items: readonly ToastItem[] = [];
let next = 1;
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

export function dismissToast(id: number): void {
  const remaining = items.filter((t) => t.id !== id);
  if (remaining.length === items.length) return;
  items = remaining;
  emit();
}

export function toast(message: string): void {
  const id = next++;
  items = [...items.slice(-2), { id, message }];
  emit();
  setTimeout(() => {
    dismissToast(id);
  }, LIFETIME_MS);
}

export function subscribeToasts(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function currentToasts(): readonly ToastItem[] {
  return items;
}
