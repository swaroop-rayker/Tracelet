/**
 * Anchors a top-layer element (a native popover) to its trigger (ADR-0019).
 *
 * The coordinates are written as CSS custom properties through CSSOM, which the CSP allows,
 * and the stylesheet reads them (`top: var(--pop-y)`). The element flips above or below
 * and clamps inside the viewport.
 */

export type Side = 'below' | 'above';
export type Align = 'start' | 'end' | 'center';

const MARGIN = 8;

export function place(
  anchor: Element,
  floating: HTMLElement,
  { side = 'below', align = 'start', gap = 6 }: { side?: Side; align?: Align; gap?: number } = {},
): void {
  const a = anchor.getBoundingClientRect();
  const width = floating.offsetWidth;
  const height = floating.offsetHeight;
  // The client size excludes scrollbars, so nothing is placed underneath one.
  const vw = document.documentElement.clientWidth;
  const vh = document.documentElement.clientHeight;

  let x =
    align === 'end'
      ? a.right - width
      : align === 'center'
        ? a.left + a.width / 2 - width / 2
        : a.left;
  x = Math.max(MARGIN, Math.min(x, vw - width - MARGIN));

  const below = a.bottom + gap;
  const above = a.top - gap - height;
  let y: number;
  if (side === 'below') {
    y = below + height > vh - MARGIN && above >= MARGIN ? above : below;
  } else {
    y = above < MARGIN && below + height <= vh - MARGIN ? below : above;
  }
  y = Math.max(MARGIN, y);

  floating.style.setProperty('--pop-x', `${String(Math.round(x))}px`);
  floating.style.setProperty('--pop-y', `${String(Math.round(y))}px`);
}

/** Re-place `floating` while it is open, as the page scrolls or resizes. Returns a stop function. */
export function follow(
  anchor: Element,
  floating: HTMLElement,
  options?: Parameters<typeof place>[2],
): () => void {
  const update = (): void => {
    place(anchor, floating, options);
  };
  update();
  window.addEventListener('scroll', update, true);
  window.addEventListener('resize', update);
  return () => {
    window.removeEventListener('scroll', update, true);
    window.removeEventListener('resize', update);
  };
}
