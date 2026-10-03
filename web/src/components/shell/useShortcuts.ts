/**
 * Global keyboard shortcuts (DESIGN §12 E2). Ctrl/⌘ K works everywhere; the single keys
 * (`/`, `?`, `[`, `g` + a page key) only when the focus is not in a text field, so typing is
 * never hijacked. Listed in the help sheet.
 */

import { useEffect, useRef } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { ALL_ITEMS } from '@/components/shell/nav';

function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement
  );
}

export function useShortcuts({
  openPalette,
  openHelp,
  toggleSidebar,
}: {
  readonly openPalette: () => void;
  readonly openHelp: () => void;
  readonly toggleSidebar: () => void;
}): void {
  const navigate = useNavigate();
  const location = useLocation();
  const pendingG = useRef<number | null>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        openPalette();
        return;
      }
      if (event.ctrlKey || event.metaKey || event.altKey || typing(event.target)) return;
      if (document.querySelector('dialog[open]') !== null) return;

      if (pendingG.current !== null) {
        window.clearTimeout(pendingG.current);
        pendingG.current = null;
        const item = ALL_ITEMS.find((i) => i.key === event.key.toLowerCase());
        if (item !== undefined) {
          event.preventDefault();
          void navigate({ pathname: item.to, search: item.filtered ? location.search : '' });
        }
        return;
      }
      switch (event.key) {
        case '/':
          event.preventDefault();
          openPalette();
          break;
        case '?':
          event.preventDefault();
          openHelp();
          break;
        case '[':
          event.preventDefault();
          toggleSidebar();
          break;
        case 'g':
          pendingG.current = window.setTimeout(() => {
            pendingG.current = null;
          }, 1000);
          break;
        default:
          break;
      }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
    };
  }, [navigate, location.search, openPalette, openHelp, toggleSidebar]);
}
