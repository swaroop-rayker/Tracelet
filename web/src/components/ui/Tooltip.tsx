/**
 * Tooltip (DESIGN §5.5): short, non-essential help. It shows after 500 ms on hover and at once
 * on keyboard focus, hides on leave, blur or Escape, and never holds the only copy of anything
 * important.
 *
 * A native `popover="manual"` element in the top layer, anchored by `position.ts`. With
 * `labelOnly` the tooltip repeats the trigger's accessible name (an IconButton's
 * `aria-label`), so it is hidden from assistive technology rather than read twice.
 */

import { useEffect, useId, useRef, type ReactNode } from 'react';
import { Icon } from '@/components/icons';
import { follow } from '@/components/ui/position';

const Info = Icon.Info;

const HOVER_DELAY_MS = 500;

export function Tooltip({
  content,
  children,
  labelOnly = false,
  side = 'above',
  anyFocus = false,
}: {
  readonly content: ReactNode;
  /**
   * The trigger. A function receives the tooltip's id, to put on the focusable element as
   * `aria-describedby` -- the wrapper itself never takes focus.
   */
  readonly children: ReactNode | ((describedBy: string | undefined) => ReactNode);
  readonly labelOnly?: boolean;
  readonly side?: 'above' | 'below';
  /** Show on any focus, a tap included, not only keyboard focus: for touch screens. */
  readonly anyFocus?: boolean;
}): React.JSX.Element {
  const id = useId();
  const anchor = useRef<HTMLSpanElement | null>(null);
  const tip = useRef<HTMLDivElement | null>(null);
  const timer = useRef<number | null>(null);
  const stop = useRef<(() => void) | null>(null);

  const hide = (): void => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
    stop.current?.();
    stop.current = null;
    const el = tip.current;
    if (el?.matches(':popover-open') === true) el.hidePopover();
  };
  const show = (): void => {
    const el = tip.current;
    const at = anchor.current;
    if (el === null || at === null || el.matches(':popover-open')) return;
    el.showPopover();
    stop.current = follow(at, el, { side, align: 'center' });
  };
  const showLater = (): void => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(show, HOVER_DELAY_MS);
  };

  useEffect(() => hide, []);

  return (
    <span
      ref={anchor}
      className="tip-anchor"
      onMouseEnter={showLater}
      onMouseLeave={hide}
      onFocus={(event) => {
        if (anyFocus || event.target.matches(':focus-visible')) show();
      }}
      onBlur={hide}
      onKeyDown={(event) => {
        if (event.key === 'Escape') hide();
      }}
    >
      {typeof children === 'function' ? children(labelOnly ? undefined : id) : children}
      <div
        ref={tip}
        id={id}
        popover="manual"
        role={labelOnly ? undefined : 'tooltip'}
        aria-hidden={labelOnly ? true : undefined}
        className="tip"
      >
        {content}
      </div>
    </span>
  );
}

/** The ⓘ beside a term: a small button whose description is the definition (DESIGN §7.4). */
export function InfoTip({
  term,
  children,
}: {
  /** What is being explained, for the button's name: "About stage mix". */
  readonly term: string;
  readonly children: ReactNode;
}): React.JSX.Element {
  return (
    <Tooltip content={children} side="below" anyFocus>
      {(describedBy) => (
        <button
          type="button"
          className="info-tip"
          aria-label={`About ${term}`}
          aria-describedby={describedBy}
        >
          <Info size={13} strokeWidth={1.75} aria-hidden="true" />
        </button>
      )}
    </Tooltip>
  );
}
